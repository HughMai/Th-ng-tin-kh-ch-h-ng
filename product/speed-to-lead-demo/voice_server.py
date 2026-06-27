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
import time
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


def _log_latency(stats: dict, reason: str = "closed") -> None:
    def val(key: str) -> str:
        item = stats.get(key)
        return "na" if item is None else str(item)

    print(
        "[voice latency] "
        f"reason={reason} "
        f"dg_connect_ms={val('dg_connect_ms')} "
        f"preconnect={stats.get('preconnect', '-')} "
        f"dg_ready_ms={val('dg_ready_ms')} "
        f"first_user_audio_ms={val('first_user_audio_ms')} "
        f"first_agent_audio_ms={val('first_agent_audio_ms')} "
        f"function_calls={stats.get('function_calls', 0)} "
        f"total_ms={_elapsed_ms(stats['started_at'])}",
        flush=True,
    )


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


def _listen_provider() -> dict:
    provider = {"type": "deepgram", "model": DG_LISTEN_MODEL}
    if DG_LISTEN_VERSION:
        provider["version"] = DG_LISTEN_VERSION
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
            "listen": {"provider": _listen_provider()},
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


def _run_function(
    t: dict, caller: str, call_sid: str, name: str, args: dict, actions: dict
) -> str:
    if name == "alert_owner":
        return _queue_alert(actions, args)
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
                dg = await _take_or_open_dg(call_sid, latency)
                await dg.send(json.dumps(_agent_settings(tenant)))
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
                dg, ws, tenant, caller, call_sid, stream_sid, latency, actions
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
    dg, twilio: WebSocket, tenant, caller, call_sid, stream_sid, latency: dict, actions: dict
) -> None:
    """Agent audio + events: Deepgram -> Twilio playout (audio) and tool handling
    (FunctionCall). Barge-in flushes Twilio's buffer so the agent stops talking."""
    async for raw in dg:
        if isinstance(raw, (bytes, bytearray)):
            # Agent speech (mulaw) -> base64 -> Twilio.
            if stream_sid:
                _set_latency_once(latency, "first_agent_audio_ms")
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
                content = await asyncio.to_thread(
                    _run_function, tenant, caller, call_sid, name, args, actions
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
        elif mtype in ("Error", "Warning"):
            # Surface Deepgram-side failures (e.g. a dead/unsupported think model)
            # — these otherwise close the socket and drop the call with no trace.
            print(f"[voice] deepgram {mtype}: {msg.get('code')} — {msg.get('description')}", flush=True)
        elif VOICE_DEBUG_EVENTS:
            print(f"[voice event] {mtype}: {msg}", flush=True)
        # Transcripts, Ready, etc. ride through silently on the hot path.


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
