"""Live phone voice agent: Twilio Media Streams <-> Deepgram Voice Agent API.

app.py returns <Connect><Stream> pointing at /twilio/voice-stream when a tenant
has `voice_answer: true`. This module bridges Twilio's live call audio to
Deepgram's Voice Agent API, which owns the hard parts — STT, turn-taking,
barge-in and Aura-2 TTS — and hosts the Claude brain. When Claude calls a tool
(alert_owner / book_job / end_call) Deepgram sends a FunctionCall event; we run
the side-effect through the same modules app.py's SMS path uses and reply.

The brain is reused, not rewritten: voice_engine.build_voice_system(t) becomes
the agent's instructions and voice_engine.TOOLS become Deepgram functions.
(stream_voice_turn — the self-hosted-LLM loop — is dormant on this managed path;
it's the fallback if we ever stop using Deepgram's hosted LLM.)

Run the probe first to lock the exact Deepgram Settings schema against your real
key:  GET /voice/probe?key=<DASHBOARD_TOKEN>
"""

from __future__ import annotations

import asyncio
import base64
import difflib
import json
import os
import re
import secrets
import time
import unicodedata
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()  # DEEPGRAM_API_KEY / Anthropic key live in .env

from zoneinfo import ZoneInfo  # noqa: E402

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect  # noqa: E402
import websockets  # noqa: E402

import call_state  # noqa: E402
import gcal  # noqa: E402
import store  # noqa: E402
import telegram_io  # noqa: E402
import tenants  # noqa: E402
import twilio_io  # noqa: E402
import voice_engine  # noqa: E402
from workflow import (  # noqa: E402
    DEFAULT_TENANT,
    SYSTEM_PROMPT,
    apply_quoting_policy,
    render_for_tenant,
)

router = APIRouter()

# Deepgram key (read either canonical or the legacy name in Hughie's .env).
DEEPGRAM_KEY = os.environ.get("DEEPGRAM_API_KEY") or os.environ.get("Deepgram_API")
# Voice Agent WebSocket lives on its own host (agent.deepgram.com), separate from
# the STT/TTS/REST api.deepgram.com host.
DG_URL = "wss://agent.deepgram.com/v1/agent/converse"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Flux is built for conversational turn-taking. Override these only when live
# evidence says the agent is cutting people off or waiting too long.
DG_LISTEN_MODEL = os.environ.get("DEEPGRAM_LISTEN_MODEL", "flux-general-en")
DG_LISTEN_VERSION = os.environ.get("DEEPGRAM_LISTEN_VERSION") or (
    "v2" if DG_LISTEN_MODEL.startswith("flux-") else ""
)
DG_EOT_THRESHOLD = _env_float("DEEPGRAM_EOT_THRESHOLD", 0.65)
DG_EAGER_EOT_THRESHOLD = _env_float("DEEPGRAM_EAGER_EOT_THRESHOLD", 0.45)
DG_EOT_TIMEOUT_MS = _env_int("DEEPGRAM_EOT_TIMEOUT_MS", 1500)
VOICE_SILENCE_REPROMPT_MS = _env_int("VOICE_SILENCE_REPROMPT_MS", 7000)
VOICE_UNCLEAR_SPEECH_MS = _env_int("VOICE_UNCLEAR_SPEECH_MS", 6000)
# Max idle re-prompts per agent turn before going quiet. Caps the verbatim
# re-ask loop that fires when a 2nd AgentAudioDone leaks past the single
# suppress flag in _inject_agent_message.
MAX_IDLE_REPROMPTS = _env_int("MAX_IDLE_REPROMPTS", 1)

# --- call watchdog: end loops, dead air, and overruns ------------------------
# The hosted Deepgram brain re-asks the same question on its OWN turn-taking
# (line echo / false endpointing -> extra LLM turns; live evidence: identical
# assistant turns 1s apart, caller transcripts with words:[]). Our reprompt
# guardrails can't reach Deepgram's turns, so a watchdog ends the call instead
# of looping forever. All three knobs are env-tunable.
VOICE_MAX_CALL_SEC = _env_float("VOICE_MAX_CALL_SEC", 240.0)  # hard cap ("end at 4 minutes")
VOICE_SILENCE_HANGUP_SEC = _env_float("VOICE_SILENCE_HANGUP_SEC", 25.0)  # no user speech this long -> end
VOICE_LOOP_MAX_REPEATS = _env_int("VOICE_LOOP_MAX_REPEATS", 2)  # >=N identical agent turns -> end
# Only count repeats that land inside this window. Targets the rapid Deepgram
# self-loop (1s apart); lets our own 7s reprompt nudge through untouched.
VOICE_LOOP_WINDOW_SEC = _env_float("VOICE_LOOP_WINDOW_SEC", 6.0)
_WATCHDOG_GOODBYE = {  # best-effort close spoken before hanging up
    "max_call_sec": "I'll need to wrap up our call now. The team will follow up shortly. Thanks for calling!",
    "silence": "I haven't heard from you in a moment, so I'll let you go. Call us back anytime. Bye!",
    "repeat_loop": "I think we got disconnected. I'll have the team follow up with you. Bye for now!",
}
# Aura-2 voice. Australian defaults fit AU trades better than the original US
# Thalia voice. Try aura-2-theia-en if Hyperion sounds too warm/slow.
AURA_VOICE = os.environ.get("DEEPGRAM_VOICE", "aura-2-hyperion-en")
# Deepgram can also manage Cartesia TTS inside the Voice Agent API. Set
# DEEPGRAM_TTS_PROVIDER=cartesia to switch from Aura to Cartesia Sonic-2.
# Default voice is "Australian Woman" (female, AU accent) — the most reliably
# documented AU-female stock id (cited by SignalWire + Deutsche Telekom Cartesia
# integrations). Verify it still resolves on Sonic-2 via /voice/probe; alternates
# (Matilda, Grace, Australian Narrator Lady) need their ids copied from the
# Cartesia voice library. Cartesia, like the Anthropic think provider, likely
# wants its API key registered in the Deepgram console rather than in this
# payload — the probe surfaces any auth/schema rejection.
DG_TTS_PROVIDER = os.environ.get("DEEPGRAM_TTS_PROVIDER", "deepgram").strip().lower()
CARTESIA_MODEL_ID = os.environ.get("CARTESIA_MODEL_ID", "sonic-2")
CARTESIA_VOICE_ID = os.environ.get(
    "CARTESIA_VOICE_ID", "043cfc81-d69f-4bee-ae1e-7862cb358650"  # "Australian Woman"
)
CARTESIA_SPEED = os.environ.get("CARTESIA_SPEED", "normal")
# Deepgram hosts the LLM and only accepts model ids on its managed allowlist.
# Haiku is the live-call default for speed; use claude-sonnet-4-6 only when a
# tenant proves it needs stronger reasoning and can tolerate extra latency.
DG_THINK_MODEL = os.environ.get("DEEPGRAM_THINK_MODEL", "claude-haiku-4-5")
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")
VOICE_DEBUG_EVENTS = os.environ.get("VOICE_DEBUG_EVENTS", "").lower() in {"1", "true", "yes"}
# Best-effort Deepgram pre-connection. The Voice Agent WebSocket handshake is
# cross-ocean (this box sits in Brazil) and is the largest single chunk of the
# greeting latency (~800ms). We open it during Twilio's ring — fired from
# app.py's voice webhook — and reuse it when the caller's media arrives. Only
# the bare socket is pre-connected; Settings is still sent on the stream path,
# so the greeting always speaks into a live bridge (sending Settings early would
# speak the greeting into a socket with no caller yet and lose it). Kill switch:
# VOICE_PRECONNECT=0. Any failure silently falls back to a fresh open on the
# stream, identical to today — preconnect can make the call faster, never broken.
VOICE_PRECONNECT = os.environ.get("VOICE_PRECONNECT", "1").strip().lower() in {"1", "true", "yes"}
PRECONNECT_TTL_S = _env_float("VOICE_PRECONNECT_TTL", 8.0)
PRECONNECT_WAIT_S = _env_float("VOICE_PRECONNECT_WAIT", 0.5)
_preconnected: dict[str, asyncio.Future] = {}


# --- shared datetime helpers -------------------------------------------------
# These mirror app.py:_now_line / _roll_to_future. Kept local to avoid a circular
# import on app.py (which mounts this router). Extract to a shared module if a
# third copy appears.

def _now_line(t: dict) -> str:
    try:
        now = datetime.now(ZoneInfo(t.get("timezone", "Australia/Sydney")))
    except Exception:  # noqa: BLE001
        now = datetime.now(timezone.utc)
    return (
        f"Current date and time: {now:%A %d %B %Y, %I:%M %p} "
        f"({t.get('timezone', 'UTC')}). Resolve any day or time the caller gives "
        f"to the NEXT upcoming occurrence from now."
    )


def _roll_to_future(start_iso: str | None, end_iso: str | None) -> tuple[str, str]:
    """Never book in the past: roll the window forward a week at a time."""
    try:
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso)
    except (TypeError, ValueError):
        return start_iso or "", end_iso or ""
    now = datetime.now(start.tzinfo or timezone.utc)
    while start <= now:
        start += timedelta(days=7)
        end += timedelta(days=7)
    return start.isoformat(), end.isoformat()


def _owner_mobile(t: dict) -> str:
    return t.get("owner_mobile") or os.environ.get("ELECTRICIAN_MOBILE", "")


def _elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def _set_latency_once(stats: dict, key: str) -> None:
    if stats.get(key) is None:
        stats[key] = _elapsed_ms(stats["started_at"])


def _capture_turn_response(latency: dict) -> None:
    """Caller-perceived reply speed for one turn. Two timers, both cleared on
    capture so mid-reply audio chunks don't double-count; the greeting is
    excluded (no preceding user turn — first_agent_audio_ms covers it).

    - turn_resp_ms (post-EOT): ConversationText role=user -> first reply audio.
      The brain+tools+TTS cost AFTER Deepgram confirmed end-of-turn.
    - turn_eot_ms (EOT-inclusive): UserStoppedSpeaking -> first reply audio. The
      caller-perceived gap starts here, so this also captures the EOT dwell
      (silence-wait + transcript finalization) that turn_resp_ms hides. The diff
      between the two is the eot_threshold / eot_timeout_ms lever's headroom.
      Armed on the VAD UserStoppedSpeaking event; if that event never arrives it
      stays 'na'."""
    pend = latency.get("turn_user_end")
    if pend is None:
        return
    now = time.perf_counter()
    resp_ms = int((now - pend) * 1000)
    latency["turn_user_end"] = None
    count = int(latency.get("turn_count", 0)) + 1
    latency["turn_count"] = count
    latency["turn_resp_sum_ms"] = int(latency.get("turn_resp_sum_ms", 0)) + resp_ms
    latency["turn_resp_max_ms"] = max(int(latency.get("turn_resp_max_ms", 0)), resp_ms)
    eot_pend = latency.get("turn_eot")
    if eot_pend is not None:
        eot_ms = int((now - eot_pend) * 1000)
        latency["turn_eot"] = None
        latency["turn_eot_sum_ms"] = int(latency.get("turn_eot_sum_ms", 0)) + eot_ms
        latency["turn_eot_max_ms"] = max(int(latency.get("turn_eot_max_ms", 0)), eot_ms)
        print(f"[voice turn] n={count} resp_ms={resp_ms} eot_ms={eot_ms}", flush=True)
    else:
        print(f"[voice turn] n={count} resp_ms={resp_ms}", flush=True)


def _log_latency(stats: dict, reason: str = "closed") -> None:
    def val(key: str) -> str:
        item = stats.get(key)
        return "na" if item is None else str(item)

    turns = int(stats.get("turn_count", 0))
    turn_avg = int(stats.get("turn_resp_sum_ms", 0) / turns) if turns else 0
    eot_sum = stats.get("turn_eot_sum_ms")
    eot_avg = int(eot_sum / turns) if (turns and eot_sum is not None) else 0
    print(
        "[voice latency] "
        f"reason={reason} "
        f"dg_connect_ms={val('dg_connect_ms')} "
        f"preconnect={stats.get('preconnect', '-')} "
        f"dg_ready_ms={val('dg_ready_ms')} "
        f"first_user_audio_ms={val('first_user_audio_ms')} "
        f"first_agent_audio_ms={val('first_agent_audio_ms')} "
        f"function_calls={stats.get('function_calls', 0)} "
        f"turns={turns} "
        f"turn_resp_max_ms={stats.get('turn_resp_max_ms', 0)} "
        f"turn_resp_avg_ms={turn_avg} "
        f"turn_eot_max_ms={stats.get('turn_eot_max_ms', 0)} "
        f"turn_eot_avg_ms={eot_avg} "
        f"total_ms={_elapsed_ms(stats['started_at'])}",
        flush=True,
    )


# --- Deepgram agent config (brain + tools reused from voice_engine) ----------

def _greeting(t: dict) -> str:
    """Spoken the instant the call connects via Deepgram `greeting`."""
    return f"Hi, I'm Syanna from {t['business_name']}. How can I help you today?"


def _reprompt_from_agent_text(text: str) -> str:
    """Return the last spoken question so silence can trigger one short re-ask."""
    questions = re.findall(r"[^.!?]*\?", text or "")
    if not questions:
        return ""
    question = " ".join(questions[-1].strip().split())
    return question if 0 < len(question) <= 120 else ""


def _norm_utterance(text: str) -> str:
    """Normalized form of an agent line for verbatim-repeat detection
    (case- and whitespace-insensitive; punctuation kept, since two spoken
    repeats transcribe char-identical)."""
    return " ".join((text or "").lower().split())


def _cancel_idle_reprompt(idle: dict) -> None:
    task = idle.get("task")
    if task and not task.done():
        task.cancel()
    idle["task"] = None


def _cancel_unclear_recovery(idle: dict) -> None:
    task = idle.get("unclear_task")
    if task and not task.done():
        task.cancel()
    idle["unclear_task"] = None


def _mark_user_activity(idle: dict) -> None:
    """A genuine user *transcript* arrived: fresh reprompt budget for this turn,
    and reset the silence clock + repeat streak the watchdog reads."""
    idle["user_seq"] = int(idle.get("user_seq", 0)) + 1
    idle["reprompt_count"] = 0
    idle["last_injected"] = ""
    idle["last_user_mono"] = time.perf_counter()
    idle["repeat_streak"] = 0
    idle["last_assistant_norm"] = ""
    _cancel_idle_reprompt(idle)
    _cancel_unclear_recovery(idle)


def _on_vad_activity(idle: dict) -> None:
    """VAD/Interruption (barge-in, or agent-TTS echo): advance the abort sequence
    and cancel pending nudges, but do NOT reset the reprompt cap or last injected
    line — only a real user transcript does. Stops echo tripping VAD from
    re-arming the verbatim re-ask loop."""
    idle["user_seq"] = int(idle.get("user_seq", 0)) + 1
    _cancel_idle_reprompt(idle)
    _cancel_unclear_recovery(idle)


async def _inject_agent_message(dg, idle: dict, message: str) -> None:
    idle["suppress_next_audio_done"] = True
    idle["last_injected"] = message
    # Shared nudge budget across idle + unclear paths; credited back on InjectionRefused.
    idle["reprompt_count"] = int(idle.get("reprompt_count", 0)) + 1
    await dg.send(
        json.dumps(
            {
                "type": "InjectAgentMessage",
                "behavior": "default",
                "message": message,
            }
        )
    )


async def _send_idle_reprompt(dg, idle: dict, user_seq: int, message: str) -> None:
    try:
        await asyncio.sleep(max(0, VOICE_SILENCE_REPROMPT_MS) / 1000)
        if int(idle.get("user_seq", 0)) != user_seq:
            return
        _cancel_unclear_recovery(idle)  # only one nudge in flight at a time
        await _inject_agent_message(dg, idle, message)
    except asyncio.CancelledError:
        pass


async def _send_unclear_speech_recovery(dg, idle: dict, user_seq: int) -> None:
    try:
        await asyncio.sleep(max(0, VOICE_UNCLEAR_SPEECH_MS) / 1000)
        if int(idle.get("user_seq", 0)) != user_seq:
            return
        _cancel_idle_reprompt(idle)  # only one nudge in flight at a time
        question = _reprompt_from_agent_text(idle.get("last_agent_text", ""))
        message = (
            f"I didn't catch that. {question}"
            if question
            else "I didn't catch that. What do you need help with?"
        )
        await _inject_agent_message(dg, idle, message)
    except asyncio.CancelledError:
        pass


def _schedule_unclear_speech_recovery(dg, idle: dict) -> None:
    if VOICE_UNCLEAR_SPEECH_MS <= 0:
        return
    if int(idle.get("reprompt_count", 0)) >= MAX_IDLE_REPROMPTS:
        return
    _cancel_unclear_recovery(idle)
    idle["unclear_task"] = asyncio.create_task(
        _send_unclear_speech_recovery(dg, idle, int(idle.get("user_seq", 0)))
    )


def _schedule_idle_reprompt(dg, idle: dict) -> None:
    if VOICE_SILENCE_REPROMPT_MS <= 0:
        return
    if int(idle.get("reprompt_count", 0)) >= MAX_IDLE_REPROMPTS:
        return
    message = _reprompt_from_agent_text(idle.get("last_agent_text", ""))
    if not message:
        return
    if message == idle.get("last_injected"):
        return  # never re-inject the exact line we just nudged with
    _cancel_idle_reprompt(idle)
    idle["task"] = asyncio.create_task(
        _send_idle_reprompt(dg, idle, int(idle.get("user_seq", 0)), message)
    )


def _update_repeat_streak(idle: dict, norm: str, now: float) -> None:
    """Track consecutive verbatim agent turns. Increments only when this line
    matches the previous one AND landed inside the loop window (so our own 7s
    reprompt nudge never counts); otherwise resets to 1."""
    prev = idle.get("last_assistant_norm", "")
    if norm and norm == prev and (now - idle.get("last_assistant_mono", now)) < VOICE_LOOP_WINDOW_SEC:
        idle["repeat_streak"] = int(idle.get("repeat_streak", 1)) + 1
    else:
        idle["repeat_streak"] = 1
    idle["last_assistant_norm"] = norm
    idle["last_assistant_mono"] = now


def _watchdog_trip_reason(idle: dict, now: float) -> str | None:
    """Pure termination check (no sockets/Twilio) -> unit-testable. Returns a
    key into _WATCHDOG_GOODBYE, or None when the call should continue."""
    started = idle.get("call_started_mono")
    if started is not None and now - started >= VOICE_MAX_CALL_SEC:
        return "max_call_sec"
    last_user = idle.get("last_user_mono")
    if last_user is not None and now - last_user >= VOICE_SILENCE_HANGUP_SEC:
        return "silence"
    if int(idle.get("repeat_streak", 0)) >= VOICE_LOOP_MAX_REPEATS:
        return "repeat_loop"
    return None


async def _watchdog_terminate(dg, idle: dict, call_sid: str, reason: str) -> None:
    """Speak a short goodbye (best-effort), let it start playing, then hang up.
    Idempotent via idle['ending'] — safe if the watchdog and the LLM end_call
    tool race."""
    if idle.get("ending"):
        return
    idle["ending"] = True
    print(f"[voice] watchdog ending call ({reason})", flush=True)
    goodbye = _WATCHDOG_GOODBYE.get(reason, "")
    if goodbye:
        try:
            await dg.send(
                json.dumps(
                    {"type": "InjectAgentMessage", "behavior": "default", "message": goodbye}
                )
            )
        except Exception:  # noqa: BLE001
            pass
    await asyncio.sleep(2.0)  # let the goodbye play before we drop the line
    if call_sid:
        try:
            await asyncio.to_thread(twilio_io.hang_up, call_sid)
        except Exception as exc:  # noqa: BLE001
            print(f"[voice] watchdog hang-up failed: {exc}")


async def _call_watchdog(dg, idle: dict, call_sid: str) -> None:
    """Poll idle ~1s and end the call when a watchdog condition trips. Runs
    alongside the bridge and is cancelled in its finally block."""
    try:
        while True:
            await asyncio.sleep(1.0)
            if idle.get("ending"):
                return
            reason = _watchdog_trip_reason(idle, time.perf_counter())
            if reason:
                await _watchdog_terminate(dg, idle, call_sid, reason)
                return
    except asyncio.CancelledError:
        pass


def _norm_place(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"\bst\b", "saint", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _place_choices(items: list[str] | None) -> dict[str, str]:
    choices: dict[str, str] = {}
    for item in items or []:
        if isinstance(item, str) and item.strip():
            choices[_norm_place(item)] = item.strip()
    return choices


def _best_place_match(raw: str, choices: dict[str, str]) -> tuple[str, float]:
    needle = _norm_place(raw)
    if not needle or not choices:
        return "", 0.0
    if needle in choices:
        return choices[needle], 1.0
    best_key = ""
    best_score = 0.0
    for key in choices:
        score = difflib.SequenceMatcher(None, needle, key).ratio()
        if needle in key or key in needle:
            score = max(score, min(len(needle), len(key)) / max(len(needle), len(key)))
        if score > best_score:
            best_key = key
            best_score = score
    return (choices[best_key], best_score) if best_key else ("", 0.0)


def _service_area_result(
    t: dict, raw_suburb: str, *, status: str, canonical: str = "", confidence: float = 0.0
) -> str:
    in_area = True if status in {"in_area", "confirm"} else False if status == "out_of_area" else None
    short = t.get("service_area_short", "the service area")
    if status == "in_area":
        instruction = f"Use {canonical} as the suburb and keep going."
    elif status == "confirm":
        instruction = f"Ask exactly: Did you mean {canonical}?"
    elif status == "out_of_area":
        instruction = f"Say {t.get('business_name', 'we')} only services {short}, then close politely."
    else:
        instruction = f"Ask exactly: Is that around {short}?"
    return json.dumps(
        {
            "raw_suburb": raw_suburb,
            "status": status,
            "canonical_suburb": canonical,
            "in_area": in_area,
            "confidence": round(confidence, 3),
            "instruction": instruction,
        }
    )


def _check_service_area(t: dict, raw_suburb: str) -> str:
    raw = (raw_suburb or "").strip()
    if not raw:
        return _service_area_result(t, raw, status="unknown")

    suburbs = _place_choices(t.get("service_suburbs"))
    aliases = {
        _norm_place(alias): canonical
        for alias, canonical in (t.get("suburb_aliases") or {}).items()
        if isinstance(alias, str) and isinstance(canonical, str)
    }
    out_of_area = _place_choices(t.get("out_of_area_suburbs"))
    norm = _norm_place(raw)

    if norm in suburbs:
        return _service_area_result(t, raw, status="in_area", canonical=suburbs[norm], confidence=1.0)
    if norm in aliases:
        return _service_area_result(t, raw, status="confirm", canonical=aliases[norm], confidence=0.95)

    out_match, out_score = _best_place_match(raw, out_of_area)
    if out_match and out_score >= 0.9:
        return _service_area_result(t, raw, status="out_of_area", canonical=out_match, confidence=out_score)

    match, score = _best_place_match(raw, suburbs)
    if match and score >= 0.88:
        return _service_area_result(t, raw, status="in_area", canonical=match, confidence=score)
    if match and score >= 0.64:
        return _service_area_result(t, raw, status="confirm", canonical=match, confidence=score)

    return _service_area_result(t, raw, status="unknown", confidence=score)


def _tools_for_deepgram() -> list[dict]:
    """voice_engine.TOOLS (Anthropic schema) -> Deepgram function shape. Deepgram
    calls these `parameters`; the descriptions are unchanged.

    report_state is EXCLUDED from the live Deepgram menu. The prompt used to force
    it every turn, and Deepgram's managed flow is function-first (it waits on our
    FunctionCallResponse before speaking), so each per-turn report_state was a
    serialized round-trip inflating first-reply latency — for a Tier-1 log-only
    signal that never steers the call (the shadow FSM still observes every
    transcript + tool call via _fsm_observe / note_tool_call; only the LLM-emitted
    claim is dropped). Re-enable by removing this filter — the tool def, the
    _run_function handler and call_state.ingest_report_state are all intact."""
    return [
        {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        }
        for tool in voice_engine.TOOLS
        if tool["name"] != "report_state"
    ]


def _listen_keyterms(t: dict) -> list[str]:
    terms: list[str] = []
    for item in t.get("service_suburbs") or []:
        if isinstance(item, str) and item.strip():
            terms.append(item.strip())
    business = t.get("business_name")
    if business:
        terms.append(str(business).strip())
    seen = set()
    unique = []
    for term in terms:
        key = _norm_place(term)
        if key and key not in seen:
            seen.add(key)
            unique.append(term)
    return unique[:100]


def _listen_provider(t: dict | None = None) -> dict:
    provider = {"type": "deepgram", "model": DG_LISTEN_MODEL}
    if DG_LISTEN_VERSION:
        provider["version"] = DG_LISTEN_VERSION
    if t:
        keyterms = _listen_keyterms(t)
        if keyterms:
            provider["keyterms"] = keyterms
    if DG_LISTEN_MODEL.startswith("flux-") and DG_LISTEN_VERSION == "v2":
        provider.update(
            {
                "eot_threshold": DG_EOT_THRESHOLD,
                "eager_eot_threshold": DG_EAGER_EOT_THRESHOLD,
                "eot_timeout_ms": DG_EOT_TIMEOUT_MS,
            }
        )
    return provider


def _speak_provider() -> dict:
    if DG_TTS_PROVIDER == "cartesia" and CARTESIA_VOICE_ID:
        return {
            "type": "cartesia",
            "model_id": CARTESIA_MODEL_ID,
            "voice": {"mode": "id", "id": CARTESIA_VOICE_ID},
            "speed": CARTESIA_SPEED,
        }
    return {"type": "deepgram", "model": AURA_VOICE}


def _lean_voice_prompt(t: dict) -> str:
    """Lean voice brain for Deepgram's hosted think.prompt, which caps length.
    Same trade RULES as the SMS brain (rendered from SYSTEM_PROMPT + quoting
    policy), but the long example exchanges, notification examples and knowledge
    file are dropped — the lowest-value bytes on a live call and the parts that
    push the prompt past the cap. VOICE_MODE on top supplies the spoken + tool
    contract. (Knowledge file intentionally omitted; re-add a trimmed digest
    later if a tenant needs its facts on the voice path.)"""
    lean = dict(t)
    lean["examples_block"] = "(example exchanges omitted on the voice path)"
    lean["notification_examples"] = ""
    body = apply_quoting_policy(render_for_tenant(SYSTEM_PROMPT, lean), lean)
    return voice_engine.VOICE_MODE.format(
        owner=t["owner_name"], business=t["business_name"]
    ) + body


def _agent_settings(t: dict) -> dict:
    """The Deepgram Voice Agent Settings message. Schema locked by probing the
    live API: providers are nested objects, the brain is think.prompt (NOT
    instructions), the Aura voice is the speak model name, and the tools are
    think.functions with no endpoint — so Deepgram runs them client-side and the
    calls come back over this same socket as FunctionCallRequest."""
    prompt = _lean_voice_prompt(t) + "\n\n" + _now_line(t)
    return {
        "type": "Settings",
        "audio": {
            "input": {"encoding": "mulaw", "sample_rate": 8000},
            "output": {"encoding": "mulaw", "sample_rate": 8000, "container": "none"},
        },
        "agent": {
            "language": "en",
            "listen": {"provider": _listen_provider(t)},
            "think": {
                "provider": {
                    "type": "anthropic",
                    "model": DG_THINK_MODEL,
                    "temperature": 0.3,
                },
                "prompt": prompt,
                "functions": _tools_for_deepgram(),
            },
            "speak": {"provider": _speak_provider()},
            "greeting": _greeting(t),
        },
    }


def _demo_greeting(name: str) -> str:
    """Spoken the instant a website 'get a demo call' connects."""
    first = name.split()[0] if name else "there"
    return (
        f"Hi {first}, this is a live demo of LeadResponder — that's exactly how "
        "I'd call one of your missed-call leads back, within about 20 seconds. "
        "Want me to show you how I'd answer and book in a job?"
    )


def _demo_prompt(name: str) -> str:
    """The demo brain: replaces the trade brain so the prospect experiences the
    product rather than a fictitious trade call. Kept short for the prompt cap."""
    first = name.split()[0] if name else "there"
    return (
        "You are the LeadResponder AI voice agent running a LIVE DEMO call. "
        f"The person on the line is {first}, a prospect who asked on our website "
        "to experience the product.\n\n"
        "GOAL: in 1-3 short exchanges, make them FEEL how LeadResponder answers "
        "a home-service business's missed calls and texts instantly, qualifies the "
        "caller, and books the job — so a missed call is never a lost job.\n\n"
        "STYLE: warm, brief, Australian-casual. Be honest that you're an AI "
        "assistant. Keep turns short; never monologue.\n\n"
        "PITCH (light): built for trades and home-service pros (electricians, "
        "plumbers, locksmiths). From $99/month, no lock-in. If they're keen, offer "
        "to have Hughie, the founder, text them to get set up, and ask what kind "
        "of business they run.\n\n"
        "RULES: this is illustrative — do NOT book a real trade job or invent a "
        "tradesperson, and do not quote trade job prices. If they're not interested "
        "or it's a wrong number, wish them well and end the call. If they ask how "
        "it works or the price, answer briefly and offer the follow-up."
    )


def _apply_demo_mode(settings: dict, params: dict) -> None:
    """Swap the trade brain for the LeadResponder demo pitch when the call came
    from the website 'get a demo call' form (demo_mode stream param set). The
    prospect then experiences the product instead of a fictitious trade call."""
    if str(params.get("demo_mode") or "").lower() not in ("1", "true", "yes"):
        return
    name = str(params.get("demo_prospect_name") or "").strip()
    settings["agent"]["greeting"] = _demo_greeting(name)
    settings["agent"]["think"]["prompt"] = _demo_prompt(name)


# --- post-call action queue --------------------------------------------------

def _new_call_actions() -> dict:
    return {"alerts": [], "booking": None, "flushed": False}


def _queue_alert(actions: dict, args: dict) -> str:
    actions.setdefault("alerts", []).append(
        {
            "kind": args.get("kind", "new_lead"),
            "message": (args.get("message") or "").strip(),
        }
    )
    return "Noted for the post-call owner summary."


def _queue_booking(actions: dict, args: dict) -> str:
    actions["booking"] = dict(args)
    return "Booking details noted for after the call."


def _create_booking(t: dict, caller: str, args: dict) -> tuple[str, str]:
    """Create the calendar event, returning (window, link). Best-effort."""
    window = args.get("window_text", "a visit")
    start, end = _roll_to_future(args.get("start_iso"), args.get("end_iso"))
    addr = args.get("address") or ""
    link = ""
    if gcal.is_connected(t):
        try:
            summary = f"{args.get('job_type') or 'Job'} - {caller}" + (
                f" @ {addr}" if addr else ""
            )
            link = gcal.create_event(
                t,
                summary=summary,
                start_iso=start,
                end_iso=end,
                description=f"{window}\nCustomer: {caller}"
                + (f"\nAddress: {addr}" if addr else ""),
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[voice] gcal booking failed: {exc}")
    return window, link


def _dominant_kind(alerts: list[dict], booking: dict | None) -> str:
    if booking:
        return "booked"
    kinds = [str(item.get("kind") or "new_lead") for item in alerts]
    for kind in ("emergency", "callback", "quote", "booked", "new_lead"):
        if kind in kinds:
            return kind
    return "new_lead"


def _format_owner_summary(
    caller: str, actions: dict, booking_result: tuple[str, str] | None
) -> tuple[str, str]:
    alerts = actions.get("alerts") or []
    booking = actions.get("booking")
    kind = _dominant_kind(alerts, booking)
    who = caller or "unknown caller"
    lines: list[str] = []

    for item in alerts:
        msg = (item.get("message") or "").strip()
        if msg:
            lines.append(msg)

    if booking_result:
        window, link = booking_result
        lines.append(f"Booked: {window}")
        if link:
            lines.append(link)
        if booking:
            job_type = (booking.get("job_type") or "").strip()
            address = (booking.get("address") or "").strip()
            if job_type:
                lines.append(f"Job: {job_type}")
            if address:
                lines.append(f"Address: {address}")

    if not lines:
        lines.append("Voice call finished. No extra details captured.")

    return kind, f"[{kind.upper()}] {who}\n" + "\n".join(lines)


def _flush_post_call_actions(t: dict, caller: str, actions: dict) -> None:
    """Send one consolidated owner notification after the call finishes."""
    if actions.get("flushed"):
        return
    actions["flushed"] = True

    alerts = actions.get("alerts") or []
    booking = actions.get("booking")
    if not alerts and not booking:
        return

    booking_result = _create_booking(t, caller, booking) if booking else None
    kind, owner_body = _format_owner_summary(caller, actions, booking_result)
    owner = _owner_mobile(t)
    if owner:
        try:
            twilio_io.send_sms(owner, owner_body)
        except Exception as exc:  # noqa: BLE001
            print(f"[voice] owner summary SMS failed: {exc}")

    chat = t.get("telegram_chat_id")
    if chat:
        try:
            who = caller or "unknown caller"
            mid = telegram_io.send_message(chat, f"Voice {kind} - {who}\n{owner_body}")
            if mid:
                store.tg_map(chat, mid, t["tenant_id"], caller)
        except Exception as exc:  # noqa: BLE001
            print(f"[voice] telegram summary failed: {exc}")


def _end_call(t: dict, caller: str, call_sid: str, actions: dict) -> str:
    """Wait briefly after the goodbye, hang up, then flush owner actions."""
    time.sleep(2)
    if call_sid:
        try:
            twilio_io.hang_up(call_sid)
        except Exception as exc:  # noqa: BLE001
            print(f"[voice] hang-up failed: {exc}")
    _flush_post_call_actions(t, caller, actions)
    return "Call ended."


# --- shadow-FSM directive handling (Tier 1: log only, never enforce) --------

# How a directive's op reads as a gate-log `decision`. Tier 1 logs every one of
# these and executes none — Tier 2 will act on alert/sms/inject.
_DIRECTIVE_DECISION = {
    "alert": "force_fire",
    "sms": "force_fire",
    "inject": "force_fire",
    "no_alert": "suppress",
    "log_gate": "log",
}


def _handle_fsm_directives(directives, call_sid: str, fsm) -> None:
    """Write the FSM's per-event gate verdicts to the eval corpus. Sync, called
    off the event loop. Best-effort — never raises into the call path."""
    for d in directives or []:
        gate = getattr(d, "gate", "")
        if not gate:
            continue
        decision = (d.payload or {}).get("decision") or _DIRECTIVE_DECISION.get(d.op, "log")
        summary = json.dumps(d.payload, default=str)[:240]
        try:
            store.log_gate(
                call_sid, gate, decision,
                input_summary=summary,
                fsm_state_after=fsm.state.value,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[voice] fsm log_gate failed: {exc}")


def _fsm_observe(fsm, role: str, content: str, call_sid: str) -> None:
    """Run the shadow FSM over one transcript event + log its directives. Sync —
    called off the loop via asyncio.to_thread. Swallows all errors."""
    try:
        directives = fsm.observe(role, content)
        _handle_fsm_directives(directives, call_sid, fsm)
    except Exception as exc:  # noqa: BLE001
        print(f"[voice] fsm observe failed: {exc}")


def _run_function(
    t: dict, caller: str, call_sid: str, name: str, args: dict, actions: dict,
    fsm=None,
) -> str:
    # report_state — the divergence signal. Logged + acknowledged; never steers
    # in Tier 1 (shadow). Always returns valid JSON so Deepgram is never broken.
    if name == "report_state":
        if fsm is None:
            return json.dumps({"approved": True, "instruction": ""})
        try:
            result_json, log_fields = fsm.ingest_report_state(args)
            store.log_gate(
                call_sid, "report_state", "log",
                input_summary=(log_fields.get("summary") or "")[:240],
                fsm_state_after=log_fields.get("after", ""),
                report_state_claim=log_fields.get("claim", ""),
                divergence_flag=log_fields.get("divergence", 0),
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[voice] fsm report_state failed: {exc}")
            return json.dumps({"approved": True, "instruction": ""})
        return result_json

    # Let the shadow FSM observe every other tool call (logged only — Tier 1
    # never gates the existing tools, so their behavior is unchanged).
    if fsm is not None:
        try:
            _handle_fsm_directives(fsm.note_tool_call(name, args), call_sid, fsm)
        except Exception as exc:  # noqa: BLE001
            print(f"[voice] fsm note_tool_call failed: {exc}")

    if name == "alert_owner":
        return _queue_alert(actions, args)
    if name == "check_service_area":
        result = _check_service_area(t, args.get("raw_suburb", ""))
        if fsm is not None:
            try:
                parsed = json.loads(result)
                fsm.note_service_area(
                    parsed.get("status", "unknown"), parsed.get("canonical_suburb", "")
                )
            except Exception:  # noqa: BLE001
                pass
        return result
    if name == "book_job":
        return _queue_booking(actions, args)
    if name == "end_call":
        return _end_call(t, caller, call_sid, actions)
    return ""  # unknown tool: no-op, let Deepgram carry on


# --- Twilio Media Streams <-> Deepgram Voice Agent bridge --------------------

async def _open_dg(headers: dict):
    """Connect to Deepgram. websockets renamed extra_headers -> additional_headers
    in v14; try the new name first, fall back for older installs."""
    try:
        return await websockets.connect(DG_URL, additional_headers=headers)
    except TypeError:  # pragma: no cover  (older websockets)
        return await websockets.connect(DG_URL, extra_headers=headers)


async def _open_dg_authed():
    """Open the Voice Agent socket (handshake only — Settings is sent on the
    stream path so the greeting speaks into a live bridge)."""
    if not DEEPGRAM_KEY:
        raise RuntimeError(
            "DEEPGRAM_API_KEY not set (env: DEEPGRAM_API_KEY or Deepgram_API)"
        )
    return await _open_dg({"Authorization": f"Token {DEEPGRAM_KEY}"})


async def _safe_close(dg) -> None:
    try:
        await dg.close()
    except Exception:  # noqa: BLE001
        pass


async def preconnect_call(call_sid: str) -> None:
    """Fire-and-forget from app.py's voice webhook: open the Deepgram socket
    during Twilio's ring so the handshake is warm when the caller's media
    arrives. Settings is NOT sent here. Any failure is swallowed — the stream
    always falls back to opening fresh, identical to no preconnect."""
    if not VOICE_PRECONNECT or not call_sid or call_sid in _preconnected:
        return
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _preconnected[call_sid] = fut
    asyncio.create_task(_reap_preconnect(call_sid, fut))
    try:
        dg = await _open_dg_authed()
    except Exception:  # noqa: BLE001  (any failure -> stream opens fresh)
        if not fut.done():
            fut.set_exception(RuntimeError("preconnect open failed"))
        return
    if _preconnected.get(call_sid) is fut and not fut.done():
        fut.set_result(dg)
    else:
        await _safe_close(dg)  # reaped/superseded while connecting — don't leak


async def _reap_preconnect(call_sid: str, fut: asyncio.Future) -> None:
    """Close a pre-connected socket nobody claimed within TTL (caller hung up
    during the ring, or the stream took a different socket)."""
    await asyncio.sleep(PRECONNECT_TTL_S)
    if _preconnected.pop(call_sid, None) is not fut:
        return  # already consumed by the stream
    if fut.done() and not fut.cancelled() and fut.exception() is None:
        await _safe_close(fut.result())


async def _take_or_open_dg(call_sid: str, latency: dict):
    """Return a warm Deepgram socket if one was pre-connected for this CallSid,
    else open fresh (today's behaviour). Waits at most PRECONNECT_WAIT_S for an
    in-flight preconnect — long enough to catch one that's about to finish
    (it has a head start, so finishing it beats opening fresh), short enough
    that a pathologically slow connect can't stall the caller much past today.
    Any miss falls back to a fresh open. The caller sends Settings next."""
    fut = _preconnected.get(call_sid) if (VOICE_PRECONNECT and call_sid) else None
    if fut is not None:
        try:
            dg = await asyncio.wait_for(asyncio.shield(fut), PRECONNECT_WAIT_S)
            _preconnected.pop(call_sid, None)  # claimed — stop the reaper
            latency["preconnect"] = "hit"
            latency["dg_connect_ms"] = 0  # handshake was hidden behind the ring
            return dg
        except Exception:  # noqa: BLE001  (timeout / preconnect error -> fresh)
            latency["preconnect"] = "miss"
            # leave an in-flight future for its own reaper to close
    dg_started = time.perf_counter()
    dg = await _open_dg_authed()
    latency["dg_connect_ms"] = int((time.perf_counter() - dg_started) * 1000)
    return dg


@router.websocket("/twilio/voice-stream")
async def voice_stream(ws: WebSocket) -> None:
    """Twilio connects here on <Connect><Stream>. We open Deepgram and bridge
    audio both ways until the call ends. The Twilio `start` event carries the
    tenant_id (set as a <Parameter> in app.py's TwiML) and the caller's number."""
    await ws.accept()
    latency = {
        "started_at": time.perf_counter(),
        "dg_connect_ms": None,
        "dg_ready_ms": None,
        "first_user_audio_ms": None,
        "first_agent_audio_ms": None,
        "function_calls": 0,
    }
    tenant: dict = DEFAULT_TENANT
    caller = ""
    call_sid = ""
    stream_sid = ""
    actions = _new_call_actions()
    fsm = None
    dg = None
    close_reason = "closed"
    try:
        # 1. Wait for Twilio's `start` (after a `connected`), then open Deepgram.
        async for raw in ws.iter_text():
            msg = json.loads(raw)
            if msg.get("event") == "connected":
                continue
            if msg.get("event") == "start":
                start = msg.get("start", {})
                params = start.get("customParameters") or {}
                stream_sid = msg.get("streamSid") or start.get("streamSid", "")
                call_sid = msg.get("callSid") or start.get("callSid") or params.get("call_sid", "")
                caller = start.get("from") or params.get("caller", "")
                tid = params.get("tenant_id")
                if tid:
                    try:
                        tenant = tenants.load_tenant(tid)
                    except (FileNotFoundError, ValueError):
                        pass
                try:
                    fsm = call_state.CallFSM(tenant, caller)
                except Exception as exc:  # noqa: BLE001
                    print(f"[voice] fsm init failed: {exc}")
                await asyncio.to_thread(
                    store.start_voice_call,
                    tenant["tenant_id"],
                    caller,
                    call_sid,
                    stream_sid,
                )
                dg = await _take_or_open_dg(call_sid, latency)
                settings = _agent_settings(tenant)
                _apply_demo_mode(settings, params)  # demo-form callback -> LeadResponder pitch
                await dg.send(json.dumps(settings))
                latency["dg_ready_ms"] = _elapsed_ms(latency["started_at"])
                break
            # media before start shouldn't happen; keep waiting.
        if dg is None:
            close_reason = "no_deepgram"
            return  # Twilio hung up before start.

        # 2. Bridge both directions until either socket closes.
        await asyncio.gather(
            _twilio_to_deepgram(ws, dg, latency),
            _deepgram_to_twilio(
                dg, ws, tenant, caller, call_sid, stream_sid, latency, actions, fsm
            ),
        )
    except WebSocketDisconnect:
        close_reason = "twilio_disconnect"
    except Exception as exc:  # noqa: BLE001
        close_reason = "bridge_error"
        print(f"[voice] bridge error: {exc}")
    finally:
        if dg is not None:
            try:
                await dg.close()
            except Exception:  # noqa: BLE001
                pass
        if fsm is not None and call_sid:
            try:
                fsm.close_reason = close_reason
                await asyncio.to_thread(
                    store.save_voice_state,
                    call_sid, tenant["tenant_id"], caller, fsm.snapshot(),
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[voice] save_voice_state failed: {exc}")
        await asyncio.to_thread(store.finish_voice_call, call_sid, close_reason)
        await asyncio.to_thread(_flush_post_call_actions, tenant, caller, actions)
        _log_latency(latency, close_reason)


async def _twilio_to_deepgram(twilio: WebSocket, dg, latency: dict) -> None:
    """Caller audio: Twilio media payloads (base64 mulaw) -> raw bytes -> Deepgram."""
    async for raw in twilio.iter_text():
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        event = msg.get("event")
        if event == "media":
            payload = (msg.get("media") or {}).get("payload")
            if payload:
                _set_latency_once(latency, "first_user_audio_ms")
                await dg.send(base64.b64decode(payload))
        elif event == "stop":
            break


async def _deepgram_to_twilio(
    dg, twilio: WebSocket, tenant, caller, call_sid, stream_sid, latency: dict, actions: dict,
    fsm=None,
) -> None:
    """Agent audio + events: Deepgram -> Twilio playout (audio) and tool handling
    (FunctionCall). Barge-in flushes Twilio's buffer so the agent stops talking."""
    idle = {
        "task": None,
        "unclear_task": None,
        "user_seq": 0,
        "reprompt_count": 0,
        "last_injected": "",
        "transcript_seq": 0,
        "last_agent_text": "",
        "suppress_next_audio_done": False,
        # call-watchdog state. last_user_mono starts at call start so a caller
        # who never speaks (pocket dial) is still dropped after the silence cap.
        "call_started_mono": time.perf_counter(),
        "last_user_mono": time.perf_counter(),
        "last_assistant_norm": "",
        "last_assistant_mono": time.perf_counter(),
        "repeat_streak": 0,
        "ending": False,
    }
    watchdog_task = asyncio.create_task(_call_watchdog(dg, idle, call_sid))
    try:
        async for raw in dg:
            if isinstance(raw, (bytes, bytearray)):
                # Agent speech (mulaw) -> base64 -> Twilio.
                if stream_sid:
                    _set_latency_once(latency, "first_agent_audio_ms")
                    _capture_turn_response(latency)
                    await twilio.send_text(
                        json.dumps(
                            {
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {"payload": base64.b64encode(raw).decode()},
                            }
                        )
                    )
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type")
            if mtype == "FunctionCallRequest":
                latency["function_calls"] = int(latency.get("function_calls", 0)) + len(msg.get("functions", []))
                # Deepgram wants us to run one or more client-side functions. Each
                # carries an id (echo it back), a name, and arguments as a JSON string.
                for fn in msg.get("functions", []):
                    fid = fn.get("id")
                    name = fn.get("name")
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    # Side-effects are sync (twilio/gcal/sqlite) — run off the loop.
                    # fsm MUST be threaded here: without it every FSM tool-call
                    # observation (report_state divergence, note_tool_call,
                    # note_service_area) silently no-ops on the live path.
                    content = await asyncio.to_thread(
                        _run_function, tenant, caller, call_sid, name, args, actions, fsm
                    )
                    await dg.send(
                        json.dumps({
                            "type": "FunctionCallResponse",
                            "id": fid,
                            "name": name,
                            "content": content or "",
                        })
                    )
            elif mtype == "ConversationText":
                role = msg.get("role")
                content = (msg.get("content") or "").strip()
                if content and role in {"assistant", "user"}:
                    idle["transcript_seq"] = int(idle.get("transcript_seq", 0)) + 1
                    await asyncio.to_thread(
                        store.append_voice_transcript,
                        call_sid,
                        tenant["tenant_id"],
                        caller,
                        int(idle["transcript_seq"]),
                        role,
                        content,
                    )
                    if fsm is not None:
                        await asyncio.to_thread(_fsm_observe, fsm, role, content, call_sid)
                    if role == "assistant":
                        idle["last_agent_text"] = content
                        _update_repeat_streak(idle, _norm_utterance(content), time.perf_counter())
                        _cancel_unclear_recovery(idle)
                    elif role == "user":
                        _mark_user_activity(idle)
                        # User turn just ended — arm the per-turn response timer.
                        # The next agent audio chunk completes it (see
                        # _capture_turn_response).
                        latency["turn_user_end"] = time.perf_counter()
            elif mtype == "AgentAudioDone":
                if idle.get("suppress_next_audio_done"):
                    idle["suppress_next_audio_done"] = False
                else:
                    _schedule_idle_reprompt(dg, idle)
            elif mtype in ("UserStartedSpeaking", "Interruption"):
                # Caller talked over the agent (barge-in) — flush our playout buffer.
                # VAD event, not a transcript: don't reset the reprompt cap, so
                # agent-TTS echo can't re-arm the verbatim re-ask loop. Also clears
                # a stale EOT timer armed at a mid-utterance pause — the caller
                # resumed, so the prior UserStoppedSpeaking wasn't a real turn-end.
                _on_vad_activity(idle)
                _schedule_unclear_speech_recovery(dg, idle)
                latency["turn_eot"] = None
                if stream_sid:
                    await twilio.send_text(json.dumps({"event": "clear", "streamSid": stream_sid}))
            elif mtype == "UserStoppedSpeaking":
                # Caller stopped talking — arm the EOT-INCLUSIVE per-turn timer.
                # Fires at the VAD level, before Deepgram confirms EOT and finalizes
                # the transcript (turn_user_end arms later on ConversationText). The
                # diff vs turn_resp_ms is the hidden EOT dwell — headroom for the
                # eot_threshold / eot_timeout_ms lever. Guarded so a duplicate event
                # can't overwrite the real turn-end arm (cleared on capture / on the
                # next UserStartedSpeaking).
                if latency.get("turn_eot") is None:
                    latency["turn_eot"] = time.perf_counter()
            elif mtype == "InjectionRefused":
                idle["suppress_next_audio_done"] = False
                # Injection never played — give the nudge budget back so a refused
                # inject can't permanently mute the agent.
                idle["reprompt_count"] = max(0, int(idle.get("reprompt_count", 0)) - 1)
            elif mtype in ("Error", "Warning"):
                # Surface Deepgram-side failures (e.g. a dead/unsupported think model)
                # — these otherwise close the socket and drop the call with no trace.
                print(f"[voice] deepgram {mtype}: {msg.get('code')} — {msg.get('description')}", flush=True)
            elif VOICE_DEBUG_EVENTS:
                print(f"[voice event] {mtype}: {msg}", flush=True)
            # Transcripts, Ready, etc. ride through silently on the hot path.
    finally:
        watchdog_task.cancel()
        _cancel_idle_reprompt(idle)
        _cancel_unclear_recovery(idle)


# --- probe: verify the Deepgram config against the live key ------------------

def _redacted(settings: dict) -> dict:
    """Echo Settings without the (large) instructions blob, for a readable probe."""
    out = json.loads(json.dumps(settings))
    think = out.get("agent", {}).get("think", {})
    prompt = think.get("prompt", "")
    if prompt:
        think["prompt"] = f"[{len(prompt)} chars - the tenant brain + VOICE_MODE]"
    return out


@router.get("/voice/probe")
async def voice_probe(key: str = "") -> dict:
    """Open Deepgram with the default tenant's Settings and report what comes
    back. Use it to lock the exact Settings schema (audio/agent field names) and
    to confirm whether the Anthropic key must be registered in the Deepgram
    console before Twilio is wired. Token-gated like the deep-health endpoint."""
    if DASHBOARD_TOKEN and not secrets.compare_digest(key, DASHBOARD_TOKEN):
        raise HTTPException(status_code=403, detail="bad token")
    if not DEEPGRAM_KEY:
        raise HTTPException(status_code=500, detail="DEEPGRAM_API_KEY not set")
    settings = _agent_settings(DEFAULT_TENANT)
    events: list[str] = []
    dg = None
    try:
        dg = await _open_dg({"Authorization": f"Token {DEEPGRAM_KEY}"})
        await dg.send(json.dumps(settings))
        try:
            for _ in range(6):  # grab the first frames: Ready, or a rejection
                raw = await asyncio.wait_for(dg.recv(), timeout=4)
                events.append(raw.decode(errors="replace") if isinstance(raw, (bytes, bytearray)) else raw)
        except asyncio.TimeoutError:
            pass
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "settings_sent": _redacted(settings), "events": events}
    finally:
        if dg is not None:
            try:
                await dg.close()
            except Exception:  # noqa: BLE001
                pass
    compact = [e.replace(" ", "") for e in events]
    ready = any('"type":"Ready"' in c for c in compact)
    return {"ok": ready, "settings_sent": _redacted(settings), "events": events}
