"""FastAPI server for the electrician speed-to-lead product.

The conversation engine (`workflow.py`) is fronted by the Twilio webhooks
(`/twilio/*`) — the live product. A real missed call or SMS to the business
number runs the qualify -> triage -> book workflow. Owners watch their pipeline
and take over leads from the dashboard (`/dashboard/*`).

Run locally:  python -m uvicorn app:app --reload   (then http://127.0.0.1:8000)
"""

import base64
import hashlib
import hmac
import html
import json
import os
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
from fastapi.responses import HTMLResponse, RedirectResponse

import accounts
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse

from zoneinfo import ZoneInfo

import gcal
import store
import telegram_io
import tenants
import twilio_io
from workflow import DEFAULT_TENANT, run_turn, system_for

HERE = Path(__file__).parent
app = FastAPI(title="Speed-to-Lead")

# --- Config (from environment) ----------------------------------------------
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
# Fallback alert number when a tenant has no owner_mobile configured.
FALLBACK_OWNER_MOBILE = os.environ.get("ELECTRICIAN_MOBILE", "")

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
        if caller:
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
    """At-a-glance ROI cards above the pipeline."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cards = (
        ("🆕", sum(1 for l in leads if (l.get("created_at") or "").startswith(today)), "new today"),
        ("📞", sum(1 for l in leads if l.get("stage") == "missed_call"), "missed calls"),
        ("📅", sum(1 for l in leads if l.get("booking")), "booked"),
        ("📋", len(leads), "total leads"),
    )
    return "<div class=cards>" + "".join(
        f"<div class=stat><b>{n}</b><span>{_esc(f'{e} {label}')}</span></div>"
        for e, n, label in cards
    ) + "</div>"


def _calendar_banner(tenant_id: str, t: dict, k: str) -> str:
    """Self-serve Google Calendar status: connected ✓ or a Connect button."""
    if not gcal.is_configured():
        return ""  # OAuth not set up on this deployment — hide the control.
    tid = _esc(tenant_id)
    if gcal.is_connected(t):
        cal = _esc((t.get("google_calendar") or {}).get("calendar_id", "primary"))
        inner = (
            f"<span>📅 Google Calendar connected ✓ <span class=muted>({cal})</span></span>"
            f"<a class='btn alt' href='/dashboard/{tid}/calendar/connect?key={k}'>Reconnect</a>"
        )
    else:
        inner = (
            "<span>📅 Calendar not connected — confirmed jobs won't auto-book.</span>"
            f"<a class=btn href='/dashboard/{tid}/calendar/connect?key={k}'>Connect Google Calendar</a>"
        )
    return (
        "<div style='display:flex;justify-content:space-between;align-items:center;"
        "gap:10px;flex-wrap:wrap;background:#fff;border-radius:10px;padding:12px 14px;"
        "margin:0 0 14px;box-shadow:0 1px 3px rgba(0,0,0,.08)'>"
        f"{inner}</div>"
    )


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
    return (
        "<div class=lead><div class=top>"
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
) -> HTMLResponse:
    refresh = f"<meta http-equiv=refresh content={auto_refresh}>" if auto_refresh else ""
    return HTMLResponse(
        "<!doctype html><html><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width, initial-scale=1'>"
        "<link rel=icon href=\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'"
        " viewBox='0 0 100 100'><text y='.9em' font-size='90'>%F0%9F%93%9E</text></svg>\">"
        f"{refresh}"
        f"<title>{_esc(title)}</title><style>"
        "body{font:15px/1.5 system-ui,sans-serif;margin:0;background:#f6f7f9;color:#1a1a1a}"
        "header{background:#111;color:#fff;padding:14px 20px;font-weight:600}"
        "main{max-width:980px;margin:0 auto;padding:20px}"
        "a{color:#1558d6;text-decoration:none}a:hover{text-decoration:underline}"
        "table{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;"
        "overflow:hidden;margin:8px 0 22px;box-shadow:0 1px 3px rgba(0,0,0,.08)}"
        "th,td{text-align:left;padding:9px 12px;border-bottom:1px solid #eee;font-size:14px;vertical-align:top}"
        "th{background:#fafafa;font-size:12px;text-transform:uppercase;letter-spacing:.03em;color:#666}"
        "h1{font-size:20px;margin:6px 0}h2{margin:18px 0 6px;font-size:15px}"
        ".badge{display:inline-block;padding:2px 8px;border-radius:99px;font-size:12px;background:#eef}"
        ".emergency{background:#fde8e8;color:#b91c1c}.muted{color:#888}"
        ".recall{background:#fff3cd;color:#92600a}"
        ".bubble{max-width:78%;padding:8px 12px;border-radius:12px;margin:6px 0;white-space:pre-wrap}"
        ".cust{background:#eef1f6}.ai{background:#dcf5e6;margin-left:auto}"
        ".row{display:flex;flex-direction:column}"
        # Tier 1 dashboard UI: stat cards, lead cards, one-tap action buttons.
        ".cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:12px 0 18px}"
        ".stat{background:#fff;border-radius:10px;padding:12px 14px;box-shadow:0 1px 3px rgba(0,0,0,.08)}"
        ".stat b{display:block;font-size:26px;line-height:1.1}.stat span{font-size:12px;color:#666}"
        ".lead{background:#fff;border-radius:10px;padding:12px 14px;margin:8px 0;box-shadow:0 1px 3px rgba(0,0,0,.08)}"
        ".lead .top{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}"
        ".ph{font-weight:600;font-size:16px}"
        ".lead .meta{color:#555;font-size:14px;margin:4px 0}.booked{color:#0a7d33;font-weight:600}"
        ".foot{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:10px;flex-wrap:wrap}"
        ".btn{display:inline-block;padding:7px 14px;border-radius:6px;background:#1558d6;color:#fff;font-size:13px}"
        ".btn:hover{text-decoration:none;opacity:.92}.btn.alt{background:#eef;color:#1558d6}"
        f"</style></head><body><header>{_esc(brand)}</header><main>{body}</main></body></html>"
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
    body = (
        "<p class=muted><a href='/logout'>log out</a></p>"
        f"<table><tr><th>Business</th><th>Leads</th><th>Open</th></tr>{rows}</table>"
    )
    return _page("Dashboard", body)


@app.get("/dashboard/{tenant_id}")
def dashboard_tenant(tenant_id: str, request: Request, key: str = "") -> Response:
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
    groups: dict[str, list] = {}
    for lead in all_leads:
        groups.setdefault(lead.get("stage") or "other", []).append(lead)

    # Admins get a back-link to all businesses; clients just get log out.
    nav = (
        f"<a href='/dashboard?key={k}'>← all businesses</a>"
        if user["role"] == "admin"
        else "<a href='/logout'>log out</a>"
    )
    order, labels, cols, explicit = _dashboard_cfg(t)
    body = (
        f"<p class=muted>{nav}</p><h1>{_esc(t['business_name'])}</h1>"
        + _calendar_banner(tenant_id, t, k)
        + _stat_cards(all_leads)
    )
    # An explicit stage_order hides any stage not listed; otherwise show extras.
    stages_seq = list(order) if explicit else order + [s for s in groups if s not in order]
    shown = False
    for stage in stages_seq:
        items = groups.get(stage)
        if not items:
            continue
        shown = True
        label = labels.get(stage, stage.title())
        body += f"<h2>{_esc(label)} ({len(items)})</h2>"
        body += "".join(_lead_card(tenant_id, lead, k, cols) for lead in items)
    if not shown:
        body += (
            "<p class=muted>No leads yet — your next missed call or text "
            "lands here automatically.</p>"
        )
    return _page(
        t["business_name"], body, brand=f"{t['business_name']} — Leads", auto_refresh=30
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
            "<p class=muted>📞 Missed call — we texted this caller back "
            "automatically. No reply yet; might be worth a ring.</p>"
        )
    try:
        brand = f"{tenants.load_tenant(tenant_id)['business_name']} — Leads"
    except (FileNotFoundError, ValueError):
        brand = "Speed-to-Lead — Owner Dashboard"
    body = (
        f"<p><a href='/dashboard/{_esc(tenant_id)}?key={k}'>← back</a></p>"
        f"<h1>{_esc(lead['phone'])}</h1><p class=muted>{fields}</p>"
        f"<div class=row>{bubbles}</div>"
    )
    return _page(lead["phone"], body, brand=brand)


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
