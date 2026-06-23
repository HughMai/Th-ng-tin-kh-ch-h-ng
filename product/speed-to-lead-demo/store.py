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
                   suburb, property_type, booking, created_at, updated_at
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
    """Record an inbound/forwarded call as a lead the moment it lands, so the
    owner sees it even if the caller never texts back. No-op if a conversation
    already exists for this caller — never downgrade an active lead to a missed
    call. When the caller does reply, the normal SMS flow upgrades this row."""
    with _connect() as db:
        db.execute(
            """
            INSERT INTO conversations (tenant_id, phone, thread_json, stage)
            VALUES (?, ?, '[]', 'missed_call')
            ON CONFLICT(tenant_id, phone) DO NOTHING
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
