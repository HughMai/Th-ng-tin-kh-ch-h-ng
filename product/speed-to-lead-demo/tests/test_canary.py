"""Canary logic tests — no network. The probe's decision rules and the
alert-on-failure wiring, with fetch + send injected.
"""

from __future__ import annotations

import json

import canary


# --- evaluate(): pass/fail from a probe response ----------------------------

def test_evaluate_ok():
    body = json.dumps({"status": "ok", "latency_ms": 420, "checks": {"engine": "ok"}})
    ok, detail = canary.evaluate(200, body)
    assert ok and "420ms" in detail


def test_evaluate_non_200_fails():
    ok, detail = canary.evaluate(503, '{"status":"fail"}')
    assert not ok and "503" in detail


def test_evaluate_status_fail():
    body = json.dumps({"status": "fail", "checks": {"engine": "error: down"}})
    ok, detail = canary.evaluate(200, body)
    assert not ok and "engine" in detail


def test_evaluate_garbage_body_fails():
    ok, detail = canary.evaluate(200, "<html>502 bad gateway</html>")
    assert not ok and "unparseable" in detail


# --- run(): alerts only on failure (and on heartbeat) -----------------------

def _sender():
    sent = []
    return sent, lambda text: sent.append(text)


def test_run_alerts_on_unreachable():
    sent, send = _sender()

    def fetch_raises():
        raise OSError("connection refused")

    rc = canary.run(fetcher=fetch_raises, sender=send, heartbeat=False)
    assert rc == 1
    assert sent and "FAILED" in sent[0] and "unreachable" in sent[0]


def test_run_alerts_on_bad_status():
    sent, send = _sender()
    rc = canary.run(
        fetcher=lambda: (503, '{"status":"fail","checks":{"db":"error: locked"}}'),
        sender=send,
        heartbeat=False,
    )
    assert rc == 1 and sent


def test_run_silent_on_success():
    sent, send = _sender()
    rc = canary.run(
        fetcher=lambda: (200, '{"status":"ok","latency_ms":300}'),
        sender=send,
        heartbeat=False,
    )
    assert rc == 0
    assert sent == [], "canary must stay quiet when healthy"


def test_run_heartbeat_pings_on_success():
    sent, send = _sender()
    rc = canary.run(
        fetcher=lambda: (200, '{"status":"ok","latency_ms":300}'),
        sender=send,
        heartbeat=True,
    )
    assert rc == 0
    assert sent and "OK" in sent[0]
