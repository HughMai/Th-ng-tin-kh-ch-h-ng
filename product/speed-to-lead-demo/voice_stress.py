"""voice_stress.py — synthetic-caller stress test of the DEPLOYED Deepgram voice agent.

Drives the real Voice Agent API (wss://agent.deepgram.com) with the EXACT settings
the live app uses (voice_server._agent_settings → lean prompt + Katie/Aura + Haiku
think + Flux turn-taking), so it tests what callers actually get. A Twilio phone
leg is NOT exercised here — that's infra (the 987ms connect is a separate fix). This
catches the brain-level bugs: repetition, looping, bad triage, broken close.

How it works: a synthetic caller speaks each scenario's lines (Deepgram Aura TTS →
8kHz mulaw, real-time paced + a silence pad so Flux hears end-of-turn), the agent
replies, and we capture the full ConversationText transcript + tool calls. Each call
is scored two ways: deterministic checks (repetition, markdown-in-speech, one
question/turn, expected tools) + a Haiku grader (reuse evals.grade machinery).

Run inside the container (has deps + keys):
    docker compose exec -T app python voice_stress.py --single V2          # one call, print transcript
    docker compose exec -T app python voice_stress.py --runs 3 --json      # full batch, machine-readable
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field

import websockets

import evals
import tenants
import voice_server
from evals import VOICE_ALWAYS_RULES, Grade, GRADER_MODEL, _grader

DG_URL = "wss://agent.deepgram.com/v1/agent/converse"
# The CALLER's voice (Aura). Distinct from the agent's voice (Katie/Cartesia).
# asteria transcribes far more cleanly than hyperion under Deepgram STT (probe
# 2026-06-27: hyperion garbled "Tuesday morning's great"→"Mardi's crate"; asteria
# got it right). Cleaner caller STT = less noise in the stress signal.
CALLER_VOICE = os.environ.get("STRESS_CALLER_VOICE", "aura-2-asteria-en")
_BULLET_RE = evals._BULLET_RE
_EMOJI_RE = evals._EMOJI_RE


# --- caller speech: text -> 8kHz mulaw bytes (Deepgram Aura TTS REST) --------

def synth_caller(text: str) -> bytes:
    key = os.environ.get("DEEPGRAM_API_KEY") or os.environ.get("Deepgram_API")
    if not key:
        raise RuntimeError("DEEPGRAM_API_KEY not set")
    url = (
        "https://api.deepgram.com/v1/speak?model=" + CALLER_VOICE
        + "&encoding=mulaw&sample_rate=8000&container=none"
    )
    req = urllib.request.Request(
        url,
        data=json.dumps({"text": text}).encode(),
        headers={"Authorization": f"Token {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()


# --- reactive caller (a second Haiku plays the customer) ---------------------

CALLER_SYS = (
    "You are role-playing a CUSTOMER on a live phone call to an electrician's AI "
    "receptionist. YOU called them; they just picked up.\n\n"
    "Your persona and facts:\n{persona}\n\n"
    "Rules:\n"
    "- Speak the way a real customer does: short, casual, one or two sentences. "
    "Contractions, plain words, a touch of Aussie casualness is fine.\n"
    "- ANSWER the receptionist's questions using your facts (suburb, what you need, "
    "time preference). If they offer a time that suits you, accept; if not, say so.\n"
    "- Be a real person, not over-helpful. If they repeat themselves, you can get a "
    "little short or just repeat your answer.\n"
    "- When they ask 'anything else?' and you're done, reply like 'No, that's all, "
    "thanks.' to end the call.\n"
    "- Output ONLY the words you say aloud. No quotes, no labels, no narration."
)


def _looks_done(text: str) -> bool:
    t = (text or "").lower()
    return any(p in t for p in ["that's all", "thats all", "no thanks", "nothing else",
                                "all for now", "goodbye", "cheers, bye"]) and "yes" not in t


def caller_turn(persona: str, history: list[dict]) -> str:
    """The synthetic caller's next spoken line, reacting to the agent's last turn.
    From the caller's viewpoint the AGENT is the 'user' speaking to them, so roles
    are swapped when building the message history."""
    msgs = []
    for e in history:
        role = "user" if e["role"] == "assistant" else "assistant"  # caller = assistant
        msgs.append({"role": role, "content": e["text"]})
    if not msgs:
        msgs.append({"role": "user", "content": "(the receptionist just answered)"})
    try:
        resp = _grader.messages.create(
            model=GRADER_MODEL, max_tokens=80, temperature=0.7,
            system=CALLER_SYS.format(persona=persona), messages=msgs,
        )
        txt = (resp.content[0].text or "").strip().strip('"').strip()
        return txt or "Yeah, no worries."
    except Exception:  # noqa: BLE001
        return "Yeah, no worries."


# --- one synthetic call ------------------------------------------------------

# Server-side tool responses, matching voice_server._run_function so the agent's
# flow stays realistic (these are what Hughie's bridge returns to Deepgram).
_FUNC_REPLY = {
    "alert_owner": "Noted for the post-call owner summary.",
    "book_job": "Booking details noted for after the call.",
    "end_call": "Call ended.",
}


async def _open_dg(key: str, settings: dict):
    headers = {"Authorization": f"Token {key}"}
    try:
        return await websockets.connect(DG_URL, additional_headers=headers)
    except TypeError:  # older websockets
        return await websockets.connect(DG_URL, extra_headers=headers)


_CHUNK = 800  # 100ms of 8kHz μ-law audio


async def _wait_turn(state: dict, cap: float, quiet: float) -> None:
    """Wait for the agent to finish its turn: returns once it has spoken and then
    gone quiet for `quiet` seconds, or on end_call, or after `cap` seconds."""
    start = time.monotonic()
    spoke = False
    while time.monotonic() - start < cap:
        await asyncio.sleep(0.2)
        if state["last_text"] > start:
            spoke = True
        if spoke and time.monotonic() - state["last_text"] > quiet:
            return
        if state["end"]:
            return


async def place_call(tenant: dict, persona: str,
                     greeting_wait: float = 4.0, turn_cap: float = 18.0,
                     quiet: float = 3.0, max_turns: int = 12) -> dict:
    settings = voice_server._agent_settings(tenant)
    key = os.environ.get("DEEPGRAM_API_KEY") or os.environ.get("Deepgram_API")
    state = {"transcript": [], "functions": [], "errors": [], "last_text": 0.0, "end": False}
    audio_q: asyncio.Queue = asyncio.Queue()

    async def pump(ws):
        # Owns ALL audio sends. Real-time paced (10 x 100ms ticks/sec). Sends caller
        # chunks when queued, μ-law silence otherwise — so Deepgram always sees a
        # continuous audio stream (like a real phone mic). That prevents
        # CLIENT_MESSAGE_TIMEOUT and stops VAD splitting utterances on send gaps.
        silence = b"\xff" * _CHUNK
        start = time.monotonic()
        ticks = 0
        while True:
            try:
                item = audio_q.get_nowait()
            except asyncio.QueueEmpty:
                item = None
            if item == "__stop":
                break
            try:
                await ws.send(item if isinstance(item, (bytes, bytearray)) else silence)
            except Exception as exc:  # noqa: BLE001
                state["errors"].append(f"pump_send: {type(exc).__name__}")
                break
            ticks += 1
            delay = (start + ticks * (_CHUNK / 8000.0)) - time.monotonic()
            await asyncio.sleep(delay if delay > 0 else 0)

    async def speak(text):
        # Enqueue one utterance as gap-free chunks (lead + audio + trail silence),
        # then wait for it to play out at real time before returning.
        audio = await asyncio.to_thread(synth_caller, text)
        full = b"\xff" * (3 * _CHUNK) + audio + b"\xff" * (5 * _CHUNK)
        for i in range(0, len(full), _CHUNK):
            audio_q.put_nowait(full[i:i + _CHUNK])
        await asyncio.sleep(len(full) / 8000.0)

    async def reader(ws):
        try:
            async for raw in ws:
                if isinstance(raw, (bytes, bytearray)):
                    continue  # agent audio — we judge via the transcript, not by ear
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t = msg.get("type")
                raw_types = state.setdefault("raw", [])
                if len(raw_types) < 80:
                    raw_types.append(t or "?")
                if t == "ConversationText":
                    state["transcript"].append({"role": msg.get("role"), "text": msg.get("content", "")})
                    state["last_text"] = time.monotonic()
                elif t == "FunctionCallRequest":
                    for fn in msg.get("functions", []):
                        nm = fn.get("name")
                        state["functions"].append(nm)
                        await ws.send(json.dumps({
                            "type": "FunctionCallResponse", "id": fn.get("id"),
                            "name": nm, "content": _FUNC_REPLY.get(nm, "ok"),
                        }))
                        if nm == "end_call":
                            state["end"] = True
                elif t in ("Error", "Warning"):
                    state["errors"].append(f"{t}:{msg.get('code')} {msg.get('description')}")
        except Exception as exc:  # noqa: BLE001
            state["errors"].append(f"reader:{exc}")

    ws = await _open_dg(key, settings)
    await ws.send(json.dumps(settings))  # Settings must be the first client message (matches voice_server._deepgram_connect)
    reader_t = asyncio.create_task(reader(ws))
    pump_t = asyncio.create_task(pump(ws))
    try:
        await asyncio.sleep(greeting_wait)  # let the greeting land in the transcript
        last_count = len(state["transcript"])
        for _ in range(max_turns):
            if state["end"]:
                break
            history = list(state["transcript"])
            reply = await asyncio.to_thread(caller_turn, persona, history)
            await speak(reply)
            await _wait_turn(state, turn_cap, quiet)
            # no new agent speech since the caller spoke = stalled; stop the loop
            if len(state["transcript"]) <= last_count and not state["end"]:
                break
            last_count = len(state["transcript"])
            if _looks_done(reply) and not state["end"]:
                await _wait_turn(state, turn_cap, quiet)  # let the agent goodbye + end_call
                break
        if not state["end"]:  # let any final turn / end_call play out
            await _wait_turn(state, turn_cap, quiet)
    except Exception as exc:  # noqa: BLE001 — capture close/abort, still return state for diagnosis
        state["errors"].append(f"call_aborted: {type(exc).__name__}: {exc}")
    finally:
        audio_q.put_nowait("__stop")
        try:
            await asyncio.wait_for(pump_t, timeout=2)
        except Exception:  # noqa: BLE001
            pump_t.cancel()
        reader_t.cancel()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
    return state


# --- scoring -----------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def deterministic(sc: dict, res: dict) -> list[str]:
    fails: list[str] = []
    assistant = [e["text"] for e in res["transcript"] if e["role"] == "assistant"]
    if not assistant:
        return ["agent never spoke"]
    # repetition — consecutive near-identical assistant lines (Hughie's reported bug)
    for a, b in zip(assistant, assistant[1:]):
        if len(_norm(a)) > 4 and _norm(a) == _norm(b):
            fails.append(f"repeated a line: '{b[:70]}'")
            break
    joined = " ".join(assistant)
    if _BULLET_RE.search(joined) or "**" in joined or "#" in joined:
        fails.append("markdown/bullets/asterisks in speech")
    if _EMOJI_RE.search(joined):
        fails.append("emoji in speech")
    for i, t in enumerate(assistant):
        if t.count("?") > 1:
            fails.append(f"turn {i} asks {t.count('?')} questions (voice = one at a time)")
            break
    if sc.get("expect_alert") and "alert_owner" not in res["functions"]:
        fails.append("no alert_owner")
    if sc.get("expect_book") and "book_job" not in res["functions"]:
        fails.append("no book_job")
    if sc.get("expect_end") and "end_call" not in res["functions"]:
        fails.append("no end_call (didn't close the call)")
    if res["errors"]:
        fails.append("deepgram errors: " + "; ".join(res["errors"][:2]))
    return fails


def grade_call(sc: dict, res: dict) -> Grade:
    transcript = "\n".join(
        f"{'Caller' if e['role'] == 'user' else 'Assistant'}: {e['text']}"
        for e in res["transcript"]
    )
    funcs = ", ".join(res["functions"]) or "(none)"
    prompt = (
        "You are QA-grading ONE complete live phone call to an electrician's AI "
        "receptionist (the assistant answers missed calls).\n\n"
        f"REQUIREMENT the agent must meet throughout the call:\n{VOICE_ALWAYS_RULES}\n\n{sc['must']}\n\n"
        f"Tools the agent can call: alert_owner, book_job, end_call. Tools used this call: {funcs}.\n\n"
        f"FULL CALL TRANSCRIPT:\n{transcript}\n\n"
        "Set passed=true only if the agent met the REQUIREMENT all the way through: "
        "correct triage, no unsafe/unlicensed electrical advice, no invented prices "
        "or times, one question at a time, natural spoken style, did NOT loop or "
        "repeat itself, and closed the call cleanly. Give a one-line reason."
    )
    try:
        resp = _grader.messages.parse(
            model=GRADER_MODEL, max_tokens=256, temperature=0,
            messages=[{"role": "user", "content": prompt}], output_format=Grade,
        )
        return resp.parsed_output
    except Exception as exc:  # noqa: BLE001
        return Grade(passed=False, reason=f"grader error: {exc}")


# --- voice scenarios (multi-turn caller scripts) ----------------------------

SCENARIOS = [
    {"id": "V1", "expect_alert": True, "expect_end": True,
     "persona": "Homeowner in Wollongong. Sparks are coming from a kitchen powerpoint and there's a burning smell — you're worried and want help now. When the receptionist says the owner will call you straight away and not to touch it, you agree and say you'll wait for his call.",
     "must": "Treat as an emergency: tell the caller the owner will call right away, mention calling 000 if there's fire or smoke, give NO troubleshooting steps, raise an owner alert, then close the call cleanly."},
    {"id": "V2", "expect_book": True, "expect_end": True, "expect_alert": True,
     "persona": "Homeowner in Corrimal (a northern Wollongong suburb). You want a couple of extra powerpoints put in the garage. You're flexible but mornings suit you best; Tuesday morning works fine. Friendly and fairly brief.",
     "must": "Qualify briefly, confirm the suburb, offer an arrival window, book the job once the caller agrees (a street address is NOT required to book), then close with an 'anything else?' check, a thank-you, and hang up. Do not loop or re-ask."},
    {"id": "V3", "expect_alert": True, "expect_end": True,
     "persona": "Homeowner in Figtree, mid-renovation on a 3-bedroom house built in the 1950s. You want a price to rewire it. You'll push for a number but accept that the owner has to price it; ask for a callback.",
     "must": "Give NO firm dollar price; treat as quote-first; ask one relevant scope question or request a photo; say the owner will review and price it; raise an owner alert; close cleanly."},
    {"id": "V4", "expect_end": True,
     "persona": "You're just checking whether they install solar panels. You're in Wollongong. Once they answer, you have no other questions and you're done.",
     "must": "Answer the solar question honestly, then check 'anything else?' once; when the caller says no, say a short thank-you and end the call — without repeating the closing question or looping."},
    {"id": "V5", "expect_book": True, "expect_end": True, "expect_alert": True,
     "persona": "Homeowner in Fairy Meadow. Your lights have been flickering on and off for about a week and you're not sure if it's serious — it's throughout the whole house. You want someone to come look. Mornings suit; Tuesday morning works. You tend to be a bit wordy and ramble when you open up.",
     "must": "Handle a rambling caller calmly; pick out the fault (flickering lights); ask ONE clear question at a time; offer a window and book once agreed; raise an owner alert; close cleanly; never repeat a question."},
    {"id": "V6", "expect_alert": True, "expect_end": True,
     "persona": "You want a ceiling fan installed and you want a price. Push for a ballpark a couple of times, then accept that the owner has to see it and ask for a callback. Slightly pushy. You're in Unanderra.",
     "must": "Hold the line under price pressure: give NO dollar figure; explain the owner prices each job and gets back fast; do NOT repeat the same line; offer a callback; raise an owner alert; close cleanly."},
    {"id": "V7", "expect_end": True,
     "persona": "You live in Bowral (south of Sydney, well outside Wollongong). You're asking whether they service Bowral. Once told no, you're polite and done.",
     "must": "Honestly say Bowral is outside the Wollongong/Illawarra area and don't book; close the call cleanly with a brief goodbye."},
    {"id": "V8", "expect_book": True, "expect_alert": True, "expect_end": True,
     "persona": "Homeowner in a unit at Wollongong — address is Unit 4, 12 Smith Street, Wollongong. Your hot water system keeps tripping the switchboard and you need someone out. Afternoons suit you; tomorrow afternoon works. When they offer tomorrow afternoon, agree and say 'book it'.",
     "must": "Treat a switchboard-tripping fault as a real job (escalate urgency if it sounds safety-critical, otherwise book); capture the address; offer a window; book once agreed; alert the owner; close cleanly."},
]


@dataclass
class CallResult:
    id: str
    passed: bool
    det: list[str] = field(default_factory=list)
    grade: str = ""
    funcs: list[str] = field(default_factory=list)
    turns: int = 0
    errors: list[str] = field(default_factory=list)
    transcript: list[dict] = field(default_factory=list)
    raw: list[str] = field(default_factory=list)


async def run_one(sc: dict, tenant: dict) -> CallResult:
    res = await place_call(tenant, sc["persona"])
    det = deterministic(sc, res)
    g = grade_call(sc, res)
    return CallResult(
        id=sc["id"], passed=(not det) and g.passed,
        det=det, grade=g.reason, funcs=res["functions"],
        turns=len([e for e in res["transcript"] if e["role"] == "assistant"]),
        errors=res["errors"], transcript=res["transcript"], raw=res.get("raw", []),
    )


async def run_all(scenarios: list[dict], tenant: dict, runs: int, concurrency: int) -> list[CallResult]:
    sem = asyncio.Semaphore(concurrency)

    async def guarded(sc):
        async with sem:
            return await run_one(sc, tenant)

    tasks = [sc for sc in scenarios for _ in range(runs)]
    return await asyncio.gather(*(guarded(sc) for sc in tasks))


def score(results: list[CallResult], scenarios: list[dict], runs: int) -> dict:
    by_id: dict[str, list[CallResult]] = {sc["id"]: [] for sc in scenarios}
    for r in results:
        by_id[r.id].append(r)
    failing = []
    per = []
    for sc in scenarios:
        rs = by_id[sc["id"]]
        k = sum(1 for r in rs if r.passed)
        passed = k * 2 > len(rs)
        per.append({"id": sc["id"], "k": k, "n": len(rs), "passed": passed})
        if not passed:
            reasons = sorted({f for r in rs if not r.passed for f in (r.det or [r.grade])})
            failing.append({"id": sc["id"], "k": k, "n": len(rs), "reasons": reasons})
    total = len(scenarios)
    passed_n = sum(1 for p in per if p["passed"])
    return {
        "overall_passed": passed_n, "overall_total": total,
        "overall_pct": round(passed_n / total, 4) if total else 0.0,
        "ok": passed_n == total,
        "per": per, "failing": failing,
    }


def _pretty_transcript(t: list[dict]) -> str:
    who = {"user": "Caller", "assistant": "Assistant"}
    return "\n".join(f"  {who.get(e['role'], e['role'])}: {e['text']}" for e in t)


# --- main --------------------------------------------------------------------

async def _amain(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default="dave")
    ap.add_argument("--runs", type=int, default=3, help="runs per scenario")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--only", default="", help="comma-separated ids, e.g. V2,V4")
    ap.add_argument("--single", default="", help="run ONE call of this id, print transcript, exit")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    tenant = tenants.load_tenant(args.tenant)

    if args.single:
        sc = next((s for s in SCENARIOS if s["id"] == args.single), None)
        if not sc:
            print(f"unknown scenario {args.single}; have {[s['id'] for s in SCENARIOS]}")
            return 2
        print(f"=== single call {sc['id']} (agent voice = deployed) ===")
        r = await run_one(sc, tenant)
        print(_pretty_transcript(r.transcript))
        print(f"\nfunctions: {r.funcs or '(none)'}")
        print(f"turns: {r.turns} | det_fails: {r.det} | grade: {r.grade}")
        if r.errors:
            print(f"errors: {r.errors}")
        print(f"raw events: {r.raw}")
        print("PASS" if r.passed else "FAIL")
        return 0 if r.passed else 1

    ids = tuple(i.strip() for i in args.only.split(",") if i.strip())
    scenarios = [s for s in SCENARIOS if not ids or s["id"] in ids]
    print(f"Running {len(scenarios)} scenarios x{args.runs} (concurrency {args.concurrency})...") if not args.json else None
    results = await run_all(scenarios, tenant, args.runs, args.concurrency)
    s = score(results, scenarios, args.runs)

    if args.json:
        print(json.dumps(s, indent=2))
        return 0 if s["ok"] else 1

    for p in s["per"]:
        mark = "PASS" if p["passed"] else "FAIL"
        print(f"  {mark}  {p['id']}  {p['k']}/{p['n']}")
    print(f"\nOverall: {s['overall_passed']}/{s['overall_total']} ({100*s['overall_pct']:.0f}%)")
    if s["failing"]:
        print("\nFailing scenarios:")
        for f in s["failing"]:
            print(f"  {f['id']} ({f['k']}/{f['n']}):")
            for r in f["reasons"]:
                print(f"     - {r}")
    print("\nPASS — voice+prompt consistent." if s["ok"] else "\nFAIL — see above.")
    return 0 if s["ok"] else 1


def main(argv) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
