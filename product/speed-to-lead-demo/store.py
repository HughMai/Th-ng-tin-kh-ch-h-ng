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
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                phone          TEXT PRIMARY KEY,
                thread_json    TEXT NOT NULL,
                stage          TEXT,
                triage         TEXT,
                job_type       TEXT,
                urgency        TEXT,
                suburb         TEXT,
                property_type  TEXT,
                booking        TEXT,
                created_at     TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
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


def load_thread(phone: str) -> list[dict]:
    """Return the conversation thread for a phone number, in Anthropic format.

    An empty list means this is a brand-new enquiry.
    """
    with _connect() as db:
        row = db.execute(
            "SELECT thread_json FROM conversations WHERE phone = ?", (phone,)
        ).fetchone()
    return json.loads(row["thread_json"]) if row else []


def save_turn(phone: str, thread: list[dict], turn) -> None:
    """Persist the updated thread and the latest qualified fields.

    `turn` is the engine's AgentTurn for this exchange.
    """
    q = turn.qualified
    with _connect() as db:
        db.execute(
            """
            INSERT INTO conversations
                (phone, thread_json, stage, triage, job_type, urgency,
                 suburb, property_type, booking)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(phone) DO UPDATE SET
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
