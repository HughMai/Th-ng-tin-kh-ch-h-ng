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
                   call_count, last_call_at
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
        d["snippet"] = last[:80]
        d["messages"] = len(thread)
        leads.append(d)
    return leads


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
