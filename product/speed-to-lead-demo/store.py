"""SQLite store for live SMS conversations.

The web demo is stateless — the browser holds the thread and replays it each
turn. Production can't: every inbound SMS is its own HTTP request, so the
conversation has to live somewhere between texts. This is that somewhere.

One table, `conversations`, keyed by the customer's phone number. It holds the
full message thread (the engine replays it every turn) plus the latest
qualified fields — which double as the lead record and the source of the
before/after proof stat. SQLite in WAL mode handles one business number's
traffic comfortably and adds no service to deploy or monitor.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_db_path = "data/leads.db"


def configure(db_path: str) -> None:
    """Set the database file location and create the schema if it's missing."""
    global _db_path
    _db_path = db_path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _connect() as db:
        # Dev-safe migration: a pre-multi-tenant table is keyed by phone alone.
        # Conversation state is a live cache (leads are also logged to JSONL), so
        # recreate rather than write a column migration.
        cols = [r[1] for r in db.execute("PRAGMA table_info(conversations)").fetchall()]
        if cols and "tenant_id" not in cols:
            db.execute("DROP TABLE conversations")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                tenant_id      TEXT NOT NULL,
                phone          TEXT NOT NULL,
                thread_json    TEXT NOT NULL,
                stage          TEXT,
                triage         TEXT,
                job_type       TEXT,
                urgency        TEXT,
                suburb         TEXT,
                property_type  TEXT,
                booking        TEXT,
                created_at     TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (tenant_id, phone)
            )
            """
        )
        # Additive migrations: add later columns to an existing table in place.
        existing = [r[1] for r in db.execute("PRAGMA table_info(conversations)").fetchall()]
        for col, ddl in (
            ("call_count", "INTEGER NOT NULL DEFAULT 0"),
            ("last_call_at", "TEXT"),
            ("human_handling", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if col not in existing:
                db.execute(f"ALTER TABLE conversations ADD COLUMN {col} {ddl}")
        # Twilio retries a webhook (same MessageSid/CallSid) when our response is
        # slow — without dedup that re-runs the engine and texts the customer
        # twice. This table records every event id we've already handled.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_events (
                event_key  TEXT PRIMARY KEY,
                seen_at    TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        # Website "get a demo call" form -> outbound AI callback. Drives the
        # per-phone 24h cap and the global daily cap in /api/demo-call, which
        # spends Twilio credit calling whoever fills the form, so it must be
        # abuse-bounded.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS demo_calls (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                phone         TEXT NOT NULL,
                call_sid      TEXT,
                prospect_name TEXT,
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_demo_calls_phone_time "
            "ON demo_calls (phone, created_at)"
        )
        # Maps a Telegram message we posted -> the lead it's about, so when the
        # owner replies to that message we know which customer to text.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS tg_messages (
                chat_id    INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                tenant_id  TEXT NOT NULL,
                phone      TEXT NOT NULL,
                PRIMARY KEY (chat_id, message_id)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_calls (
                call_sid     TEXT PRIMARY KEY,
                tenant_id    TEXT NOT NULL,
                phone        TEXT NOT NULL,
                stream_sid   TEXT,
                started_at   TEXT NOT NULL DEFAULT (datetime('now')),
                ended_at     TEXT,
                close_reason TEXT,
                updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_transcripts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                call_sid   TEXT NOT NULL,
                tenant_id  TEXT NOT NULL,
                phone      TEXT NOT NULL,
                seq        INTEGER NOT NULL,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(call_sid, seq)
            )
        """
        )
        # Shadow-FSM (Tier 1): structured voice-lead capture + the gate-decision
        # eval corpus. Additive — no change to existing tables. call_sid is NOT a
        # FK to voice_calls on purpose: start_voice_call() skips the voice_calls
        # insert when the caller's number is withheld, and a log row must never
        # fail to write because its parent is missing. SQLite FKs are off here
        # anyway, so this just future-proofs the table.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_call_states (
                call_sid               TEXT PRIMARY KEY,
                tenant_id              TEXT NOT NULL,
                phone                  TEXT NOT NULL,
                state_path             TEXT NOT NULL,
                current_state          TEXT NOT NULL,
                drop_point             TEXT,
                triage                 TEXT,
                urgency                TEXT,
                job_type               TEXT,
                suburb_raw             TEXT,
                suburb_canonical       TEXT,
                area_status            TEXT,
                area_confirmed         INTEGER DEFAULT 0,
                offered_two_windows    INTEGER DEFAULT 0,
                agreement              TEXT,
                booked                 INTEGER DEFAULT 0,
                booking_window         TEXT,
                booking_start_iso      TEXT,
                booking_end_iso        TEXT,
                first_lead_alert_fired INTEGER DEFAULT 0,
                emergency_force_fired  INTEGER DEFAULT 0,
                guardrail_flags        TEXT,
                turns                  INTEGER DEFAULT 0,
                close_reason           TEXT,
                created_at             TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at             TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        # The moat signal lives here: divergence_flag = 1 when the FSM's decision
        # disagreed with what the LLM claimed via report_state. One row per gate
        # decision per call — this is the dataset that proves the FSM earns its
        # keep vs prompt-only logic.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_gate_log (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                call_sid            TEXT NOT NULL,
                ts                  TEXT NOT NULL DEFAULT (datetime('now')),
                gate                TEXT NOT NULL,
                input_summary       TEXT,
                decision            TEXT NOT NULL,
                fsm_state_before    TEXT,
                fsm_state_after     TEXT,
                report_state_claim  TEXT,
                divergence_flag     INTEGER DEFAULT 0
            )
            """
        )


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(_db_path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    try:
        yield db
        db.commit()
    finally:
        db.close()


def load_thread(tenant_id: str, phone: str) -> list[dict]:
    """Return the conversation thread for a (tenant, phone), in Anthropic format.

    An empty list means this is a brand-new enquiry.
    """
    with _connect() as db:
        row = db.execute(
            "SELECT thread_json FROM conversations WHERE tenant_id = ? AND phone = ?",
            (tenant_id, phone),
        ).fetchone()
    return json.loads(row["thread_json"]) if row else []


def list_leads(tenant_id: str) -> list[dict]:
    """All leads for a tenant, newest first, with a one-line last-message
    snippet. For the owner dashboard pipeline view."""
    with _connect() as db:
        rows = db.execute(
            """
            SELECT phone, thread_json, stage, triage, job_type, urgency,
                   suburb, property_type, booking, created_at, updated_at,
                   call_count, last_call_at,
                   (
                       SELECT vt.content
                       FROM voice_transcripts vt
                       WHERE vt.tenant_id = conversations.tenant_id
                         AND vt.phone = conversations.phone
                       ORDER BY vt.created_at DESC, vt.seq DESC
                       LIMIT 1
                   ) AS voice_snippet,
                   (
                       SELECT COUNT(*)
                       FROM voice_transcripts vt
                       WHERE vt.tenant_id = conversations.tenant_id
                         AND vt.phone = conversations.phone
                   ) AS voice_messages
            FROM conversations WHERE tenant_id = ?
            ORDER BY updated_at DESC
            """,
            (tenant_id,),
        ).fetchall()
    leads = []
    for r in rows:
        d = dict(r)
        thread = json.loads(d.pop("thread_json") or "[]")
        last = thread[-1]["content"] if thread else ""
        d["snippet"] = (last or d.pop("voice_snippet") or "")[:80]
        d["messages"] = len(thread)
        d["voice_messages"] = d.get("voice_messages") or 0
        leads.append(d)
    return leads


def report_counts(tenant_id: str, since_iso: str) -> dict:
    """Aggregate counts for leads created since `since_iso` (a UTC
    'YYYY-MM-DD HH:MM:SS' string — same format created_at is stored in, so a
    lexical >= comparison is a real date filter). Powers the owner ROI report:
    how many leads came in, how many would have been missed calls, and what the
    AI booked / escalated / sent to quote in the window. SUM(...) is NULL when no
    rows match, so each is coalesced to 0."""
    with _connect() as db:
        row = db.execute(
            """
            SELECT
                COUNT(*) AS leads,
                COALESCE(SUM(CASE WHEN call_count > 0 THEN 1 ELSE 0 END), 0)
                    AS missed_calls,
                COALESCE(SUM(CASE WHEN booking IS NOT NULL AND booking != ''
                    THEN 1 ELSE 0 END), 0) AS booked,
                COALESCE(SUM(CASE WHEN triage = 'emergency' OR stage = 'escalated'
                    THEN 1 ELSE 0 END), 0) AS emergencies,
                COALESCE(SUM(CASE WHEN triage = 'quote_first' THEN 1 ELSE 0 END), 0)
                    AS quotes
            FROM conversations
            WHERE tenant_id = ? AND created_at >= ?
            """,
            (tenant_id, since_iso),
        ).fetchone()
    return dict(row)


def lead_detail(tenant_id: str, phone: str) -> dict | None:
    """One lead's full record including the parsed conversation thread."""
    with _connect() as db:
        row = db.execute(
            "SELECT * FROM conversations WHERE tenant_id = ? AND phone = ?",
            (tenant_id, phone),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["thread"] = json.loads(d.pop("thread_json") or "[]")
    d["voice_calls"] = list_voice_calls(tenant_id, phone)
    return d


def log_missed_call(tenant_id: str, phone: str) -> None:
    """Record an inbound/forwarded call. A brand-new caller becomes a
    `missed_call` lead so the owner sees it even if they never text back. A
    caller who's already a lead is NOT downgraded — instead we bump them to the
    top (newest `updated_at`) and increment `call_count`, so a repeat caller
    surfaces as a hot lead. `last_call_at` records the most recent ring."""
    with _connect() as db:
        db.execute(
            """
            INSERT INTO conversations
                (tenant_id, phone, thread_json, stage, call_count, last_call_at)
            VALUES (?, ?, '[]', 'missed_call', 1, datetime('now'))
            ON CONFLICT(tenant_id, phone) DO UPDATE SET
                call_count   = call_count + 1,
                last_call_at = datetime('now'),
                updated_at   = datetime('now')
            """,
            (tenant_id, phone),
        )


def start_voice_call(tenant_id: str, phone: str, call_sid: str, stream_sid: str = "") -> None:
    """Create a lead row and a per-call record for a live AI voice call."""
    if not phone:
        return
    with _connect() as db:
        existing_call = (
            db.execute("SELECT 1 FROM voice_calls WHERE call_sid = ?", (call_sid,)).fetchone()
            if call_sid
            else None
        )
        if not existing_call:
            db.execute(
                """
                INSERT INTO conversations
                    (tenant_id, phone, thread_json, stage, call_count, last_call_at)
                VALUES (?, ?, '[]', 'voice_call', 1, datetime('now'))
                ON CONFLICT(tenant_id, phone) DO UPDATE SET
                    call_count   = call_count + 1,
                    last_call_at = datetime('now'),
                    updated_at   = datetime('now'),
                    stage        = CASE
                        WHEN stage IS NULL OR stage = '' OR stage = 'missed_call'
                        THEN 'voice_call'
                        ELSE stage
                    END
                """,
                (tenant_id, phone),
            )
        if call_sid:
            db.execute(
                """
                INSERT INTO voice_calls
                    (call_sid, tenant_id, phone, stream_sid)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(call_sid) DO UPDATE SET
                    tenant_id  = excluded.tenant_id,
                    phone      = excluded.phone,
                    stream_sid = excluded.stream_sid,
                    updated_at = datetime('now')
                """,
                (call_sid, tenant_id, phone, stream_sid),
            )


def finish_voice_call(call_sid: str, close_reason: str) -> None:
    """Mark a live voice call as finished."""
    if not call_sid:
        return
    with _connect() as db:
        db.execute(
            """
            UPDATE voice_calls
            SET ended_at = COALESCE(ended_at, datetime('now')),
                close_reason = ?,
                updated_at = datetime('now')
            WHERE call_sid = ?
            """,
            (close_reason, call_sid),
        )


def append_voice_transcript(
    call_sid: str, tenant_id: str, phone: str, seq: int, role: str, content: str
) -> None:
    """Persist one Deepgram ConversationText event for a voice call."""
    content = (content or "").strip()
    if not call_sid or not role or not content:
        return
    with _connect() as db:
        db.execute(
            """
            INSERT OR IGNORE INTO voice_transcripts
                (call_sid, tenant_id, phone, seq, role, content)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (call_sid, tenant_id, phone, seq, role, content),
        )
        db.execute(
            "UPDATE voice_calls SET updated_at = datetime('now') WHERE call_sid = ?",
            (call_sid,),
        )


def save_voice_state(
    call_sid: str, tenant_id: str, phone: str, snapshot: dict
) -> None:
    """Upsert one shadow-FSM snapshot per call (written once, at call end).

    `snapshot` is CallFSM.snapshot(); list/dict values are JSON-encoded. A missing
    or empty call_sid is skipped, mirroring start_voice_call's guard."""
    if not call_sid:
        return
    def _j(value):
        return json.dumps(value) if isinstance(value, (list, dict)) else (value if value is not None else "")

    cols = [
        "state_path", "current_state", "drop_point", "triage", "urgency",
        "job_type", "suburb_raw", "suburb_canonical", "area_status",
        "area_confirmed", "offered_two_windows", "agreement", "booked",
        "booking_window", "booking_start_iso", "booking_end_iso",
        "first_lead_alert_fired", "emergency_force_fired", "guardrail_flags",
        "turns", "close_reason",
    ]
    vals = [
        _j(snapshot.get("state_path") or []),
        snapshot.get("current_state") or "",
        snapshot.get("drop_point") or "",
        snapshot.get("triage"),
        snapshot.get("urgency"),
        snapshot.get("job_type"),
        snapshot.get("suburb_raw"),
        snapshot.get("suburb_canonical"),
        snapshot.get("area_status"),
        1 if snapshot.get("area_confirmed") else 0,
        1 if snapshot.get("offered_two_windows") else 0,
        snapshot.get("agreement"),
        1 if snapshot.get("booked") else 0,
        snapshot.get("booking_window"),
        snapshot.get("booking_start_iso"),
        snapshot.get("booking_end_iso"),
        1 if snapshot.get("first_lead_alert_fired") else 0,
        1 if snapshot.get("emergency_force_fired") else 0,
        _j(snapshot.get("guardrail_flags") or []),
        int(snapshot.get("turns") or 0),
        snapshot.get("close_reason"),
    ]
    assignments = ", ".join(f"{c} = excluded.{c}" for c in cols)
    with _connect() as db:
        db.execute(
            f"""
            INSERT INTO voice_call_states
                (call_sid, tenant_id, phone, {", ".join(cols)})
            VALUES (?, ?, ?, {", ".join("?" for _ in cols)})
            ON CONFLICT(call_sid) DO UPDATE SET
                {assignments},
                updated_at = datetime('now')
            """,
            (call_sid, tenant_id, phone or "", *vals),
        )


def log_gate(
    call_sid: str,
    gate: str,
    decision: str,
    *,
    input_summary: str = "",
    fsm_state_before: str = "",
    fsm_state_after: str = "",
    report_state_claim: str = "",
    divergence_flag: int = 0,
) -> None:
    """Append one shadow-FSM gate decision to the eval corpus. Best-effort:
    never raises into the call path — the caller wraps DB errors."""
    if not call_sid or not gate:
        return
    try:
        with _connect() as db:
            db.execute(
                """
                INSERT INTO voice_gate_log
                    (call_sid, gate, input_summary, decision,
                     fsm_state_before, fsm_state_after,
                     report_state_claim, divergence_flag)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call_sid, gate, input_summary, decision,
                    fsm_state_before, fsm_state_after,
                    report_state_claim, 1 if divergence_flag else 0,
                ),
            )
    except sqlite3.Error as exc:
        print(f"[voice] log_gate failed: {exc}")


def list_voice_calls(tenant_id: str, phone: str, limit: int = 5) -> list[dict]:
    """Recent voice calls for one lead, each with its transcript rows."""
    with _connect() as db:
        calls = [
            dict(r)
            for r in db.execute(
                """
                SELECT call_sid, tenant_id, phone, stream_sid, started_at, ended_at,
                       close_reason, updated_at
                FROM voice_calls
                WHERE tenant_id = ? AND phone = ?
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (tenant_id, phone, limit),
            ).fetchall()
        ]
        for call in calls:
            call["transcript"] = [
                dict(r)
                for r in db.execute(
                    """
                    SELECT role, content, created_at, seq
                    FROM voice_transcripts
                    WHERE call_sid = ?
                    ORDER BY seq ASC
                    """,
                    (call["call_sid"],),
                ).fetchall()
            ]
    return calls


def save_turn(tenant_id: str, phone: str, thread: list[dict], turn) -> None:
    """Persist the updated thread and the latest qualified fields for a tenant.

    `turn` is the engine's AgentTurn for this exchange.
    """
    q = turn.qualified
    with _connect() as db:
        db.execute(
            """
            INSERT INTO conversations
                (tenant_id, phone, thread_json, stage, triage, job_type, urgency,
                 suburb, property_type, booking)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id, phone) DO UPDATE SET
                thread_json   = excluded.thread_json,
                stage         = excluded.stage,
                triage        = excluded.triage,
                job_type      = excluded.job_type,
                urgency       = excluded.urgency,
                suburb        = excluded.suburb,
                property_type = excluded.property_type,
                booking       = excluded.booking,
                updated_at    = datetime('now')
            """,
            (
                tenant_id,
                phone,
                json.dumps(thread),
                turn.stage,
                turn.triage,
                q.job_type,
                q.urgency,
                q.suburb,
                q.property_type,
                turn.booking,
            ),
        )


def lead_exists(tenant_id: str, phone: str) -> bool:
    """True if we already have any record for this (tenant, caller)."""
    with _connect() as db:
        row = db.execute(
            "SELECT 1 FROM conversations WHERE tenant_id = ? AND phone = ?",
            (tenant_id, phone),
        ).fetchone()
    return row is not None


def append_message(tenant_id: str, phone: str, role: str, content: str) -> None:
    """Append one message to a lead's thread and bump updated_at, without
    touching the qualified fields. Used when a human (the owner) is driving the
    conversation, or to persist a customer text while the AI is paused."""
    with _connect() as db:
        row = db.execute(
            "SELECT thread_json FROM conversations WHERE tenant_id = ? AND phone = ?",
            (tenant_id, phone),
        ).fetchone()
        thread = json.loads(row["thread_json"]) if row else []
        thread.append({"role": role, "content": content})
        db.execute(
            """
            INSERT INTO conversations (tenant_id, phone, thread_json, stage)
            VALUES (?, ?, ?, 'qualifying')
            ON CONFLICT(tenant_id, phone) DO UPDATE SET
                thread_json = excluded.thread_json,
                updated_at  = datetime('now')
            """,
            (tenant_id, phone, json.dumps(thread)),
        )


def set_human_handling(tenant_id: str, phone: str, on: bool) -> None:
    """Pause (or resume) the AI for one lead. While paused, inbound customer
    texts are mirrored to the owner but not auto-answered."""
    with _connect() as db:
        db.execute(
            "UPDATE conversations SET human_handling = ? WHERE tenant_id = ? AND phone = ?",
            (1 if on else 0, tenant_id, phone),
        )


def is_human_handling(tenant_id: str, phone: str) -> bool:
    with _connect() as db:
        row = db.execute(
            "SELECT human_handling FROM conversations WHERE tenant_id = ? AND phone = ?",
            (tenant_id, phone),
        ).fetchone()
    return bool(row and row["human_handling"])


def close_lead(tenant_id: str, phone: str) -> None:
    """Mark a lead closed (owner is done with it) so follow-ups won't fire."""
    with _connect() as db:
        db.execute(
            "UPDATE conversations SET stage = 'closed', human_handling = 0, "
            "updated_at = datetime('now') WHERE tenant_id = ? AND phone = ?",
            (tenant_id, phone),
        )


def mark_seen(event_key: str) -> bool:
    """Record a Twilio event id (e.g. 'sms:<MessageSid>') and return True the
    FIRST time it's seen, False on a duplicate. The insert-or-ignore is atomic,
    so two racing retries can't both come back True — exactly one wins and
    processes the event; the rest are no-ops. This is the idempotency guard that
    stops a slow turn from texting the customer twice."""
    with _connect() as db:
        cur = db.execute(
            "INSERT OR IGNORE INTO processed_events (event_key) VALUES (?)",
            (event_key,),
        )
        return cur.rowcount == 1


def record_demo_call(phone: str, call_sid: str | None = None, prospect_name: str = "") -> None:
    """Log a website 'get a demo call' callback. Feeds the caps below."""
    with _connect() as db:
        db.execute(
            "INSERT INTO demo_calls (phone, call_sid, prospect_name) VALUES (?, ?, ?)",
            (phone, call_sid, prospect_name),
        )


def recent_demo_calls(phone: str, hours: int = 24) -> int:
    """How many demo callbacks `phone` has had in the last `hours`. The per-phone
    cap stops a troll hammering one number or a repeat submitter burning calls."""
    with _connect() as db:
        row = db.execute(
            "SELECT COUNT(*) FROM demo_calls WHERE phone = ? "
            "AND created_at > datetime('now', ?)",
            (phone, f"-{hours} hours"),
        ).fetchone()
    return row[0] if row else 0


def demo_calls_today() -> int:
    """Total demo callbacks since midnight UTC. The global daily cap bounding
    Twilio spend if the public form is ever abused."""
    with _connect() as db:
        row = db.execute(
            "SELECT COUNT(*) FROM demo_calls WHERE date(created_at) = date('now')"
        ).fetchone()
    return row[0] if row else 0


def tg_map(chat_id: int, message_id: int, tenant_id: str, phone: str) -> None:
    """Remember which lead a posted Telegram message is about."""
    with _connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO tg_messages (chat_id, message_id, tenant_id, phone) "
            "VALUES (?, ?, ?, ?)",
            (chat_id, message_id, tenant_id, phone),
        )


def tg_lookup(chat_id: int, message_id: int) -> str | None:
    """The customer phone a replied-to Telegram message belongs to, or None."""
    with _connect() as db:
        row = db.execute(
            "SELECT phone FROM tg_messages WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        ).fetchone()
    return row["phone"] if row else None
