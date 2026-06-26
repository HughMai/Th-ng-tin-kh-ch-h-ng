"""Layer 2 — webhook integration tests. The silent-drop guards.

Each test POSTs a real Twilio-shaped webhook to the app and asserts the
invariant holds: the customer gets a reply and/or the owner is alerted, on every
inbound event — including when the AI engine throws. Twilio's outbound SMS and
the Claude engine are mocked, so these are free, deterministic, and fast.
"""

from __future__ import annotations

import app as appmod
from conftest import OWNER, PLATFORM_NUMBER, make_turn


def post_sms(client, body="hi", from_="+61411111111", to=PLATFORM_NUMBER, sid=None):
    data = {"From": from_, "Body": body, "To": to}
    if sid:
        data["MessageSid"] = sid
    return client.post("/twilio/sms", data=data)


def post_voice_status(client, status="no-answer", from_="+61422222222", to=PLATFORM_NUMBER, sid=None):
    data = {"DialCallStatus": status, "From": from_, "To": to}
    if sid:
        data["CallSid"] = sid
    return client.post("/twilio/voice-status", data=data)


def to_customer(sent, customer):
    return [m for m in sent if m["to"] == customer]


def to_owner(sent):
    return [m for m in sent if m["to"] == OWNER]


# --- The core guarantee: a reply always reaches the customer ----------------

def test_inbound_sms_replies_to_customer(client, sent, valid_sig, monkeypatch):
    monkeypatch.setattr(appmod, "run_turn", lambda *a, **k: make_turn(reply="Righto — what suburb?"))
    cust = "+61411111111"
    assert post_sms(client, "fan install", from_=cust).status_code == 200
    replies = to_customer(sent, cust)
    assert replies, "customer never received a reply — DROPPED LEAD"
    assert replies[-1]["body"] == "Righto — what suburb?"
    # Reply goes out from the tenant's own number when they have one, so the
    # customer's thread stays intact. dave has none configured, so from_ is None,
    # which send_sms() falls back to the platform number at send time.
    import tenants
    assert replies[-1]["from_"] == tenants.load_tenant("dave").get("twilio_number")


def test_first_message_alerts_owner(client, sent, valid_sig, monkeypatch):
    monkeypatch.setattr(
        appmod, "run_turn",
        lambda *a, **k: make_turn(
            electrician_notification="NEW LEAD — fan install, getting details.",
            notification_kind="new_lead",
        ),
    )
    assert post_sms(client, "need a fan installed").status_code == 200
    alerts = to_owner(sent)
    assert alerts, "owner was never alerted to a new lead"
    assert "NEW LEAD" in alerts[-1]["body"]


# --- THE critical one: the engine dies, the lead must NOT --------------------

def test_engine_failure_still_texts_customer_and_owner(client, sent, valid_sig, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("Claude is down")

    monkeypatch.setattr(appmod, "run_turn", boom)
    cust = "+61433333333"
    resp = post_sms(client, "my power is out", from_=cust)

    # Never hand Twilio a 500 — that would make Twilio retry/give up and the
    # customer would hear nothing.
    assert resp.status_code == 200
    # Customer gets a reassurance, owner gets a hand-it-to-a-human alert.
    assert to_customer(sent, cust), "customer dropped when the engine failed"
    owner_alerts = to_owner(sent)
    assert owner_alerts, "owner not alerted when the engine failed"
    assert "my power is out" in owner_alerts[-1]["body"]  # the lead's words are preserved


# --- Missed call -> automatic text-back -------------------------------------

def test_missed_call_texts_back_and_logs_lead(client, sent, valid_sig):
    import store
    cust = "+61444444444"
    assert post_voice_status(client, "no-answer", from_=cust).status_code == 200
    assert to_customer(sent, cust), "missed call never got a text-back"
    assert store.lead_exists("dave", cust), "missed call not recorded as a lead"


def test_answered_call_sends_nothing(client, sent, valid_sig):
    # A call the owner actually answered must not trigger a text-back.
    assert post_voice_status(client, "completed", from_="+61455555555").status_code == 200
    assert sent == []


# --- Security boundary: spoofed webhooks are rejected, not processed ---------

def test_spoofed_webhook_is_rejected(client, sent):
    # No valid_sig fixture -> the real signature check runs and fails.
    resp = post_sms(client, "spoofed")
    assert resp.status_code == 403
    assert sent == [], "a spoofed request must never trigger an outbound SMS"


# --- Human takeover: AI goes quiet but the message is still captured ---------

def test_human_handling_silences_ai_but_records_message(client, sent, valid_sig, monkeypatch):
    import store
    cust = "+61466666666"
    store.append_message("dave", cust, "user", "first")  # create the lead
    store.set_human_handling("dave", cust, True)

    def must_not_run(*a, **k):
        raise AssertionError("engine ran while a human was handling the lead")

    monkeypatch.setattr(appmod, "run_turn", must_not_run)
    assert post_sms(client, "second message", from_=cust).status_code == 200

    detail = store.lead_detail("dave", cust)
    assert any(m["content"] == "second message" for m in detail["thread"]), \
        "customer message lost while human was handling it"
    assert to_customer(sent, cust) == [], "AI replied while a human was handling"


# --- Idempotency: Twilio retries must not double-text the customer ----------

def test_duplicate_sms_webhook_replies_only_once(client, sent, valid_sig, monkeypatch):
    calls = {"n": 0}

    def counting(*a, **k):
        calls["n"] += 1
        return make_turn(reply="On it.")

    monkeypatch.setattr(appmod, "run_turn", counting)
    cust = "+61477777777"
    # Same MessageSid twice = Twilio retrying because our first reply was slow.
    assert post_sms(client, "fan", from_=cust, sid="SM123").status_code == 200
    assert post_sms(client, "fan", from_=cust, sid="SM123").status_code == 200

    assert calls["n"] == 1, "engine ran twice on a retried webhook"
    assert len(to_customer(sent, cust)) == 1, "customer was texted twice on a retry"


def test_duplicate_voice_status_texts_back_only_once(client, sent, valid_sig):
    import store
    cust = "+61488888888"
    assert post_voice_status(client, "no-answer", from_=cust, sid="CA999").status_code == 200
    assert post_voice_status(client, "no-answer", from_=cust, sid="CA999").status_code == 200

    assert len(to_customer(sent, cust)) == 1, "missed-call text-back fired twice on a retry"
    assert store.lead_detail("dave", cust)["call_count"] == 1, "retry double-counted the call"


# --- Deep health: proves engine+DB, sends no SMS, creates no lead -----------

def test_health_deep_ok_when_engine_works(client, sent, monkeypatch):
    monkeypatch.setattr(appmod, "run_turn", lambda *a, **k: make_turn(reply="pong"))
    resp = client.get("/health/deep")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert sent == [], "deep health must not send any SMS"


def test_health_deep_fails_when_engine_down(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("Claude credits exhausted")

    monkeypatch.setattr(appmod, "run_turn", boom)
    resp = client.get("/health/deep")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "fail"
    assert "error" in body["checks"]["engine"]
