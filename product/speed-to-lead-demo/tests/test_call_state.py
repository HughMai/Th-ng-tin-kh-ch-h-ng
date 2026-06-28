"""call_state.CallFSM tests — pure, no network, no DB. Canned transcript + tool
streams fed through the shadow FSM, exactly the way test_goal_loop drives
decide(). The FSM does no I/O, so importing it here is cheap and deterministic.

These cover the Tier 1 gates that must be trustworthy before any gate is
promoted from "log" to "enforce": G0 (not_a_job), G3 (two-tier emergency,
accent-robust), G6/G7 (offer + agreement), the can_book/can_close gates, and the
divergence_flag that is the whole point of the shadow run.
"""

from __future__ import annotations

import call_state


def _replay(events, tenant=None):
    """Drive a fresh FSM through a canned call. Each event is either:
      (role, content) — a ConversationText event
      "a line"         — shorthand for an assistant line
      {"service_area": (status, canonical)}
      {"name": tool, "args": {...}}"""
    fsm = call_state.CallFSM(tenant or {}, "+61400000000")
    directives = []
    for ev in events:
        if isinstance(ev, str):
            directives.extend(fsm.observe("assistant", ev))
        elif isinstance(ev, tuple):
            role, content = ev
            directives.extend(fsm.observe(role, content))
        elif isinstance(ev, dict):
            if "service_area" in ev:
                status, canonical = ev["service_area"]
                fsm.note_service_area(status, canonical)
            else:
                directives.extend(fsm.note_tool_call(ev["name"], ev.get("args", {})))
    return fsm, directives


# --- happy-path state path ---------------------------------------------------

def test_happy_path_walks_to_close_and_books():
    fsm, _ = _replay([
        ("user", "hi, my power points stopped working"),
        "Sure, what suburb are you in?",
        ("user", "Fairy Meadow"),
        {"service_area": ("in_area", "Fairy Meadow")},
        "I can do Tuesday 8 to 11, or Wednesday 1 to 4.",
        ("user", "Tuesday works"),
        {"name": "book_job", "args": {"window_text": "Tuesday 8 to 11",
                                      "start_iso": "2026-06-30T08:00:00+10:00",
                                      "end_iso": "2026-06-30T11:00:00+10:00"}},
        "Locked in. Anything else I can help with?",
        ("user", "no that's all"),
        {"name": "end_call"},
    ])
    assert fsm.state == call_state.CallState.CLOSE
    reached = [s["to"] for s in fsm.state_path]
    for expected in ("intent", "service_area", "offer", "confirm_book", "book", "close"):
        assert expected in reached, reached
    assert fsm.booked is True
    assert fsm.capture.agreement == "yes"
    assert fsm.capture.suburb_canonical == "Fairy Meadow"
    assert fsm.capture.area_confirmed is True
    assert fsm.capture.anything_else_asked is True


def test_first_lead_alert_fires_on_turn_one():
    _, directives = _replay([
        ("user", "my hot water system is dead"),
        "What suburb are you in?",
    ])
    assert any(d.gate == "G2" and d.op == "alert" for d in directives)
    # alert_owner on turn 1 should suppress a later G2 (idempotent guarantee)


# --- G3: two-tier, accent-robust emergency -----------------------------------

def test_detect_emergency_tiers():
    assert call_state.detect_emergency("I can smell gas in the house") == 2
    assert call_state.detect_emergency("my husband collapsed and isn't breathing") == 2
    assert call_state.detect_emergency("sparks are flying from the powerpoint") == 1
    assert call_state.detect_emergency("i just want to book a job") == 0


def test_emergency_accent_robust_boning_smell():
    # "boning smell" is how a strong accent + noisy line can render "burning
    # smell". The safety backstop must still fire.
    fsm, directives = _replay([("user", "there's a boning smell from the switchboard")])
    assert fsm.capture.triage == "emergency"
    assert fsm.state == call_state.CallState.ESCALATE
    ops = {d.op for d in directives}
    assert "alert" in ops and "sms" in ops and "inject" in ops


def test_gas_leak_forces_escalate_and_no_offer():
    fsm, _ = _replay([("user", "I think I've got a gas leak")])
    assert fsm.state == call_state.CallState.ESCALATE
    assert fsm.capture.emergency_force_fired is True


# --- G0: not_a_job (no owner alert) ------------------------------------------

def test_not_a_job_closes_without_alert():
    fsm, directives = _replay([("user", "sorry, is this Telstra?")])
    assert fsm.capture.triage == "not_a_job"
    assert fsm.state == call_state.CallState.CLOSE
    assert any(d.op == "no_alert" for d in directives)


# --- G7: negation/amendment-aware agreement ----------------------------------

def test_agreement_parsing():
    assert call_state.parse_agreement("yeah book it") == "yes"
    assert call_state.parse_agreement("Tuesday works") == "yes"
    assert call_state.parse_agreement("no worries, let's do it") == "yes"
    assert call_state.parse_agreement("yeah nah, not today") == "no"
    assert call_state.parse_agreement("actually can we do Wednesday") == "unsure"
    assert call_state.parse_agreement("my power is out") is None


def test_agreement_requires_a_matching_window():
    # Caller agrees but names a window that was never offered -> G7 fails (logs,
    # doesn't block, in Tier 1).
    fsm, directives = _replay([
        ("user", "my lights are out"),
        "What suburb?",
        ("user", "Unanderra"),
        {"service_area": ("in_area", "Unanderra")},
        "I can do Tuesday 8 to 11 or Wednesday 1 to 4.",
        ("user", "Friday morning works for me"),
    ])
    g7 = [d for d in directives if d.gate == "G7"]
    assert g7 and g7[-1].payload["decision"] == "fail"
    assert fsm.capture.agreement == "unsure"


# --- gates: can_book / can_close --------------------------------------------

def test_can_book_requires_area_suburb_and_agreement():
    fsm = call_state.CallFSM({}, "+61")
    assert fsm.can_book()[0] is False
    fsm.note_service_area("in_area", "Wollongong")
    fsm.capture.agreement = "yes"
    ok, reason = fsm.can_book()
    assert ok is True, reason


def test_can_close_blocks_agreed_but_not_booked():
    fsm = call_state.CallFSM({}, "+61")
    fsm.capture.agreement = "yes"
    fsm.capture.anything_else_asked = True
    ok, reason = fsm.can_close()
    assert ok is False and "G11" in reason
    fsm.booked = True
    assert fsm.can_close()[0] is True


def test_can_close_needs_anything_else_asked():
    fsm = call_state.CallFSM({}, "+61")
    ok, reason = fsm.can_close()
    assert ok is False and "G9" in reason


# --- shadow mode: book_job logs would_block but still records ----------------

def test_book_job_shadow_logs_block_but_records():
    # No area, no agreement -> can_book is False, but Tier 1 must NOT prevent
    # the booking (it only logs what it WOULD have done).
    fsm = call_state.CallFSM({}, "+61")
    directives = fsm.note_tool_call("book_job", {"window_text": "Tuesday 8 to 11"})
    assert fsm.booked is True
    decisions = [d.payload["decision"] for d in directives if d.gate == "book_job"]
    assert decisions == ["would_block"]


# --- divergence_flag (the moat signal) ---------------------------------------

def test_divergence_flag_flips_when_llm_disagrees():
    fsm, _ = _replay([
        ("user", "hi my lights went out"),
        "What suburb are you in?",
    ])
    # FSM is mid-gather (INTENT) -> it expects proposed_next "gather".
    assert fsm._proposed_next() == "gather"
    _, log_disagree = fsm.ingest_report_state({"proposed_next": "book"})
    assert log_disagree["divergence"] == 1
    _, log_agree = fsm.ingest_report_state({"proposed_next": "gather"})
    assert log_agree["divergence"] == 0


def test_report_state_enriches_capture():
    fsm = call_state.CallFSM({}, "+61")
    fsm.ingest_report_state({"triage": "bookable", "suburb_raw": "Coniston",
                             "urgency": "this_week"})
    assert fsm.capture.triage == "bookable"
    assert fsm.capture.suburb_raw == "Coniston"
    assert fsm.capture.urgency == "this_week"


def test_report_state_result_is_approved_with_no_instruction():
    # Tier 1 never steers the LLM — the tool result is always approved, empty
    # instruction, valid JSON for Deepgram.
    import json
    fsm = call_state.CallFSM({}, "+61")
    result_json, _ = fsm.ingest_report_state({"proposed_next": "close"})
    result = json.loads(result_json)
    assert result["approved"] is True
    assert result["instruction"] == ""


# --- snapshot shape ----------------------------------------------------------

def test_snapshot_is_persistable_shape():
    fsm, _ = _replay([("user", "hello"), "Hi there, what's the issue?"])
    snap = fsm.snapshot()
    assert snap["current_state"] == "intent"
    assert isinstance(snap["state_path"], list)
    assert snap["state_path"][0]["from"] == "greet"
    assert snap["turns"] == 1
    assert isinstance(snap["guardrail_flags"], list)


# --- emergency matcher precision (no false positives on common AU words) ------

def test_sparky_does_not_false_trigger_emergency():
    # "sparky" is AU slang for electrician. At edit-distance 1 it collides with a
    # bare "sparks" keyword — over-firing would flag nearly every booking call as
    # an emergency. The keyword list uses disambiguating phrases instead.
    assert call_state.detect_emergency("I need a sparky to look at my wiring") == 0


def test_gas_alone_does_not_false_trigger():
    # Bare "gas" collides with "gasp" / indigestion ("I've got gas"). Real gas
    # emergencies say "gas leak" / "smell gas", which the phrases catch.
    assert call_state.detect_emergency("I've had terrible gas all day") == 0
    assert call_state.detect_emergency("I think I've got a gas leak") == 2


# --- G2 first-lead idempotency -----------------------------------------------

def test_g2_does_not_refire_on_second_assistant_turn():
    _, directives = _replay([
        ("user", "my hot water system is dead"),
        "What suburb are you in?",
        ("user", "Fairy Meadow"),
        "I can do Tuesday 8 to 11.",
    ])
    g2 = [d for d in directives if d.gate == "G2" and d.op == "alert"]
    assert len(g2) == 1  # singleton: the latch + assistant_turns==1 guard hold


def test_g2_suppressed_when_alert_owner_already_called():
    # If the LLM paged the owner before its first spoken turn, G2 must NOT
    # double-page.
    fsm = call_state.CallFSM({}, "+61")
    directives = []
    directives += fsm.observe("user", "my hot water is dead")
    directives += fsm.note_tool_call("alert_owner", {"kind": "new_lead"})
    directives += fsm.observe("assistant", "What suburb are you in?")  # turn 1
    assert [d for d in directives if d.gate == "G2"] == []


# --- more spec gates ---------------------------------------------------------

def test_out_of_area_transitions_to_fallback():
    fsm = call_state.CallFSM({}, "+61")
    fsm.observe("user", "I need help out at Bowral")
    fsm.note_service_area("out_of_area", "Bowral")
    assert fsm.state == call_state.CallState.FALLBACK
    assert fsm.capture.area_confirmed is False
    ok, reason = fsm.can_book()  # a booking here would be gated on area (G4)
    assert ok is False and "G4" in reason


def test_g10_turn_budget_forces_fallback():
    # Five user turns stuck in INTENT (cap 3; the first turn charged GREET) blows
    # the budget -> FALLBACK + a G10 directive. Caps chatty loops the idle timer
    # can't catch.
    directives = []
    fsm = call_state.CallFSM({}, "+61")
    for _ in range(5):
        directives += fsm.observe("user", "hello are you there")
    assert fsm.state == call_state.CallState.FALLBACK
    assert any(d.gate == "G10" for d in directives)


def test_report_state_agreement_divergence():
    # Isolates the agreement branch of divergence: same proposed_next, but the
    # LLM's agreement claim disagrees with what the FSM observed.
    fsm = call_state.CallFSM({}, "+61")
    fsm.observe("user", "my power is out")
    fsm.note_service_area("in_area", "Wollongong")
    fsm.observe("assistant", "Tuesday 8 to 11 or Wednesday 1 to 4.")
    fsm.observe("user", "Tuesday works")  # agreement yes -> CONFIRM_BOOK
    assert fsm._proposed_next() == "book"
    _, disagree = fsm.ingest_report_state({"proposed_next": "book", "agreement": "no"})
    assert disagree["divergence"] == 1
    _, agree = fsm.ingest_report_state({"proposed_next": "book", "agreement": "yes"})
    assert agree["divergence"] == 0


# --- purity + robustness (Tier 1 invariants) --------------------------------
# The shadow FSM must stay I/O-free (canned-input unit-testable) and must never
# crash the live call on weird transcript / report_state payloads. These guard
# those contracts statically and at the edge cases the real Deepgram feed can
# produce (null content, giant null-byte bursts, malformed tool args).

import ast as _ast
import json as _json
import pathlib as _pathlib
import sqlite3 as _sqlite3

import pytest
import store as _store


def test_call_state_module_is_io_free():
    """call_state must import NO network/DB/loop module — it is the pure shadow
    half of the FSM. A stray import would make canned-input tests flaky and could
    block the live event loop."""
    src = _pathlib.Path(call_state.__file__).read_text(encoding="utf-8")
    forbidden = {
        "store", "voice_server", "twilio_io", "gcal", "requests", "httpx",
        "sqlite3", "aiohttp", "asyncio", "socket",
    }
    top_level = set()
    for node in _ast.walk(_ast.parse(src)):
        if isinstance(node, _ast.Import):
            top_level |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, _ast.ImportFrom) and node.module:
            top_level.add(node.module.split(".")[0])
    assert not (top_level & forbidden), (
        f"call_state imports an I/O module: {sorted(top_level & forbidden)}"
    )


def test_observe_never_raises_on_odd_text():
    """ConversationText can be empty, null, or a giant blob of noise. observe()
    runs on the hot call path and must always return a list."""
    fsm = call_state.CallFSM({}, "+61")
    for role, content in [
        ("user", None),
        ("user", ""),
        ("assistant", ""),
        ("user", "\x00" * 100_000),  # noisy STT burst
        (None, "hello"),             # unknown role
        ("assistant", None),
    ]:
        out = fsm.observe(role, content)
        assert isinstance(out, list), (
            f"observe({role!r}, {content!r}) returned {type(out).__name__}"
        )


def test_observe_known_gap_non_string_content_raises():
    """KNOWN ROBUSTNESS GAP: observe() does not coerce non-string content. A
    numeric payload (or any non-str) raises AttributeError inside _norm() because
    it calls .lower() unconditionally. The live path wraps observe in
    _fsm_observe's try/except so the call survives, but the FSM loses that turn.
    This test encodes the CURRENT behaviour so a future hardening (cast to str)
    flips it red and forces the test surface to be updated deliberately."""
    fsm = call_state.CallFSM({}, "+61")
    with pytest.raises(AttributeError):
        fsm.observe("user", 123)


def test_ingest_report_state_never_raises_on_malformed():
    """report_state args come from the LLM tool call and may be missing / None /
    wrong shape. ingest_report_state must always return a (json, log_fields)
    pair whose first element parses to approved:true — Deepgram consumes that
    JSON directly and a crash here would break the call."""
    fsm = call_state.CallFSM({}, "+61")
    for args in [{}, None, {"triage": None},
                 {"triage": "emergency", "proposed_next": 7},  # wrong type
                 {"unknown_key": "x"}]:
        out = fsm.ingest_report_state(args)
        assert isinstance(out, tuple) and len(out) == 2, (
            f"ingest_report_state({args!r}) -> {out!r}"
        )
        result = _json.loads(out[0])
        assert result.get("approved") is True, (
            f"ingest_report_state({args!r}) not approved: {result}"
        )
        assert isinstance(out[1], dict)


def test_report_state_never_steers_in_tier1():
    """Tier 1 is shadow-only: even a claimed escalate must NOT produce an
    instruction. The tool result's instruction is always "" so Deepgram is never
    redirected by the FSM (steering is a Tier 2 change in voice_server)."""
    fsm = call_state.CallFSM({}, "+61")
    result_json, _ = fsm.ingest_report_state(
        {"triage": "emergency", "proposed_next": "escalate"}
    )
    result = _json.loads(result_json)
    assert result["approved"] is True
    assert result["instruction"] == "", f"FSM must not steer in Tier 1: {result}"


def test_snapshot_round_trips_through_store(fresh_db):
    """save_voice_state persists snapshot() faithfully — the columns read back
    must match the keys the FSM produced, so the eval corpus reflects truth
    rather than a serialization guess. Uses the conftest temp DB."""
    fsm = call_state.CallFSM({"tenant_id": "t-shadow"}, "+61411111111")
    fsm.observe("user", "I can smell gas")                  # -> ESCALATE, emergency
    fsm.observe("assistant", "I'm alerting the team now.")  # assistant_turns -> 1
    snap = fsm.snapshot()

    _store.save_voice_state("CA_ROUNDTRIP", "t-shadow", "+61411111111", snap)

    with _sqlite3.connect(_store._db_path) as db:
        db.row_factory = _sqlite3.Row
        row = dict(
            db.execute(
                "SELECT * FROM voice_call_states WHERE call_sid = ?",
                ("CA_ROUNDTRIP",),
            ).fetchone()
        )

    assert row["current_state"] == snap["current_state"] == "escalate"
    assert row["triage"] == snap["triage"] == "emergency"
    assert row["drop_point"] == snap["drop_point"]
    assert _json.loads(row["state_path"]) == snap["state_path"]
    assert bool(row["emergency_force_fired"]) is True
    assert row["turns"] == snap["turns"] == 1
    assert row["guardrail_flags"] == _json.dumps(snap["guardrail_flags"])
    assert row["urgency"] == snap["urgency"]


def test_configure_creates_fsm_tables_idempotently(tmp_path):
    """configure() runs at app start and in tests; calling it twice must not
    error and must not duplicate the FSM tables."""
    db_path = str(tmp_path / "idem.db")
    _store.configure(db_path)
    _store.configure(db_path)  # must not raise

    with _sqlite3.connect(db_path) as db:
        tables = [
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        ]

    assert tables.count("voice_call_states") == 1
    assert tables.count("voice_gate_log") == 1
