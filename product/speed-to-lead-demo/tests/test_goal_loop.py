"""goal_loop.decide() tests — no network, no subprocess. The loop's decision rules
(continue toward the goal, abort on cap/thrash/engine-break, advisory-only SMS
regression) driven by canned scorecards, exactly like test_canary injects a
fetcher. goal_loop is stdlib + subprocess only, so importing it here is cheap.
"""

from __future__ import annotations

import goal_loop


# --- scorecard builders -----------------------------------------------------

def _voice(*, ok=False, overall_pct=0.5, s_robust=0, s_total=9, failing=None):
    return {
        "ok": ok,
        "overall_pct": overall_pct,
        "safety_robust": s_robust,
        "safety_total": s_total,
        "failing": failing or [],
    }


def _fail(sid, *, safety=False, k=1, n=3):
    return {"id": sid, "safety": safety, "k": k, "n": n, "reasons": ["x"]}


def _sms(pct):
    return {"ok": pct >= 0.9, "overall_pct": pct, "failing": [],
            "safety_robust": 9, "safety_total": 9}


# --- the goal ---------------------------------------------------------------

def test_goal_met_when_voice_passes():
    v = _voice(ok=True, overall_pct=0.93, s_robust=9, s_total=9)
    action, st, reason = goal_loop.decide(v, None, {}, max_iters=8)
    assert action == "GOAL_MET"
    assert "met" in reason
    assert st.get("iter_count", 0) == 0  # a pass doesn't consume an iteration


def test_continue_when_voice_still_failing():
    v = _voice(ok=False, overall_pct=0.80, s_robust=8, s_total=9,
               failing=[_fail("E1", safety=True), _fail("B1")])
    action, st, reason = goal_loop.decide(v, None, {}, max_iters=8)
    assert action == "CONTINUE"
    assert st["iter_count"] == 1
    assert st["best_voice_pct"] == 0.80
    assert "80%" in reason


# --- advisory SMS regression (warns, never blocks) --------------------------

def test_sms_baseline_captured_on_first_pass():
    v = _voice(ok=False, overall_pct=0.8, failing=[_fail("E1", safety=True)])
    action, st, _ = goal_loop.decide(v, _sms(0.92), {}, max_iters=8)
    assert action == "CONTINUE"
    assert st["sms_baseline_pct"] == 0.92
    assert st["sms_warning"] is False


def test_sms_regression_is_advisory_not_blocking():
    v = _voice(ok=False, overall_pct=0.8, failing=[_fail("E1", safety=True)])
    _, st, _ = goal_loop.decide(v, _sms(0.92), {}, max_iters=8)          # baseline 0.92
    action, st, reason = goal_loop.decide(v, _sms(0.70), st, max_iters=8)  # SMS dropped
    assert action == "CONTINUE"  # advisory: warn, but keep fixing voice
    assert st["sms_warning"] is True
    assert "SMS regressed" in reason


# --- guards -----------------------------------------------------------------

def test_abort_at_iteration_cap():
    v = _voice(ok=False, overall_pct=0.5, failing=[_fail("E1", safety=True)])
    state = {"iter_count": 7}  # one more pass -> 8, the cap
    action, st, reason = goal_loop.decide(v, None, state, max_iters=8)
    assert action == "ABORT"
    assert "cap" in reason
    assert st["iter_count"] == 8


def test_thrash_aborts_after_two_no_progress_passes():
    v = _voice(ok=False, overall_pct=0.5, failing=[_fail("E1", safety=True)])
    _, st1, _ = goal_loop.decide(v, None, {}, max_iters=8)      # first sighting
    _, st2, _ = goal_loop.decide(v, None, st1, max_iters=8)     # same set, no_progress=1
    action, st3, reason = goal_loop.decide(v, None, st2, max_iters=8)  # no_progress=2 -> ABORT
    assert action == "ABORT"
    assert "thrash" in reason
    assert st3["no_progress_count"] == 2


def test_new_failure_resets_thrash_counter():
    v1 = _voice(ok=False, failing=[_fail("E1", safety=True)])
    v2 = _voice(ok=False, failing=[_fail("Q1", safety=True)])  # different failing set
    _, st1, _ = goal_loop.decide(v1, None, {}, max_iters=8)
    _, st2, _ = goal_loop.decide(v1, None, st1, max_iters=8)   # no_progress=1
    action, st3, _ = goal_loop.decide(v2, None, st2, max_iters=8)  # new failure -> reset
    assert action == "CONTINUE"
    assert st3["no_progress_count"] == 0


def test_engine_break_aborts():
    action, st, reason = goal_loop.decide(None, None, {}, max_iters=8)
    assert action == "ABORT"
    assert st["engine_error_streak"] == 1

    action2, st2, reason2 = goal_loop.decide(None, None, st, max_iters=8)
    assert action2 == "ABORT"
    assert st2["engine_error_streak"] == 2
    assert "twice" in reason2


# --- top_failure ordering ---------------------------------------------------

def test_top_failure_safety_first_then_lowest_pass_rate():
    score = {"failing": [
        _fail("B1", safety=False, k=2, n=3),  # non-safety, 0.67
        _fail("E1", safety=True, k=1, n=3),   # safety, 0.33  <- worst safety
        _fail("Q1", safety=True, k=2, n=3),   # safety, 0.67
    ]}
    assert goal_loop.top_failure(score)["id"] == "E1"


def test_top_failure_none_when_clean():
    assert goal_loop.top_failure({"failing": []}) is None
