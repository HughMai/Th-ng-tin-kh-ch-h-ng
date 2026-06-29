"""Tests for the website 'get a demo call' form -> outbound AI callback.

The endpoint spends Twilio credit calling strangers, so these tests pin its
guardrails: shared-secret auth, AU-only validation, consent required, the
per-phone 24h cap, the global daily cap, and that the happy path fires
place_call with the demo TwiML. No real call ever leaves the process —
place_call is monkeypatched."""

from __future__ import annotations

import pytest

import app as appmod
import twilio_io  # noqa: F401  (monkeypatched via the `placed` fixture)


@pytest.fixture
def demo_on(monkeypatch):
    """Turn the demo endpoint on with a known secret + caller ID."""
    monkeypatch.setattr(appmod, "DEMO_CALL_SECRET", "testsecret")
    monkeypatch.setattr(appmod, "DEMO_FROM_NUMBER", "+61468089224")
    monkeypatch.setattr(appmod, "DEMO_DAILY_CAP", 20)


@pytest.fixture
def placed(monkeypatch):
    """Spy on outbound calls; returns a growing list of {to, twiml, from_}."""
    out: list[dict] = []

    def fake_place_call(to, twiml, from_=None, status_callback=None):
        out.append(
            {"to": to, "twiml": twiml, "from_": from_, "status_callback": status_callback}
        )
        return "CA_test_sid"

    monkeypatch.setattr(twilio_io, "place_call", fake_place_call)
    return out


def _post(client, secret="testsecret", **fields):
    body = {"name": "Sarah", "phone": "+61468089224", "consent": True}
    body.update(fields)
    headers = {"x-demo-secret": secret} if secret is not None else {}
    return client.post("/api/demo-call", json=body, headers=headers)


def test_rejects_wrong_secret(client, demo_on, placed):
    res = _post(client, secret="wrong")
    assert res.status_code == 401
    assert placed == []


def test_rejects_non_au_number(client, demo_on, placed):
    res = _post(client, phone="+14155551234")
    assert res.status_code == 400
    assert placed == []


def test_rejects_no_consent(client, demo_on, placed):
    res = _post(client, consent=False)
    assert res.status_code == 400
    assert placed == []


def test_rejects_missing_name(client, demo_on, placed):
    res = _post(client, name="   ")
    assert res.status_code == 400
    assert placed == []


def test_happy_path_fires_demo_call(client, demo_on, placed):
    res = _post(client, name="Sarah Lee", phone="0468 089 224")
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    assert len(placed) == 1
    call = placed[0]
    assert call["to"] == "+61468089224"
    assert call["from_"] == "+61468089224"
    twiml = call["twiml"]
    assert 'name="tenant_id"' in twiml and 'value="demo"' in twiml
    assert 'name="demo_mode"' in twiml
    assert 'name="demo_prospect_name"' in twiml and "Sarah Lee" in twiml
    assert "/twilio/voice-stream" in twiml


def test_per_phone_24h_cap(client, demo_on, placed):
    assert _post(client, phone="+61400000001").status_code == 200
    # Same number again within 24h is blocked, even though the daily cap isn't hit.
    assert _post(client, phone="+61400000001").status_code == 429
    assert len(placed) == 1


def test_daily_cap(client, demo_on, placed, monkeypatch):
    monkeypatch.setattr(appmod, "DEMO_DAILY_CAP", 2)
    assert _post(client, phone="+61400000001").status_code == 200
    assert _post(client, phone="+61400000002").status_code == 200
    # Third distinct number today trips the global daily cap.
    assert _post(client, phone="+61400000003").status_code == 429
    assert len(placed) == 2
