"""FastAPI server for the electrician speed-to-lead product.

Two front doors onto the same conversation engine (`workflow.py`):

- The web simulator (`index.html` + `/api/*`) — the laptop sales demo.
- The Twilio webhooks (`/twilio/*`) — the live product. A real missed call or
  SMS to the business number runs the same qualify -> triage -> book workflow.

Run locally:  python -m uvicorn app:app --reload   (then http://127.0.0.1:8000)
"""

import os
from pathlib import Path
from typing import Literal

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse

import store
import twilio_io
from workflow import run_followup, run_turn

load_dotenv()

HERE = Path(__file__).parent
app = FastAPI(title="Electrician Speed-to-Lead")

# --- Config (from environment) ----------------------------------------------
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
ELECTRICIAN_MOBILE = os.environ.get("ELECTRICIAN_MOBILE", "")

# The missed-call text-back. Static by design: it must still send when the
# Claude engine is unreachable (see the fail-open telephony decision).
OPENER_SMS = (
    "Hi, it's Dave from Dave's Electrical — sorry I missed your call, I'm on "
    "the tools. What do you need a hand with, and what suburb are you in?"
)

store.configure(os.environ.get("DB_PATH", "data/leads.db"))


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
    """Inbound call: ring the electrician. If he doesn't pick up, voice-status
    fires and we text the caller back."""
    await _twilio_form(request)
    vr = VoiceResponse()
    dial = vr.dial(
        timeout=20,
        action=f"{PUBLIC_BASE_URL}/twilio/voice-status",
        method="POST",
    )
    dial.number(ELECTRICIAN_MOBILE)
    return _twiml(str(vr))


@app.post("/twilio/voice-status")
async def twilio_voice_status(request: Request) -> Response:
    """The call has ended. If it went unanswered, send the missed-call text."""
    form = await _twilio_form(request)
    if form.get("DialCallStatus") in MISSED_CALL_STATUSES:
        caller = form.get("From")
        if caller:
            twilio_io.send_sms(caller, OPENER_SMS)
    return _twiml("<Response/>")


@app.post("/twilio/sms")
async def twilio_sms(request: Request) -> Response:
    """Inbound SMS: run the engine, reply to the customer, and alert the
    electrician when the engine says to."""
    form = await _twilio_form(request)
    customer = form.get("From", "")
    body = (form.get("Body") or "").strip()

    thread = store.load_thread(customer)
    thread.append({"role": "user", "content": body})

    try:
        # Blocking Claude call — run it off the event loop so concurrent texts
        # aren't held up behind it.
        turn = await run_in_threadpool(run_turn, thread)
    except Exception as exc:
        # Engine or API failure: fail open. Never drop a lead — reassure the
        # customer and put a human (the electrician) on it.
        print(f"[error] engine failed for {customer}: {exc}")
        twilio_io.send_sms(
            ELECTRICIAN_MOBILE,
            f'[ATTN] Automation hiccup — {customer} texted in and needs a '
            f'callback. Their message: "{body}"',
        )
        mr = MessagingResponse()
        mr.message("Thanks for your message — Dave will get back to you shortly.")
        return _twiml(str(mr))

    thread.append({"role": "assistant", "content": turn.reply})
    store.save_turn(customer, thread, turn)

    if turn.electrician_notification:
        twilio_io.send_sms(ELECTRICIAN_MOBILE, turn.electrician_notification)

    mr = MessagingResponse()
    mr.message(turn.reply)
    return _twiml(str(mr))
