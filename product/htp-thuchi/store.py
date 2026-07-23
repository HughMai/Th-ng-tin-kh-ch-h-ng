"""SQLite store for Sổ Thu Chi (Hưng Thành Phát — sổ thu chi hằng ngày).

Mirrors product/htp-crm/store.py: a module-global ``_db_path`` set by
``configure()``, a ``_connect()`` contextmanager (WAL + Row factory), and
``CREATE TABLE IF NOT EXISTS`` schema.

ONE table: entries. Tiền khách trả (cọc, thanh toán, công nợ đại lý) is NOT
stored here — it's read live from the CRM over /api/thu at render time, so a
correction made in the CRM shows up immediately and can never drift.

Dates are local Vietnam dates as TEXT 'YYYY-MM-DD' (see ``today_vn()``).
Money is INTEGER VND. Never use SQLite date('now') for day logic (UTC).
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

_db_path = "data/thuchi.db"

_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

# Hình thức thanh toán — same three buckets the CRM's daily_report splits on.
METHODS = ("tien_mat", "chuyen_khoan", "khac")
METHOD_LABELS = {"tien_mat": "Tiền mặt", "chuyen_khoan": "Chuyển khoản", "khac": "Khác"}


def today_vn() -> str:
    """Today's date in Vietnam as 'YYYY-MM-DD' (every day view keys off this)."""
    return datetime.now(_TZ).strftime("%Y-%m-%d")


def now_vn() -> str:
    """Timestamp in Vietnam for created_at/updated_at stamps."""
    return datetime.now(_TZ).strftime("%Y-%m-%d %H:%M:%S")


def configure(db_path: str) -> None:
    """Set the database file location and create the schema if it's missing."""
    global _db_path
    _db_path = db_path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _connect() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS entries (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                kind         TEXT NOT NULL CHECK (kind IN ('thu','chi')),
                amount_vnd   INTEGER NOT NULL CHECK (amount_vnd > 0),
                entry_date   TEXT NOT NULL,
                description  TEXT NOT NULL DEFAULT '',
                method       TEXT NOT NULL DEFAULT 'tien_mat'
                             CHECK (method IN ('tien_mat','chuyen_khoan','khac')),
                created_by   TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL DEFAULT '',
                updated_by   TEXT NOT NULL DEFAULT '',
                updated_at   TEXT NOT NULL DEFAULT ''
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_entries_date ON entries (entry_date)")


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


# ---------------------------------------------------------------- ghi sổ

def add_entry(kind: str, amount_vnd: int, entry_date: str, description: str,
              method: str, who: str) -> int:
    """Ghi một dòng thu hoặc chi. ``who`` is the logged-in person's name — every
    row carries who wrote it so a number that looks wrong two months later has
    someone to ask."""
    stamp = now_vn()
    with _connect() as db:
        cur = db.execute(
            "INSERT INTO entries (kind, amount_vnd, entry_date, description, method, "
            "created_by, created_at, updated_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, '', '')",
            (kind, int(amount_vnd), entry_date, description.strip(), method, who, stamp),
        )
        return cur.lastrowid


def get_entry(entry_id: int) -> dict:
    with _connect() as db:
        r = db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    return dict(r) if r else {}


def update_entry(entry_id: int, kind: str, amount_vnd: int, entry_date: str,
                 description: str, method: str, who: str) -> None:
    """Sửa một dòng đã ghi. created_by stays; updated_by/updated_at record who
    touched it last (the family fixes its own typos, but the book still says who)."""
    with _connect() as db:
        db.execute(
            "UPDATE entries SET kind = ?, amount_vnd = ?, entry_date = ?, description = ?, "
            "method = ?, updated_by = ?, updated_at = ? WHERE id = ?",
            (kind, int(amount_vnd), entry_date, description.strip(), method,
             who, now_vn(), entry_id),
        )


def delete_entry(entry_id: int) -> None:
    with _connect() as db:
        db.execute("DELETE FROM entries WHERE id = ?", (entry_id,))


# ---------------------------------------------------------------- xem sổ

def entries_between(start: str, end: str) -> list:
    """Manual entries in a VN date range, inclusive. Newest first within a day
    so a fresh entry lands at the top of the list the person just typed into."""
    with _connect() as db:
        rows = db.execute(
            "SELECT * FROM entries WHERE entry_date BETWEEN ? AND ? "
            "ORDER BY entry_date DESC, id DESC", (start, end),
        ).fetchall()
    return [dict(r) for r in rows]


def totals(entries: list, crm_thu: list) -> dict:
    """Thu / Chi / Còn lại for a set of rows. ``crm_thu`` are the CRM money-in
    rows (already date-filtered by the caller) — they always count as Thu."""
    thu = sum(e["amount_vnd"] for e in entries if e["kind"] == "thu")
    thu += sum(int(p["amount_vnd"]) for p in crm_thu)
    chi = sum(e["amount_vnd"] for e in entries if e["kind"] == "chi")
    return {"thu": thu, "chi": chi, "con_lai": thu - chi}


def by_method(entries: list, crm_thu: list) -> list:
    """Thu split by hình thức (tiền mặt / chuyển khoản / khác) — the number the
    family reconciles against cash in the drawer at end of day. Mirrors the CRM's
    thu_theo_hinh_thuc. CRM methods are free text, so anything unrecognised
    (including blank) folds into Khác."""
    tally: dict = {}
    for e in entries:
        if e["kind"] != "thu":
            continue
        tally[e["method"]] = tally.get(e["method"], 0) + e["amount_vnd"]
    for p in crm_thu:
        m = _fold_method(p.get("method"))
        tally[m] = tally.get(m, 0) + int(p["amount_vnd"])
    return [{"method": m, "label": METHOD_LABELS[m], "total": t}
            for m, t in sorted(tally.items(), key=lambda kv: kv[1], reverse=True)]


def _fold_method(raw) -> str:
    """CRM `method` is a free-text column typed by hand. Map the shapes the family
    actually enters onto our three buckets; everything else is Khác."""
    s = (raw or "").strip().lower()
    if not s:
        return "khac"
    if "mat" in s or "mặt" in s or s in ("tm", "cash"):
        return "tien_mat"
    if "chuyen" in s or "chuyển" in s or "khoan" in s or "khoản" in s or s in ("ck", "bank"):
        return "chuyen_khoan"
    return "khac"


def days_in_month(month: str) -> list:
    """Every 'YYYY-MM-DD' in a 'YYYY-MM', oldest first."""
    year, mon = int(month[:4]), int(month[5:7])
    nxt_y, nxt_m = (year + 1, 1) if mon == 12 else (year, mon + 1)
    first = datetime(year, mon, 1)
    total = (datetime(nxt_y, nxt_m, 1) - first).days
    return [first.replace(day=d).strftime("%Y-%m-%d") for d in range(1, total + 1)]


def month_rows(month: str, entries: list, crm_thu: list) -> list:
    """One row per day of the month: thu, chi, còn lại. Days with no movement are
    kept so the month reads like a calendar, not a filtered list."""
    per_day: dict = {}
    for e in entries:
        d = per_day.setdefault(e["entry_date"], {"thu": 0, "chi": 0})
        d[e["kind"]] += e["amount_vnd"]
    for p in crm_thu:
        d = per_day.setdefault(p["date"], {"thu": 0, "chi": 0})
        d["thu"] += int(p["amount_vnd"])
    rows = []
    for day in days_in_month(month):
        d = per_day.get(day, {"thu": 0, "chi": 0})
        rows.append({"day": day, "thu": d["thu"], "chi": d["chi"],
                     "con_lai": d["thu"] - d["chi"]})
    return rows
