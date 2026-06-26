"""Layer 1 — unit tests. Pure logic, no network, no app server.

These cover the small functions whose bugs would quietly misroute, mis-time, or
mis-trust an inbound event.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from twilio.request_validator import RequestValidator

import app as appmod
import report
import store
import tenants
import twilio_io
from conftest import make_turn


# --- Tenant routing ---------------------------------------------------------

def test_find_by_number_unknown_is_none():
    assert tenants.find_by_number("") is None
    assert tenants.find_by_number("+10000000000") is None


def test_default_tenant_loads():
    t = tenants.load_tenant("dave")
    assert t["tenant_id"] == "dave"


# --- Booking time safety net: never book in the past ------------------------

def test_roll_to_future_moves_past_window_forward():
    # 2020-01-01 was a Wednesday at 08:00 +10:00 — long past.
    start = "2020-01-01T08:00:00+10:00"
    end = "2020-01-01T11:00:00+10:00"
    new_start, new_end = appmod._roll_to_future(start, end)
    s = datetime.fromisoformat(new_start)
    e = datetime.fromisoformat(new_end)
    now = datetime.now(s.tzinfo)
    assert s > now, "window was left in the past — would book a job that already happened"
    assert s.weekday() == 2 and s.hour == 8, "rollover changed the weekday/time"
    assert e > s


# --- Inbound signature: trust real Twilio, reject spoofers ------------------

def test_signature_accepts_genuine_and_rejects_forged():
    token = os.environ["TWILIO_AUTH_TOKEN"]
    url = "https://test.local/twilio/sms"
    params = {"From": "+61411111111", "Body": "hi", "To": "+61480000000"}
    good = RequestValidator(token).compute_signature(url, params)
    assert twilio_io.is_valid_twilio_request(url, params, good) is True
    assert twilio_io.is_valid_twilio_request(url, params, "forged") is False


# --- Store: a missed call becomes a visible lead, repeat calls escalate ------

def test_missed_call_creates_lead_then_counts_repeat():
    store.log_missed_call("dave", "+61400000001")
    assert store.lead_exists("dave", "+61400000001")
    store.log_missed_call("dave", "+61400000001")  # same caller rings again
    lead = store.lead_detail("dave", "+61400000001")
    assert lead["call_count"] == 2, "repeat caller not surfaced as a hotter lead"


# --- Report: counts and the conservative dollar figure ----------------------

def test_report_counts_and_value():
    store.log_missed_call("dave", "+61400000010")
    store.save_turn(
        "dave", "+61400000011", [{"role": "user", "content": "hi"}],
        make_turn(stage="booking", triage="bookable", booking="Tue 8-11am"),
    )
    t = {"tenant_id": "dave", "business_name": "Dave's Electrical", "avg_job_value": 400}
    r = report.build(t, days=30)
    assert r["leads"] == 2
    assert r["missed_calls"] == 1
    assert r["booked"] == 1
    assert r["booked_value"] == 400
