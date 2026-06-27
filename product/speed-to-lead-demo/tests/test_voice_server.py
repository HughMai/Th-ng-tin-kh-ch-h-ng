import asyncio
import time

from workflow import DEFAULT_TENANT

import voice_server


class _FakeSocket:
    """Minimal stand-in for a Deepgram WebSocket for pre-connect tests."""

    def __init__(self):
        self.sent = []
        self.closed = False

    async def send(self, data):
        self.sent.append(data)

    async def close(self):
        self.closed = True


def test_voice_agent_defaults_prioritize_latency_and_au_voice(monkeypatch):
    # Assert the CODE defaults, independent of any live DEEPGRAM_VOICE / TTS override.
    monkeypatch.setattr(voice_server, "AURA_VOICE", "aura-2-hyperion-en")
    monkeypatch.setattr(voice_server, "DG_TTS_PROVIDER", "deepgram")
    settings = voice_server._agent_settings(DEFAULT_TENANT)
    agent = settings["agent"]

    listen = agent["listen"]["provider"]
    assert listen["type"] == "deepgram"
    assert listen["model"] == "flux-general-en"
    assert listen["version"] == "v2"
    assert listen["eot_threshold"] == 0.65
    assert listen["eager_eot_threshold"] == 0.45
    assert listen["eot_timeout_ms"] == 1500

    think = agent["think"]["provider"]
    assert think["type"] == "anthropic"
    assert think["model"] == "claude-haiku-4-5"
    assert think["temperature"] == 0.3

    speak = agent["speak"]["provider"]
    assert speak == {"type": "deepgram", "model": "aura-2-hyperion-en"}


def test_voice_agent_can_use_deepgram_managed_cartesia(monkeypatch):
    monkeypatch.setattr(voice_server, "DG_TTS_PROVIDER", "cartesia")
    monkeypatch.setattr(voice_server, "CARTESIA_MODEL_ID", "sonic-2")
    monkeypatch.setattr(voice_server, "CARTESIA_VOICE_ID", "voice-test")
    monkeypatch.setattr(voice_server, "CARTESIA_SPEED", "fast")

    speak = voice_server._agent_settings(DEFAULT_TENANT)["agent"]["speak"]["provider"]

    assert speak == {
        "type": "cartesia",
        "model_id": "sonic-2",
        "voice": {"mode": "id", "id": "voice-test"},
        "speed": "fast",
    }


def test_voice_alerts_wait_for_post_call_flush(sent):
    actions = voice_server._new_call_actions()

    result = voice_server._run_function(
        DEFAULT_TENANT,
        "+61411111111",
        "CA123",
        "alert_owner",
        {"kind": "callback", "message": "Pat wants a call about a leaking tap."},
        actions,
    )

    assert result == "Noted for the post-call owner summary."
    assert sent == []

    voice_server._flush_post_call_actions(DEFAULT_TENANT, "+61411111111", actions)

    assert len(sent) == 1
    assert sent[0]["body"] == (
        "[CALLBACK] +61411111111\nPat wants a call about a leaking tap."
    )


def test_voice_booking_waits_for_post_call_flush(monkeypatch, sent):
    actions = voice_server._new_call_actions()
    created = []

    monkeypatch.setattr(voice_server.gcal, "is_connected", lambda _tenant: True)

    def fake_create_event(tenant, summary, start_iso, end_iso, description=""):
        created.append(
            {
                "tenant": tenant,
                "summary": summary,
                "start_iso": start_iso,
                "end_iso": end_iso,
                "description": description,
            }
        )
        return "https://calendar.test/event"

    monkeypatch.setattr(voice_server.gcal, "create_event", fake_create_event)

    result = voice_server._run_function(
        DEFAULT_TENANT,
        "+61411111111",
        "CA123",
        "book_job",
        {
            "window_text": "Tuesday morning, between eight and eleven",
            "start_iso": "2035-06-26T08:00:00+10:00",
            "end_iso": "2035-06-26T11:00:00+10:00",
            "job_type": "leaking tap",
            "address": "1 Crown Street, Wollongong",
        },
        actions,
    )

    assert result == "Booking details noted for after the call."
    assert sent == []
    assert created == []

    voice_server._flush_post_call_actions(DEFAULT_TENANT, "+61411111111", actions)

    assert len(created) == 1
    assert len(sent) == 1
    assert sent[0]["body"] == (
        "[BOOKED] +61411111111\n"
        "Booked: Tuesday morning, between eight and eleven\n"
        "https://calendar.test/event\n"
        "Job: leaking tap\n"
        "Address: 1 Crown Street, Wollongong"
    )


def test_voice_end_call_waits_hangs_up_and_flushes(monkeypatch, sent):
    actions = voice_server._new_call_actions()
    sleeps = []
    hangups = []

    monkeypatch.setattr(voice_server.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(voice_server.twilio_io, "hang_up", lambda call_sid: hangups.append(call_sid))

    voice_server._run_function(
        DEFAULT_TENANT,
        "+61411111111",
        "CA123",
        "alert_owner",
        {"kind": "new_lead", "message": "New voice enquiry about lights flickering."},
        actions,
    )

    result = voice_server._run_function(
        DEFAULT_TENANT,
        "+61411111111",
        "CA123",
        "end_call",
        {},
        actions,
    )

    assert result == "Call ended."
    assert sleeps == [2]
    assert hangups == ["CA123"]
    assert len(sent) == 1

    voice_server._flush_post_call_actions(DEFAULT_TENANT, "+61411111111", actions)
    assert len(sent) == 1


def test_preconnect_hit_reuses_warm_socket(monkeypatch):
    """A socket pre-connected during the ring is handed to the stream with the
    handshake cost counted as zero."""
    monkeypatch.setattr(voice_server, "_preconnected", {})
    warm = _FakeSocket()

    async def fake_open():
        return warm

    monkeypatch.setattr(voice_server, "_open_dg_authed", fake_open)

    async def run():
        await voice_server.preconnect_call("CA_HIT")
        latency = {"dg_connect_ms": None, "started_at": time.perf_counter()}
        dg = await voice_server._take_or_open_dg("CA_HIT", latency)
        return dg, latency

    dg, latency = asyncio.run(run())
    assert dg is warm
    assert latency["preconnect"] == "hit"
    assert latency["dg_connect_ms"] == 0


def test_preconnect_failure_falls_back_to_fresh(monkeypatch):
    """If the pre-connect itself errors, the stream opens a fresh socket — never
    a broken call."""
    monkeypatch.setattr(voice_server, "_preconnected", {})
    calls = {"n": 0}
    fresh = _FakeSocket()

    async def fake_open():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("preconnect boom")  # the pre-connect fails
        return fresh  # the stream's fresh open succeeds

    monkeypatch.setattr(voice_server, "_open_dg_authed", fake_open)

    async def run():
        await voice_server.preconnect_call("CA_MISS")
        latency = {"dg_connect_ms": None, "started_at": time.perf_counter()}
        dg = await voice_server._take_or_open_dg("CA_MISS", latency)
        return dg, latency

    dg, latency = asyncio.run(run())
    assert dg is fresh
    assert latency["preconnect"] == "miss"
    assert calls["n"] == 2  # one failed preconnect + one fresh open


def test_preconnect_kill_switch_opens_fresh(monkeypatch):
    """VOICE_PRECONNECT=0 disables pre-connect entirely — stream opens fresh."""
    monkeypatch.setattr(voice_server, "_preconnected", {})
    monkeypatch.setattr(voice_server, "VOICE_PRECONNECT", False)
    fresh = _FakeSocket()

    async def fake_open():
        return fresh

    monkeypatch.setattr(voice_server, "_open_dg_authed", fake_open)

    async def run():
        await voice_server.preconnect_call("CA_OFF")
        latency = {"dg_connect_ms": None, "started_at": time.perf_counter()}
        dg = await voice_server._take_or_open_dg("CA_OFF", latency)
        return dg, latency, dict(voice_server._preconnected)

    dg, latency, pre = asyncio.run(run())
    assert dg is fresh
    assert "CA_OFF" not in pre  # preconnect was skipped — no slot created
