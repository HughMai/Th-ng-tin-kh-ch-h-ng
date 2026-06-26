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
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()  # DEEPGRAM_API_KEY / Anthropic key live in .env

from zoneinfo import ZoneInfo  # noqa: E402

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect  # noqa: E402
import websockets  # noqa: E402

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
# Aura-2 voice. Override with DEEPGRAM_VOICE once you've heard the options and
# picked one that suits the trade (the probe lists what's accepted).
AURA_VOICE = os.environ.get("DEEPGRAM_VOICE", "aura-2-thalia-en")
# Deepgram hosts the LLM and only accepts certain model ids (its Anthropic lags
# the latest): claude-sonnet-4-20250514 for sharper booking logic, or
# claude-3-5-haiku-latest for lower latency. voice_engine.MODEL (the self-hosted
# path, dormant on this managed path) is a newer id and is NOT valid here.
DG_THINK_MODEL = os.environ.get("DEEPGRAM_THINK_MODEL", "claude-sonnet-4-20250514")
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")


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


# --- Deepgram agent config (brain + tools reused from voice_engine) ----------

def _greeting(t: dict) -> str:
    """Spoken the instant the call connects (Deepgram `greeting`). Short, warm,
    opens the floor — the instructions then drive the triage."""
    return (
        f"Hi, it's the assistant for {t['business_name']}. What can we help "
        f"you with today?"
    )


def _tools_for_deepgram() -> list[dict]:
    """voice_engine.TOOLS (Anthropic schema) -> Deepgram function shape. Deepgram
    calls these `parameters`; the descriptions are unchanged."""
    return [
        {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        }
        for tool in voice_engine.TOOLS
    ]


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
            "listen": {"provider": {"type": "deepgram", "model": "nova-3"}},
            "think": {
                "provider": {
                    "type": "anthropic",
                    "model": DG_THINK_MODEL,
                    "temperature": 0.4,
                },
                "prompt": prompt,
                "functions": _tools_for_deepgram(),
            },
            "speak": {"provider": {"type": "deepgram", "model": AURA_VOICE}},
            "greeting": _greeting(t),
        },
    }


# --- tool side-effects (run on a Deepgram FunctionCall event) ----------------

def _fire_alert(t: dict, caller: str, args: dict) -> str:
    """alert_owner -> owner SMS + Telegram mirror (same paths as the SMS engine)."""
    kind = args.get("kind", "new_lead")
    msg = args.get("message", "")
    owner = _owner_mobile(t)
    if owner:
        try:
            twilio_io.send_sms(owner, f"[{kind.upper()}] {caller}\n{msg}")
        except Exception as exc:  # noqa: BLE001
            print(f"[voice] owner SMS failed: {exc}")
    chat = t.get("telegram_chat_id")
    if chat:
        try:
            mid = telegram_io.send_message(chat, f"📞 {kind} — {caller}\n{msg}")
            if mid:
                store.tg_map(chat, mid, t["tenant_id"], caller)
        except Exception as exc:  # noqa: BLE001
            print(f"[voice] telegram mirror failed: {exc}")
    return "Owner notified."


def _book_job(t: dict, caller: str, args: dict) -> str:
    """book_job -> Google Calendar event + owner SMS. The window is rolled into
    the future before booking (never trust the model's date arithmetic blind)."""
    window = args.get("window_text", "a visit")
    start, end = _roll_to_future(args.get("start_iso"), args.get("end_iso"))
    addr = args.get("address") or ""
    link = ""
    if gcal.is_connected(t):
        try:
            summary = f"{args.get('job_type') or 'Job'} — {caller}" + (
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
    owner = _owner_mobile(t)
    if owner:
        try:
            twilio_io.send_sms(owner, f"📅 BOOKED — {caller}\n{window}" + (f"\n{link}" if link else ""))
        except Exception as exc:  # noqa: BLE001
            print(f"[voice] owner booking SMS failed: {exc}")
    return "Job booked."


def _end_call(call_sid: str) -> str:
    """end_call -> hang up the Twilio call."""
    if call_sid:
        try:
            twilio_io.hang_up(call_sid)
        except Exception as exc:  # noqa: BLE001
            print(f"[voice] hang-up failed: {exc}")
    return "Call ended."


def _run_function(t: dict, caller: str, call_sid: str, name: str, args: dict) -> str:
    if name == "alert_owner":
        return _fire_alert(t, caller, args)
    if name == "book_job":
        return _book_job(t, caller, args)
    if name == "end_call":
        return _end_call(call_sid)
    return ""  # unknown tool — no-op, let Deepgram carry on


# --- Twilio Media Streams <-> Deepgram Voice Agent bridge --------------------

async def _open_dg(headers: dict):
    """Connect to Deepgram. websockets renamed extra_headers -> additional_headers
    in v14; try the new name first, fall back for older installs."""
    try:
        return await websockets.connect(DG_URL, additional_headers=headers)
    except TypeError:  # pragma: no cover  (older websockets)
        return await websockets.connect(DG_URL, extra_headers=headers)


async def _deepgram_connect(t: dict):
    """Open the Voice Agent socket and send Settings. Returns a socket ready for
    audio + events."""
    if not DEEPGRAM_KEY:
        raise RuntimeError(
            "DEEPGRAM_API_KEY not set (env: DEEPGRAM_API_KEY or Deepgram_API)"
        )
    dg = await _open_dg({"Authorization": f"Token {DEEPGRAM_KEY}"})
    await dg.send(json.dumps(_agent_settings(t)))
    return dg


@router.websocket("/twilio/voice-stream")
async def voice_stream(ws: WebSocket) -> None:
    """Twilio connects here on <Connect><Stream>. We open Deepgram and bridge
    audio both ways until the call ends. The Twilio `start` event carries the
    tenant_id (set as a <Parameter> in app.py's TwiML) and the caller's number."""
    await ws.accept()
    tenant: dict = DEFAULT_TENANT
    caller = ""
    call_sid = ""
    stream_sid = ""
    dg = None
    try:
        # 1. Wait for Twilio's `start` (after a `connected`), then open Deepgram.
        async for raw in ws.iter_text():
            msg = json.loads(raw)
            if msg.get("event") == "connected":
                continue
            if msg.get("event") == "start":
                start = msg.get("start", {})
                stream_sid = msg.get("streamSid") or start.get("streamSid", "")
                call_sid = msg.get("callSid") or start.get("callSid", "")
                caller = start.get("from", "")
                tid = (start.get("customParameters") or {}).get("tenant_id")
                if tid:
                    try:
                        tenant = tenants.load_tenant(tid)
                    except (FileNotFoundError, ValueError):
                        pass
                dg = await _deepgram_connect(tenant)
                break
            # media before start shouldn't happen; keep waiting.
        if dg is None:
            return  # Twilio hung up before start.

        # 2. Bridge both directions until either socket closes.
        await asyncio.gather(
            _twilio_to_deepgram(ws, dg),
            _deepgram_to_twilio(dg, ws, tenant, caller, call_sid, stream_sid),
        )
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        print(f"[voice] bridge error: {exc}")
    finally:
        if dg is not None:
            try:
                await dg.close()
            except Exception:  # noqa: BLE001
                pass


async def _twilio_to_deepgram(twilio: WebSocket, dg) -> None:
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
                await dg.send(base64.b64decode(payload))
        elif event == "stop":
            break


async def _deepgram_to_twilio(dg, twilio: WebSocket, tenant, caller, call_sid, stream_sid) -> None:
    """Agent audio + events: Deepgram -> Twilio playout (audio) and tool handling
    (FunctionCall). Barge-in flushes Twilio's buffer so the agent stops talking."""
    async for raw in dg:
        if isinstance(raw, (bytes, bytearray)):
            # Agent speech (mulaw) -> base64 -> Twilio.
            if stream_sid:
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
                content = await asyncio.to_thread(
                    _run_function, tenant, caller, call_sid, name, args
                )
                await dg.send(
                    json.dumps({
                        "type": "FunctionCallResponse",
                        "id": fid,
                        "name": name,
                        "content": content or "",
                    })
                )
        elif mtype in ("UserStartedSpeaking", "Interruption"):
            # Caller talked over the agent (barge-in) — flush our playout buffer.
            if stream_sid:
                await twilio.send_text(json.dumps({"event": "clear", "streamSid": stream_sid}))
        # Transcripts, Ready, etc. ride through silently on the hot path.


# --- probe: verify the Deepgram config against the live key ------------------

def _redacted(settings: dict) -> dict:
    """Echo Settings without the (large) instructions blob, for a readable probe."""
    out = json.loads(json.dumps(settings))
    instr = out.get("agent", {}).get("instructions", "")
    out["agent"]["instructions"] = f"[{len(instr)} chars — the tenant brain + VOICE_MODE]"
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
