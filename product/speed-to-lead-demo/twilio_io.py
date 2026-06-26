"""Twilio helpers: outbound SMS, and inbound webhook signature checks.

Inbound calls and texts reach this app as Twilio webhooks. Two jobs here:
verify each webhook genuinely came from Twilio (not a spoofer), and send SMS
back out — the missed-call opener and the alerts to the electrician.

The Twilio client is built lazily so the web demo still runs with only an
Anthropic key set; Twilio credentials are needed only once a /twilio/*
endpoint is actually hit.
"""

from __future__ import annotations

import os
from functools import lru_cache

from twilio.request_validator import RequestValidator
from twilio.rest import Client


def _credential(primary: str, legacy: str) -> str:
    """Read the documented env name, with the original local name as fallback."""
    value = os.environ.get(primary) or os.environ.get(legacy)
    if not value:
        raise KeyError(primary)
    return value


@lru_cache(maxsize=1)
def _client() -> Client:
    return Client(
        _credential("TWILIO_ACCOUNT_SID", "Twilio_SID"),
        _credential("TWILIO_AUTH_TOKEN", "Twilio_Auth"),
    )


@lru_cache(maxsize=1)
def _validator() -> RequestValidator:
    return RequestValidator(_credential("TWILIO_AUTH_TOKEN", "Twilio_Auth"))


def is_valid_twilio_request(url: str, form: dict, signature: str) -> bool:
    """True if `signature` proves the request came from Twilio.

    `url` must be the exact public URL Twilio called; `form` is the POSTed
    fields. A spoofed or tampered request fails this check.
    """
    return _validator().validate(url, form, signature)


def send_sms(to: str, body: str, from_: str | None = None) -> None:
    """Send an SMS. Defaults to the platform number (TWILIO_NUMBER); pass
    `from_` to send from a specific tenant's own number — customer-facing replies
    must come from the number the customer originally texted."""
    _client().messages.create(
        to=to, from_=from_ or os.environ["TWILIO_NUMBER"], body=body
    )


def place_call(
    to: str,
    twiml: str,
    from_: str | None = None,
    status_callback: str | None = None,
) -> str:
    """Place an outbound call and return its CallSid.

    `twiml` is the inline TwiML to run when the call connects — for the voice
    callback that's a <Connect><Stream> to the media bridge. From the tenant's
    own number so the caller sees the business they just rang. `status_callback`
    is hit when the call ends; if it went to voicemail or wasn't answered, the
    caller is dropped to the SMS fallback. Answering-machine detection is on so
    the agent never starts talking to a voicemail greeting.
    """
    call = _client().calls.create(
        to=to,
        from_=from_ or os.environ["TWILIO_NUMBER"],
        twiml=twiml,
        machine_detection="Enable",
        status_callback=status_callback,
        status_callback_event=["completed"] if status_callback else None,
    )
    return call.sid


def hang_up(call_sid: str) -> None:
    """End a live call. Used by the voice agent's end_call tool to hang up once
    the conversation is genuinely finished."""
    _client().calls(call_sid).update(status="completed")
