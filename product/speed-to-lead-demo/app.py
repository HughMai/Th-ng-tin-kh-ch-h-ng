"""FastAPI server for the electrician speed-to-lead product.

The conversation engine (`workflow.py`) is fronted by the Twilio webhooks
(`/twilio/*`) — the live product. A real missed call or SMS to the business
number runs the qualify -> triage -> book workflow. Owners watch their pipeline
and take over leads from the dashboard (`/dashboard/*`).

Run locally:  python -m uvicorn app:app --reload   (then http://127.0.0.1:8000)
"""

import asyncio
import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

# Load .env before the first-party imports below — gcal reads GOOGLE_CLIENT_ID at
# import time, so the .env must be in os.environ first (matters for local dev;
# in production Docker injects env vars directly).
load_dotenv()

from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import accounts
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import Connect, VoiceResponse

from zoneinfo import ZoneInfo

import gcal
import report
import store
import telegram_io
import tenants
import twilio_io
from workflow import DEFAULT_TENANT, run_turn, system_for
from voice_server import router as voice_router, preconnect_call

HERE = Path(__file__).parent
app = FastAPI(title="Speed-to-Lead")
# Live voice agent: Twilio Media Streams <-> Deepgram. The /twilio/voice-stream
# WebSocket and the /voice/probe check live in voice_server.py.
app.include_router(voice_router)

# --- Config (from environment) ----------------------------------------------
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
# Fallback alert number when a tenant has no owner_mobile configured.
FALLBACK_OWNER_MOBILE = os.environ.get("ELECTRICIAN_MOBILE", "")
# Website "get a demo call" form. DEMO_FROM_NUMBER is the caller ID shown to the
# prospect (the AU line +61468089224); DEMO_CALL_SECRET gates the Netlify
# function as the only allowed caller; DEMO_DAILY_CAP bounds Twilio spend.
DEMO_CALL_SECRET = os.environ.get("DEMO_CALL_SECRET", "").strip()
DEMO_FROM_NUMBER = os.environ.get("DEMO_FROM_NUMBER", "").strip()
DEMO_DAILY_CAP = int(os.environ.get("DEMO_DAILY_CAP", "20"))

store.configure(os.environ.get("DB_PATH", "data/leads.db"))


def _base_url(request: Request) -> str:
    """Public origin (scheme + host, no trailing slash) for building OAuth
    redirect URIs. Prefer the configured public URL (production behind Caddy);
    fall back to the request's own origin for local dev."""
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    host = os.environ.get("PUBLIC_HOSTNAME", "").strip("/")
    if host:
        return f"https://{host}"
    return str(request.base_url).rstrip("/")


def _ws_url(request: Request, path: str) -> str:
    """wss:// origin for a Twilio Media Stream path. Same host as the public base
    URL; Caddy upgrades the WebSocket transparently."""
    host = _base_url(request).replace("https://", "").replace("http://", "").rstrip("/")
    return f"wss://{host}{path}"


def _ws_url_base(path: str) -> str:
    """wss:// URL for a Media Stream path off PUBLIC_BASE_URL — for outbound calls
    fired without an inbound request (the website demo callback trigger)."""
    if not PUBLIC_BASE_URL:
        raise HTTPException(status_code=503, detail="PUBLIC_BASE_URL is not configured.")
    host = PUBLIC_BASE_URL.replace("https://", "").replace("http://", "").rstrip("/")
    return f"wss://{host}{path}"


def _au_e164(phone: str) -> str | None:
    """Normalise to an AU E.164 number (+61…), or None if it isn't one. AU-only
    by design: the public demo form targets AU prospects and bounds the blast
    radius. Accepts +61…, 61…, or 0[23478]… (mobile or geographic landline)."""
    d = re.sub(r"\D", "", phone or "")
    if d.startswith("61") and len(d) == 11:
        rest = d[2:]
    elif d.startswith("0") and len(d) == 10:
        rest = d[1:]  # drop the trunk 0
    else:
        return None
    if len(rest) == 9 and rest[0] in "23478":
        return "+61" + rest
    return None


def _resolve_tenant(form: dict) -> dict:
    """Route an inbound Twilio webhook to its tenant by the number dialled
    (the `To` field). Falls back to the default tenant for single-number setups."""
    return tenants.find_by_number(form.get("To", "")) or DEFAULT_TENANT


def _owner_mobile(t: dict) -> str:
    return t.get("owner_mobile") or FALLBACK_OWNER_MOBILE


# --- Telegram lead mirror (per-tenant owner notifications + two-way takeover) -
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")


def _tg_chat(tenant: dict):
    """The tenant's Telegram chat id, or None if they haven't connected one."""
    return tenant.get("telegram_chat_id")


def _tenant_for_chat(chat_id) -> dict | None:
    """Find the tenant a Telegram chat belongs to (reverse of _tg_chat)."""
    for t in tenants.all_tenants():
        if t.get("telegram_chat_id") == chat_id:
            return t
    return None


def _mirror(tenant: dict, phone: str, prefix: str, content: str, label: str | None = None) -> None:
    """Post a line to the tenant's Telegram chat and remember which lead it's
    about, so an owner reply routes back to the right customer. `label` is the
    header shown after the emoji — the customer's number on inbound lines, the
    business name on the AI's lines. No-op if no Telegram chat is connected."""
    chat = _tg_chat(tenant)
    if not chat:
        return
    mid = telegram_io.send_message(chat, f"{prefix} {label or phone}\n{content}")
    if mid:
        store.tg_map(chat, mid, tenant["tenant_id"], phone)


def _tg_new_lead(tenant: dict, phone: str, how: str) -> None:
    """Header card announcing a brand-new lead in the tenant's Telegram chat."""
    chat = _tg_chat(tenant)
    if not chat:
        return
    mid = telegram_io.send_message(chat, f"🆕 New lead — {phone}\n({how})")
    if mid:
        store.tg_map(chat, mid, tenant["tenant_id"], phone)


def _now_line(t: dict) -> str:
    """A current-time note in the tenant's timezone, so the engine can turn
    'Wednesday 8-11am' into ISO booking times."""
    try:
        now = datetime.now(ZoneInfo(t.get("timezone", "Australia/Sydney")))
    except Exception:
        now = datetime.now(timezone.utc)
    return (
        f"Current date and time: {now:%A %d %B %Y, %I:%M %p} "
        f"({t.get('timezone', 'UTC')}). When the customer names a day or time, "
        f"always resolve it to the NEXT upcoming occurrence from now — never a "
        f"date in the past."
    )


def _roll_to_future(start_iso: str, end_iso: str) -> tuple[str, str]:
    """Deterministic safety net: never book in the past. If the model produced a
    start that's already gone, roll the window forward a week at a time (keeping
    the weekday + time) until it's in the future."""
    try:
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso)
    except ValueError:
        return start_iso, end_iso  # malformed — let gcal reject it
    now = datetime.now(start.tzinfo or timezone.utc)
    while start <= now:
        start += timedelta(days=7)
        end += timedelta(days=7)
    return start.isoformat(), end.isoformat()


@app.get("/health")
def health() -> dict:
    """Liveness probe for the uptime monitor."""
    return {"status": "ok"}


CANARY_TOKEN = os.environ.get("CANARY_TOKEN", "")


@app.get("/health/deep")
async def health_deep(token: str = "") -> Response:
    """Deep liveness for the synthetic canary. Proves the things that silently
    break and drop leads — the Claude engine and the database — actually work,
    not just that the process is up. Runs one real (tiny) engine turn and one DB
    round-trip; creates NO lead and sends NO SMS. Returns 200 only if all checks
    pass, else 503 with the failing check. Token-gated so it can't be abused to
    burn API calls (the canary passes ?token=)."""
    if CANARY_TOKEN and not secrets.compare_digest(token, CANARY_TOKEN):
        raise HTTPException(status_code=403, detail="bad token")

    started = time.perf_counter()
    checks: dict[str, str] = {}
    ok = True

    try:
        # Harmless write+read through the real DB path.
        store.mark_seen(f"canary:{int(time.time() * 1000)}")
        checks["db"] = "ok"
    except Exception as exc:  # noqa: BLE001
        ok = False
        checks["db"] = f"error: {exc}"

    try:
        turn = await run_in_threadpool(
            run_turn,
            [{"role": "user", "content": "ping"}],
            system_for(DEFAULT_TENANT),
        )
        if turn.reply:
            checks["engine"] = "ok"
        else:
            ok = False
            checks["engine"] = "error: empty reply"
    except Exception as exc:  # noqa: BLE001
        ok = False
        checks["engine"] = f"error: {exc}"

    return JSONResponse(
        {
            "status": "ok" if ok else "fail",
            "checks": checks,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        },
        status_code=200 if ok else 503,
    )


# --- Root -> owner dashboard -------------------------------------------------

@app.get("/")
def index(key: str = "") -> Response:
    # The owner dashboard is the main board: hitting the site lands you there
    # (it gates on login). Preserve the admin ?key= quick-access token across
    # the redirect.
    dest = "/dashboard" + (f"?key={urllib.parse.quote(key)}" if key else "")
    return RedirectResponse(dest, status_code=303)


# --- Twilio webhooks (the live product) -------------------------------------

MISSED_CALL_STATUSES = {"no-answer", "busy", "failed"}


async def _twilio_form(request: Request) -> dict:
    """Parse a Twilio webhook's form body and verify it really came from Twilio.

    Raises 403 on a bad signature. The URL handed to the validator must be the
    exact public one Twilio called — behind the reverse proxy the app can't see
    that itself, so it's rebuilt from PUBLIC_BASE_URL.
    """
    form = dict(await request.form())
    url = f"{PUBLIC_BASE_URL}{request.url.path}"
    signature = request.headers.get("X-Twilio-Signature", "")
    if not twilio_io.is_valid_twilio_request(url, form, signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")
    return form


def _twiml(body: str) -> Response:
    return Response(content=body, media_type="application/xml")


@app.post("/api/demo-call")
async def api_demo_call(request: Request):
    """Website 'get a demo call' form -> one outbound AI callback.

    Called by the Netlify function, not the public. Validates the payload,
    enforces the per-phone (24h) and global (daily) caps, then fires
    twilio_io.place_call with an inline <Connect><Stream> that bridges to the
    Deepgram demo agent. tenant_id=demo loads tenants/demo.json; caller ID is
    DEMO_FROM_NUMBER (the AU line). Mirrors the inbound /twilio/voice TwiML but
    is built off PUBLIC_BASE_URL since there's no inbound request to read from.
    """
    # 1. Auth — shared secret with the Netlify function. Not public: this endpoint
    # spends Twilio credit calling whoever fills the form.
    if not DEMO_CALL_SECRET:
        raise HTTPException(status_code=503, detail="Demo callback is not configured.")
    if not secrets.compare_digest(request.headers.get("x-demo-secret", ""), DEMO_CALL_SECRET):
        raise HTTPException(status_code=401, detail="Unauthorized.")

    # 2. Validate payload.
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")
    name = str(body.get("name", "")).strip()[:60]
    consent = bool(body.get("consent"))
    phone = _au_e164(str(body.get("phone", "")).strip())
    if not name:
        raise HTTPException(status_code=400, detail="Your name is required.")
    if not consent:
        raise HTTPException(status_code=400, detail="Consent is required to call you.")
    if not phone:
        raise HTTPException(
            status_code=400,
            detail="An Australian mobile or landline number is required.",
        )

    # 3. Abuse caps (reuse store): one callback per number per 24h, and a global
    # daily ceiling. Stops a troll hammering one number or burning Twilio credit.
    if store.recent_demo_calls(phone, 24) >= 1:
        raise HTTPException(
            status_code=429,
            detail="You've had a demo call recently — please try again tomorrow.",
        )
    if store.demo_calls_today() >= DEMO_DAILY_CAP:
        raise HTTPException(
            status_code=429, detail="Today's demo limit has been reached. Try again tomorrow."
        )

    # 4. Build the outbound TwiML and place the call.
    if not DEMO_FROM_NUMBER:
        raise HTTPException(status_code=503, detail="DEMO_FROM_NUMBER is not configured.")
    vr = VoiceResponse()
    connect = Connect()
    stream = connect.stream(url=_ws_url_base("/twilio/voice-stream"))
    stream.parameter(name="tenant_id", value="demo")
    stream.parameter(name="caller", value=phone)
    stream.parameter(name="demo_mode", value="true")
    stream.parameter(name="demo_prospect_name", value=name)
    vr.append(connect)
    status_cb = f"{PUBLIC_BASE_URL}/twilio/voice-status" if PUBLIC_BASE_URL else None
    call_sid = twilio_io.place_call(
        to=phone,
        twiml=str(vr),
        from_=DEMO_FROM_NUMBER,
        status_callback=status_cb,
    )
    store.record_demo_call(phone, call_sid, name)
    return JSONResponse({"ok": True})


@app.post("/twilio/voice")
async def twilio_voice(request: Request) -> Response:
    """Inbound call: ring the owner. If they don't pick up, voice-status fires
    and we text the caller back.

    Conditional Call Forwarding (CCF) tenants set voice_textback_only: the caller
    has already missed the owner on their real line and the carrier forwarded the
    call here, so we must NOT dial the owner back (that would loop the forwarded
    call straight back into this number — see references/ccf-setup-au.md gotcha
    #2). Instead, greet briefly, fire the text-back, and hang up."""
    form = await _twilio_form(request)
    tenant = _resolve_tenant(form)
    vr = VoiceResponse()
    if tenant.get("voice_answer"):
        # The AI picks up instantly: stream the live call to the Deepgram voice
        # agent (voice_server.py). tenant_id is passed through so the bridge
        # loads the right trade brain. Mutually exclusive with the owner-dial and
        # text-back paths below.
        connect = Connect()
        stream = connect.stream(url=_ws_url(request, "/twilio/voice-stream"))
        stream.parameter(name="tenant_id", value=tenant["tenant_id"])
        stream.parameter(name="caller", value=form.get("From", ""))
        stream.parameter(name="call_sid", value=form.get("CallSid", ""))
        # Warm the Deepgram socket during the ring so the caller's first audio
        # doesn't wait on the cross-ocean handshake (~800ms). Best-effort: any
        # failure silently falls back to a fresh open on the stream.
        call_sid = form.get("CallSid", "")
        if call_sid:
            asyncio.create_task(preconnect_call(call_sid))
        vr.append(connect)
        return _twiml(str(vr))
    if tenant.get("voice_textback_only"):
        vr.say(
            tenant.get("voice_greeting")
            or (
                f"Hi, you've reached {tenant['business_name']}. We can't get to "
                "the phone right now, but keep an eye out — we'll text you in just "
                "a moment."
            )
        )
        caller = form.get("From")
        # Idempotency: a retried voice webhook would fire a second text-back.
        sid = form.get("CallSid")
        already = bool(sid) and not store.mark_seen(f"voice:{sid}")
        if caller and not already:
            twilio_io.send_sms(caller, tenant["opener_sms"])
            is_new = not store.lead_exists(tenant["tenant_id"], caller)
            store.log_missed_call(tenant["tenant_id"], caller)
            if is_new:
                _tg_new_lead(tenant, caller, "missed call — texted back")
            _mirror(tenant, caller, "🤖", tenant["opener_sms"], label=tenant["business_name"])
        vr.hangup()
        return _twiml(str(vr))
    dial = vr.dial(
        timeout=20,
        action=f"{PUBLIC_BASE_URL}/twilio/voice-status",
        method="POST",
    )
    dial.number(_owner_mobile(tenant))
    return _twiml(str(vr))


@app.post("/twilio/voice-status")
async def twilio_voice_status(request: Request) -> Response:
    """The call has ended. If it went unanswered, send the tenant's missed-call
    text. Static by design: still sends when the Claude engine is unreachable."""
    form = await _twilio_form(request)
    if form.get("DialCallStatus") in MISSED_CALL_STATUSES:
        # Idempotency: a retried voice-status webhook would text the caller back
        # twice and double-count the missed call. First hit wins.
        sid = form.get("CallSid")
        if sid and not store.mark_seen(f"voicestatus:{sid}"):
            return _twiml("<Response/>")
        caller = form.get("From")
        if caller:
            tenant = _resolve_tenant(form)
            twilio_io.send_sms(caller, tenant["opener_sms"])
            is_new = not store.lead_exists(tenant["tenant_id"], caller)
            store.log_missed_call(tenant["tenant_id"], caller)
            if is_new:
                _tg_new_lead(tenant, caller, "missed call — texted back")
            _mirror(tenant, caller, "🤖", tenant["opener_sms"], label=tenant["business_name"])
    return _twiml("<Response/>")


@app.post("/twilio/sms")
async def twilio_sms(request: Request) -> Response:
    """Inbound SMS: route to the tenant, run their engine, reply to the customer,
    and alert the owner when the engine says to."""
    form = await _twilio_form(request)
    tenant = _resolve_tenant(form)
    owner = _owner_mobile(tenant)
    customer = form.get("From", "")
    body = (form.get("Body") or "").strip()
    tid = tenant["tenant_id"]

    # Idempotency: Twilio retries this webhook (same MessageSid) if our reply is
    # slow. Without this guard the engine runs again and the customer is texted
    # twice. The first hit wins; retries are silently acknowledged.
    sid = form.get("MessageSid")
    if sid and not store.mark_seen(f"sms:{sid}"):
        return _twiml(str(MessagingResponse()))

    # Mirror the inbound text to the owner's Telegram (and announce a new lead).
    if not store.lead_exists(tid, customer):
        _tg_new_lead(tenant, customer, "via SMS")
    _mirror(tenant, customer, "👤", body)

    # If the owner has taken this lead over from Telegram, the AI stays quiet —
    # just record the customer's message; the owner is replying by hand.
    if store.is_human_handling(tid, customer):
        store.append_message(tid, customer, "user", body)
        return _twiml(str(MessagingResponse()))

    thread = store.load_thread(tid, customer)
    thread.append({"role": "user", "content": body})

    try:
        # Blocking Claude call — run it off the event loop so concurrent texts
        # aren't held up behind it. Use the tenant's own system prompt, plus a
        # current-time note (for ISO booking times) and the customer's number
        # (so the engine never has to ask the customer for it).
        context_note = (
            f"{_now_line(tenant)}\n\n"
            f"The customer's mobile number is {customer}. You already have it — "
            f"never ask the customer for their phone number."
        )
        turn = await run_in_threadpool(
            run_turn, thread, system_for(tenant), context_note
        )
    except Exception as exc:
        # Engine or API failure: fail open. Never drop a lead — reassure the
        # customer and put a human (the owner) on it.
        print(f"[error] engine failed for {tenant['tenant_id']}/{customer}: {exc}")
        twilio_io.send_sms(
            owner,
            f'[ATTN] Automation hiccup — {customer} texted in and needs a '
            f'callback. Their message: "{body}"',
        )
        # Same REST-not-TwiML reasoning as the success path below: if the engine
        # failure was itself a timeout, a TwiML reassurance would already be too
        # late for Twilio to deliver. Send from the tenant's own number.
        twilio_io.send_sms(
            customer,
            f"Thanks for your message — {tenant['owner_name']} will get back to you shortly.",
            from_=tenant.get("twilio_number"),
        )
        return _twiml(str(MessagingResponse()))

    thread.append({"role": "assistant", "content": turn.reply})
    store.save_turn(tenant["tenant_id"], customer, thread, turn)
    _mirror(tenant, customer, "🤖", turn.reply, label=tenant["business_name"])

    # On a confirmed booking, create the calendar event. Best-effort: a calendar
    # failure must never break the customer reply or the owner alert.
    booking_link = ""
    if turn.booking_start and turn.booking_end and gcal.is_connected(tenant):
        start_iso, end_iso = _roll_to_future(turn.booking_start, turn.booking_end)
        try:
            addr = turn.qualified.address or turn.qualified.suburb or ""
            summary = f"{turn.qualified.job_type or 'Job'} — {customer}"
            if addr:
                summary += f" @ {addr}"
            description = "\n".join(
                p for p in (
                    f"Address: {addr}" if addr else "",
                    f"Customer: {customer}",
                    turn.electrician_notification or turn.booking or "",
                ) if p
            )
            booking_link = gcal.create_event(
                tenant,
                summary=summary,
                start_iso=start_iso,
                end_iso=end_iso,
                description=description,
            )
        except Exception as exc:
            print(f"[gcal] booking failed for {tenant['tenant_id']}: {exc}")

    if turn.electrician_notification:
        note = turn.electrician_notification
        if booking_link:
            note += f"\nCalendar: {booking_link}"
        twilio_io.send_sms(owner, note)

    # Deliver the customer reply via the REST API, not the webhook's TwiML
    # response. A TwiML reply only reaches the customer if we answer before
    # Twilio's ~15s webhook timeout — and the blocking Claude call above can blow
    # past it on a slow turn, so Twilio discards the reply and never even logs an
    # outbound message. The Telegram mirror is a separate call that still fires:
    # exactly the "appears in Telegram but never hits the phone" bug. A REST send
    # is decoupled from the response, so the reply lands regardless of timing.
    # Send from the tenant's own number so the customer's thread stays intact.
    twilio_io.send_sms(customer, turn.reply, from_=tenant.get("twilio_number"))
    return _twiml(str(MessagingResponse()))


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> Response:
    """Owner acts from Telegram. Replying to a lead's message texts that
    customer as the business and pauses the AI for that lead; `/resume` hands
    control back to the AI; `/done` closes the lead (stops follow-ups)."""
    if (
        TELEGRAM_WEBHOOK_SECRET
        and request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        != TELEGRAM_WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=403, detail="bad secret")
    update = await request.json()
    msg = update.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    text = (msg.get("text") or "").strip()
    reply = msg.get("reply_to_message") or {}
    if not chat_id or not text:
        return Response(status_code=200)

    tenant = _tenant_for_chat(chat_id)
    if not tenant:
        return Response(status_code=200)

    # The owner identifies the lead by replying to one of the bot's messages.
    phone = store.tg_lookup(chat_id, reply.get("message_id")) if reply else None
    if not phone:
        telegram_io.send_message(
            chat_id,
            "↩️ Reply to a lead's message to text that customer (or /resume, /done).",
        )
        return Response(status_code=200)

    tid = tenant["tenant_id"]
    rid = reply.get("message_id")
    cmd = text.split("@", 1)[0].lower()  # tolerate /done@LeadCapturev1_bot
    if cmd in ("/resume", "resume"):
        store.set_human_handling(tid, phone, False)
        telegram_io.send_message(chat_id, f"🤖 AI resumed for {phone}.", rid)
    elif cmd in ("/done", "done", "/close"):
        store.close_lead(tid, phone)
        telegram_io.send_message(chat_id, f"✅ {phone} marked done — follow-ups off.", rid)
    else:
        try:
            twilio_io.send_sms(phone, text)
        except Exception as exc:  # noqa: BLE001
            telegram_io.send_message(chat_id, f"⚠️ Couldn't text {phone}: {exc}", rid)
            return Response(status_code=200)
        store.append_message(tid, phone, "assistant", text)
        store.set_human_handling(tid, phone, True)
        telegram_io.send_message(
            chat_id, f"🧑‍🔧 Sent to {phone}. AI paused — /resume to hand back.", rid
        )
    return Response(status_code=200)


# --- Owner dashboard + client logins ----------------------------------------
# Two roles: admin (you — sees every tenant) and a client (sees only their own
# tenant). Clients log in with operator-provisioned credentials (create_account.py).
# Auth is a signed session cookie; admin can also quick-access via ?key=<token>.

DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")
SECRET_KEY = os.environ.get("SECRET_KEY") or DASHBOARD_TOKEN
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SESSION_TTL = 60 * 60 * 12  # 12 hours

STAGE_LABELS = {
    "escalated": "🔴 Emergency / Escalated",
    "missed_call": "📞 Missed call — awaiting reply",
    "booking": "📅 Booking",
    "triaging": "🔎 Triaging",
    "qualifying": "💬 Qualifying",
    "closed": "✅ Closed",
}
STAGE_ORDER = ["escalated", "missed_call", "booking", "triaging", "qualifying", "closed"]

# Columns a tenant may show in their pipeline (field -> header). "Customer"
# (the phone link) is always shown; these are the configurable extras.
COLUMN_DEFS = {
    "job_type": "Job",
    "suburb": "Suburb",
    "urgency": "Urgency",
    "triage": "Triage",
    "booking": "Booking",
    "updated_at": "Updated",
}
DEFAULT_COLUMNS = ["job_type", "suburb", "urgency", "triage", "booking", "updated_at"]


def _dashboard_cfg(t: dict) -> tuple[list[str], dict, list[str], bool]:
    """Resolve a tenant's pipeline view from an optional "dashboard" config block:
      {"columns": [...], "stage_labels": {stage: label}, "stage_order": [...]}.
    Anything omitted falls back to the defaults. Returns
    (stage_order, stage_labels, columns, order_is_explicit)."""
    cfg = t.get("dashboard") or {}
    labels = {**STAGE_LABELS, **(cfg.get("stage_labels") or {})}
    order = cfg.get("stage_order") or STAGE_ORDER
    cols = [c for c in (cfg.get("columns") or DEFAULT_COLUMNS) if c in COLUMN_DEFS]
    return order, labels, (cols or DEFAULT_COLUMNS), bool(cfg.get("stage_order"))


def _ago(ts: str) -> str:
    """Human 'time ago' from a stored UTC 'YYYY-MM-DD HH:MM:SS' timestamp."""
    if not ts:
        return ""
    try:
        when = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return _esc(ts)
    secs = (datetime.now(timezone.utc) - when).total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400:
        return f"{int(secs // 3600)} h ago"
    if secs < 7 * 86400:
        return f"{int(secs // 86400)} d ago"
    return when.strftime("%d %b")


def _stat_cards(leads: list) -> str:
    """At-a-glance pipeline counts above the lead list. Each card jumps to its
    stage section."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total = len(leads)
    booked = sum(1 for l in leads if l.get("booking"))
    missed = sum(1 for l in leads if l.get("stage") == "missed_call")
    new_today = sum(1 for l in leads if (l.get("created_at") or "").startswith(today))
    cards = (
        ("🆕", new_today, "new today", ""),
        ("📞", missed, "missed calls", "#stage-missed_call"),
        ("✅", booked, "booked", "#stage-booking"),
        ("📋", total, "total leads", "#top"),
    )
    cells = ""
    for e, n, label, href in cards:
        inner = f"<b>{n}</b><span>{_esc(f'{e} {label}')}</span>"
        cells += (
            f"<a class='stat link' href='{href}'>{inner}</a>"
            if href
            else f"<div class=stat>{inner}</div>"
        )
    return "<div class=cards>" + cells + "</div>"


def _calendar_banner(tenant_id: str, t: dict, k: str) -> str:
    """Self-serve Google Calendar status: connected ✓ or a Connect button."""
    if not gcal.is_configured():
        return ""  # OAuth not set up on this deployment — hide the control.
    tid = _esc(tenant_id)
    if gcal.is_connected(t):
        cal = _esc((t.get("google_calendar") or {}).get("calendar_id", "primary"))
        cls = "banner ok"
        inner = (
            f"<span>📅 Google Calendar connected ✓ <span class=muted>({cal})</span></span>"
            f"<a class='btn alt' href='/dashboard/{tid}/calendar/connect?key={k}'>Reconnect</a>"
        )
    else:
        cls = "banner warn"
        inner = (
            "<span>📅 Calendar not connected — confirmed jobs won't auto-book.</span>"
            f"<a class=btn href='/dashboard/{tid}/calendar/connect?key={k}'>Connect Google Calendar</a>"
        )
    return f"<div class='{cls}'>{inner}</div>"


def _lead_card(tenant_id: str, lead: dict, k: str, cols: list) -> str:
    """A single mobile-friendly lead card: number (tap to open), badges,
    qualified fields, booking, relative time, and one-tap Call / Text."""
    ph = lead["phone"]
    ph_q = urllib.parse.quote(ph)
    badges = ""
    tri = lead.get("triage") or ""
    if "triage" in cols and tri:
        cls = "emergency" if tri == "emergency" else ""
        badges += f"<span class='badge {cls}'>{_esc(tri)}</span> "
    cc = lead.get("call_count") or 0
    if cc > 1 or (cc >= 1 and lead.get("stage") != "missed_call"):
        times = f" ×{cc}" if cc > 1 else ""
        badges += f"<span class='badge recall'>📞 called again{times}</span> "
    info_fields = [c for c in cols if c not in ("updated_at", "booking", "triage")]
    meta = " · ".join(
        f"{COLUMN_DEFS[c]}: {_esc(lead.get(c))}" for c in info_fields if lead.get(c)
    )
    booking_line = (
        f"<div class='meta booked'>📅 {_esc(lead.get('booking'))}</div>"
        if "booking" in cols and lead.get("booking")
        else ""
    )
    card_cls = "lead emergency-card" if tri == "emergency" else "lead"
    return (
        f"<div class='{card_cls}'><div class=top>"
        f"<a class=ph href='/dashboard/{_esc(tenant_id)}/lead?phone={ph_q}&key={k}'>{_esc(ph)}</a>"
        f"<span>{badges}</span></div>"
        + (f"<div class=meta>{meta}</div>" if meta else "")
        + booking_line
        + "<div class=foot>"
        f"<span class=muted>{_ago(lead.get('updated_at'))}</span>"
        f"<span><a class=btn href='tel:{_esc(ph)}'>Call</a> "
        f"<a class='btn alt' href='sms:{_esc(ph)}'>Text</a></span>"
        "</div></div>"
    )


def _initials(name: str) -> str:
    parts = [
        "".join(ch for ch in p if ch.isalnum())
        for p in (name or "").replace("&", " ").split()
    ]
    parts = [p for p in parts if p]
    if not parts:
        return "ST"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return "".join(p[0] for p in parts[:2]).upper()


def _stage_text(stage: str) -> str:
    names = {
        "escalated": "Escalated",
        "missed_call": "Missed call",
        "booking": "Booking",
        "triaging": "Triaging",
        "qualifying": "Qualifying",
        "closed": "Closed",
    }
    return names.get(stage or "", (stage or "New").replace("_", " ").title())


def _stage_class(lead: dict) -> str:
    if lead.get("triage") == "emergency" or lead.get("stage") == "escalated":
        return "danger"
    if lead.get("booking"):
        return "ok"
    if lead.get("stage") == "missed_call":
        return "warn"
    return ""


def _dashboard_shell(
    t: dict,
    tenant_id: str,
    k: str,
    content: str,
    active: str = "Inbox",
    user: dict | None = None,
) -> str:
    business = t.get("business_name") or "Speed-to-Lead"
    sub = " / ".join(x for x in (t.get("trade_noun"), t.get("city")) if x)
    base = f"/dashboard/{_esc(tenant_id)}?key={k}"
    items = (
        ("Dashboard", base + "&view=dashboard"),
        ("Inbox", base + "&view=inbox"),
        ("Customers", base + "&view=customers"),
        ("Jobs", base + "&view=jobs"),
        ("Schedule", base + "&view=schedule"),
        ("Report/Results", f"/dashboard/{_esc(tenant_id)}/report?key={k}"),
        ("Settings", base + "&view=settings"),
    )
    nav = ""
    for label, href in items:
        cls = "nav-item active" if label == active else "nav-item"
        nav += (
            f"<a class='{cls}' href='{href}'><span class=nav-dot></span>"
            f"<span>{_esc(label)}</span></a>"
        )
    return (
        "<div class=app-shell>"
        "<aside class=dash-sidebar>"
        "<div class=tenant-mark>"
        f"<div class=tenant-logo>{_esc(_initials(business))}</div>"
        f"<div><div class=tenant-name>{_esc(business)}</div>"
        f"<div class=tenant-sub>{_esc(sub or 'Owner dashboard')}</div></div>"
        "</div>"
        f"<nav class=nav-list>{nav}</nav>"
        "<div class=side-foot><a class=side-link href='/logout'>Log out</a></div>"
        "</aside>"
        f"<div class=workspace>{content}</div>"
        "</div>"
    )


def _admin_shell(k: str, content: str, active: str = "Dashboard") -> str:
    items = (
        ("Dashboard", f"/dashboard?key={k}"),
        ("Inbox", f"/dashboard?key={k}"),
        ("Customers", f"/dashboard?key={k}#customers"),
        ("Jobs", f"/dashboard?key={k}#jobs"),
        ("Schedule", f"/dashboard?key={k}#schedule"),
        ("Report/Results", f"/dashboard?key={k}#reports"),
        ("Settings", f"/dashboard?key={k}#settings"),
    )
    nav = ""
    for label, href in items:
        cls = "nav-item active" if label == active else "nav-item"
        nav += (
            f"<a class='{cls}' href='{href}'><span class=nav-dot></span>"
            f"<span>{_esc(label)}</span></a>"
        )
    return (
        "<div class=app-shell>"
        "<aside class=dash-sidebar>"
        "<div class=tenant-mark>"
        "<div class=tenant-logo>ST</div>"
        "<div><div class=tenant-name>Speed-to-Lead</div>"
        "<div class=tenant-sub>Owner dashboard</div></div>"
        "</div>"
        f"<nav class=nav-list>{nav}</nav>"
        "<div class=side-foot><a class=side-link href='/logout'>Log out</a></div>"
        "</aside>"
        f"<div class='workspace pad'>{content}</div>"
        "</div>"
    )


def _lead_row(
    tenant_id: str,
    lead: dict,
    k: str,
    selected_phone: str,
    q: str = "",
    status: str = "",
) -> str:
    ph = lead["phone"]
    ph_q = urllib.parse.quote(ph)
    extras = ""
    if q:
        extras += f"&q={urllib.parse.quote(q)}"
    if status:
        extras += f"&status={urllib.parse.quote(status)}"
    active = " active" if ph == selected_phone else ""
    title = lead.get("job_type") or ph
    snippet = lead.get("snippet") or "Missed call logged. No reply yet."
    stage = _stage_text(lead.get("stage") or "")
    cls = _stage_class(lead)
    badges = f"<span class='status-pill {cls}'>{_esc(stage)}</span>"
    if lead.get("booking"):
        badges += "<span class='status-pill ok'>Booked</span>"
    elif lead.get("triage"):
        badges += f"<span class='status-pill'>{_esc(lead.get('triage'))}</span>"
    meta = []
    for field in ("suburb", "urgency"):
        if lead.get(field):
            meta.append(_esc(lead.get(field)))
    meta_line = f"<div class=customer-meta>{' / '.join(meta)}</div>" if meta else ""
    return (
        f"<a class='lead-row{active}' href='/dashboard/{_esc(tenant_id)}?phone={ph_q}&key={k}{extras}'>"
        "<div class=lead-row-top>"
        f"<div><div class=lead-title>{_esc(title)}</div>{meta_line}</div>"
        f"<span class=lead-time>{_ago(lead.get('updated_at'))}</span>"
        "</div>"
        f"<div class=lead-snippet>{_esc(snippet)}</div>"
        f"<div class=badge-line>{badges}</div>"
        "</a>"
    )


def _lead_detail_panel(tenant_id: str, selected: dict | None, k: str) -> str:
    if not selected:
        return (
            "<section class=detail-pane>"
            "<div class=detail-head><div class=customer-head>"
            "<div class=avatar>ST</div><div><div class=customer-name>No lead selected</div>"
            "<div class=customer-meta>New enquiries will open here.</div></div></div></div>"
            "<div class=channel-tabs><span class='channel-tab active'>Summary</span></div>"
            "<div class=detail-body><div class=empty-state><strong>No leads yet</strong>"
            "The next missed call or text will appear in this inbox.</div></div>"
            "</section>"
        )
    ph = selected["phone"]
    ph_q = urllib.parse.quote(ph)
    thread = selected.get("thread") or []
    voice_calls = selected.get("voice_calls") or []
    msg_count = len(thread)
    voice_msg_count = sum(len(c.get("transcript") or []) for c in voice_calls)
    call_count = selected.get("call_count") or 0
    fields = []
    for label, key in (
        ("Job", "job_type"),
        ("Suburb", "suburb"),
        ("Urgency", "urgency"),
        ("Stage", "stage"),
        ("Booking", "booking"),
    ):
        if selected.get(key):
            val = _stage_text(selected.get(key)) if key == "stage" else selected.get(key)
            fields.append(f"<li><b>{_esc(label)}:</b> {_esc(val)}</li>")
    if not fields:
        fields.append("<li>Awaiting customer details.</li>")
    bubbles = ""
    for m in thread[-6:]:
        cls = "cust" if m.get("role") == "user" else "ai"
        who = "Customer" if m.get("role") == "user" else "AI"
        bubbles += f"<div class='bubble {cls}'><b>{who}</b><br>{_esc(m.get('content'))}</div>"
    if not bubbles:
        bubbles = "<div class=empty-state>No text transcript yet.</div>"
    voice_html = ""
    if voice_calls:
        latest_call = voice_calls[0]
        voice_bubbles = ""
        for m in (latest_call.get("transcript") or [])[-8:]:
            cls = "cust" if m.get("role") == "user" else "ai"
            who = "Caller" if m.get("role") == "user" else "Syanna"
            voice_bubbles += f"<div class='bubble {cls}'><b>{who}</b><br>{_esc(m.get('content'))}</div>"
        if not voice_bubbles:
            voice_bubbles = "<div class=empty-state>Call connected; no transcript captured yet.</div>"
        meta = " · ".join(
            part
            for part in (
                latest_call.get("started_at"),
                latest_call.get("close_reason"),
                latest_call.get("call_sid"),
            )
            if part
        )
        voice_html = (
            "<div class=detail-card><h2>Latest voice call</h2>"
            f"<p class=muted>{_esc(meta)}</p>"
            f"<div class=preview-thread>{voice_bubbles}</div></div>"
        )
    return (
        "<section class=detail-pane>"
        "<div class=detail-head>"
        "<div class=customer-head>"
        f"<div class=avatar>{_esc(_initials(ph))}</div>"
        f"<div><div class=customer-name>{_esc(ph)}</div>"
        f"<div class=customer-meta>{_esc(_stage_text(selected.get('stage') or ''))} / updated {_ago(selected.get('updated_at'))}</div></div>"
        "</div>"
        "<div class=detail-actions>"
        f"<a class=icon-btn href='tel:{_esc(ph)}'>Call</a>"
        f"<a class=icon-btn href='sms:{_esc(ph)}'>Text</a>"
        f"<a class=icon-btn href='/dashboard/{_esc(tenant_id)}/lead?phone={ph_q}&key={k}'>Transcript</a>"
        "</div>"
        "</div>"
        "<div class=channel-tabs>"
        f"<span class='channel-tab active'>Call {call_count}</span>"
        f"<span class=channel-tab>Voice text {voice_msg_count}</span>"
        f"<span class=channel-tab>SMS {msg_count}</span>"
        "<span class=channel-tab>Email 0</span><span class=channel-tab>Webform 0</span>"
        "</div>"
        "<div class=detail-body>"
        f"<div class=detail-card><h2>Summary</h2><ul class=summary-points>{''.join(fields)}</ul></div>"
        f"{voice_html}"
        f"<div class=detail-card><h2>Transcript preview</h2><div class=preview-thread>{bubbles}</div></div>"
        "</div>"
        "</section>"
    )


def _lead_title(lead: dict) -> str:
    return lead.get("job_type") or lead.get("phone") or "New enquiry"


def _lead_meta(lead: dict) -> str:
    parts = []
    for field in ("suburb", "urgency", "triage"):
        if lead.get(field):
            parts.append(str(lead.get(field)))
    if lead.get("booking"):
        parts.append(f"Booked: {lead.get('booking')}")
    elif lead.get("stage"):
        parts.append(_stage_text(lead.get("stage")))
    return " / ".join(parts)


def _lead_actions(tenant_id: str, lead: dict, k: str) -> str:
    ph = lead["phone"]
    ph_q = urllib.parse.quote(ph)
    return (
        "<div class=row-actions>"
        f"<a class=icon-btn href='/dashboard/{_esc(tenant_id)}?phone={ph_q}&key={k}&view=inbox'>Open</a>"
        f"<a class=icon-btn href='tel:{_esc(ph)}'>Call</a>"
        f"<a class=icon-btn href='sms:{_esc(ph)}'>Text</a>"
        "</div>"
    )


def _data_row(tenant_id: str, lead: dict, k: str, title: str = "") -> str:
    label = title or _lead_title(lead)
    snippet = lead.get("snippet") or "No transcript yet."
    meta = _lead_meta(lead)
    cls = _stage_class(lead)
    return (
        "<div class=data-row>"
        "<div class=row-main>"
        f"<div class=row-title>{_esc(label)}</div>"
        f"<div class=row-meta>{_esc(lead.get('phone'))} / updated {_ago(lead.get('updated_at'))}</div>"
        + (f"<div class=row-meta>{_esc(meta)}</div>" if meta else "")
        + f"<div class=lead-snippet>{_esc(snippet)}</div>"
        + f"<div class=badge-line><span class='status-pill {cls}'>{_esc(_stage_text(lead.get('stage') or ''))}</span></div>"
        + "</div>"
        + _lead_actions(tenant_id, lead, k)
        + "</div>"
    )


def _view_shell(title: str, subtitle: str, inner: str) -> str:
    return (
        "<div class=page-content>"
        "<div class=view-head>"
        f"<div><h1 class=view-title>{_esc(title)}</h1>"
        f"<div class=view-sub>{_esc(subtitle)}</div></div>"
        "</div>"
        + inner
        + "</div>"
    )


def _dashboard_view(tenant_id: str, leads: list[dict], k: str) -> str:
    total = len(leads)
    open_n = sum(1 for lead in leads if lead.get("stage") != "closed")
    missed = sum(1 for lead in leads if lead.get("call_count") or lead.get("stage") == "missed_call")
    booked = sum(1 for lead in leads if lead.get("booking"))
    emergencies = sum(
        1 for lead in leads if lead.get("triage") == "emergency" or lead.get("stage") == "escalated"
    )
    cards = (
        (total, "total enquiries"),
        (open_n, "open follow-ups"),
        (missed, "calls captured"),
        (booked, "booked jobs"),
        (emergencies, "urgent escalations"),
    )
    grid = "<div class=cards>" + "".join(
        f"<div class=stat><b>{_esc(n)}</b><span>{_esc(label)}</span></div>"
        for n, label in cards
    ) + "</div>"
    latest = "".join(_data_row(tenant_id, lead, k) for lead in leads[:5])
    if not latest:
        latest = (
            "<div class=empty-state><strong>No calls captured yet</strong>"
            "Example call data will appear here after the first missed call or text.</div>"
        )
    return _view_shell("Dashboard", "Call capture overview", grid + "<div class=kicker>Latest enquiries</div><div class=data-list>" + latest + "</div>")


def _customers_view(tenant_id: str, leads: list[dict], k: str) -> str:
    rows = "".join(_data_row(tenant_id, lead, k, title=lead.get("phone")) for lead in leads)
    if not rows:
        rows = (
            "<div class=empty-state><strong>No customers yet</strong>"
            "Each caller or texter becomes a customer record here.</div>"
        )
    return _view_shell(
        "Customers",
        "Every captured phone number, with its latest request and status",
        f"<div class=data-list>{rows}</div>",
    )


def _jobs_view(tenant_id: str, leads: list[dict], k: str) -> str:
    job_leads = [
        lead
        for lead in leads
        if lead.get("job_type") or lead.get("triage") or lead.get("booking") or lead.get("stage") != "missed_call"
    ]
    rows = "".join(_data_row(tenant_id, lead, k) for lead in job_leads)
    if not rows:
        rows = (
            "<div class=empty-state><strong>No qualified jobs yet</strong>"
            "Once the caller gives enough detail, the enquiry is allocated here as a job.</div>"
        )
    return _view_shell(
        "Jobs",
        "Qualified enquiries grouped from call and text conversations",
        f"<div class=data-list>{rows}</div>",
    )


def _schedule_view(tenant_id: str, leads: list[dict], k: str) -> str:
    booked = [lead for lead in leads if lead.get("booking")]
    waiting = [lead for lead in leads if not lead.get("booking") and lead.get("stage") in ("booking", "qualifying", "triaging")]
    booked_rows = "".join(_data_row(tenant_id, lead, k, title=lead.get("booking") or _lead_title(lead)) for lead in booked)
    waiting_rows = "".join(_data_row(tenant_id, lead, k) for lead in waiting[:8])
    if not booked_rows:
        booked_rows = "<div class=empty-state><strong>No booked appointments</strong>Booked jobs will appear here.</div>"
    if not waiting_rows:
        waiting_rows = "<div class=empty-state><strong>No scheduling follow-ups</strong>Leads needing a time will appear here.</div>"
    return _view_shell(
        "Schedule",
        "Booked appointments and leads waiting on a time",
        "<div class=kicker>Booked</div><div class=data-list>"
        + booked_rows
        + "</div><div class=kicker>Needs scheduling</div><div class=data-list>"
        + waiting_rows
        + "</div>",
    )


def _settings_view(t: dict, tenant_id: str, k: str) -> str:
    fields = (
        ("Business", t.get("business_name")),
        ("Trade", t.get("trade_noun")),
        ("Service area", t.get("service_area_short") or t.get("city")),
        ("Twilio number", t.get("twilio_number") or "Not connected"),
        ("Owner mobile", t.get("owner_mobile") or "Not set"),
    )
    rows = "".join(
        "<div class=data-row><div class=row-main>"
        f"<div class=row-title>{_esc(label)}</div><div class=row-meta>{_esc(value)}</div>"
        "</div></div>"
        for label, value in fields
    )
    return _view_shell(
        "Settings",
        "Tenant routing and connection details",
        _calendar_banner(tenant_id, t, k) + f"<div class=data-list>{rows}</div>",
    )


def _sign(payload: dict) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def _unsign(token: str) -> dict | None:
    try:
        raw, sig = token.split(".", 1)
    except ValueError:
        return None
    expect = hmac.new(SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()))
    except Exception:
        return None
    if payload.get("exp", 0) < int(time.time()):
        return None
    return payload


def _current_user(request: Request, key: str = "") -> dict | None:
    """The logged-in user: {role, tenant_id} or None. Admin may pass ?key=token."""
    if key and DASHBOARD_TOKEN and secrets.compare_digest(key, DASHBOARD_TOKEN):
        return {"role": "admin", "tenant_id": None}
    if not SECRET_KEY:
        return None
    token = request.cookies.get("session", "")
    return _unsign(token) if token else None


def _can_see(user: dict, tenant_id: str) -> bool:
    return user["role"] == "admin" or user.get("tenant_id") == tenant_id


def _esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _page(
    title: str,
    body: str,
    brand: str = "Speed-to-Lead — Owner Dashboard",
    auto_refresh: int = 0,
    header_extra: str = "",
    shell: bool = False,
) -> HTMLResponse:
    refresh = f"<meta http-equiv=refresh content={auto_refresh}>" if auto_refresh else ""
    header = (
        ""
        if shell
        else (
            "<header><div class=hwrap>"
            f"<span class=brand>{_esc(brand)}</span>{header_extra}</div></header>"
        )
    )
    main_cls = " class=shell-main" if shell else ""
    return HTMLResponse(
        "<!doctype html><html><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width, initial-scale=1'>"
        "<link rel=icon href=\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'"
        " viewBox='0 0 100 100'><text y='.9em' font-size='90'>%F0%9F%93%9E</text></svg>\">"
        f"{refresh}"
        f"<title>{_esc(title)}</title><style>"
        # Clean SaaS (light) design system — tokens.
        ":root{--bg:#f4f5f7;--card:#fff;--line:#e8e9ee;--ink:#15171c;--muted:#6b7280;"
        "--nav:#141b2f;--nav-soft:#1d2740;--nav-ink:#e8edf8;--nav-muted:#98a2b3;"
        "--accent:#1684d8;--accent-soft:#eaf6ff;--ok:#15803d;--ok-soft:#e8f6ec;"
        "--danger:#dc2626;--danger-soft:#fdeceb;--warn:#b45309;--warn-soft:#fff4e2;"
        "--shadow:0 1px 2px rgba(16,24,40,.06),0 1px 3px rgba(16,24,40,.08)}"
        "*{box-sizing:border-box}"
        "body{font:15px/1.55 -apple-system,system-ui,'Segoe UI',Roboto,sans-serif;margin:0;"
        "background:var(--bg);color:var(--ink);-webkit-font-smoothing:antialiased}"
        "header{position:sticky;top:0;z-index:5;background:rgba(255,255,255,.92);"
        "backdrop-filter:saturate(1.4) blur(8px);border-bottom:1px solid var(--line)}"
        ".hwrap{max-width:980px;margin:0 auto;padding:14px 20px;display:flex;align-items:center;"
        "justify-content:space-between;gap:12px;flex-wrap:wrap}"
        ".brand{font-weight:700;font-size:16px;letter-spacing:-.01em}"
        ".chip{display:inline-flex;align-items:center;gap:6px;background:var(--accent-soft);"
        "color:var(--accent);font-size:12.5px;font-weight:600;padding:5px 11px;border-radius:99px}"
        "main{max-width:980px;margin:0 auto;padding:22px 20px 48px}"
        "main.shell-main{max-width:none;margin:0;padding:0;min-height:100vh}"
        "a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}"
        "h1{font-size:22px;letter-spacing:-.02em;margin:6px 0 2px}"
        "h2{margin:26px 0 8px;font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}"
        ".muted{color:var(--muted)}"
        "table{width:100%;border-collapse:separate;border-spacing:0;background:var(--card);"
        "border:1px solid var(--line);border-radius:12px;overflow:hidden;margin:10px 0 22px;box-shadow:var(--shadow)}"
        "th,td{text-align:left;padding:11px 14px;border-bottom:1px solid var(--line);font-size:14px;vertical-align:top}"
        "tr:last-child td{border-bottom:0}"
        "th{background:#fbfbfc;font-size:11.5px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}"
        "table tr:hover td{background:#fafbff}"
        # At-a-glance stat cards.
        ".cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:14px 0 20px}"
        ".stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;"
        "box-shadow:var(--shadow);display:block;color:inherit}"
        "a.stat.link{transition:transform .08s ease,box-shadow .08s ease}"
        "a.stat.link:hover{text-decoration:none;transform:translateY(-1px);"
        "box-shadow:0 4px 12px rgba(16,24,40,.10);border-color:#d6d9e2}"
        ".stat b{display:block;font-size:28px;font-weight:700;letter-spacing:-.02em;line-height:1.1}"
        ".stat span{font-size:12.5px;color:var(--muted)}"
        # Dashboard shell.
        ".app-shell{min-height:100vh;display:grid;grid-template-columns:232px minmax(0,1fr);background:#f6f8fb}"
        ".dash-sidebar{background:var(--nav);color:var(--nav-ink);padding:24px 14px;display:flex;"
        "flex-direction:column;gap:22px;min-width:0}"
        ".tenant-mark{padding:0 10px}.tenant-logo{width:42px;height:42px;border-radius:8px;background:#5ab6e8;"
        "display:grid;place-items:center;color:#07111f;font-weight:800;margin-bottom:12px}"
        ".tenant-name{font-size:20px;line-height:1.15;font-weight:800;overflow-wrap:anywhere}"
        ".tenant-sub{color:var(--nav-muted);font-size:12px;margin-top:5px}.nav-list{display:flex;flex-direction:column;gap:4px}"
        ".nav-item{display:flex;align-items:center;gap:10px;color:var(--nav-muted);padding:9px 10px;"
        "border-radius:8px;font-size:14px;font-weight:650;min-height:38px}.nav-item:hover{text-decoration:none;"
        "background:var(--nav-soft);color:var(--nav-ink)}.nav-item.active{background:#0f7fc7;color:#fff}"
        ".nav-dot{width:8px;height:8px;border-radius:50%;background:currentColor;opacity:.75;flex:0 0 auto}"
        ".side-foot{margin-top:auto;padding:0 10px;display:flex;flex-direction:column;gap:8px}"
        ".side-link{color:var(--nav-muted);font-size:13px}.side-link:hover{color:var(--nav-ink)}"
        ".workspace{min-width:0}.workspace.pad{padding:26px clamp(18px,3vw,36px)}"
        ".page-content{padding:26px clamp(18px,3vw,36px);max-width:980px}"
        ".inbox-layout{display:grid;grid-template-columns:minmax(320px,400px) minmax(0,1fr);min-height:100vh}"
        ".inbox-pane{background:#fff;border-right:1px solid var(--line);min-width:0;display:flex;flex-direction:column}"
        ".pane-head{padding:24px 22px 14px;border-bottom:1px solid var(--line)}"
        ".pane-title{font-size:26px;font-weight:800;letter-spacing:0;line-height:1.15;margin:0 0 14px}"
        ".tool-row{display:flex;gap:9px;align-items:center;min-width:0}.tool-row+.tool-row{margin-top:10px}"
        ".search-input,.select-input{height:38px;border:1px solid var(--line);border-radius:8px;background:#fff;"
        "padding:0 11px;color:var(--ink);font:inherit;min-width:0}.search-input{flex:1}.select-input{flex:0 0 132px}"
        ".filter-btn{height:38px;border:1px solid var(--line);border-radius:8px;background:#fff;color:var(--ink);"
        "font-weight:700;padding:0 13px;cursor:pointer}.lead-list{overflow:auto;padding:10px 10px 18px}"
        ".lead-row{display:block;color:inherit;border:1px solid transparent;border-radius:8px;padding:12px;margin:4px 0}"
        ".lead-row:hover{text-decoration:none;background:#f8fbff}.lead-row.active{background:#eef8ff;border-color:#c9e9fb}"
        ".lead-row-top{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.lead-title{font-weight:800;"
        "color:#111827;overflow-wrap:anywhere}.lead-time{color:var(--muted);font-size:12px;white-space:nowrap}"
        ".lead-snippet{color:#4b5563;font-size:13px;line-height:1.4;margin-top:7px;display:-webkit-box;"
        "-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden}.badge-line{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}"
        ".status-pill{display:inline-flex;align-items:center;height:22px;border-radius:999px;background:var(--accent-soft);"
        "color:var(--accent);font-size:11px;font-weight:800;padding:0 8px}.status-pill.ok{background:var(--ok-soft);color:var(--ok)}"
        ".status-pill.warn{background:var(--warn-soft);color:var(--warn)}.status-pill.danger{background:var(--danger-soft);color:var(--danger)}"
        ".detail-pane{min-width:0;background:#fbfcff;display:flex;flex-direction:column}.detail-head{background:#fff;"
        "border-bottom:1px solid var(--line);padding:22px 28px;display:flex;align-items:center;justify-content:space-between;"
        "gap:18px;flex-wrap:wrap}.customer-head{display:flex;align-items:center;gap:13px;min-width:0}"
        ".avatar{width:42px;height:42px;border-radius:50%;background:#f8c35b;color:#152033;display:grid;place-items:center;"
        "font-weight:800;flex:0 0 auto}.customer-name{font-weight:800;font-size:16px;overflow-wrap:anywhere}"
        ".customer-meta{color:var(--muted);font-size:12px}.detail-actions{display:flex;gap:8px;flex-wrap:wrap}"
        ".icon-btn{border:1px solid var(--line);background:#fff;color:var(--ink);border-radius:8px;height:36px;padding:0 12px;"
        "display:inline-flex;align-items:center;font-size:13px;font-weight:800}.icon-btn:hover{text-decoration:none;border-color:#cfd5df}"
        ".channel-tabs{display:flex;gap:8px;align-items:center;padding:12px 28px;background:#fff;border-bottom:1px solid var(--line);"
        "overflow:auto}.channel-tab{white-space:nowrap;font-size:13px;color:#667085;padding:7px 10px;border-radius:8px}"
        ".channel-tab.active{background:var(--accent-soft);color:var(--accent);font-weight:800}.detail-body{padding:22px 28px 34px;max-width:860px}"
        ".detail-card{background:#fff;border:1px solid var(--line);border-radius:8px;padding:18px;margin-bottom:14px;box-shadow:var(--shadow)}"
        ".detail-card h2{margin:0 0 10px;text-transform:none;letter-spacing:0;font-size:15px;color:#111827}"
        ".summary-points{margin:0;padding-left:18px;color:#4b5563}.summary-points li{margin:4px 0}.preview-thread{display:flex;flex-direction:column;gap:8px}"
        ".empty-state{border:1px dashed #ccd3df;border-radius:8px;padding:24px;background:#fff;color:var(--muted);text-align:center}"
        ".empty-state strong{display:block;color:#111827;font-size:16px;margin-bottom:4px}"
        ".view-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap;margin-bottom:16px}"
        ".view-title{font-size:26px;font-weight:800;letter-spacing:0;line-height:1.15;margin:0}.view-sub{color:var(--muted);margin-top:4px}"
        ".data-list{display:grid;gap:10px}.data-row{background:#fff;border:1px solid var(--line);border-radius:8px;padding:14px;"
        "box-shadow:var(--shadow);display:flex;justify-content:space-between;gap:14px;align-items:flex-start}"
        ".row-main{min-width:0}.row-title{font-weight:800;color:#111827;overflow-wrap:anywhere}.row-meta{color:#4b5563;font-size:13px;margin-top:4px}"
        ".row-actions{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}.kicker{font-size:12px;font-weight:800;color:var(--muted);"
        "text-transform:uppercase;letter-spacing:.04em;margin:22px 0 8px}.mini-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}"
        # Lead cards.
        ".lead{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;"
        "margin:10px 0;box-shadow:var(--shadow)}"
        ".lead.emergency-card{border-left:3px solid var(--danger)}"
        ".lead .top{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}"
        ".ph{font-weight:700;font-size:16px;letter-spacing:-.01em}"
        ".lead .meta{color:#4b5563;font-size:14px;margin:6px 0}.booked{color:var(--ok);font-weight:600}"
        ".foot{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:12px;flex-wrap:wrap}"
        # Badges.
        ".badge{display:inline-block;padding:3px 9px;border-radius:99px;font-size:11.5px;font-weight:600;"
        "background:var(--accent-soft);color:var(--accent)}"
        ".emergency{background:var(--danger-soft);color:var(--danger)}"
        ".recall{background:var(--warn-soft);color:var(--warn)}"
        # Buttons.
        ".btn{display:inline-block;padding:8px 15px;border-radius:8px;background:var(--accent);color:#fff;"
        "font-size:13px;font-weight:600}.btn:hover{text-decoration:none;filter:brightness(1.05)}"
        ".btn.alt{background:var(--accent-soft);color:var(--accent)}"
        # Self-serve calendar banner.
        ".banner{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;"
        "background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px;"
        "margin:0 0 14px;box-shadow:var(--shadow)}"
        ".banner.ok{background:var(--ok-soft);border-color:#cfe9d6}"
        ".banner.warn{background:var(--warn-soft);border-color:#f3e2c2}"
        # Conversation bubbles (lead detail).
        ".row{display:flex;flex-direction:column;gap:2px}"
        ".bubble{max-width:78%;padding:9px 13px;border-radius:14px;margin:4px 0;white-space:pre-wrap;"
        "font-size:14px;box-shadow:var(--shadow)}"
        ".bubble b{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}"
        ".cust{background:var(--card);border:1px solid var(--line)}"
        ".ai{background:var(--accent-soft);margin-left:auto}"
        "@media(max-width:900px){.app-shell{display:block}.dash-sidebar{position:sticky;top:0;z-index:4;padding:14px;gap:12px}"
        ".tenant-mark{display:flex;align-items:center;gap:10px;padding:0}.tenant-logo{width:34px;height:34px;margin:0}"
        ".tenant-name{font-size:16px}.tenant-sub{display:none}.nav-list{flex-direction:row;overflow:auto;padding-bottom:2px}"
        ".nav-item{flex:0 0 auto}.side-foot{display:none}.inbox-layout{grid-template-columns:1fr;min-height:0}"
        ".inbox-pane{border-right:0;border-bottom:1px solid var(--line)}.lead-list{max-height:none;overflow:visible}"
        ".detail-head,.channel-tabs,.detail-body{padding-left:18px;padding-right:18px}.workspace.pad,.page-content{padding:18px}}"
        "@media(max-width:480px){body{font-size:14px}.pane-head{padding:18px 14px 12px}.pane-title{font-size:22px}"
        ".tool-row{flex-wrap:wrap}.search-input{flex:1 1 100%}.select-input{flex:1 1 120px}.filter-btn{flex:0 0 auto}"
        ".detail-head{align-items:flex-start}.icon-btn{height:34px;padding:0 10px}.bubble{max-width:92%}}"
        "</style></head><body>"
        f"{header}<main{main_cls}>{body}</main></body></html>"
    )


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/login", response_class=HTMLResponse)
def login_form(error: str = "") -> HTMLResponse:
    msg = (
        "<p style='color:#b91c1c'>Wrong username or password.</p>" if error else ""
    )
    body = (
        f"{msg}<form method=post action=/login class=row style='max-width:320px'>"
        "<h1>Log in</h1>"
        "<label>Username<br><input name=username autofocus "
        "style='width:100%;padding:8px;margin:4px 0 10px'></label>"
        "<label>Password<br><input name=password type=password "
        "style='width:100%;padding:8px;margin:4px 0 14px'></label>"
        "<button style='padding:9px 16px;background:#111;color:#fff;border:0;"
        "border-radius:6px;cursor:pointer'>Log in</button></form>"
    )
    return _page("Log in", body)


@app.post("/login")
def login_submit(username: str = Form(...), password: str = Form(...)) -> Response:
    if (
        ADMIN_USERNAME
        and ADMIN_PASSWORD
        and secrets.compare_digest(username, ADMIN_USERNAME)
        and secrets.compare_digest(password, ADMIN_PASSWORD)
    ):
        payload = {"role": "admin", "tenant_id": None}
    else:
        tenant_id = accounts.verify(username, password)
        if not tenant_id:
            return RedirectResponse("/login?error=1", status_code=303)
        payload = {"role": "tenant", "tenant_id": tenant_id}
    payload["exp"] = int(time.time()) + SESSION_TTL
    resp = RedirectResponse("/dashboard", status_code=303)
    resp.set_cookie(
        "session", _sign(payload), httponly=True, samesite="lax", max_age=SESSION_TTL
    )
    return resp


@app.get("/logout")
def logout() -> Response:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session")
    return resp


@app.get("/dashboard")
def dashboard(request: Request, key: str = "") -> Response:
    user = _current_user(request, key)
    if not user:
        return RedirectResponse("/login", status_code=303)
    # A client only ever sees their own tenant.
    if user["role"] == "tenant":
        return RedirectResponse(f"/dashboard/{user['tenant_id']}", status_code=303)
    k = urllib.parse.quote(key)
    rows = ""
    for t in tenants.all_tenants():
        leads = store.list_leads(t["tenant_id"])
        open_n = sum(1 for lead in leads if lead.get("stage") != "closed")
        rows += (
            f"<tr><td><a href='/dashboard/{_esc(t['tenant_id'])}?key={k}'>"
            f"{_esc(t['business_name'])}</a><div class=muted>{_esc(t['trade_noun'])}"
            f" · {_esc(t.get('twilio_number') or 'no number')}</div></td>"
            f"<td>{len(leads)}</td><td>{open_n}</td></tr>"
        )
    if not rows:
        rows = "<tr><td colspan=3 class=muted>No tenants yet.</td></tr>"
    admin_body = (
        "<div class=detail-card>"
        "<h1 class=pane-title>Businesses</h1>"
        f"<table><tr><th>Business</th><th>Leads</th><th>Open</th></tr>{rows}</table>"
        "</div>"
    )
    return _page("Dashboard", _admin_shell(k, admin_body), shell=True)



@app.get("/dashboard/{tenant_id}")
def dashboard_tenant(
    tenant_id: str,
    request: Request,
    key: str = "",
    phone: str = "",
    q: str = "",
    status: str = "",
    view: str = "inbox",
) -> Response:
    user = _current_user(request, key)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not _can_see(user, tenant_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        t = tenants.load_tenant(tenant_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Unknown tenant")
    k = urllib.parse.quote(key)
    all_leads = store.list_leads(tenant_id)
    view = view if view in ("dashboard", "inbox", "customers", "jobs", "schedule", "settings") else "inbox"
    if view != "inbox":
        view_map = {
            "dashboard": ("Dashboard", _dashboard_view(tenant_id, all_leads, k)),
            "customers": ("Customers", _customers_view(tenant_id, all_leads, k)),
            "jobs": ("Jobs", _jobs_view(tenant_id, all_leads, k)),
            "schedule": ("Schedule", _schedule_view(tenant_id, all_leads, k)),
            "settings": ("Settings", _settings_view(t, tenant_id, k)),
        }
        active, content = view_map[view]
        body = _dashboard_shell(t, tenant_id, k, content, active=active, user=user)
        return _page(
            t["business_name"],
            body,
            brand=f"{t['business_name']} - {active}",
            auto_refresh=30,
            shell=True,
        )
    needle = q.strip().lower()
    status = status if status in ("", "open", "missed_call", "booking", "closed") else ""
    visible_leads = all_leads
    if needle:
        visible_leads = [
            lead
            for lead in visible_leads
            if needle
            in " ".join(
                str(lead.get(field) or "")
                for field in ("phone", "job_type", "suburb", "urgency", "triage", "snippet")
            ).lower()
        ]
    if status == "open":
        visible_leads = [lead for lead in visible_leads if lead.get("stage") != "closed"]
    elif status:
        visible_leads = [lead for lead in visible_leads if lead.get("stage") == status]

    selected_phone = phone or (visible_leads[0]["phone"] if visible_leads else "")
    selected = store.lead_detail(tenant_id, selected_phone) if selected_phone else None
    lead_rows = "".join(
        _lead_row(tenant_id, lead, k, selected_phone, q, status) for lead in visible_leads
    )
    if not lead_rows:
        lead_rows = (
            "<div class=empty-state><strong>No matching leads</strong>"
            "New enquiries will appear here as soon as they arrive.</div>"
        )
    status_options = (
        ("", "All messages"),
        ("open", "Open"),
        ("missed_call", "Missed calls"),
        ("booking", "Booked"),
        ("closed", "Closed"),
    )
    opts = "".join(
        f"<option value='{_esc(value)}'{' selected' if value == status else ''}>{_esc(label)}</option>"
        for value, label in status_options
    )
    inbox = (
        "<div class=inbox-layout>"
        "<section class=inbox-pane>"
        "<div class=pane-head>"
        "<h1 class=pane-title>Inbox</h1>"
        f"<form method=get action='/dashboard/{_esc(tenant_id)}'>"
        f"<input type=hidden name=key value='{_esc(key)}'>"
        "<div class=tool-row>"
        f"<input class=search-input name=q value='{_esc(q)}' placeholder='Search'>"
        "<button class=filter-btn type=submit>Filter</button>"
        "</div>"
        "<div class=tool-row>"
        f"<select class=select-input name=status>{opts}</select>"
        "<select class=select-input name=sort disabled><option>Newest</option></select>"
        "</div>"
        "</form>"
        "</div>"
        f"<div class=lead-list>{lead_rows}</div>"
        "</section>"
        + _lead_detail_panel(tenant_id, selected, k)
        + "</div>"
    )
    body = _dashboard_shell(t, tenant_id, k, inbox, active="Inbox", user=user)
    return _page(
        t["business_name"],
        body,
        brand=f"{t['business_name']} - Leads",
        auto_refresh=30,
        shell=True,
    )



@app.get("/dashboard/{tenant_id}/lead")
def dashboard_lead(
    tenant_id: str, phone: str, request: Request, key: str = ""
) -> Response:
    user = _current_user(request, key)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not _can_see(user, tenant_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    lead = store.lead_detail(tenant_id, phone)
    if not lead:
        raise HTTPException(status_code=404, detail="Unknown lead")
    k = urllib.parse.quote(key)
    try:
        t = tenants.load_tenant(tenant_id)
    except (FileNotFoundError, ValueError):
        t = {"business_name": "Speed-to-Lead", "trade_noun": "", "city": ""}
    fields = " · ".join(
        f"{f}: {_esc(lead.get(f))}"
        for f in ("job_type", "urgency", "suburb", "property_type", "triage", "stage", "booking")
        if lead.get(f)
    )
    bubbles = ""
    for m in lead["thread"]:
        cls = "cust" if m["role"] == "user" else "ai"
        who = "Customer" if m["role"] == "user" else "AI"
        bubbles += f"<div class='bubble {cls}'><b>{who}</b><br>{_esc(m['content'])}</div>"
    if not bubbles:
        bubbles = (
            "<p class=muted>No SMS transcript yet.</p>"
        )
    voice_sections = ""
    for call in lead.get("voice_calls") or []:
        call_rows = ""
        for m in call.get("transcript") or []:
            cls = "cust" if m.get("role") == "user" else "ai"
            who = "Caller" if m.get("role") == "user" else "Syanna"
            call_rows += f"<div class='bubble {cls}'><b>{who}</b><br>{_esc(m.get('content'))}</div>"
        if not call_rows:
            call_rows = "<p class=muted>Call connected; no transcript captured yet.</p>"
        meta = " · ".join(
            part
            for part in (
                call.get("started_at"),
                call.get("close_reason"),
                call.get("call_sid"),
            )
            if part
        )
        voice_sections += (
            "<div class=detail-card>"
            f"<h2>Voice call</h2><p class=muted>{_esc(meta)}</p>"
            f"<div class=row>{call_rows}</div></div>"
        )
    if not voice_sections:
        voice_sections = "<p class=muted>No voice transcript captured yet.</p>"
    try:
        brand = f"{tenants.load_tenant(tenant_id)['business_name']} — Leads"
    except (FileNotFoundError, ValueError):
        brand = "Speed-to-Lead — Owner Dashboard"
    body = (
        f"<p><a href='/dashboard/{_esc(tenant_id)}?key={k}'>← back</a></p>"
        f"<h1>{_esc(lead['phone'])}</h1><p class=muted>{fields}</p>"
        f"{voice_sections}"
        f"<h2>SMS transcript</h2><div class=row>{bubbles}</div>"
    )
    shell_body = _dashboard_shell(
        t,
        tenant_id,
        k,
        f"<div class=page-content>{body}</div>",
        active="Inbox",
        user=user,
    )
    return _page(lead["phone"], shell_body, brand=brand, shell=True)


# --- Owner ROI report -------------------------------------------------------
# The proof that justifies the invoice: what the AI caught and booked over a
# period, with a conservative dollar value (booked jobs only). See report.py.

REPORT_PERIODS = (7, 30, 90)


@app.get("/dashboard/{tenant_id}/report")
def dashboard_report(
    tenant_id: str, request: Request, key: str = "", days: int = 30
) -> Response:
    user = _current_user(request, key)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not _can_see(user, tenant_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        t = tenants.load_tenant(tenant_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Unknown tenant")
    if days not in REPORT_PERIODS:
        days = 30
    k = urllib.parse.quote(key)
    r = report.build(t, days)

    # Period switcher (7 / 30 / 90 days), current one bolded.
    switch = " · ".join(
        (
            f"<b>last {d} days</b>"
            if d == days
            else f"<a href='/dashboard/{_esc(tenant_id)}/report?days={d}&key={k}'>last {d} days</a>"
        )
        for d in REPORT_PERIODS
    )

    # Headline: the dollar figure that makes the value visible.
    hero = (
        "<div class='lead' style='text-align:center;padding:26px 16px'>"
        f"<div style='font-size:40px;font-weight:700;letter-spacing:-.02em;line-height:1.1'>"
        f"≈ ${r['booked_value']:,}</div>"
        "<div class=muted style='margin-top:6px'>estimated value of work booked</div>"
        "</div>"
    )

    cards = [
        (r["leads"], "📋 enquiries caught"),
        (r["missed_calls"], "📞 missed calls texted back"),
        (r["booked"], "✅ jobs booked"),
        (r["emergencies"], "🔴 emergencies escalated"),
        (r["quotes"], "📝 quotes sent"),
    ]
    cells = "".join(
        f"<div class=stat><b>{_esc(n)}</b><span>{_esc(label)}</span></div>"
        for n, label in cards
    )
    grid = f"<div class=cards>{cells}</div>"

    footnote = (
        "<p class=muted style='font-size:12.5px'>Value is a conservative estimate: "
        f"booked jobs × ${r['avg_job_value']:,} average job value. Quotes and "
        "emergencies aren't priced in.</p>"
    )

    body = (
        f"<p id=top class=muted><a href='/dashboard/{_esc(tenant_id)}?key={k}'>← back to leads</a></p>"
        f"<h1>Your results</h1><p class=muted>{switch}</p>"
        + hero
        + grid
        + footnote
    )
    shell_body = _dashboard_shell(
        t,
        tenant_id,
        k,
        f"<div class=page-content>{body}</div>",
        active="Report/Results",
        user=user,
    )
    return _page(
        f"{t['business_name']} - Results",
        shell_body,
        brand=f"{t['business_name']} - Results",
        shell=True,
    )



# --- Self-serve Google Calendar connect -------------------------------------
# The client clicks "Connect" on their dashboard -> we redirect them to Google
# -> Google redirects back to /oauth/google/callback with a code -> we exchange
# it for a refresh token and store it on their tenant. The `state` we sign is a
# capability scoped to one tenant (short TTL): only this server can mint it, and
# only after the connect route confirmed the caller may see that tenant.

OAUTH_CALLBACK_PATH = "/oauth/google/callback"
OAUTH_STATE_TTL = 600  # 10 minutes to complete the consent screen


@app.get("/dashboard/{tenant_id}/calendar/connect")
def calendar_connect(tenant_id: str, request: Request, key: str = "") -> Response:
    user = _current_user(request, key)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not _can_see(user, tenant_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        tenants.load_tenant(tenant_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Unknown tenant")
    if not gcal.is_configured():
        raise HTTPException(status_code=503, detail="Google OAuth not configured")
    redirect_uri = _base_url(request) + OAUTH_CALLBACK_PATH
    state = _sign({"tenant_id": tenant_id, "key": key, "exp": int(time.time()) + OAUTH_STATE_TTL})
    return RedirectResponse(gcal.authorization_url(redirect_uri, state), status_code=303)


@app.get(OAUTH_CALLBACK_PATH)
def google_callback(request: Request, code: str = "", state: str = "", error: str = "") -> Response:
    if error:
        return _page("Calendar", f"<h1>Connection cancelled</h1><p class=muted>{_esc(error)}</p>")
    payload = _unsign(state)
    if not payload or "tenant_id" not in payload:
        raise HTTPException(status_code=400, detail="Invalid or expired state")
    tenant_id = payload["tenant_id"]
    key = payload.get("key", "")
    redirect_uri = _base_url(request) + OAUTH_CALLBACK_PATH
    try:
        refresh_token = gcal.exchange_code(redirect_uri, code)
    except Exception as exc:  # surface Google errors as a friendly page, not a 500
        return _page("Calendar", f"<h1>Couldn't connect</h1><p class=muted>{_esc(exc)}</p>")
    if not refresh_token:
        return _page(
            "Calendar",
            "<h1>Almost there</h1><p>Google didn't return a refresh token. "
            "Please click Connect again and approve the consent screen.</p>",
        )
    tenants.save_google_calendar(tenant_id, refresh_token)
    dest = f"/dashboard/{tenant_id}" + (f"?key={urllib.parse.quote(key)}" if key else "")
    return RedirectResponse(dest, status_code=303)
