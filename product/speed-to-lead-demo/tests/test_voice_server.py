import asyncio
import json
import time

from workflow import DEFAULT_TENANT

import call_state
import voice_server


AREA_TENANT = {
    **DEFAULT_TENANT,
    "business_name": "Crown Street Auto",
    "service_area_short": "Wollongong and the Illawarra",
    "service_suburbs": ["West Wollongong", "Fairy Meadow", "Unanderra"],
    "suburb_aliases": {"west woomble": "West Wollongong"},
    "out_of_area_suburbs": ["Bowral", "Sydney"],
}


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


def test_voice_agent_opens_with_syanna_and_no_repeat_prompt():
    settings = voice_server._agent_settings(DEFAULT_TENANT)
    agent = settings["agent"]
    prompt = agent["think"]["prompt"]

    assert agent["greeting"] == (
        f"Hi, I'm Syanna from {DEFAULT_TENANT['business_name']}. "
        "How can I help you today?"
    )
    assert "You are Syanna, the AI front desk" in prompt
    assert "Treat that as already spoken" in prompt
    assert "No compliments, encouragement, filler, or praise." in prompt


def test_voice_idle_reprompt_repeats_only_the_question():
    text = "Hi, I'm Syanna from Crown Street Auto. How can I help you today?"

    assert voice_server._reprompt_from_agent_text(text) == "How can I help you today?"


def test_voice_idle_reprompt_injects_after_silence(monkeypatch):
    monkeypatch.setattr(voice_server, "VOICE_SILENCE_REPROMPT_MS", 1)
    dg = _FakeSocket()
    idle = {
        "task": None,
        "user_seq": 0,
        "last_agent_text": "",
        "suppress_next_audio_done": False,
    }

    asyncio.run(voice_server._send_idle_reprompt(dg, idle, 0, "How can I help?"))

    assert json.loads(dg.sent[0]) == {
        "type": "InjectAgentMessage",
        "behavior": "default",
        "message": "How can I help?",
    }
    assert idle["suppress_next_audio_done"] is True


def test_voice_idle_reprompt_cancels_when_customer_starts_speaking(monkeypatch):
    monkeypatch.setattr(voice_server, "VOICE_SILENCE_REPROMPT_MS", 1)
    dg = _FakeSocket()
    idle = {
        "task": None,
        "user_seq": 1,
        "last_agent_text": "",
        "suppress_next_audio_done": False,
    }

    asyncio.run(voice_server._send_idle_reprompt(dg, idle, 0, "How can I help?"))

    assert dg.sent == []
    assert idle["suppress_next_audio_done"] is False


def test_voice_idle_reprompt_capped_after_one_fire(monkeypatch):
    # A 2nd AgentAudioDone must not re-arm a verbatim re-ask loop.
    monkeypatch.setattr(voice_server, "MAX_IDLE_REPROMPTS", 1)
    dg = _FakeSocket()
    idle = {
        "task": None,
        "user_seq": 0,
        "reprompt_count": 1,
        "last_agent_text": "How can I help?",
    }
    voice_server._schedule_idle_reprompt(dg, idle)
    assert idle["task"] is None  # capped: no new re-prompt armed


def test_voice_idle_reprompt_counts_toward_cap(monkeypatch):
    monkeypatch.setattr(voice_server, "VOICE_SILENCE_REPROMPT_MS", 1)
    dg = _FakeSocket()
    idle = {
        "task": None,
        "user_seq": 0,
        "reprompt_count": 0,
        "last_agent_text": "",
        "suppress_next_audio_done": False,
    }
    asyncio.run(voice_server._send_idle_reprompt(dg, idle, 0, "How can I help?"))
    assert idle["reprompt_count"] == 1


def test_voice_user_speech_resets_reprompt_cap():
    idle = {"user_seq": 0, "reprompt_count": 1, "last_injected": "How can I help?", "task": None, "unclear_task": None}
    voice_server._mark_user_activity(idle)
    assert idle["reprompt_count"] == 0  # next agent turn may nudge once again
    assert idle["last_injected"] == ""


def test_voice_vad_does_not_reset_reprompt_cap():
    # Agent-TTS echo tripping VAD must NOT reset the cap, or it re-arms the loop.
    idle = {"user_seq": 0, "reprompt_count": 1, "last_injected": "How can I help?", "task": None, "unclear_task": None}
    voice_server._on_vad_activity(idle)
    assert idle["reprompt_count"] == 1
    assert idle["last_injected"] == "How can I help?"
    assert idle["user_seq"] == 1


def test_voice_idle_reprompt_skips_verbatim_repeat_of_last_injected():
    dg = _FakeSocket()
    idle = {
        "task": None,
        "user_seq": 0,
        "reprompt_count": 0,
        "last_agent_text": "What's the issue with your car?",
        "last_injected": "What's the issue with your car?",
    }
    voice_server._schedule_idle_reprompt(dg, idle)
    assert idle["task"] is None  # would just repeat what we already injected -> skip


def test_voice_unclear_speech_recovery_injects_last_question(monkeypatch):
    monkeypatch.setattr(voice_server, "VOICE_UNCLEAR_SPEECH_MS", 1)
    dg = _FakeSocket()
    idle = {
        "task": None,
        "unclear_task": None,
        "user_seq": 0,
        "last_agent_text": "How can I help you today?",
        "suppress_next_audio_done": False,
    }

    asyncio.run(voice_server._send_unclear_speech_recovery(dg, idle, 0))

    assert json.loads(dg.sent[0]) == {
        "type": "InjectAgentMessage",
        "behavior": "default",
        "message": "I didn't catch that. How can I help you today?",
    }


def test_service_area_tool_alias_confirms_misheard_suburb():
    result = json.loads(voice_server._check_service_area(AREA_TENANT, "west woomble"))

    assert result["status"] == "confirm"
    assert result["canonical_suburb"] == "West Wollongong"
    assert result["in_area"] is True


def test_service_area_tool_exact_and_out_of_area():
    exact = json.loads(voice_server._check_service_area(AREA_TENANT, "Fairy Meadow"))
    outside = json.loads(voice_server._check_service_area(AREA_TENANT, "Bowral"))

    assert exact["status"] == "in_area"
    assert exact["canonical_suburb"] == "Fairy Meadow"
    assert outside["status"] == "out_of_area"
    assert outside["in_area"] is False


def test_voice_settings_include_service_area_keyterms():
    provider = voice_server._agent_settings(AREA_TENANT)["agent"]["listen"]["provider"]

    assert "West Wollongong" in provider["keyterms"]
    assert "Crown Street Auto" in provider["keyterms"]


def test_voice_tools_include_check_service_area():
    functions = voice_server._agent_settings(AREA_TENANT)["agent"]["think"]["functions"]

    assert "check_service_area" in {fn["name"] for fn in functions}


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


# --- call watchdog ----------------------------------------------------------

def test_norm_utterance_collapses_case_and_whitespace():
    assert voice_server._norm_utterance("  What's the issue?? ") == "what's the issue??"
    assert voice_server._norm_utterance("") == ""


def test_repeat_streak_counts_rapid_verbatim_repeat(monkeypatch):
    # Two identical agent turns 1s apart (the observed Deepgram self-loop) -> 2.
    monkeypatch.setattr(voice_server, "VOICE_LOOP_WINDOW_SEC", 6.0)
    idle = {"last_assistant_norm": "", "last_assistant_mono": 100.0, "repeat_streak": 0}
    voice_server._update_repeat_streak(idle, "what's the issue", 101.0)
    assert idle["repeat_streak"] == 1
    voice_server._update_repeat_streak(idle, "what's the issue", 102.0)
    assert idle["repeat_streak"] == 2


def test_repeat_streak_ignores_slow_repeat_like_our_nudge(monkeypatch):
    # Same line 8s later (our 7s reprompt nudge) must NOT stack toward a trip.
    monkeypatch.setattr(voice_server, "VOICE_LOOP_WINDOW_SEC", 6.0)
    idle = {"last_assistant_norm": "how can i help you today", "last_assistant_mono": 100.0, "repeat_streak": 1}
    voice_server._update_repeat_streak(idle, "how can i help you today", 108.0)
    assert idle["repeat_streak"] == 1


def test_repeat_streak_resets_on_a_different_line(monkeypatch):
    monkeypatch.setattr(voice_server, "VOICE_LOOP_WINDOW_SEC", 6.0)
    idle = {"last_assistant_norm": "what's the issue", "last_assistant_mono": 100.0, "repeat_streak": 2}
    voice_server._update_repeat_streak(idle, "yeah i'm here", 101.0)
    assert idle["repeat_streak"] == 1


def test_mark_user_activity_resets_watchdog_state():
    idle = {
        "user_seq": 0, "reprompt_count": 1, "last_injected": "x",
        "task": None, "unclear_task": None,
        "last_user_mono": 0.0, "repeat_streak": 2, "last_assistant_norm": "hi",
    }
    voice_server._mark_user_activity(idle)
    assert idle["repeat_streak"] == 0
    assert idle["last_assistant_norm"] == ""
    assert idle["last_user_mono"] > 0.0  # silence clock restarted


def test_watchdog_trips_on_max_call_sec(monkeypatch):
    monkeypatch.setattr(voice_server, "VOICE_MAX_CALL_SEC", 240.0)
    monkeypatch.setattr(voice_server, "VOICE_SILENCE_HANGUP_SEC", 25.0)
    monkeypatch.setattr(voice_server, "VOICE_LOOP_MAX_REPEATS", 2)
    idle = {"call_started_mono": 0.0, "last_user_mono": 200.0, "repeat_streak": 0}
    assert voice_server._watchdog_trip_reason(idle, 300.0) == "max_call_sec"


def test_watchdog_trips_on_silence(monkeypatch):
    monkeypatch.setattr(voice_server, "VOICE_MAX_CALL_SEC", 240.0)
    monkeypatch.setattr(voice_server, "VOICE_SILENCE_HANGUP_SEC", 25.0)
    monkeypatch.setattr(voice_server, "VOICE_LOOP_MAX_REPEATS", 2)
    idle = {"call_started_mono": 100.0, "last_user_mono": 100.0, "repeat_streak": 0}
    assert voice_server._watchdog_trip_reason(idle, 130.0) == "silence"


def test_watchdog_trips_on_repeat_loop(monkeypatch):
    monkeypatch.setattr(voice_server, "VOICE_MAX_CALL_SEC", 240.0)
    monkeypatch.setattr(voice_server, "VOICE_SILENCE_HANGUP_SEC", 25.0)
    monkeypatch.setattr(voice_server, "VOICE_LOOP_MAX_REPEATS", 2)
    idle = {"call_started_mono": 100.0, "last_user_mono": 101.0, "repeat_streak": 2}
    assert voice_server._watchdog_trip_reason(idle, 101.5) == "repeat_loop"


def test_watchdog_does_not_trip_on_active_call(monkeypatch):
    monkeypatch.setattr(voice_server, "VOICE_MAX_CALL_SEC", 240.0)
    monkeypatch.setattr(voice_server, "VOICE_SILENCE_HANGUP_SEC", 25.0)
    monkeypatch.setattr(voice_server, "VOICE_LOOP_MAX_REPEATS", 2)
    idle = {"call_started_mono": 100.0, "last_user_mono": 101.0, "repeat_streak": 1}
    assert voice_server._watchdog_trip_reason(idle, 102.0) is None


def test_watchdog_cap_takes_priority_over_silence(monkeypatch):
    # Both cap and silence exceeded -> the hard cap (checked first) wins.
    monkeypatch.setattr(voice_server, "VOICE_MAX_CALL_SEC", 240.0)
    monkeypatch.setattr(voice_server, "VOICE_SILENCE_HANGUP_SEC", 25.0)
    idle = {"call_started_mono": 0.0, "last_user_mono": 0.0, "repeat_streak": 0}
    assert voice_server._watchdog_trip_reason(idle, 300.0) == "max_call_sec"


def test_watchdog_terminate_speaks_goodbye_and_hangs_up(monkeypatch):
    dg = _FakeSocket()
    idle = {"ending": False}
    hangups = []

    async def _nosleep(*_a, **_k):
        return None

    monkeypatch.setattr(voice_server.asyncio, "sleep", _nosleep)
    monkeypatch.setattr(voice_server.twilio_io, "hang_up", lambda sid: hangups.append(sid))

    asyncio.run(voice_server._watchdog_terminate(dg, idle, "CA1", "repeat_loop"))

    assert idle["ending"] is True
    assert hangups == ["CA1"]
    assert json.loads(dg.sent[0])["message"].startswith("I think we got disconnected")

    # Idempotent: a second terminate (e.g. the LLM end_call racing the watchdog) is a no-op.
    asyncio.run(voice_server._watchdog_terminate(dg, idle, "CA1", "silence"))
    assert len(dg.sent) == 1
    assert hangups == ["CA1"]


# --- shadow-FSM wiring (Tier 1) ---------------------------------------------
# These pin the integration seam that the pure-FSM tests in test_call_state.py
# can't reach: that voice_server actually threads the FSM into the tool path,
# that the divergence signal reaches the gate log, that the shadow invariant
# (never force-fire alert/SMS/inject) holds, and that an FSM fault can never
# break a live tool call. They exist because a one-token wiring omission once
# silently zeroed all of this while the suite stayed green.


def _record_log_gate(monkeypatch):
    """Spy on store.log_gate, returning the list of recorded rows."""
    rows = []

    def rec(call_sid, gate, decision, **kw):
        rows.append({"call_sid": call_sid, "gate": gate, "decision": decision, **kw})

    monkeypatch.setattr(voice_server.store, "log_gate", rec)
    return rows


def test_production_functioncall_path_threads_fsm(monkeypatch):
    """THE regression guard: the FunctionCallRequest branch in
    _deepgram_to_twilio MUST pass fsm into _run_function. Without it every
    tool-call observation (report_state divergence, note_tool_call,
    note_service_area) is silently dead on the live +61468089224 line."""
    fsm = call_state.CallFSM(DEFAULT_TENANT, "+61411111111")

    captured = {}

    def spy(t, caller, call_sid, name, args, actions, fsm=None):
        captured["fsm"] = fsm
        captured["name"] = name
        return '{"approved": true, "instruction": ""}'

    monkeypatch.setattr(voice_server, "_run_function", spy)
    # No-op the watchdog so the bridge loop has nothing else to drive.
    monkeypatch.setattr(voice_server, "_call_watchdog", lambda *a, **k: asyncio.sleep(0))

    class _FakeDg:
        def __init__(self, messages):
            self._msgs = list(messages)
            self.sent = []

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._msgs:
                return self._msgs.pop(0)
            raise StopAsyncIteration

        async def send(self, data):
            self.sent.append(data)

    class _FakeTwilio:
        def __init__(self):
            self.sent = []

        async def send_text(self, data):
            self.sent.append(data)

    msg = json.dumps({
        "type": "FunctionCallRequest",
        "functions": [{"id": "1", "name": "report_state",
                       "arguments": json.dumps({"proposed_next": "book"})}],
    })
    dg = _FakeDg([msg])
    latency = {"started_at": time.perf_counter(), "function_calls": 0}

    asyncio.run(voice_server._deepgram_to_twilio(
        dg, _FakeTwilio(), DEFAULT_TENANT, "+61411111111", "CA1", "SZ1", latency,
        voice_server._new_call_actions(), fsm))

    assert captured.get("fsm") is fsm  # fsm was threaded through, not defaulted to None
    assert captured.get("name") == "report_state"


def test_shadow_directives_never_force_fire(monkeypatch):
    """Core Tier-1 invariant: _handle_fsm_directives logs alert/sms/inject
    directives but must NOT trigger send_sms / _queue_alert / inject. If a
    future refactor wires these to real side effects, this trips — not a live
    call."""
    trips = {"sms": 0, "alert": 0, "inject": 0}
    monkeypatch.setattr(voice_server.twilio_io, "send_sms",
                        lambda *a, **k: trips.__setitem__("sms", trips["sms"] + 1))
    monkeypatch.setattr(voice_server, "_queue_alert",
                        lambda *a, **k: trips.__setitem__("alert", trips["alert"] + 1))

    async def _trip_inject(*a, **k):
        trips["inject"] += 1

    monkeypatch.setattr(voice_server, "_inject_agent_message", _trip_inject)
    rows = _record_log_gate(monkeypatch)

    directives = [
        call_state.Directive("alert", "G3", {"kind": "emergency", "tier": 2}),
        call_state.Directive("sms", "G3", {"tier": 2}),
        call_state.Directive("inject", "G3", {"tier": 2}),
        call_state.Directive("alert", "G2", {"kind": "new_lead"}),
    ]
    fsm = call_state.CallFSM({}, "+61")
    voice_server._handle_fsm_directives(directives, "CA_SHADOW", fsm)

    assert trips == {"sms": 0, "alert": 0, "inject": 0}  # no side effect fired
    assert [r["gate"] for r in rows] == ["G3", "G3", "G3", "G2"]  # all logged


def test_report_state_divergence_reaches_gate_log(monkeypatch):
    """The moat signal: a disagreeing report_state claim must land in
    voice_gate_log with divergence_flag=1 via the _run_function seam."""
    rows = _record_log_gate(monkeypatch)
    fsm = call_state.CallFSM(DEFAULT_TENANT, "+61411111111")
    fsm.observe("user", "hi my lights went out")  # FSM now expects proposed_next "gather"

    result = voice_server._run_function(
        DEFAULT_TENANT, "+61411111111", "CA_DIV", "report_state",
        {"proposed_next": "book"}, voice_server._new_call_actions(), fsm,
    )

    parsed = json.loads(result)
    assert parsed["approved"] is True and parsed["instruction"] == ""
    assert rows and rows[-1]["gate"] == "report_state"
    assert rows[-1]["divergence_flag"] == 1


def test_report_state_without_fsm_returns_safe_json_and_logs_nothing(monkeypatch):
    """fsm=None (e.g. init failed, or a path that never built one) must still
    return valid JSON for Deepgram and write no gate row."""
    rows = _record_log_gate(monkeypatch)
    result = voice_server._run_function(
        DEFAULT_TENANT, "+61411111111", "CA", "report_state", {}, voice_server._new_call_actions(),
    )
    assert json.loads(result) == {"approved": True, "instruction": ""}
    assert rows == []


def test_check_service_area_feeds_fsm_canonical_suburb():
    """The G4/G5 input: check_service_area's authoritative result must populate
    fsm.capture.suburb_canonical + area_confirmed through the seam."""
    fsm = call_state.CallFSM(AREA_TENANT, "+61411111111")
    voice_server._run_function(
        AREA_TENANT, "+61411111111", "CA", "check_service_area",
        {"raw_suburb": "Fairy Meadow"}, voice_server._new_call_actions(), fsm,
    )
    assert fsm.capture.suburb_canonical == "Fairy Meadow"
    assert fsm.capture.area_confirmed is True


def test_book_job_logs_gate_decision_via_run_function(monkeypatch):
    """A book_job tool call must produce a voice_gate_log row (decision reflects
    can_book()), while the booking itself still queues exactly as before."""
    rows = _record_log_gate(monkeypatch)
    actions = voice_server._new_call_actions()
    fsm = call_state.CallFSM(AREA_TENANT, "+61411111111")
    result = voice_server._run_function(
        AREA_TENANT, "+61411111111", "CA", "book_job",
        {"window_text": "Tuesday 8 to 11", "start_iso": "2026-06-30T08:00:00+10:00",
         "end_iso": "2026-06-30T11:00:00+10:00"}, actions, fsm,
    )
    assert result == "Booking details noted for after the call."  # behavior unchanged
    assert actions["booking"]["window_text"] == "Tuesday 8 to 11"
    assert any(r["gate"] == "book_job" for r in rows)


def test_run_function_survives_fsm_and_db_exceptions(monkeypatch):
    """An FSM or DB fault must NEVER break a live tool call. report_state returns
    safe JSON; the other tools return their unchanged result strings."""
    fsm = call_state.CallFSM(DEFAULT_TENANT, "+61411111111")

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(fsm, "ingest_report_state", _boom)
    result = json.loads(voice_server._run_function(
        DEFAULT_TENANT, "+61", "CA", "report_state", {"proposed_next": "close"},
        voice_server._new_call_actions(), fsm,
    ))
    assert result == {"approved": True, "instruction": ""}

    monkeypatch.setattr(fsm, "note_tool_call", _boom)
    out = voice_server._run_function(
        DEFAULT_TENANT, "+61", "CA", "alert_owner", {"kind": "new_lead", "message": "x"},
        voice_server._new_call_actions(), fsm,
    )
    assert out == "Noted for the post-call owner summary."  # FSM fault swallowed, tool still ran


# --- shadow-FSM live-path integration (Tier 1) -----------------------------
# These drive _deepgram_to_twilio — the REAL Deepgram message loop — over a
# canned message stream, so the LIVE call site (not _run_function in isolation)
# is what's under test.
#
# CONTEXT: when this task started, the FunctionCallRequest branch called
# _run_function WITHOUT `fsm`, silently zeroing every FSM tool-call observation
# (report_state divergence, note_tool_call, note_service_area) on the live
# +61468089224 line. The working tree now forwards `fsm` at that call site
# (voice_server.py ~L1093, with an explanatory comment), so these regression
# guards PASS today; each will fail the instant a refactor drops `fsm` again.
#
# _CannedDG is an async iterator over a fixed list of raw JSON messages plus a
# send() that records FunctionCallResponse acks. The call watchdog is neutralized
# so only the message loop is under test. store.log_gate is always spied so a
# test can prove whether the FSM's gate decision reached the eval corpus.

import sqlite3 as _sqlite3


class _CannedDG:
    """Async-iterator stand-in for the Deepgram socket."""

    def __init__(self, messages):
        self._left = list(messages)
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._left:
            raise StopAsyncIteration
        return self._left.pop(0)

    async def send(self, data):
        self.sent.append(data)


class _FakeTwilio:
    def __init__(self):
        self.sent = []

    async def send_text(self, data):
        self.sent.append(data)


async def _noop_watchdog(dg, idle, call_sid):
    return None


def _drive_deepgram(monkeypatch, messages, *, fsm, log_gate):
    """Run _deepgram_to_twilio over canned Deepgram `messages` with a real `fsm`.
    `log_gate` is installed as store.log_gate (a recorder or a raiser). Returns
    the CannedDG so the caller can inspect the FunctionCallResponse acks."""
    monkeypatch.setattr(voice_server, "_call_watchdog", _noop_watchdog)
    monkeypatch.setattr(voice_server.store, "log_gate", log_gate)
    dg = _CannedDG(messages)
    twilio = _FakeTwilio()
    actions = voice_server._new_call_actions()
    latency = {"function_calls": 0}
    asyncio.run(
        voice_server._deepgram_to_twilio(
            dg, twilio, DEFAULT_TENANT, "+61411111111", "CA_SHADOW",
            "SSTREAM", latency, actions, fsm,
        )
    )
    return dg


def test_regression_report_state_live_path_logs_gate(monkeypatch):
    """REGRESSION GUARD for the live report_state path.

    A report_state FunctionCallRequest flowing through the LIVE
    _deepgram_to_twilio loop must log gate='report_state'. The call site at
    voice_server.py ~L1093 currently forwards `fsm` to _run_function, so this
    PASSES today. If a future refactor drops `fsm`, _run_function hits the
    `fsm is None` short-circuit and store.log_gate is never called; this
    assertion then fails and surfaces the regression. (That was the original bug
    this task was scoped around; the working tree has since been fixed, so this
    guard now locks the fix in rather than exposing a live bug.)"""
    fsm = voice_server.call_state.CallFSM(DEFAULT_TENANT, "+61411111111")
    gate_calls = []

    def spy(call_sid, gate, decision, **kw):
        gate_calls.append({"call_sid": call_sid, "gate": gate, "decision": decision})

    msg = json.dumps({
        "type": "FunctionCallRequest",
        "functions": [{
            "id": "fn1",
            "name": "report_state",
            "arguments": json.dumps(
                {"triage": "emergency", "proposed_next": "escalate"}
            ),
        }],
    })
    dg = _drive_deepgram(monkeypatch, [msg], fsm=fsm, log_gate=spy)

    # The tool branch RAN (Deepgram got its ack) — proves we reached the live
    # call site rather than erroring earlier.
    assert any(
        json.loads(s).get("type") == "FunctionCallResponse" for s in dg.sent
    ), "FunctionCallResponse never sent — loop did not reach the tool branch"

    # Regression assertion: store.log_gate must be called with gate='report_state'.
    # PASSES today (call site forwards `fsm`); FAILS if a refactor drops `fsm`,
    # which would make _run_function short-circuit and lose the gate decision.
    rs_logs = [c for c in gate_calls if c["gate"] == "report_state"]
    assert rs_logs, (
        "store.log_gate was never called with gate='report_state' on the live "
        "_deepgram_to_twilio path. The call site at voice_server.py ~L1093 "
        "is forwarding no `fsm` to _run_function, so the report_state ack short-"
        "circuits and the gate decision is lost from the eval corpus. "
        f"gate_calls observed: {gate_calls}"
    )


def test_source_guard_live_run_function_call_passes_fsm():
    """SOURCE-LEVEL regression guard (no event loop). Parses voice_server.py,
    finds every `asyncio.to_thread(_run_function, ...)` call inside
    _deepgram_to_twilio, and asserts each forwards `fsm`. This is the precise,
    fast trip-wire for the original bug: a one-token edit that drops `fsm` from
    that call site silently zeroes every FSM tool-call observation on the live
    line. PASSES today; fails the instant `fsm` is dropped."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(voice_server.__file__).read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_deepgram_to_twilio"
    )

    checked = 0
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        is_to_thread = (
            (isinstance(func, ast.Attribute) and func.attr == "to_thread")
            or (isinstance(func, ast.Name) and func.id == "to_thread")
        )
        if not is_to_thread:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Name) and first.id == "_run_function"):
            continue
        checked += 1
        arg_names = {a.id for a in node.args if isinstance(a, ast.Name)}
        assert "fsm" in arg_names, (
            "asyncio.to_thread(_run_function, ...) inside _deepgram_to_twilio "
            "does NOT forward `fsm`. Without it, report_state divergence, "
            "note_tool_call, and note_service_area all silently no-op on the "
            "live path. Re-add `fsm` to the call."
        )
    assert checked >= 1, (
        "no asyncio.to_thread(_run_function, ...) call found in "
        "_deepgram_to_twilio; the live tool path may have been restructured"
    )


def test_shadow_mode_logs_observe_and_swallows_log_failure(monkeypatch):
    """Shadow-mode safety (the live-line invariant). A ConversationText user turn
    must (a) run observe and log a gate, and (b) NEVER let a logging failure
    escape into the media loop — a locked DB cannot break the live call."""
    fsm = voice_server.call_state.CallFSM(DEFAULT_TENANT, "+61411111111")
    # G0 not_a_job -> observe emits a no_alert directive that is logged.
    msg = json.dumps({
        "type": "ConversationText",
        "role": "user",
        "content": "sorry, is this Telstra?",
    })

    # (a) observe ran and reached the gate log.
    gate_calls = []

    def recorder(call_sid, gate, decision, **kw):
        gate_calls.append({"gate": gate, "decision": decision})

    _drive_deepgram(monkeypatch, [msg], fsm=fsm, log_gate=recorder)
    assert any(c["gate"] == "G0" for c in gate_calls), (
        f"observe did not log a G0 gate decision; gate_calls={gate_calls}"
    )

    # (b) a logging failure must not escape into the media/websocket loop.
    def raising(call_sid, gate, decision, **kw):
        raise _sqlite3.OperationalError("database is locked")

    # Must not raise — _handle_fsm_directives / _fsm_observe swallow it.
    _drive_deepgram(monkeypatch, [msg], fsm=fsm, log_gate=raising)


def test_report_state_ack_when_fsm_is_none(monkeypatch):
    """Degraded path: calling _run_function for report_state with fsm=None returns
    a Deepgram-safe ack and does NOT touch store.log_gate. The live path no longer
    hits this (it forwards fsm), but it remains the fallback if an FSM ever fails
    to construct — Deepgram must still get valid JSON and no row is written."""
    called = []

    def spy(call_sid, gate, decision, **kw):
        called.append(gate)

    monkeypatch.setattr(voice_server.store, "log_gate", spy)
    actions = voice_server._new_call_actions()
    result = voice_server._run_function(
        DEFAULT_TENANT, "+61411111111", "CA_X", "report_state",
        {"triage": "emergency", "proposed_next": "escalate"}, actions, None,
    )
    assert json.loads(result) == {"approved": True, "instruction": ""}
    assert called == [], f"store.log_gate must not be called when fsm=None; got {called}"
