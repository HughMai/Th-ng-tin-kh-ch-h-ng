"""FastAPI server for the electrician speed-to-lead product.

Two front doors onto the same conversation engine (`workflow.py`):

- The web simulator (`index.html` + `/api/*`) — the laptop sales demo.
- The Twilio webhooks (`/twilio/*`) — the live product. A real missed call or
  SMS to the business number runs the same qualify -> triage -> book workflow.

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
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

import accounts
from pydantic import BaseModel
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse

from zoneinfo import ZoneInfo

import gcal
import store
import tenants
import twilio_io
from workflow import DEFAULT_TENANT, run_followup, run_turn, system_for

load_dotenv()

HERE = Path(__file__).parent
app = FastAPI(title="Speed-to-Lead")

# --- Config (from environment) ----------------------------------------------
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
# Fallback alert number when a tenant has no owner_mobile configured.
FALLBACK_OWNER_MOBILE = os.environ.get("ELECTRICIAN_MOBILE", "")

store.configure(os.environ.get("DB_PATH", "data/leads.db"))


def _resolve_tenant(form: dict) -> dict:
    """Route an inbound Twilio webhook to its tenant by the number dialled
    (the `To` field). Falls back to the default tenant for single-number setups."""
    return tenants.find_by_number(form.get("To", "")) or DEFAULT_TENANT


def _owner_mobile(t: dict) -> str:
    return t.get("owner_mobile") or FALLBACK_OWNER_MOBILE


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


# --- Web simulator (the laptop sales demo) ----------------------------------

class Msg(BaseModel):
    role: Literal["customer", "ai"]
    text: str


class ChatRequest(BaseModel):
    messages: list[Msg]


class FollowUpRequest(BaseModel):
    messages: list[Msg]
    attempt: int  # 1 = first nudge (~2h later), 2 = second nudge (~24h later)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(HERE / "index.html")


@app.post("/api/message")
def message(req: ChatRequest) -> dict:
    # The browser holds the whole SMS thread and sends it each turn — the
    # workflow engine is stateless, exactly as it will be behind a webhook.
    conversation = [
        {"role": "user" if m.role == "customer" else "assistant", "content": m.text}
        for m in req.messages
    ]
    try:
        turn = run_turn(conversation)
    except anthropic.APIError as exc:
        raise HTTPException(status_code=502, detail=f"Claude API error: {exc}")
    return turn.model_dump()


@app.post("/api/followup")
def followup(req: FollowUpRequest) -> dict:
    # The customer has gone quiet. Ask the engine to compose a nudge — or to
    # decide the lead has had enough chasing and stop.
    conversation = [
        {"role": "user" if m.role == "customer" else "assistant", "content": m.text}
        for m in req.messages
    ]
    try:
        result = run_followup(conversation, req.attempt)
    except anthropic.APIError as exc:
        raise HTTPException(status_code=502, detail=f"Claude API error: {exc}")
    return result.model_dump()


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
            store.log_missed_call(tenant["tenant_id"], caller)
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
            store.log_missed_call(tenant["tenant_id"], caller)
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

    thread = store.load_thread(tenant["tenant_id"], customer)
    thread.append({"role": "user", "content": body})

    try:
        # Blocking Claude call — run it off the event loop so concurrent texts
        # aren't held up behind it. Use the tenant's own system prompt, plus a
        # current-time note so it can compute ISO booking times.
        turn = await run_in_threadpool(
            run_turn, thread, system_for(tenant), _now_line(tenant)
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
        mr = MessagingResponse()
        mr.message(
            f"Thanks for your message — {tenant['owner_name']} will get back to you shortly."
        )
        return _twiml(str(mr))

    thread.append({"role": "assistant", "content": turn.reply})
    store.save_turn(tenant["tenant_id"], customer, thread, turn)

    # On a confirmed booking, create the calendar event. Best-effort: a calendar
    # failure must never break the customer reply or the owner alert.
    booking_link = ""
    if turn.booking_start and turn.booking_end and gcal.is_connected(tenant):
        start_iso, end_iso = _roll_to_future(turn.booking_start, turn.booking_end)
        try:
            booking_link = gcal.create_event(
                tenant,
                summary=f"{turn.qualified.job_type or 'Job'} — {customer}",
                start_iso=start_iso,
                end_iso=end_iso,
                description=turn.electrician_notification or turn.booking or "",
            )
        except Exception as exc:
            print(f"[gcal] booking failed for {tenant['tenant_id']}: {exc}")

    if turn.electrician_notification:
        note = turn.electrician_notification
        if booking_link:
            note += f"\nCalendar: {booking_link}"
        twilio_io.send_sms(owner, note)

    mr = MessagingResponse()
    mr.message(turn.reply)
    return _twiml(str(mr))


# --- Lead capture (demo simulator -> Telegram + JSONL) ----------------------
# When a prospect engages the simulator and submits the CTA form, capture
# their identity + a 1-line summary of what they asked the demo. Land it in
# the persistent JSONL log AND ping Hughie's Telegram via the existing
# @BaoBei09bot (the Hermes bot — see references/hermes-setup.md). No AI in
# this loop; /outreach handles AI-drafted follow-up.

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
LEADS_PATH = Path(os.environ.get("LEADS_PATH", "data/leads.jsonl"))


class LeadRequest(BaseModel):
    name: str
    business: str
    contact: str  # phone or email — single field, prospect's choice
    messages: list[Msg]  # the demo conversation thread so far


@app.post("/api/lead")
def lead(req: LeadRequest) -> dict:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "name": req.name.strip(),
        "business": req.business.strip(),
        "contact": req.contact.strip(),
        "summary": _summarize_for_lead(req.messages),
        "n_messages": len(req.messages),
    }
    _append_lead(record)
    _telegram_notify(record)
    return {"ok": True}


def _summarize_for_lead(messages: list[Msg]) -> str:
    """1-line summary: the prospect's first message + the engine's last reply."""
    first_c = next((m.text for m in messages if m.role == "customer"), "")
    last_ai = next((m.text for m in reversed(messages) if m.role == "ai"), "")
    return f"First: {first_c[:80]} | Last reply: {last_ai[:80]}"


def _append_lead(record: dict) -> None:
    LEADS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LEADS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _telegram_notify(record: dict) -> None:
    """Ping Hughie via the existing Hermes bot. Lead is logged regardless."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[lead] no Telegram creds — saved to file only: {record['name']}")
        return
    text = (
        f"[NEW LEAD] {record['name']} ({record['business']})\n"
        f"Contact: {record['contact']}\n"
        f"Demo: {record['summary']}\n"
        f"({record['n_messages']} msgs)"
    )
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data=urllib.parse.urlencode(
                {"chat_id": TELEGRAM_CHAT_ID, "text": text}
            ).encode(),
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10).close()
    except Exception as exc:
        print(f"[lead] Telegram notify failed (lead is still in JSONL): {exc}")


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


def _cell(lead: dict, field: str) -> str:
    """Render one pipeline table cell for a lead field."""
    if field == "triage":
        tri = lead.get("triage") or ""
        if not tri:
            return "<td></td>"
        cls = "emergency" if tri == "emergency" else ""
        return f"<td><span class='badge {cls}'>{_esc(tri)}</span></td>"
    if field == "updated_at":
        return f"<td class=muted>{_esc(lead.get('updated_at'))}</td>"
    return f"<td>{_esc(lead.get(field))}</td>"


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


def _page(title: str, body: str, brand: str = "Speed-to-Lead — Owner Dashboard") -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width, initial-scale=1'>"
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
        ".bubble{max-width:78%;padding:8px 12px;border-radius:12px;margin:6px 0;white-space:pre-wrap}"
        ".cust{background:#eef1f6}.ai{background:#dcf5e6;margin-left:auto}"
        ".row{display:flex;flex-direction:column}"
        f"</style></head><body><header>{_esc(brand)}</header><main>{body}</main></body></html>"
    )


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
    groups: dict[str, list] = {}
    for lead in store.list_leads(tenant_id):
        groups.setdefault(lead.get("stage") or "other", []).append(lead)

    # Admins get a back-link to all businesses; clients just get log out.
    nav = (
        f"<a href='/dashboard?key={k}'>← all businesses</a>"
        if user["role"] == "admin"
        else "<a href='/logout'>log out</a>"
    )
    body = f"<p class=muted>{nav}</p><h1>{_esc(t['business_name'])}</h1>"
    order, labels, cols, explicit = _dashboard_cfg(t)
    # An explicit stage_order hides any stage not listed; otherwise show extras.
    stages_seq = list(order) if explicit else order + [s for s in groups if s not in order]
    header = "<tr><th>Customer</th>" + "".join(
        f"<th>{_esc(COLUMN_DEFS[c])}</th>" for c in cols
    ) + "</tr>"
    shown = False
    for stage in stages_seq:
        items = groups.get(stage)
        if not items:
            continue
        shown = True
        rows = ""
        for lead in items:
            ph = urllib.parse.quote(lead["phone"])
            cells = "".join(_cell(lead, c) for c in cols)
            rows += (
                f"<tr><td><a href='/dashboard/{_esc(tenant_id)}/lead?phone={ph}&key={k}'>"
                f"{_esc(lead['phone'])}</a></td>{cells}</tr>"
            )
        label = labels.get(stage, stage.title())
        body += (
            f"<h2>{_esc(label)} ({len(items)})</h2><table>{header}{rows}</table>"
        )
    if not shown:
        body += "<p class=muted>No leads yet.</p>"
    return _page(t["business_name"], body, brand=f"{t['business_name']} — Leads")


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
