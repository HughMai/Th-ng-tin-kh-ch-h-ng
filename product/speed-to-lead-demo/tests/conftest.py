"""Test fixtures for the speed-to-lead app.

These tests defend ONE invariant: every inbound call/text results in the
customer getting a reply AND the lead becoming visible to the owner — even when
the AI engine, calendar, or Telegram fail. To prove that offline (free, fast,
deterministic) we mock the two external sides — Twilio (outbound SMS) and Claude
(the engine) — and assert on what the app *tried to send*.

Env is set before `app`/`workflow` import because both read credentials at
import time (the Anthropic client is built, store is configured). Values are
fakes — no real call ever leaves the process; `send_sms` is monkeypatched.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Make the app package importable when pytest runs from anywhere.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Fake credentials, set before the app imports read them.
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-not-real")
os.environ.setdefault("PUBLIC_BASE_URL", "https://test.local")
os.environ.setdefault("TWILIO_NUMBER", "+61480000000")
os.environ.setdefault("TWILIO_ACCOUNT_SID", "ACtest")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "testtoken")
os.environ.setdefault("ELECTRICIAN_MOBILE", "+61400999999")  # fallback owner alert
os.environ.setdefault("DASHBOARD_TOKEN", "dashtoken")
os.environ.setdefault("SECRET_KEY", "secretsecret")
os.environ.setdefault("DB_PATH", str(Path(tempfile.mkdtemp()) / "leads.db"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app as appmod  # noqa: E402
import store  # noqa: E402
import telegram_io  # noqa: E402
import twilio_io  # noqa: E402
from workflow import AgentTurn, Qualified  # noqa: E402

OWNER = os.environ["ELECTRICIAN_MOBILE"]
PLATFORM_NUMBER = os.environ["TWILIO_NUMBER"]


def make_turn(**over) -> AgentTurn:
    """A valid AgentTurn with sensible defaults; override only what a test cares
    about. Stands in for a real Claude response so tests are deterministic+free."""
    base = dict(
        reply="Thanks for your message — what suburb are you in?",
        stage="qualifying",
        triage=None,
        qualified=Qualified(
            job_type=None, urgency=None, suburb=None, address=None, property_type=None
        ),
        electrician_notification=None,
        notification_kind=None,
        booking=None,
        booking_start=None,
        booking_end=None,
        conversation_complete=False,
    )
    base.update(over)
    return AgentTurn(**base)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """A clean SQLite file per test, so tests never bleed into each other."""
    store.configure(str(tmp_path / "leads.db"))
    yield


@pytest.fixture
def sent(monkeypatch):
    """Spy on every outbound SMS. Returns a growing list of
    {to, body, from_} — the proof that a reply/alert was actually dispatched."""
    out: list[dict] = []

    def fake_send(to, body, from_=None):
        out.append({"to": to, "body": body, "from_": from_})

    monkeypatch.setattr(twilio_io, "send_sms", fake_send)
    return out


@pytest.fixture(autouse=True)
def mute_telegram(monkeypatch):
    """Telegram is a side-channel; never hit the network in tests."""
    monkeypatch.setattr(telegram_io, "send_message", lambda *a, **k: 1)


@pytest.fixture
def valid_sig(monkeypatch):
    """Treat the inbound webhook signature as genuine (the signature check
    itself is tested directly in test_unit.py)."""
    monkeypatch.setattr(twilio_io, "is_valid_twilio_request", lambda *a, **k: True)


@pytest.fixture
def client():
    return TestClient(appmod.app)
