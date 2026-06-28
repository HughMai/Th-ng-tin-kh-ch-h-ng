"""Shadow state machine for the speed-to-lead voice agent (Tier 1: observe + log).

Deepgram owns the hosted LLM and the conversation history, so we can't rebuild
the prompt each turn the way a textbook voice FSM does. Instead this module is a
*shadow* FSM that runs alongside Deepgram, derives call state from the only
signals our server actually sees (the ConversationText transcript + the tool-call
stream), and emits *directives*. ``voice_server`` performs every side-effect; this
module does NO I/O (no network, no DB, no subprocess) so it is unit-testable with
canned transcript streams — exactly the purity contract ``goal_loop.decide`` keeps.

Tier 1 scope (this file): OBSERVE + LOG ONLY. Every gate is computed and emitted
as a directive, but ``voice_server`` executes none of the alert/sms/inject
directives yet — it only writes them to ``voice_gate_log``. That log's
``divergence_flag`` (FSM decision != LLM ``report_state`` claim) is the eval corpus
that proves the FSM earns its keep before we let it enforce anything. Promoting a
gate from "log" to "enforce" is a Tier 2 change in ``voice_server``, not here.

Honest caveat baked into the design: on Deepgram's streaming path ConversationText
flows straight to TTS as it's generated, so any directive that "steers the next
turn" is DETECTIVE, not PREVENTIVE — the caller has already heard the wrong thing.
These gates log misses and steer the NEXT turn; they do not block the current
utterance. See `.planning/voice-fsm-spec.md` §2.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum


class CallState(str, Enum):
    GREET = "greet"
    INTENT = "intent"
    GATHER = "gather"
    SERVICE_AREA = "service_area"
    OFFER = "offer"
    CONFIRM_BOOK = "confirm_book"
    BOOK = "book"
    CLOSE = "close"
    ESCALATE = "escalate"
    FALLBACK = "fallback"
    ABANDONED = "abandoned"


@dataclass
class Directive:
    """One FSM verdict for ``voice_server`` to log (Tier 1) or execute (Tier 2).

    op      : alert | sms | inject | log_gate | no_alert
    gate    : G0..G11 | report_state | book_job | "" (transitions aren't logged)
    payload : machine-readable detail serialized into the gate-log row
    """

    op: str
    gate: str = ""
    payload: dict = field(default_factory=dict)


@dataclass
class LeadCapture:
    triage: str | None = None
    urgency: str | None = None
    job_type: str | None = None
    suburb_raw: str | None = None
    suburb_canonical: str | None = None
    area_status: str | None = None
    area_confirmed: bool = False
    offered_two_windows: bool = False
    offered_windows: list = field(default_factory=list)
    agreement: str | None = None
    agreed_window: str | None = None
    anything_else_asked: bool = False
    first_lead_alert_fired: bool = False
    emergency_force_fired: bool = False
    guardrail_flags: list = field(default_factory=list)


# --- regex / keyword banks ---------------------------------------------------

# Emergency keywords, two tiers (spec §7). Tier 2 is the dangerous non-electrical
# set (gas/medical) that an electrician's line attracts and the old electric-only
# list missed. Matched with the accent-robust matcher below, NOT bare regex.
# Bare "sparks"/"gas" are deliberately OMITTED: at edit-distance 1 they false-match
# "sparky" (AU slang for electrician -> would flag almost every booking call) and
# "gasp"/indigestion. The real danger is phrased ("sparks flying", "gas leak").
EMERGENCY_TIER1 = [
    "sparks flying", "sparks coming", "sparks shooting", "sparking", "smoking",
    "smoke", "burning smell", "electric shock", "shocked", "electrocuted",
    "fire", "arcing", "live wire", "water near wiring", "water near switchboard",
    "water near powerpoint", "hot switchboard", "switchboard hot", "mains power",
    "buzzing",
]
EMERGENCY_TIER2 = [
    "gas leak", "smell gas", "can smell gas", "carbon monoxide", "not breathing",
    "chest pain", "collapsed", "unconscious", "bleeding", "fallen power line",
    "downed power line", "flooded switchboard", "oxygen", "medical equipment",
    "no power medical", "having a heart", "stroke", "seizure",
]

_NOT_A_JOB = re.compile(
    r"\b(wrong number|is this (telstra|optus|centrelink|the police|a bank|medicare|the ato)"
    r"|centrelink|sorry wrong|who is this|who\'s this|i have the wrong"
    r"|must have the wrong|dialled the wrong)\b",
    re.I,
)
_ANYTHING_ELSE = re.compile(
    r"anything else|anything i can help|is there anything else|help you with anything",
    re.I,
)
_PRICING = re.compile(
    r"\$\s?\d|\d+\s?(dollars|bucks)|around\s\d|between\s\d+\s?and\s\d+"
    r"|couple of hundred|under a (grand|thousand)|roughly (five hundred|500)",
    re.I,
)
_AFFIRM_EXACT = re.compile(r"\bno worries\b|\bno problem\b|\bnot a problem\b", re.I)
_AFFIRM = re.compile(
    r"\b(yes|yeah|yep|yup|sure|ok|okay|book it|sounds good|that'?s good|good"
    r"|that works|works|works for me|fine|let'?s do it|go ahead|perfect"
    r"|please do|lock it in|confirmed|do it)\b",
    re.I,
)
_NEGATION = re.compile(r"\b(nah|nope|no|not|don'?t|can'?t|cannot|no way)\b", re.I)
_AMENDMENT = re.compile(
    r"\b(but|however|instead|actually|rather|what about|how about)\b", re.I,
)
_DAYS = re.compile(
    r"\b(mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday"
    r"|friday|saturday|sunday|today|tomorrow|arvo|morning)\b",
    re.I,
)
_WINDOW_RANGE = re.compile(r"\d{1,2}\s*(?:am|pm)?\s*(?:to|-|–|until)\s*\d{1,2}", re.I)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _lev(a: str, b: str) -> int:
    """Levenshtein distance (iterative, stdlib-only)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _tok_dist(kw: str, tokens: list[str]) -> bool:
    """Does keyword token `kw` match any text token? Length-aware edit budget:
    longer tokens tolerate more edits, so 'burning'~'boning' (dist 2) fires but
    'gas' won't false-match 'has' (dist 1, too short to allow any)."""
    n = len(kw)
    if n == 0:
        return False
    max_d = 2 if n >= 7 else (1 if n >= 5 else 0)
    for t in tokens:
        if n >= 3 and len(t) >= 3 and (kw in t or t in kw):
            return True
        if _lev(kw, t) <= max_d:
            return True
    return False


def _fuzzy_phrase_in(text: str, phrase: str) -> bool:
    """True if `phrase` appears in `text`, accent/STT-robust. Whole-phrase
    substring first (handles exact + simple inflections like 'smoking'~'smoke');
    else every phrase token must fuzzy-match some text token. Catches
    'boning smell' for keyword 'burning smell' under accent + line noise."""
    if not phrase:
        return False
    if phrase in text:
        return True
    ptoks = [t for t in phrase.split() if t]
    ttoks = [t for t in text.split() if t]
    if not ptoks:
        return False
    if len(ptoks) == 1:
        return _tok_dist(ptoks[0], ttoks)
    return all(_tok_dist(p, ttoks) for p in ptoks)


def detect_emergency(text: str) -> int:
    """0 = none, 1 = electrical danger, 2 = non-electrical danger (gas/medical).
    Tier 2 is checked first: a gas-leak call must get the 000/utility script."""
    t = _norm(text)
    for phrase in EMERGENCY_TIER2:
        if _fuzzy_phrase_in(t, _norm(phrase)):
            return 2
    for phrase in EMERGENCY_TIER1:
        if _fuzzy_phrase_in(t, _norm(phrase)):
            return 1
    return 0


def parse_agreement(text: str) -> str | None:
    """"yes" | "no" | "unsure" | None. Negation-aware and amendment-aware:
    'yeah nah' -> no, 'actually Wednesday' -> unsure, 'Tuesday works' -> yes."""
    if not text:
        return None
    if _AMENDMENT.search(text):
        return "unsure"
    if _AFFIRM_EXACT.search(text):  # "no worries" / "no problem" are yes
        return "yes"
    if _NEGATION.search(text):
        return "no"
    if _AFFIRM.search(text):
        return "yes"
    return None


def detect_windows(text: str) -> tuple[list[str], bool]:
    """Return (distinct day-ish tokens, whether >= 2 windows were offered)."""
    days: list[str] = []
    seen: set[str] = set()
    for d in _DAYS.findall(text):
        key = d.lower()[:3]
        if key not in seen:
            seen.add(key)
            days.append(d.lower())
    ranges = _WINDOW_RANGE.findall(text)
    two = len(days) >= 2 or len(ranges) >= 2
    return days, two


def match_window(text: str, offered: list[str]) -> str | None:
    """Did the caller agree to one of the offered windows? Lenient: an explicit
    'book it / do it / the first one' affirms the most recent offer."""
    if not offered:
        return None
    t = _norm(text)
    if any(p in t for p in ("book it", "do it", "lock it", "the first", "first one", "the second", "second one")):
        return offered[0]
    for w in offered:
        if w[:3] in t:
            return w
    return None


class CallFSM:
    """Pure shadow FSM. Hold one per call; feed it transcript events and tool
    calls; read directives + snapshot. No I/O anywhere."""

    # Per-state turn caps (G10). Light in Tier 1 — the corpus will tune these.
    _BUDGET = {"intent": 3, "gather": 2, "service_area": 2, "offer": 2, "confirm_book": 3}

    def __init__(self, tenant: dict, caller: str):
        self.tenant = tenant or {}
        self.caller = caller or ""
        self.state = CallState.GREET
        self.capture = LeadCapture()
        self.state_path: list[dict] = []
        self.assistant_turns = 0
        self.user_turns = 0
        self.turns_in_state: dict[str, int] = {}
        self.alert_owner_called = False
        self.booked = False
        self.booking_start_iso: str | None = None
        self.booking_end_iso: str | None = None
        self.close_reason: str | None = None

    # --- internal helpers ---

    def _transition(self, new: CallState, trigger: str) -> None:
        if new == self.state:
            return
        self.state_path.append(
            {"from": self.state.value, "to": new.value, "trigger": trigger}
        )
        self.state = new
        self.turns_in_state.setdefault(new.value, 0)

    def _bump_turn(self) -> Directive | None:
        """G10: if the current state has blown its turn budget, fall back."""
        cap = self._BUDGET.get(self.state.value)
        if cap and self.turns_in_state.get(self.state.value, 0) > cap:
            self._transition(CallState.FALLBACK, "G10 budget exhausted")
            return Directive("log_gate", "G10", {"decision": "force", "state": self.state.value})
        return None

    # --- the observe seam (called from _deepgram_to_twilio) ---

    def observe(self, role: str, content: str) -> list[Directive]:
        """One ConversationText event. Runs G0/G2/G3/G6/G7/G9/G10 and returns the
        directives emitted this event. Mutates internal state; does no I/O."""
        directives: list[Directive] = []
        text = content or ""
        if role == "user":
            self.user_turns += 1
            self.turns_in_state[self.state.value] = self.turns_in_state.get(self.state.value, 0) + 1
            directives += self._on_user(text)
            bud = self._bump_turn()
            if bud:
                directives.append(bud)
        elif role == "assistant":
            self.assistant_turns += 1
            directives += self._on_assistant(text)
        return directives

    def _on_user(self, text: str) -> list[Directive]:
        directives: list[Directive] = []
        norm = _norm(text)

        # G0 — wrong number / not a job: close, NEVER alert the owner.
        if _NOT_A_JOB.search(norm):
            self.capture.triage = "not_a_job"
            self._transition(CallState.CLOSE, "G0 not_a_job")
            directives.append(Directive("no_alert", "G0", {"reason": "not a job"}))
            return directives

        # G3 — emergency (two-tier, accent-robust). Safety signal, highest priority.
        tier = detect_emergency(norm)
        if tier:
            self.capture.triage = "emergency"
            self.capture.emergency_force_fired = True
            self._transition(CallState.ESCALATE, f"G3 emergency tier {tier}")
            directives.append(Directive("alert", "G3", {"kind": "emergency", "tier": tier}))
            directives.append(Directive("sms", "G3", {"tier": tier}))
            directives.append(Directive("inject", "G3", {"tier": tier}))
            return directives

        # First real user utterance: leave the greeting.
        if self.state == CallState.GREET:
            self._transition(CallState.INTENT, "first user turn")

        # G7 — agreement (only meaningful once windows are on the table).
        if self.state in (CallState.OFFER, CallState.CONFIRM_BOOK):
            agree = parse_agreement(norm)
            if agree == "yes":
                win = match_window(norm, self.capture.offered_windows)
                if win:
                    self.capture.agreement = "yes"
                    self.capture.agreed_window = win
                    self._transition(CallState.CONFIRM_BOOK, "G7 agreement")
                    directives.append(Directive("log_gate", "G7", {"decision": "pass", "window": win}))
                else:
                    self.capture.agreement = "unsure"
                    directives.append(
                        Directive("log_gate", "G7", {"decision": "fail", "reason": "no matching window"})
                    )
            elif agree == "no":
                self.capture.agreement = "no"
        return directives

    def _on_assistant(self, text: str) -> list[Directive]:
        directives: list[Directive] = []
        norm = _norm(text)

        # G2 — first-lead guarantee: the owner is pinged on turn 1 even if the
        # LLM forgets alert_owner. (Tier 2 executes this; Tier 1 logs it.)
        if (
            self.assistant_turns == 1
            and not self.alert_owner_called
            and not self.capture.first_lead_alert_fired
            and self.capture.triage not in ("emergency", "not_a_job")
        ):
            self.capture.first_lead_alert_fired = True
            directives.append(Directive("alert", "G2", {"kind": "new_lead"}))

        # G6 — two arrival windows offered.
        days, two = detect_windows(norm)
        if two:
            self.capture.offered_two_windows = True
            if days:
                self.capture.offered_windows = days
            if self.state in (CallState.INTENT, CallState.GATHER, CallState.SERVICE_AREA):
                self._transition(CallState.OFFER, "G6 two windows")
            directives.append(Directive("log_gate", "G6", {"decision": "pass", "windows": days}))

        # G9 — "Anything else?" asked exactly once (idempotent flag).
        if _ANYTHING_ELSE.search(norm):
            self.capture.anything_else_asked = True

        # Guardrail (detection only): pricing quoted on air.
        if _PRICING.search(norm):
            self.capture.guardrail_flags.append("pricing_detected")
        return directives

    # --- tool-call observation (called from _run_function) ---

    def note_service_area(self, status: str, canonical: str) -> None:
        """Learn the authoritative area verdict from check_service_area's result."""
        status = status or "unknown"
        self.capture.area_status = status
        if canonical:
            self.capture.suburb_canonical = canonical
        if status == "in_area":
            self.capture.area_confirmed = True
            if self.state in (CallState.GREET, CallState.INTENT, CallState.GATHER, CallState.SERVICE_AREA):
                self._transition(CallState.SERVICE_AREA, "G4 in_area")
        elif status == "out_of_area":
            self.capture.area_confirmed = False
            self._transition(CallState.FALLBACK, "G4 out_of_area")
        # "confirm" / "unknown": stay, the caller must affirm.

    def note_tool_call(self, name: str, args: dict) -> list[Directive]:
        """Observe a tool the LLM called. Returns gate directives (logged)."""
        directives: list[Directive] = []
        args = args or {}
        if name == "alert_owner":
            self.alert_owner_called = True
            if args.get("kind") == "emergency":
                self.capture.emergency_force_fired = True
        elif name == "book_job":
            ok, reason = self.can_book()
            self.booked = True
            self.capture.job_type = args.get("job_type") or self.capture.job_type
            self.capture.agreed_window = args.get("window_text") or self.capture.agreed_window
            self.booking_start_iso = args.get("start_iso")
            self.booking_end_iso = args.get("end_iso")
            if ok:
                self._transition(CallState.BOOK, "book_job")
            directives.append(
                Directive("log_gate", "book_job",
                          {"decision": "pass" if ok else "would_block", "reason": reason})
            )
        elif name == "end_call":
            ok, reason = self.can_close()
            if ok:
                self._transition(CallState.CLOSE, "end_call")
            directives.append(
                Directive("log_gate", "G11",
                          {"decision": "pass" if ok else "would_block", "reason": reason})
            )
        return directives

    # --- gates ---

    def can_book(self) -> tuple[bool, str]:
        """G4 & G5 & G7 (& a basic G8). Full gcal free/busy check is Tier 2."""
        if not self.capture.area_confirmed:
            return False, "area not confirmed (G4)"
        if not self.capture.suburb_canonical:
            return False, "suburb not canonical (G5)"
        if self.capture.agreement != "yes":
            return False, "no agreement (G7)"
        return True, "ok"

    def can_close(self) -> tuple[bool, str]:
        """G2 + G9 + G11. not_a_job / emergency may close without booking."""
        if self.capture.triage == "not_a_job":
            return True, "not a job"
        if self.capture.triage == "emergency":
            return True, "escalated"
        if self.capture.agreement == "yes" and not self.booked:
            return False, "agreed but not booked (G11)"
        if not self.capture.anything_else_asked:
            return False, "anything-else not asked (G9)"
        return True, "ok"

    # --- report_state (the divergence signal) ---

    def _proposed_next(self) -> str:
        s = self.state
        if s in (CallState.GREET, CallState.INTENT, CallState.GATHER):
            return "gather"
        if s == CallState.SERVICE_AREA:
            return "offer"
        if s == CallState.OFFER:
            return "confirm_book"
        if s == CallState.CONFIRM_BOOK:
            return "book" if self.capture.agreement == "yes" else "confirm_book"
        if s == CallState.ESCALATE:
            return "escalate"
        if s == CallState.FALLBACK:
            return "fallback"
        return "close"

    def ingest_report_state(self, args: dict) -> tuple[str, dict]:
        """Absorb the LLM's per-turn claim, compare to observed truth, return
        (tool_result_json, log_fields). Tier 1: result is always approved with
        no instruction (shadow — never steers). divergence_flag is the moat."""
        args = args or {}
        claim = {
            "triage": args.get("triage"),
            "proposed_next": args.get("proposed_next"),
            "urgency": args.get("urgency"),
            "job_type": args.get("job_type"),
            "suburb_raw": args.get("suburb_raw"),
            "area_status": args.get("area_status"),
            "agreement": args.get("agreement"),
            "offered_two_windows": args.get("offered_two_windows"),
            "confidence": args.get("confidence"),
        }
        # Enrich capture with anything the LLM heard that our regex missed.
        if claim["triage"] and not self.capture.triage:
            self.capture.triage = claim["triage"]
        if claim["suburb_raw"] and not self.capture.suburb_raw:
            self.capture.suburb_raw = claim["suburb_raw"]
        if claim["urgency"] and not self.capture.urgency:
            self.capture.urgency = claim["urgency"]
        if claim["job_type"] and not self.capture.job_type:
            self.capture.job_type = claim["job_type"]

        fsm_next = self._proposed_next()
        divergence = 0
        if claim["proposed_next"] and claim["proposed_next"] != fsm_next:
            divergence = 1
        if (
            claim["agreement"]
            and self.capture.agreement
            and claim["agreement"] != self.capture.agreement
        ):
            divergence = 1

        result = {
            "approved": True,  # shadow mode: never steers
            "current_state": self.state.value,
            "next_state": fsm_next,
            "reason": "logged (shadow)",
            "instruction": "",
        }
        log_fields = {
            "decision": "log",
            "after": fsm_next,
            "claim": json.dumps(claim, default=str),
            "divergence": divergence,
            "summary": f"claim_next={claim['proposed_next']} fsm_next={fsm_next}"
                       f" claim_agree={claim['agreement']} fsm_agree={self.capture.agreement}",
        }
        return json.dumps(result), log_fields

    # --- persistence ---

    def snapshot(self) -> dict:
        c = self.capture
        return {
            "state_path": self.state_path,
            "current_state": self.state.value,
            "drop_point": self.state.value,
            "triage": c.triage,
            "urgency": c.urgency,
            "job_type": c.job_type,
            "suburb_raw": c.suburb_raw,
            "suburb_canonical": c.suburb_canonical,
            "area_status": c.area_status,
            "area_confirmed": c.area_confirmed,
            "offered_two_windows": c.offered_two_windows,
            "agreement": c.agreement,
            "booked": self.booked,
            "booking_window": c.agreed_window,
            "booking_start_iso": self.booking_start_iso,
            "booking_end_iso": self.booking_end_iso,
            "first_lead_alert_fired": c.first_lead_alert_fired,
            "emergency_force_fired": c.emergency_force_fired,
            "guardrail_flags": c.guardrail_flags,
            "turns": self.assistant_turns,
            "close_reason": self.close_reason,
        }
