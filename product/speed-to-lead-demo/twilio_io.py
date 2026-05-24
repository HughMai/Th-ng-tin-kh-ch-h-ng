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


@lru_cache(maxsize=1)
def _client() -> Client:
    return Client(
        os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]
    )


@lru_cache(maxsize=1)
def _validator() -> RequestValidator:
    return RequestValidator(os.environ["TWILIO_AUTH_TOKEN"])


def is_valid_twilio_request(url: str, form: dict, signature: str) -> bool:
    """True if `signature` proves the request came from Twilio.

    `url` must be the exact public URL Twilio called; `form` is the POSTed
    fields. A spoofed or tampered request fails this check.
    """
    return _validator().validate(url, form, signature)


def send_sms(to: str, body: str) -> None:
    """Send an SMS from the business number."""
    _client().messages.create(
        to=to, from_=os.environ["TWILIO_NUMBER"], body=body
    )
