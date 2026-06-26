"""Goal-driven auto-fix loop for the voice speed-to-lead brain.

Single-shot, canary-shaped: one run = assess + decide + write a fix brief, then
exit. No internal while/sleep — the outer re-invocation is run_loop.ps1, which
runs THIS script, then `claude -p` (the fixer-brain) against the brief it wrote,
looping until the goal is met. Exit code drives the loop:

    0  GOAL_MET   — voice eval passes (safety 100% robust + overall >=90%). Stop.
    1  CONTINUE   — voice eval still failing; a fix brief has been written. The
                    fixer should run, then this script again.
    2  ABORT      — iteration / spend cap hit, thrashing on the same failures,
                    or the engine broke. Stops the loop and surfaces to Hughie.

THE GOAL
    python evals.py --mode voice --tenant dave --runs 3 --json   exits 0

WHAT THE LOOP MAY EDIT (the allowlist — enforced by enforce_scope via git)
    voice_engine.py  — the VOICE_MODE override (voice-specific behaviour)
    workflow.py      — SYSTEM_PROMPT (the shared trade/triage/safety brain)
    knowledge.md     — domain-knowledge prose
Everything else under this dir (evals.py, tenants/*.json, voice_server.py, app.py,
secrets) is off-limits: edits there are reverted at the start of the next pass.

The SMS regression gate is ADVISORY (Hughie's choice): an edit that lifts voice
but drops SMS is flagged, not reverted — SMS is measured every pass and warned on.

Stdlib + subprocess only. No anthropic import — the fixer (Claude Code) owns the
edits; this script only measures and decides.

    python goal_loop.py --mode voice --tenant dave          # one pass
    python goal_loop.py --mode voice --reset                # start the count fresh
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import canary  # local: reuse send_telegram for the human stop-alert on ABORT

PRODUCT_DIR = Path(__file__).resolve().parent
LOOP_DIR = PRODUCT_DIR / ".loop"
STATE_PATH = LOOP_DIR / "state.json"
BRIEF_PATH = LOOP_DIR / "fix_brief.md"

# The fixer may touch only these (repo-relative-to-this-dir) paths.
ALLOWLIST = ("voice_engine.py", "workflow.py", "knowledge.md")

# Rough per-pass API spend estimate (both modes, runs=3) for the spend cap. The
# iteration cap is the hard bound; this is an advisory backstop.
EST_PER_ITER = 0.75


# --- assess: run evals.py once and parse its JSON scorecard ------------------

def assess(mode: str, tenant: str, runs: int, concurrency: int) -> dict | None:
    """Run `python evals.py --mode <mode> --json` and return the parsed scorecard,
    or None if the subprocess failed or the JSON couldn't be parsed (engine broke).
    evals streams [usage]/[voice usage] log lines to stdout ahead of the JSON
    object; those lines contain no '{', so we slice from the first '{' to EOF."""
    cmd = [
        sys.executable, str(PRODUCT_DIR / "evals.py"),
        "--mode", mode, "--tenant", tenant,
        "--runs", str(runs), "--concurrency", str(concurrency),
        "--json",
    ]
    proc = subprocess.run(cmd, cwd=PRODUCT_DIR, capture_output=True, text=True)
    out = proc.stdout
    start = out.find("{")
    if start < 0:
        print(f"[loop] {mode} eval produced no JSON (exit {proc.returncode})", file=sys.stderr)
        if proc.stderr.strip():
            print(proc.stderr[-800:], file=sys.stderr)
        return None
    try:
        return json.loads(out[start:])
    except json.JSONDecodeError as exc:
        print(f"[loop] {mode} eval JSON unparseable: {exc}", file=sys.stderr)
        return None


# --- decide: pure verdict over the two scorecards + persisted state ----------

def decide(
    voice: dict | None,
    sms: dict | None,
    state: dict,
    max_iters: int,
    est_per_iter: float = EST_PER_ITER,
    spend_cap: float = 5.0,
) -> tuple[str, dict, str]:
    """Return (action, new_state, reason). action ∈ {GOAL_MET, CONTINUE, ABORT}.
    Pure: no I/O, no subprocess — unit-tested by passing canned scorecards."""
    state = dict(state)  # never mutate the caller's dict

    # Engine health first: a pass that crashes can't be scored.
    if voice is None:
        streak = state.get("engine_error_streak", 0) + 1
        state["engine_error_streak"] = streak
        state["iter_count"] = state.get("iter_count", 0) + 1
        if streak >= 2:
            return "ABORT", state, "voice eval crashed twice in a row — the fixer likely broke the engine or an import"
        return "ABORT", state, "voice eval produced no score (subprocess/parse failure)"
    state["engine_error_streak"] = 0

    if voice.get("ok"):
        return "GOAL_MET", state, "voice goal met — safety robust and overall >= 90%"

    # Advance the pass counter and running bests.
    state["iter_count"] = state.get("iter_count", 0) + 1
    pct = voice.get("overall_pct", 0.0)
    state["best_voice_pct"] = max(state.get("best_voice_pct", 0.0), pct)
    state["cumulative_spend_estimate"] = (
        state.get("cumulative_spend_estimate", 0.0) + est_per_iter
    )
    state["safety_robust"] = voice.get("safety_robust")
    state["safety_total"] = voice.get("safety_total")

    # Advisory SMS regression: capture the baseline on the first pass we see SMS.
    if sms is not None:
        if "sms_baseline_pct" not in state:
            state["sms_baseline_pct"] = sms.get("overall_pct", 0.0)
        state["latest_sms_pct"] = sms.get("overall_pct", 0.0)
        state["sms_warning"] = state["latest_sms_pct"] < state["sms_baseline_pct"]

    # Thrash guard: identical failing set as the previous pass = no progress.
    failing_ids = tuple(sorted(f["id"] for f in voice.get("failing", [])))
    if failing_ids and failing_ids == tuple(state.get("last_failing_ids", [])):
        state["no_progress_count"] = state.get("no_progress_count", 0) + 1
    else:
        state["no_progress_count"] = 0
    state["last_failing_ids"] = list(failing_ids)

    if state["iter_count"] >= max_iters:
        return ("ABORT", state,
                f"iteration cap ({max_iters}) reached — best voice {state['best_voice_pct']:.0%}")
    if state["cumulative_spend_estimate"] >= spend_cap:
        return ("ABORT", state,
                f"estimated spend cap (${spend_cap:g}) reached — best voice {state['best_voice_pct']:.0%}")
    if state["no_progress_count"] >= 2:
        return ("ABORT", state,
                f"thrashing — same failures for 2 passes: {list(failing_ids)}")

    sms_note = ""
    if state.get("sms_warning"):
        sms_note = (f"  ⚠ advisory: SMS regressed "
                    f"{state['sms_baseline_pct']:.0%} -> {state['latest_sms_pct']:.0%}")
    return ("CONTINUE", state,
            f"voice at {pct:.0%} overall, safety robust "
            f"{voice.get('safety_robust')}/{voice.get('safety_total')}{sms_note}")


# --- the fix brief (the only thing the fixer reads) --------------------------

def top_failure(voice: dict) -> dict | None:
    """The one scenario to target this pass: safety failures before non-safety,
    then the lowest pass-rate (most broken) first."""
    failing = voice.get("failing") or []
    if not failing:
        return None

    def rank(f: dict) -> tuple[int, float]:
        pass_rate = f.get("k", 0) / max(f.get("n", 1), 1)
        return (0 if f.get("safety") else 1, pass_rate)  # safety first, lowest pass-rate first

    return sorted(failing, key=rank)[0]


def write_fix_brief(voice: dict, state: dict, reason: str) -> dict | None:
    top = top_failure(voice)
    if top is None:
        return None
    sid = top.get("id", "?")
    tag = " [SAFETY]" if top.get("safety") else ""
    convo = "\n".join(
        f"  {'Customer' if m.get('role') == 'u' else 'Assistant'}: {m.get('text', '')}"
        for m in top.get("convo", [])
    )
    must = top.get("must", "(missing)")
    reasons = (
        "\n".join(f"  - {r}" for r in top.get("reasons", []))
        or "  - (no deterministic reason — graded on the `must` line)"
    )
    overall = voice.get("overall_pct", 0)
    s_robust = voice.get("safety_robust")
    s_total = voice.get("safety_total")
    body = f"""# Fix brief — voice eval pass {state.get('iter_count', '?')}

Status: {reason}

## Goal (unchanging)
`python evals.py --mode voice --tenant dave --runs 3 --json` exits 0 —
safety 100% robust AND overall >= 90%. Right now: overall {overall:.0%},
safety robust {s_robust}/{s_total}.

## Fix ONE thing this pass — scenario {sid}{tag}
Conversation so far:
{convo}

What the reply must do (`must`):
  {must}

Why it currently fails:
{reasons}

## Where you may edit (the allowlist)
- voice_engine.py -> VOICE_MODE (voice-only behaviour: speak-first opener, no
  formatting for TTS, one question, route actions through alert_owner / book_job
  / end_call tools, send new_lead on turn 1).
- workflow.py -> SYSTEM_PROMPT text body (the shared trade/triage/safety brain
  under VOICE_MODE — most triage/safety/precision failures live here). Edit TEXT
  only. NOTE: apply_quoting_policy does an exact-match replace on the sentence
  'NEVER quote a price or estimate. {{owner}} prices the work.' — if you rewrite
  that sentence, update the `base` in apply_quoting_policy too or it silently
  no-ops for non-dave tenants.
- knowledge.md -> domain-knowledge prose.

## Off-limits (enforce_scope reverts edits here on the next pass)
evals.py, tenants/*.json, .env / secrets, voice_server.py, app.py, twilio_io.py,
telegram_io.py, gcal.py. And the FROZEN INTERFACE — edit text, never rename:
run_turn / run_followup / system_for / stream_voice_turn signatures, and the
AgentTurn / FollowUp / VoiceTurn pydantic field names.

## Rules
- Make ONE surgical edit that targets ONLY scenario {sid}. Don't shotgun the
  prompt — each pass must be attributable to one failure.
- Don't refactor or 'improve' adjacent code. Match the surrounding style.
- Then re-run:  python goal_loop.py --mode voice --tenant dave
"""
    BRIEF_PATH.write_text(body, encoding="utf-8")
    return top


# --- scope guard: revert any off-scope edits the fixer made ------------------

def enforce_scope() -> list[str]:
    """The fixer (Claude Code) runs between passes. Before we assess, discard any
    tracked edits it made OUTSIDE the allowlist. Paths are relative to this dir
    (git --relative). Returns the reverted paths."""
    out = subprocess.run(
        ["git", "diff", "--name-only", "--relative"],
        cwd=PRODUCT_DIR, capture_output=True, text=True,
    )
    reverted: list[str] = []
    for rel in (l.strip() for l in out.stdout.splitlines() if l.strip()):
        if rel in ALLOWLIST:
            continue  # the actual fix — leave it
        subprocess.run(["git", "checkout", "--", rel], cwd=PRODUCT_DIR)
        reverted.append(rel)
    return reverted


# --- state ------------------------------------------------------------------

def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def save_state(state: dict) -> None:
    LOOP_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


# --- entrypoint -------------------------------------------------------------

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["voice", "sms"], default="voice",
                    help="the goal mode (default voice). SMS-only loops just need --mode sms.")
    ap.add_argument("--tenant", default="dave")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--max-iters", type=int, default=8, help="hard iteration cap (default 8)")
    ap.add_argument("--spend-cap", type=float, default=5.0, help="estimated $ cap (default 5)")
    ap.add_argument("--reset", action="store_true", help="clear .loop/state.json and start fresh")
    args = ap.parse_args(argv)

    LOOP_DIR.mkdir(parents=True, exist_ok=True)
    if args.reset and STATE_PATH.exists():
        STATE_PATH.unlink()

    reverted = enforce_scope()
    if reverted:
        print(f"[loop] reverted {len(reverted)} off-scope edit(s): {reverted}", file=sys.stderr)

    state = load_state()
    voice = assess(args.mode, args.tenant, args.runs, args.concurrency)
    # SMS is always measured as the regression baseline, regardless of goal mode.
    sms = None
    if args.mode == "voice":
        sms = assess("sms", args.tenant, args.runs, args.concurrency)

    action, state, reason = decide(
        voice, sms, state, args.max_iters, EST_PER_ITER, args.spend_cap
    )
    save_state(state)

    print(f"[loop] pass {state.get('iter_count', 0)}: {action} — {reason}", file=sys.stderr)

    if action == "CONTINUE":
        top = write_fix_brief(voice, state, reason)
        if top:
            print(f"[loop] fix brief written → {BRIEF_PATH} (target: {top['id']})", file=sys.stderr)
        return 1
    if action == "GOAL_MET":
        # tidy: a stale brief from a prior pass is no longer relevant
        if BRIEF_PATH.exists():
            BRIEF_PATH.unlink()
        return 0

    # ABORT — surface to a human. Best-effort Telegram ping (no-op if unconfigured).
    canary.send_telegram(
        f"🛑 Voice goal-loop ABORTED (pass {state.get('iter_count', 0)})\n{reason}\n"
        f"Best voice: {state.get('best_voice_pct', 0):.0%}. "
        f"Re-run with --reset once the failing scenario(s) are unblocked."
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
