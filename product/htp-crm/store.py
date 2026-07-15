"""SQLite store for the HTP CRM (Hưng Thành Phát — cửa cuốn / cửa kéo / nhôm kính).

Mirrors product/qr-menu/store.py: a module-global ``_db_path`` set by
``configure()``, a ``_connect()`` contextmanager (WAL + Row factory), and
``CREATE TABLE IF NOT EXISTS`` schema. Single-tenant, family-run.

Six tables: customers, quotes, orders, service_calls, reminders, debt_entries.
All follow-up logic ("chase this quote today", "warranty expiring") is computed
at READ time from dates — no cron, no background jobs.

Dates are local Vietnam dates as TEXT 'YYYY-MM-DD' (see ``today_vn()``).
Money is INTEGER VND. Never use SQLite date('now') for due-date logic (UTC).
"""
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional
from zoneinfo import ZoneInfo

import pricing

_db_path = "data/htp.db"

# ---- follow-up rules (edit here; deliberately not a config UI) --------------
CHASE_FIRST_DAYS = 2    # quote sent, never contacted -> chase after N days
CHASE_SECOND_DAYS = 5   # quote >= N days old -> stronger template ("Lần 2")
RECONTACT_DAYS = 3      # after "Đã nhắn" -> hide for N days, then re-surface
CHECKIN_MONTHS = 6      # post-install courtesy check-in
EXPIRY_WARN_DAYS = 30   # warranty-expiring nudge window
DEBT_OVERDUE_DAYS = 30  # dealer with no payment for N days -> "cần thu"
REVIEW_ASK_DAYS = 14    # ask KH for a Google review within N days of install
KH_DEBT_DAYS = 7        # KH order installed N days ago, still unpaid -> "còn nợ"

_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def today_vn() -> str:
    """Today's date in Vietnam as 'YYYY-MM-DD' (all due-date math keys off this)."""
    return datetime.now(_TZ).strftime("%Y-%m-%d")


def strip_diacritics(s: str) -> str:
    """Lowercase + fold Vietnamese diacritics so 'hung' matches 'Hùng'."""
    s = (s or "").lower().replace("đ", "d")
    nfd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def normalize_phone(s: str) -> str:
    """Digits only ('090 123-4567' -> '0901234567')."""
    return "".join(c for c in (s or "") if c.isdigit())


def bao_gia_so(quote_id: int, date: str = "") -> str:
    """Human-facing báo giá number 'BG-2026-0042' — derived from the row id +
    its year, not stored. Not a gapless legal sequence; just a stable reference
    the customer can quote back. Year from sent_date (fallback: today VN)."""
    year = (date or "")[:4]
    if not (len(year) == 4 and year.isdigit()):
        year = today_vn()[:4]
    return f"BG-{year}-{int(quote_id):04d}"


def configure(db_path: str) -> None:
    """Set the database file location and create the schema if it's missing."""
    global _db_path
    _db_path = db_path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _connect() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                name_search TEXT NOT NULL,
                phone       TEXT NOT NULL DEFAULT '',
                zalo_phone  TEXT,
                type        TEXT NOT NULL DEFAULT 'KH' CHECK (type IN ('KH','DL')),
                source      TEXT NOT NULL DEFAULT 'khac'
                            CHECK (source IN ('gioi_thieu','facebook','google','vang_lai','khac')),
                address     TEXT,
                note        TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_customers_search ON customers(name_search)")
        _migrate_customers_columns(db)
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS quotes (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id       INTEGER NOT NULL REFERENCES customers(id),
                product           TEXT NOT NULL CHECK (product IN ('nhom_kinh','cua_cuon','cua_keo','khac')),
                description       TEXT,
                value_vnd         INTEGER,
                sent_date         TEXT NOT NULL,
                status            TEXT NOT NULL DEFAULT 'sent'
                                  CHECK (status IN ('sent','chasing','won','lost')),
                lost_reason       TEXT,
                last_contact_date TEXT,
                order_id          INTEGER REFERENCES orders(id),
                created_at        TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_quotes_status ON quotes(status, sent_date)")
        _migrate_quotes_columns(db)
        # Multi-item quotes: one báo giá can hold several door-unit line items
        # (matching the original calculator's multi-unit-per-visit model).
        # product's CHECK is fresh here (no legacy quotes.product data to fight),
        # so it can include 'xingfa' from day one; quotes.product stays limited
        # to its original values and maps to 'khac' for mixed/xingfa-only quotes
        # (see recompute_quote_derived_fields).
        # cong_nghe/mau are reused generically per product type — they map to
        # differently-labeled fields per door type (see pricing.DOOR_CONFIG):
        #   cua_cuon/nhom_kinh: cong_nghe = "Công nghệ"/"Phân loại", mau = "Chọn loại/mẫu"
        #   cua_keo: cong_nghe = "Loại" (Có lá/Không lá), mau = "Chọn mẫu" (6zem/8zem/...)
        #   xingfa: cong_nghe is NULL (no first-level select), mau = "Chọn mẫu"
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS quote_items (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                quote_id        INTEGER NOT NULL REFERENCES quotes(id),
                product         TEXT NOT NULL CHECK (product IN ('nhom_kinh','cua_cuon','cua_keo','xingfa')),
                cong_nghe       TEXT,
                mau             TEXT,
                ngang_mm        INTEGER NOT NULL,
                cao_mm          INTEGER NOT NULL,
                mau_sac         TEXT,
                thanh_tien      INTEGER NOT NULL,
                is_manual_price INTEGER NOT NULL DEFAULT 0,
                sort_order      INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_quote_items_quote ON quote_items(quote_id)")
        _migrate_quote_items_columns(db)
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id        INTEGER NOT NULL REFERENCES customers(id),
                quote_id           INTEGER REFERENCES quotes(id),
                product            TEXT NOT NULL CHECK (product IN ('nhom_kinh','cua_cuon','cua_keo','khac')),
                description        TEXT,
                value_vnd          INTEGER,
                install_date       TEXT,
                warranty_months    INTEGER NOT NULL DEFAULT 24,
                checkin_done_at    TEXT,
                expiry_notified_at TEXT,
                review_requested_at TEXT,
                created_at         TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_orders_install ON orders(install_date)")
        _migrate_orders_columns(db)
        # Invoice line items ("hạng mục hóa đơn"): per-order billing rows so a won
        # job keeps its per-door detail (kích thước, màu, giá) instead of collapsing
        # into a lump orders.value_vnd. Snapshot-copied from quote_items on chốt
        # (see snapshot_order_items_from_quote); once items exist, SUM(thanh_tien)
        # is the source of truth for orders.value_vnd (recompute_order_value).
        # ngang_mm/cao_mm are nullable and product allows 'khac' because phụ kiện
        # and generic/lump lines are also invoice rows here, unlike quote_items.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS order_items (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id        INTEGER NOT NULL REFERENCES orders(id),
                product         TEXT NOT NULL DEFAULT 'khac'
                                CHECK (product IN ('nhom_kinh','cua_cuon','cua_keo','xingfa','khac')),
                cong_nghe       TEXT,
                mau             TEXT,
                ngang_mm        INTEGER,
                cao_mm          INTEGER,
                mau_sac         TEXT,
                description     TEXT,
                so_luong        INTEGER NOT NULL DEFAULT 1,
                don_gia         INTEGER,
                thanh_tien      INTEGER NOT NULL,
                is_manual_price INTEGER NOT NULL DEFAULT 0,
                sort_order      INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id)")
        # Payments against an order (cọc + thanh toán). Balance is derived at
        # READ time (orders.value_vnd - SUM(amount_vnd)) — same no-cron pattern
        # as debt_entries. This covers KH (retail) jobs; dealers (ĐL) keep using
        # the running-credit debt_entries ledger instead.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS order_payments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id    INTEGER NOT NULL REFERENCES orders(id),
                kind        TEXT NOT NULL DEFAULT 'thanh_toan'
                            CHECK (kind IN ('coc','thanh_toan')),
                amount_vnd  INTEGER NOT NULL CHECK (amount_vnd > 0),
                pay_date    TEXT NOT NULL,
                method      TEXT,
                note        TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_order_payments_order ON order_payments(order_id)")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS service_calls (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id   INTEGER NOT NULL REFERENCES orders(id),
                call_date  TEXT NOT NULL,
                issue      TEXT NOT NULL,
                resolution TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL REFERENCES customers(id),
                due_date    TEXT NOT NULL,
                note        TEXT NOT NULL,
                done_at     TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS debt_entries (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL REFERENCES customers(id),
                entry_type  TEXT NOT NULL CHECK (entry_type IN ('charge','payment')),
                amount_vnd  INTEGER NOT NULL CHECK (amount_vnd > 0),
                entry_date  TEXT NOT NULL,
                note        TEXT,
                order_id    INTEGER REFERENCES orders(id),
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_debt_customer ON debt_entries(customer_id, entry_date)"
        )
        # Raw Zalo OA webhook events (follows, inbound messages). Kept even after
        # a customer is linked — it's the audit trail for figuring out who's who.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS zalo_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                zalo_user_id  TEXT NOT NULL,
                event_name    TEXT NOT NULL,
                text          TEXT,
                received_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_zalo_events_user ON zalo_events(zalo_user_id)")
        # Care/interaction log ("chăm sóc") — append-only, never edited. Proves
        # follow-up volume happened (Hormozi: "volume negates luck") instead of
        # letting quotes.last_contact_date silently overwrite the history.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS touches (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL REFERENCES customers(id),
                quote_id    INTEGER REFERENCES quotes(id),
                order_id    INTEGER REFERENCES orders(id),
                kind        TEXT NOT NULL CHECK (kind IN
                            ('goi','zalo','zalo_api','trang_thai','ghi_chu','nhac_xong','bao_tri','danh_gia')),
                detail      TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_touches_customer ON touches(customer_id, created_at)")
        # Numbered work list last sent to the Zalo group bot ("1. Cô Lan...").
        # "xong <N>" resolves against the row for today's date + that number.
        # Overwritten on every send (morning digest, manual re-send, "viec")
        # so a reply always resolves against the freshest message in the group.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_digest (
                digest_date TEXT NOT NULL,
                item_no     INTEGER NOT NULL,
                order_id    INTEGER NOT NULL REFERENCES orders(id),
                PRIMARY KEY (digest_date, item_no)
            )
            """
        )
        # Backfill khách-chính stage for rows that predate the split: anyone with
        # an order or a won quote is a converted customer. Idempotent + safe to
        # run every startup — it only promotes lead -> customer, never demotes.
        db.execute(
            "UPDATE customers SET stage='customer' WHERE stage='lead' AND ("
            "id IN (SELECT customer_id FROM quotes WHERE status='won') "
            "OR id IN (SELECT customer_id FROM orders))"
        )
    # Outside configure()'s transaction — the snapshot helpers open their own
    # connections and would deadlock against an uncommitted write lock.
    _backfill_order_items()
    _backfill_order_deposits()
    _recompute_order_values_with_vat()


def _backfill_order_deposits() -> None:
    """Seed a 'coc' payment from the linked quote's đặt cọc for orders that
    predate order_payments, so historical KH jobs don't show a false full
    balance in "Khách lẻ còn nợ". Only touches orders with zero payments —
    idempotent, safe to run every startup (same contract as the item backfill)."""
    with _connect() as db:
        rows = db.execute(
            "SELECT o.id, q.deposit_vnd FROM orders o JOIN quotes q ON q.id = o.quote_id "
            "WHERE q.deposit_vnd IS NOT NULL AND q.deposit_vnd > 0 "
            "AND o.id NOT IN (SELECT order_id FROM order_payments)"
        ).fetchall()
        pending = [dict(r) for r in rows]
    for o in pending:
        add_order_payment(o["id"], "coc", o["deposit_vnd"], note="Cọc từ báo giá (cũ)")


def _backfill_order_items() -> None:
    """Backfill invoice lines for orders that predate order_items: snapshot from
    the linked quote when there is one, else one generic line from the lump
    value_vnd. Idempotent — only touches orders with zero items, safe to run
    every startup (same contract as the stage backfill above)."""
    with _connect() as db:
        rows = db.execute(
            "SELECT id, quote_id, description, value_vnd FROM orders "
            "WHERE id NOT IN (SELECT order_id FROM order_items)"
        ).fetchall()
        pending = [dict(r) for r in rows]
    for o in pending:
        if o["quote_id"]:
            snapshot_order_items_from_quote(o["id"], o["quote_id"])
        elif o["value_vnd"]:
            add_order_item(o["id"], description=o["description"] or "Đơn hàng",
                           thanh_tien=o["value_vnd"])


def _recompute_order_values_with_vat() -> None:
    """Re-derive every order's value_vnd from its (pre-VAT) invoice lines so orders
    created before value_vnd became VAT-inclusive pick up the tax. Idempotent —
    recompute_order_value reads the line items and re-applies the rate each run,
    so this is safe to run on every startup (same contract as the backfills)."""
    with _connect() as db:
        ids = [r["order_id"] for r in
               db.execute("SELECT DISTINCT order_id FROM order_items").fetchall()]
    for oid in ids:
        recompute_order_value(oid)


def _migrate_customers_columns(db: sqlite3.Connection) -> None:
    """Additive columns for Zalo OA API linking + folded address search."""
    existing = {row["name"] for row in db.execute("PRAGMA table_info(customers)")}
    for col, coltype in (
        ("zalo_user_id", "TEXT"),        # Zalo's opaque per-OA user id, from a webhook event
        ("zalo_linked_at", "TEXT"),      # when staff confirmed the link
        ("zalo_last_inbound_at", "TEXT"),  # last time this customer messaged the OA — gates send window
        ("address_search", "TEXT"),      # folded address so search matches "tran phu" -> "Trần Phú"
        ("stage", "TEXT NOT NULL DEFAULT 'lead'"),  # lead (xin báo giá) -> customer (khách chính, on chốt)
        ("email", "TEXT"),               # completes the contact profile (name/phone/email/address)
    ):
        if col not in existing:
            db.execute(f"ALTER TABLE customers ADD COLUMN {col} {coltype}")
    # Backfill folded address for rows created before the column existed.
    for row in db.execute("SELECT id, address FROM customers WHERE address_search IS NULL").fetchall():
        db.execute("UPDATE customers SET address_search = ? WHERE id = ?",
                   (strip_diacritics(row["address"] or ""), row["id"]))


def _migrate_quotes_columns(db: sqlite3.Connection) -> None:
    """Additive columns for the multi-item quote flow. quotes.product/status
    CHECK constraints are left untouched (SQLite can't cheaply ALTER a CHECK);
    mixed/xingfa-only multi-item quotes map quotes.product to 'khac' instead
    (see recompute_quote_derived_fields)."""
    existing = {row["name"] for row in db.execute("PRAGMA table_info(quotes)")}
    for col, coltype in (
        ("accessories", "TEXT"),      # "Motor x2, Remote x1" — unpriced summary, matches original tool
        ("deposit_vnd", "INTEGER"),
        ("install_date", "TEXT"),     # desired install date AT QUOTING TIME — distinct from orders.install_date
        ("note", "TEXT"),             # free-text notes; description is overwritten by recompute_quote_derived_fields
    ):
        if col not in existing:
            db.execute(f"ALTER TABLE quotes ADD COLUMN {col} {coltype}")


def _migrate_orders_columns(db: sqlite3.Connection) -> None:
    """Additive columns for production tracking: a stage the job is at and an
    urgent (làm gấp) flag that pins it to the top of the Tiến độ board."""
    existing = {row["name"] for row in db.execute("PRAGMA table_info(orders)")}
    for col, coltype in (
        ("stage", "TEXT NOT NULL DEFAULT 'cho_san_xuat'"),  # cho_san_xuat|dang_san_xuat|dang_lap|hoan_thanh
        ("urgent", "INTEGER NOT NULL DEFAULT 0"),
        ("urgent_at", "TEXT"),  # when it was flagged gấp (for the notify message)
    ):
        if col not in existing:
            db.execute(f"ALTER TABLE orders ADD COLUMN {col} {coltype}")


def _migrate_quote_items_columns(db: sqlite3.Connection) -> None:
    """Additive: mau_sac (door colour) — captured by the intake wizard. Distinct
    from the overloaded ``mau`` column, which holds the priced model, not colour."""
    existing = {row["name"] for row in db.execute("PRAGMA table_info(quote_items)")}
    if "mau_sac" not in existing:
        db.execute("ALTER TABLE quote_items ADD COLUMN mau_sac TEXT")


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


# ---------------------------------------------------------------- customers

def create_customer(name: str, phone: str, type_: str = "KH", source: str = "khac",
                    address: str = "", note: str = "", zalo_phone: str = "",
                    stage: str = "lead", email: str = "") -> int:
    with _connect() as db:
        cur = db.execute(
            "INSERT INTO customers (name, name_search, phone, zalo_phone, type, source, address, "
            "address_search, note, stage, email) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name.strip(), strip_diacritics(name), normalize_phone(phone),
             normalize_phone(zalo_phone) or None, type_, source,
             address.strip() or None, strip_diacritics(address), note.strip() or None, stage,
             email.strip() or None),
        )
        return cur.lastrowid


def promote_customer(customer_id: int) -> None:
    """Lead -> khách chính. Called on conversion (won quote / order created).
    Idempotent; never demotes."""
    with _connect() as db:
        db.execute(
            "UPDATE customers SET stage='customer', updated_at=datetime('now') "
            "WHERE id=? AND stage!='customer'",
            (customer_id,),
        )


def update_customer(customer_id: int, name: str, phone: str, type_: str, source: str,
                    address: str = "", note: str = "", zalo_phone: str = "",
                    email: str = "") -> bool:
    with _connect() as db:
        cur = db.execute(
            "UPDATE customers SET name=?, name_search=?, phone=?, zalo_phone=?, type=?, source=?, "
            "address=?, address_search=?, note=?, email=?, updated_at=datetime('now') WHERE id=?",
            (name.strip(), strip_diacritics(name), normalize_phone(phone),
             normalize_phone(zalo_phone) or None, type_, source,
             address.strip() or None, strip_diacritics(address), note.strip() or None,
             email.strip() or None, customer_id),
        )
        return cur.rowcount > 0


_DELETE_BLOCKER_LABELS = (("quotes", "báo giá"), ("orders", "đơn hàng"),
                          ("debt_entries", "công nợ"), ("reminders", "nhắc hẹn"))


def delete_customer(customer_id: int) -> Optional[list]:
    """Deletes only if the customer has zero quotes/orders/debt/reminders —
    real business + warranty records that must never silently disappear on a
    misclick. Returns None on success, or a list of blocking record
    descriptions (e.g. ["1 đơn hàng"]) if deletion was refused."""
    with _connect() as db:
        blockers = []
        for table, label in _DELETE_BLOCKER_LABELS:
            n = db.execute(f"SELECT COUNT(*) FROM {table} WHERE customer_id=?", (customer_id,)).fetchone()[0]
            if n:
                blockers.append(f"{n} {label}")
        if blockers:
            return blockers
        db.execute("DELETE FROM customers WHERE id=?", (customer_id,))
    return None


def get_customer(customer_id: int) -> Optional[dict]:
    with _connect() as db:
        r = db.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    return dict(r) if r else None


def list_customers(q: str = "", type_filter: str = "", stage_filter: str = "") -> list:
    """Search by folded name, phone digits, or folded address; optional KH/DL and
    stage (lead|customer) filters. No stage_filter = all (used by the pickers)."""
    sql = "SELECT * FROM customers"
    where, params = [], []
    if q:
        where.append("(name_search LIKE ? OR phone LIKE ? OR address_search LIKE ?)")
        folded = f"%{strip_diacritics(q)}%"
        digits = f"%{normalize_phone(q) or q}%"
        params += [folded, digits, folded]
    if type_filter in ("KH", "DL"):
        where.append("type = ?")
        params.append(type_filter)
    if stage_filter in ("lead", "customer"):
        where.append("stage = ?")
        params.append(stage_filter)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY name_search"
    with _connect() as db:
        rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def find_customer_by_phone(phone: str) -> Optional[dict]:
    p = normalize_phone(phone)
    if not p:
        return None
    with _connect() as db:
        r = db.execute("SELECT * FROM customers WHERE phone = ?", (p,)).fetchone()
    return dict(r) if r else None


def customers_sharing_phone(customer_id: int) -> list:
    """Other customers with the SAME phone as this one — powers the "⚠️ trùng
    SĐT" warning on the customer page (duplicates are warned, never blocked)."""
    with _connect() as db:
        row = db.execute("SELECT phone FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if not row or not row["phone"]:
            return []
        rows = db.execute(
            "SELECT id, name, type FROM customers WHERE phone = ? AND id != ? ORDER BY id",
            (row["phone"], customer_id),
        ).fetchall()
    return [dict(r) for r in rows]


# Every table that carries a customers(id) foreign key — merge reassigns them all.
_CUSTOMER_FK_TABLES = ("quotes", "orders", "reminders", "debt_entries", "touches")


def merge_customers(survivor_id: int, dup_id: int) -> bool:
    """Fold duplicate customer ``dup_id`` into ``survivor_id`` (same person, same
    phone). Every quote / order / reminder / debt entry / touch is reassigned to
    the survivor; the survivor backfills any contact field it's missing from the
    dup (Zalo link, zalo_phone, email, address, note) and is promoted to "khách
    chính" if the dup was one; then the dup row is deleted. One transaction.
    Returns False if either id is unknown or they're the same row."""
    if survivor_id == dup_id:
        return False
    with _connect() as db:
        surv = db.execute("SELECT * FROM customers WHERE id = ?", (survivor_id,)).fetchone()
        dup = db.execute("SELECT * FROM customers WHERE id = ?", (dup_id,)).fetchone()
        if not surv or not dup:
            return False
        surv, dup = dict(surv), dict(dup)
        for tbl in _CUSTOMER_FK_TABLES:  # tbl is from a fixed whitelist, not user input
            db.execute(f"UPDATE {tbl} SET customer_id = ? WHERE customer_id = ?",
                       (survivor_id, dup_id))
        backfill = {col: dup[col] for col in
                    ("zalo_user_id", "zalo_linked_at", "zalo_last_inbound_at",
                     "zalo_phone", "email", "address", "address_search", "note")
                    if not surv.get(col) and dup.get(col)}
        if dup.get("stage") == "customer" and surv.get("stage") != "customer":
            backfill["stage"] = "customer"
        if backfill:
            sets = ", ".join(f"{c} = ?" for c in backfill)
            db.execute(f"UPDATE customers SET {sets}, updated_at = datetime('now') WHERE id = ?",
                       (*backfill.values(), survivor_id))
        db.execute("DELETE FROM customers WHERE id = ?", (dup_id,))
    return True


# ---------------------------------------------------------------- touches (chăm sóc)

def add_touch(customer_id: int, kind: str, detail: str = "", quote_id: Optional[int] = None,
              order_id: Optional[int] = None) -> int:
    """Append-only care/interaction log entry. Called from the mutation points
    listed in SIMPLIFY-PLAN.md — never edited or deleted afterward."""
    with _connect() as db:
        cur = db.execute(
            "INSERT INTO touches (customer_id, quote_id, order_id, kind, detail) VALUES (?, ?, ?, ?, ?)",
            (customer_id, quote_id, order_id, kind, (detail or "").strip() or None),
        )
        return cur.lastrowid


def list_touches(customer_id: int, limit: int = 20) -> list:
    """Newest first, with days_ago (VN calendar) for the customer timeline."""
    with _connect() as db:
        rows = db.execute(
            "SELECT *, CAST(julianday(?) - julianday(substr(created_at, 1, 10)) AS INTEGER) AS days_ago "
            "FROM touches WHERE customer_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (today_vn(), customer_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- quotes

def create_quote(customer_id: int, product: str, description: str = "",
                 value_vnd: Optional[int] = None, sent_date: str = "") -> int:
    with _connect() as db:
        cur = db.execute(
            "INSERT INTO quotes (customer_id, product, description, value_vnd, sent_date) "
            "VALUES (?, ?, ?, ?, ?)",
            (customer_id, product, description.strip() or None, value_vnd,
             sent_date or today_vn()),
        )
        return cur.lastrowid


def get_quote(quote_id: int) -> Optional[dict]:
    with _connect() as db:
        r = db.execute(
            "SELECT q.*, c.name AS customer_name, c.phone, c.zalo_phone, c.type AS customer_type "
            "FROM quotes q JOIN customers c ON c.id = q.customer_id WHERE q.id = ?",
            (quote_id,),
        ).fetchone()
    return dict(r) if r else None


def list_quotes(segment: str = "open") -> list:
    """segment: open (sent+chasing) | won | lost | all.
    'all' (the /bao-gia board) excludes won quotes whose order has reached
    hoàn thành — those move to quotes_archived() instead. 'won'/'lost' stay
    exhaustive since báo cáo/reporting reads those segments directly."""
    base = (
        "SELECT q.*, c.name AS customer_name, c.phone, c.zalo_phone, "
        "CAST(julianday(?) - julianday(q.sent_date) AS INTEGER) AS days_sent, "
        "(SELECT COUNT(*) FROM quote_items qi WHERE qi.quote_id = q.id) AS item_count "
        "FROM quotes q JOIN customers c ON c.id = q.customer_id"
    )
    params: list = [today_vn()]
    if segment == "open":
        base += " WHERE q.status IN ('sent','chasing')"
    elif segment in ("won", "lost"):
        base += " WHERE q.status = ?"
        params.append(segment)
    elif segment == "all":
        base += (" WHERE NOT (q.status = 'won' AND q.order_id IN "
                  "(SELECT id FROM orders WHERE stage = 'hoan_thanh'))")
    base += " ORDER BY q.sent_date DESC, q.id DESC"
    with _connect() as db:
        rows = db.execute(base, params).fetchall()
    return [dict(r) for r in rows]


def quotes_archived() -> list:
    """Won quotes whose order has reached hoàn thành — archived out of the
    default báo giá board, still viewable in a collapsed section."""
    with _connect() as db:
        rows = db.execute(
            "SELECT q.*, c.name AS customer_name, c.phone, c.zalo_phone "
            "FROM quotes q JOIN customers c ON c.id = q.customer_id "
            "JOIN orders o ON o.id = q.order_id "
            "WHERE q.status = 'won' AND o.stage = 'hoan_thanh' "
            "ORDER BY o.updated_at DESC, q.id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def open_quotes_summary() -> dict:
    """Count + total VND sitting in the open pipeline (the header number)."""
    with _connect() as db:
        r = db.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(value_vnd), 0) AS total "
            "FROM quotes WHERE status IN ('sent','chasing')"
        ).fetchone()
    return {"n": r["n"], "total": r["total"]}


def quotes_for_customer(customer_id: int) -> list:
    with _connect() as db:
        rows = db.execute(
            "SELECT * FROM quotes WHERE customer_id = ? ORDER BY sent_date DESC, id DESC",
            (customer_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- multi-item quotes

def create_quote_header(customer_id: int, accessories: str = "", deposit_vnd: Optional[int] = None,
                        install_date: str = "", note: str = "") -> int:
    """Starts a multi-item báo giá: inserted immediately as status='sent',
    product='khac', value_vnd=0. No draft state — items are added afterward
    via add_quote_item(), which keeps value_vnd/product/description in sync."""
    with _connect() as db:
        cur = db.execute(
            "INSERT INTO quotes (customer_id, product, description, value_vnd, sent_date, "
            "accessories, deposit_vnd, install_date, note) VALUES (?, 'khac', NULL, 0, ?, ?, ?, ?, ?)",
            (customer_id, today_vn(), accessories or None, deposit_vnd, install_date or None, note or None),
        )
        return cur.lastrowid


def add_quote_item(quote_id: int, product: str, cong_nghe: str, mau: str,
                   ngang_mm: int, cao_mm: int, thanh_tien: int, is_manual_price: bool,
                   mau_sac: str = "") -> int:
    with _connect() as db:
        next_sort = db.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM quote_items WHERE quote_id = ?",
            (quote_id,),
        ).fetchone()["n"]
        cur = db.execute(
            "INSERT INTO quote_items (quote_id, product, cong_nghe, mau, ngang_mm, cao_mm, "
            "mau_sac, thanh_tien, is_manual_price, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (quote_id, product, cong_nghe or None, mau or None, ngang_mm, cao_mm,
             (mau_sac or None), thanh_tien, 1 if is_manual_price else 0, next_sort),
        )
        item_id = cur.lastrowid
    recompute_quote_derived_fields(quote_id)
    return item_id


def get_quote_item(item_id: int) -> Optional[dict]:
    with _connect() as db:
        r = db.execute("SELECT * FROM quote_items WHERE id = ?", (item_id,)).fetchone()
    return dict(r) if r else None


def update_quote_item(item_id: int, product: str, cong_nghe: str, mau: str,
                      ngang_mm: int, cao_mm: int, thanh_tien: int, is_manual_price: bool) -> None:
    """Edit an already-added bộ cửa (wrong kích thước, đổi màu…). Leaves
    mau_sac untouched — this form doesn't collect it, same as add_quote_item's
    caller in quote_item_new."""
    with _connect() as db:
        r = db.execute("SELECT quote_id FROM quote_items WHERE id = ?", (item_id,)).fetchone()
        if not r:
            return
        quote_id = r["quote_id"]
        db.execute(
            "UPDATE quote_items SET product=?, cong_nghe=?, mau=?, ngang_mm=?, cao_mm=?, "
            "thanh_tien=?, is_manual_price=? WHERE id=?",
            (product, cong_nghe or None, mau or None, ngang_mm, cao_mm, thanh_tien,
             1 if is_manual_price else 0, item_id),
        )
    recompute_quote_derived_fields(quote_id)


def delete_quote_item(item_id: int) -> None:
    with _connect() as db:
        r = db.execute("SELECT quote_id FROM quote_items WHERE id = ?", (item_id,)).fetchone()
        if not r:
            return
        quote_id = r["quote_id"]
        db.execute("DELETE FROM quote_items WHERE id = ?", (item_id,))
    recompute_quote_derived_fields(quote_id)


def quote_items_for(quote_id: int) -> list:
    with _connect() as db:
        rows = db.execute(
            "SELECT * FROM quote_items WHERE quote_id = ? ORDER BY sort_order", (quote_id,)
        ).fetchall()
    return [dict(r) for r in rows]


_SINGLE_TYPE_PRODUCTS = {"nhom_kinh", "cua_cuon", "cua_keo"}


def recompute_quote_derived_fields(quote_id: int) -> None:
    """Single source of truth for keeping quotes.value_vnd/product/description
    in sync with quote_items, called after every add/delete. Nowhere else
    derives these fields. value_vnd is the pre-VAT Tổng cộng: door line totals
    plus priced phụ kiện (motors tiered per Cửa Cuốn area + locks/battery)."""
    items = quote_items_for(quote_id)
    with _connect() as db:
        r = db.execute(
            "SELECT q.accessories, c.type AS customer_type FROM quotes q "
            "JOIN customers c ON c.id = q.customer_id WHERE q.id = ?",
            (quote_id,),
        ).fetchone()
    accessories = (r["accessories"] if r else "") or ""
    customer_type = (r["customer_type"] if r else "KH")
    door_total = sum(i["thanh_tien"] for i in items)
    value_vnd = door_total + pricing.calc_phukien(accessories, items, customer_type)
    distinct = {i["product"] for i in items}
    if len(distinct) == 1 and next(iter(distinct)) in _SINGLE_TYPE_PRODUCTS:
        product = next(iter(distinct))
    else:
        product = "khac"  # covers: 0 items, xingfa-only, and any mixed-type case
    description = f"{len(items)} hạng mục" if items else None
    with _connect() as db:
        db.execute(
            "UPDATE quotes SET value_vnd = ?, product = ?, description = ?, updated_at = datetime('now') "
            "WHERE id = ?",
            (value_vnd, product, description, quote_id),
        )


def mark_quote_contacted(quote_id: int) -> bool:
    """'Đã nhắn' — stamp today; a fresh 'sent' quote moves to 'chasing'."""
    with _connect() as db:
        cur = db.execute(
            "UPDATE quotes SET last_contact_date = ?, "
            "status = CASE WHEN status = 'sent' THEN 'chasing' ELSE status END, "
            "updated_at = datetime('now') "
            "WHERE id = ? AND status IN ('sent','chasing')",
            (today_vn(), quote_id),
        )
        ok = cur.rowcount > 0
        row = db.execute("SELECT customer_id FROM quotes WHERE id = ?", (quote_id,)).fetchone() if ok else None
    if ok and row:
        add_touch(row["customer_id"], "zalo", f"Đã nhắn báo giá #{quote_id}", quote_id=quote_id)
    return ok


def set_quote_status(quote_id: int, status: str, lost_reason: str = "") -> bool:
    with _connect() as db:
        old = db.execute("SELECT customer_id, status FROM quotes WHERE id = ?", (quote_id,)).fetchone()
        cur = db.execute(
            "UPDATE quotes SET status = ?, lost_reason = ?, updated_at = datetime('now') WHERE id = ?",
            (status, lost_reason or None, quote_id),
        )
    ok = cur.rowcount > 0
    if status == "won" and old:
        promote_customer(old["customer_id"])  # a won quote ⇒ khách chính
    if ok and old:
        detail = f"{old['status']} → {status}" + (f" ({lost_reason})" if status == "lost" and lost_reason else "")
        add_touch(old["customer_id"], "trang_thai", detail, quote_id=quote_id)
    return ok


def update_quote_notes(quote_id: int, install_date: str, note: str) -> bool:
    """Ngày lắp đặt dự kiến + ghi chú are adjustable any time, same as đặt cọc."""
    with _connect() as db:
        cur = db.execute(
            "UPDATE quotes SET install_date=?, note=?, updated_at=datetime('now') WHERE id=?",
            (install_date or None, note.strip() or None, quote_id),
        )
        return cur.rowcount > 0


def update_quote_accessories(quote_id: int, accessories: str) -> None:
    """Phụ kiện selection is adjustable any time after creation, same as
    đặt cọc/ghi chú. Feeds value_vnd via calc_phukien, so recompute after."""
    with _connect() as db:
        db.execute(
            "UPDATE quotes SET accessories=?, updated_at=datetime('now') WHERE id=?",
            (accessories or None, quote_id),
        )
    recompute_quote_derived_fields(quote_id)


def set_quote_deposit(quote_id: int, deposit_vnd: Optional[int]) -> bool:
    """Đặt cọc is adjustable any time after the quote is created."""
    with _connect() as db:
        cur = db.execute(
            "UPDATE quotes SET deposit_vnd = ?, updated_at = datetime('now') WHERE id = ?",
            (deposit_vnd, quote_id),
        )
        return cur.rowcount > 0


def link_quote_order(quote_id: int, order_id: int) -> bool:
    with _connect() as db:
        cur = db.execute(
            "UPDATE quotes SET order_id = ?, updated_at = datetime('now') WHERE id = ?",
            (order_id, quote_id),
        )
        return cur.rowcount > 0


def quotes_to_chase(today: str = "") -> list:
    """Hôm nay section A. Never-contacted quotes surface CHASE_FIRST_DAYS after
    sent_date; after a nudge they hide for RECONTACT_DAYS then re-surface —
    forever, until won/lost. >= CHASE_SECOND_DAYS old -> nudge_level 2."""
    today = today or today_vn()
    with _connect() as db:
        rows = db.execute(
            """
            SELECT q.*, c.name AS customer_name, c.phone, c.zalo_phone,
                   CAST(julianday(?) - julianday(q.sent_date) AS INTEGER) AS days_sent,
                   CASE WHEN julianday(?) - julianday(q.sent_date) >= ? THEN 2 ELSE 1 END AS nudge_level
            FROM quotes q JOIN customers c ON c.id = q.customer_id
            WHERE q.status IN ('sent','chasing')
              AND julianday(?) - julianday(COALESCE(q.last_contact_date, q.sent_date))
                  >= CASE WHEN q.last_contact_date IS NULL THEN ? ELSE ? END
            ORDER BY q.value_vnd DESC
            """,
            (today, today, CHASE_SECOND_DAYS, today, CHASE_FIRST_DAYS, RECONTACT_DAYS),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- orders

def create_order(customer_id: int, product: str, description: str = "",
                 value_vnd: Optional[int] = None, install_date: str = "",
                 warranty_months: int = 24, quote_id: Optional[int] = None) -> int:
    with _connect() as db:
        cur = db.execute(
            "INSERT INTO orders (customer_id, quote_id, product, description, value_vnd, "
            "install_date, warranty_months) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (customer_id, quote_id, product, description.strip() or None, value_vnd,
             install_date or None, warranty_months),
        )
        order_id = cur.lastrowid
    if value_vnd:
        # Quick orders (no quote) still get one generic invoice line so every
        # đơn hàng is billable. The chốt path snapshot below DELETE-then-replaces
        # this with the quote's real lines — no duplicates.
        add_order_item(order_id, description=description.strip() or "Đơn hàng",
                       thanh_tien=value_vnd)
    promote_customer(customer_id)  # an order ⇒ khách chính (covers seed + chốt paths)
    return order_id


def create_order_from_quote(quote_id: int) -> Optional[int]:
    """Turn a won quote into a production job (đơn hàng) and link them. Carries
    the quote's product/value/desired install date; stage starts 'cho_san_xuat'.
    Idempotent-ish: if the quote already has an order, returns that order id."""
    q = get_quote(quote_id)
    if not q:
        return None
    if q.get("order_id"):
        return q["order_id"]
    product = q["product"] if q["product"] in ("nhom_kinh", "cua_cuon", "cua_keo") else "khac"
    oid = create_order(q["customer_id"], product, q.get("description") or "",
                       q.get("value_vnd"), q.get("install_date") or "", 24, quote_id)
    link_quote_order(quote_id, oid)
    snapshot_order_items_from_quote(oid, quote_id)
    if q.get("deposit_vnd"):  # carry the báo giá's đặt cọc as the order's first payment
        add_order_payment(oid, "coc", q["deposit_vnd"], note="Cọc từ báo giá")
    return oid


# ------------------------------------------------- order items (hạng mục hóa đơn)

def order_items_for(order_id: int) -> list:
    with _connect() as db:
        rows = db.execute(
            "SELECT * FROM order_items WHERE order_id = ? ORDER BY sort_order", (order_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def add_order_item(order_id: int, description: str = "", thanh_tien: int = 0, so_luong: int = 1,
                   don_gia: Optional[int] = None, product: str = "khac", cong_nghe: str = "",
                   mau: str = "", ngang_mm: Optional[int] = None, cao_mm: Optional[int] = None,
                   mau_sac: str = "", is_manual_price: bool = True) -> int:
    with _connect() as db:
        next_sort = db.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM order_items WHERE order_id = ?",
            (order_id,),
        ).fetchone()["n"]
        cur = db.execute(
            "INSERT INTO order_items (order_id, product, cong_nghe, mau, ngang_mm, cao_mm, "
            "mau_sac, description, so_luong, don_gia, thanh_tien, is_manual_price, sort_order) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (order_id, product, cong_nghe or None, mau or None, ngang_mm, cao_mm,
             mau_sac or None, (description or "").strip() or None, so_luong, don_gia,
             thanh_tien, 1 if is_manual_price else 0, next_sort),
        )
        item_id = cur.lastrowid
    recompute_order_value(order_id)
    return item_id


def update_order_item(item_id: int, description: str, so_luong: int, thanh_tien: int,
                      don_gia: Optional[int] = None) -> bool:
    """Invoice-line edit: label/qty/price only. Door spec columns (product,
    kích thước, màu) stay as snapshotted — respec means re-quoting."""
    with _connect() as db:
        r = db.execute("SELECT order_id FROM order_items WHERE id = ?", (item_id,)).fetchone()
        if not r:
            return False
        order_id = r["order_id"]
        db.execute(
            "UPDATE order_items SET description=?, so_luong=?, don_gia=?, thanh_tien=? WHERE id=?",
            ((description or "").strip() or None, so_luong, don_gia, thanh_tien, item_id),
        )
    recompute_order_value(order_id)
    return True


def delete_order_item(item_id: int) -> None:
    with _connect() as db:
        r = db.execute("SELECT order_id FROM order_items WHERE id = ?", (item_id,)).fetchone()
        if not r:
            return
        order_id = r["order_id"]
        db.execute("DELETE FROM order_items WHERE id = ?", (item_id,))
    recompute_order_value(order_id)


# VAT applied to order totals. Quotes stay pre-VAT (recompute_quote_derived_fields);
# an order's value_vnd is the VAT-inclusive amount the customer actually owes — it
# matches the báo giá's TỔNG CỘNG and the hóa đơn's Tổng tiền, so the đơn hàng price
# and còn nợ (both derived from value_vnd) already include VAT.
VAT_RATE = 0.1


def recompute_order_value(order_id: int) -> None:
    """Once an order has invoice lines, value_vnd = SUM(thanh_tien) + VAT — the
    VAT-inclusive total the customer owes (mirrors the báo giá TỔNG CỘNG and the
    hóa đơn Tổng tiền; per-line thanh_tien stay pre-VAT). Zero items =
    legacy/lump order — leave value_vnd untouched."""
    with _connect() as db:
        r = db.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(thanh_tien), 0) AS total "
            "FROM order_items WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        if r["n"]:
            subtotal = r["total"]
            value = subtotal + round(subtotal * VAT_RATE)
            db.execute(
                "UPDATE orders SET value_vnd = ?, updated_at = datetime('now') WHERE id = ?",
                (value, order_id),
            )


def snapshot_order_items_from_quote(order_id: int, quote_id: int) -> None:
    """Copy the won quote's line items (+ priced phụ kiện) onto the order as
    invoice lines, so the đơn hàng keeps per-door detail even if the quote is
    edited later. DELETE-then-insert = idempotent (re-chốt, startup backfill).
    Quick quotes (no items, lump value_vnd) become one generic line."""
    q = get_quote(quote_id)
    if not q:
        return
    items = quote_items_for(quote_id)
    phukien = pricing.phukien_line_items(q.get("accessories") or "", items,
                                         q.get("customer_type") or "KH")
    with _connect() as db:
        db.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
        sort = 0
        for it in items:
            db.execute(
                "INSERT INTO order_items (order_id, product, cong_nghe, mau, ngang_mm, cao_mm, "
                "mau_sac, so_luong, don_gia, thanh_tien, is_manual_price, sort_order) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
                (order_id, it["product"], it["cong_nghe"], it["mau"], it["ngang_mm"],
                 it["cao_mm"], it["mau_sac"], it["thanh_tien"], it["thanh_tien"],
                 it["is_manual_price"], sort),
            )
            sort += 1
        for p in phukien:
            db.execute(
                "INSERT INTO order_items (order_id, product, description, so_luong, don_gia, "
                "thanh_tien, sort_order) VALUES (?, 'khac', ?, ?, ?, ?, ?)",
                (order_id, p["name"], p["qty"], p["unit_cost"], p["total"], sort),
            )
            sort += 1
        if not items and q.get("value_vnd"):
            db.execute(
                "INSERT INTO order_items (order_id, product, description, so_luong, don_gia, "
                "thanh_tien, is_manual_price, sort_order) VALUES (?, 'khac', ?, 1, ?, ?, 1, 0)",
                (order_id, (q.get("description") or "").strip() or "Đơn hàng",
                 q["value_vnd"], q["value_vnd"]),
            )
    recompute_order_value(order_id)


# ------------------------------------------------- order payments (thanh toán)

def add_order_payment(order_id: int, kind: str = "thanh_toan", amount_vnd: int = 0,
                      pay_date: str = "", method: str = "", note: str = "") -> int:
    """Record a cọc/thanh toán against an order. Balance is derived at read time
    (value_vnd - SUM payments), same contract as dealer_balance()."""
    with _connect() as db:
        cur = db.execute(
            "INSERT INTO order_payments (order_id, kind, amount_vnd, pay_date, method, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (order_id, kind, amount_vnd, pay_date or today_vn(),
             (method or "").strip() or None, (note or "").strip() or None),
        )
        return cur.lastrowid


def order_payments_for(order_id: int) -> list:
    with _connect() as db:
        rows = db.execute(
            "SELECT * FROM order_payments WHERE order_id = ? ORDER BY pay_date, id",
            (order_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def order_paid(order_id: int) -> int:
    with _connect() as db:
        r = db.execute(
            "SELECT COALESCE(SUM(amount_vnd), 0) AS paid FROM order_payments WHERE order_id = ?",
            (order_id,),
        ).fetchone()
    return r["paid"]


def delete_order_payment(payment_id: int) -> bool:
    """Remove a cọc/thanh toán entry — reopens a KH order that was marked paid
    by mistake. Balance is derived, so deleting the payment restores the
    outstanding balance (the reverse of add_order_payment)."""
    with _connect() as db:
        cur = db.execute("DELETE FROM order_payments WHERE id = ?", (payment_id,))
        return cur.rowcount > 0


_ORDER_STAGES = ("cho_san_xuat", "dang_san_xuat", "dang_lap", "hoan_thanh")


def set_order_stage(order_id: int, stage: str) -> bool:
    if stage not in _ORDER_STAGES:
        raise ValueError(f"unknown stage: {stage}")
    with _connect() as db:
        old = db.execute("SELECT customer_id, stage FROM orders WHERE id = ?", (order_id,)).fetchone()
        cur = db.execute(
            "UPDATE orders SET stage = ?, updated_at = datetime('now') WHERE id = ?",
            (stage, order_id),
        )
    ok = cur.rowcount > 0
    if ok and old:
        add_touch(old["customer_id"], "trang_thai", f"{old['stage']} → {stage}", order_id=order_id)
    return ok


def set_order_urgent(order_id: int, urgent: bool) -> bool:
    with _connect() as db:
        cur = db.execute(
            "UPDATE orders SET urgent = ?, "
            "urgent_at = CASE WHEN ? THEN datetime('now') ELSE NULL END, "
            "updated_at = datetime('now') WHERE id = ?",
            (1 if urgent else 0, 1 if urgent else 0, order_id),
        )
        return cur.rowcount > 0


_ORDER_SELECT = (
    "SELECT o.*, c.name AS customer_name, c.phone, c.zalo_phone, c.address, c.email, c.type AS customer_type, "
    "CASE WHEN o.install_date IS NOT NULL "
    "     THEN date(o.install_date, '+' || o.warranty_months || ' months') END AS expiry_date, "
    "(SELECT COALESCE(SUM(amount_vnd), 0) FROM order_payments p WHERE p.order_id = o.id) AS paid_vnd, "
    "COALESCE(o.value_vnd, 0) - "
    "  (SELECT COALESCE(SUM(amount_vnd), 0) FROM order_payments p WHERE p.order_id = o.id) AS balance_vnd "
    "FROM orders o JOIN customers c ON c.id = o.customer_id"
)


def get_order(order_id: int) -> Optional[dict]:
    with _connect() as db:
        r = db.execute(_ORDER_SELECT + " WHERE o.id = ?", (order_id,)).fetchone()
    return dict(r) if r else None


def delete_order(order_id: int) -> bool:
    """Xóa đơn hàng — purges everything that belongs to the order (hạng mục,
    thanh toán, sửa chữa, digest entries) and marks the originating báo giá
    'Mất' (lost, unlinked) so it drops out of the active pipeline instead of
    re-surfacing in 'Đã gửi' with chase nudges. Drag the Mất card back to re-open
    if the job comes back. Keeps the customer's lịch sử chăm sóc (touches) and
    any công nợ ledger entries — only detaches their dangling order_id reference."""
    with _connect() as db:
        o = db.execute("SELECT quote_id FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not o:
            return False
        if o["quote_id"]:
            db.execute(
                "UPDATE quotes SET status='lost', order_id=NULL, updated_at=datetime('now') WHERE id = ?",
                (o["quote_id"],),
            )
        db.execute("UPDATE touches SET order_id = NULL WHERE order_id = ?", (order_id,))
        db.execute("UPDATE debt_entries SET order_id = NULL WHERE order_id = ?", (order_id,))
        db.execute("DELETE FROM bot_digest WHERE order_id = ?", (order_id,))
        db.execute("DELETE FROM service_calls WHERE order_id = ?", (order_id,))
        db.execute("DELETE FROM order_payments WHERE order_id = ?", (order_id,))
        db.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
        cur = db.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    return cur.rowcount > 0


def orders_active() -> list:
    """Tiến độ (production) — orders not yet hoàn thành. Urgent (gấp) pinned to
    the top, then oldest chốt first (FIFO)."""
    with _connect() as db:
        rows = db.execute(
            _ORDER_SELECT + " WHERE o.stage != 'hoan_thanh' "
            "ORDER BY o.urgent DESC, o.created_at ASC, o.id ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def orders_completed() -> list:
    """Đã hoàn thành — archived out of the default Đơn hàng view, still fully
    viewable on demand (newest first)."""
    with _connect() as db:
        rows = db.execute(
            _ORDER_SELECT + " WHERE o.stage = 'hoan_thanh' ORDER BY o.updated_at DESC, o.id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def orders_for_customer(customer_id: int) -> list:
    with _connect() as db:
        rows = db.execute(
            _ORDER_SELECT + " WHERE o.customer_id = ? ORDER BY o.id DESC", (customer_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def set_order_install(order_id: int, install_date: str) -> bool:
    with _connect() as db:
        cur = db.execute(
            "UPDATE orders SET install_date = ?, updated_at = datetime('now') WHERE id = ?",
            (install_date or None, order_id),
        )
        return cur.rowcount > 0


# --------------------------------------------------- Zalo group bot digest

def orders_for_digest(today: str = "") -> list:
    """Unfinished orders worth putting in the Zalo group work list: overdue,
    due today, due tomorrow, or flagged urgent regardless of date. Ordered
    overdue-first so the most pressing jobs lead the numbered list."""
    today = today or today_vn()
    with _connect() as db:
        rows = db.execute(
            _ORDER_SELECT + " WHERE o.stage != 'hoan_thanh' "
            "AND (o.urgent = 1 OR (o.install_date IS NOT NULL AND o.install_date <= date(?, '+1 day'))) "
            "ORDER BY "
            "CASE WHEN o.install_date IS NOT NULL AND o.install_date < ? THEN 0 "
            "     WHEN o.install_date = ? THEN 1 "
            "     WHEN o.install_date IS NOT NULL AND o.install_date = date(?, '+1 day') THEN 2 "
            "     ELSE 3 END, "
            "o.urgent DESC, o.install_date ASC, o.id ASC",
            (today, today, today, today),
        ).fetchall()
    return [dict(r) for r in rows]


def save_digest(digest_date: str, order_ids: list) -> None:
    """Overwrite today's numbered list — every send (morning, manual, "viec")
    replaces it so "xong <N>" always resolves against the latest message."""
    with _connect() as db:
        db.execute("DELETE FROM bot_digest WHERE digest_date = ?", (digest_date,))
        db.executemany(
            "INSERT INTO bot_digest (digest_date, item_no, order_id) VALUES (?, ?, ?)",
            [(digest_date, i, order_id) for i, order_id in enumerate(order_ids, 1)],
        )


def get_digest_order(digest_date: str, item_no: int) -> Optional[int]:
    with _connect() as db:
        row = db.execute(
            "SELECT order_id FROM bot_digest WHERE digest_date = ? AND item_no = ?",
            (digest_date, item_no),
        ).fetchone()
    return row["order_id"] if row else None


_ORDER_FLAGS = {"checkin_done_at", "expiry_notified_at", "review_requested_at"}
# Only these two flags get a touch entry (SIMPLIFY-PLAN.md instrumentation
# list) — expiry_notified_at ("da-nhan-bh") isn't in scope.
_FLAG_TOUCH_KIND = {"checkin_done_at": "bao_tri", "review_requested_at": "danh_gia"}


def set_order_flag(order_id: int, flag: str) -> bool:
    """Dismiss a Today nudge (✔ buttons). ``flag`` must be a known column."""
    if flag not in _ORDER_FLAGS:
        raise ValueError(f"unknown order flag: {flag}")
    with _connect() as db:
        cur = db.execute(
            f"UPDATE orders SET {flag} = datetime('now'), updated_at = datetime('now') WHERE id = ?",
            (order_id,),
        )
        ok = cur.rowcount > 0
        row = None
        if ok and flag in _FLAG_TOUCH_KIND:
            row = db.execute("SELECT customer_id FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row:
        add_touch(row["customer_id"], _FLAG_TOUCH_KIND[flag], "", order_id=order_id)
    return ok


def orders_checkin_due(today: str = "") -> list:
    """Hôm nay section B: installed >= CHECKIN_MONTHS ago, warranty still live."""
    today = today or today_vn()
    with _connect() as db:
        rows = db.execute(
            _ORDER_SELECT + """
            WHERE o.install_date IS NOT NULL AND o.checkin_done_at IS NULL
              AND date(o.install_date, '+' || ? || ' months') <= ?
              AND date(o.install_date, '+' || o.warranty_months || ' months') >= ?
            ORDER BY o.install_date
            """,
            (CHECKIN_MONTHS, today, today),
        ).fetchall()
    return [dict(r) for r in rows]


def orders_expiring(today: str = "") -> list:
    """Hôm nay section C: warranty expiry within the next EXPIRY_WARN_DAYS."""
    today = today or today_vn()
    with _connect() as db:
        rows = db.execute(
            _ORDER_SELECT + """
            WHERE o.install_date IS NOT NULL AND o.expiry_notified_at IS NULL
              AND date(o.install_date, '+' || o.warranty_months || ' months')
                  BETWEEN ? AND date(?, '+' || ? || ' days')
            ORDER BY expiry_date
            """,
            (today, today, EXPIRY_WARN_DAYS),
        ).fetchall()
    return [dict(r) for r in rows]


def orders_review_due(today: str = "") -> list:
    """Hôm nay section F: KH order installed within the last REVIEW_ASK_DAYS."""
    today = today or today_vn()
    with _connect() as db:
        rows = db.execute(
            _ORDER_SELECT + """
            WHERE o.install_date IS NOT NULL AND o.review_requested_at IS NULL
              AND c.type = 'KH'
              AND julianday(?) - julianday(o.install_date) BETWEEN 0 AND ?
            ORDER BY o.install_date DESC
            """,
            (today, REVIEW_ASK_DAYS),
        ).fetchall()
    return [dict(r) for r in rows]


def orders_debt_due(today: str = "") -> list:
    """Hôm nay section G: KH orders installed >= KH_DEBT_DAYS ago that still
    carry a balance (value_vnd - payments > 0). Auto-clears once paid in full —
    no dismiss flag. Dealers (ĐL) are handled by the công nợ ledger, not here."""
    today = today or today_vn()
    with _connect() as db:
        rows = db.execute(
            _ORDER_SELECT + """
            WHERE c.type = 'KH' AND o.install_date IS NOT NULL
              AND julianday(?) - julianday(o.install_date) >= ?
              AND COALESCE(o.value_vnd, 0) - (
                    SELECT COALESCE(SUM(amount_vnd), 0) FROM order_payments p WHERE p.order_id = o.id
                  ) > 0
            ORDER BY o.install_date
            """,
            (today, KH_DEBT_DAYS),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- service calls

def add_service_call(order_id: int, issue: str, resolution: str = "", call_date: str = "") -> int:
    with _connect() as db:
        cur = db.execute(
            "INSERT INTO service_calls (order_id, call_date, issue, resolution) VALUES (?, ?, ?, ?)",
            (order_id, call_date or today_vn(), issue.strip(), resolution.strip() or None),
        )
        return cur.lastrowid


def service_calls_for_order(order_id: int) -> list:
    with _connect() as db:
        rows = db.execute(
            "SELECT * FROM service_calls WHERE order_id = ? ORDER BY call_date DESC, id DESC",
            (order_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- reminders

def create_reminder(customer_id: int, due_date: str, note: str) -> int:
    with _connect() as db:
        cur = db.execute(
            "INSERT INTO reminders (customer_id, due_date, note) VALUES (?, ?, ?)",
            (customer_id, due_date, note.strip()),
        )
        return cur.lastrowid


def mark_reminder_done(reminder_id: int) -> bool:
    with _connect() as db:
        cur = db.execute(
            "UPDATE reminders SET done_at = datetime('now') WHERE id = ? AND done_at IS NULL",
            (reminder_id,),
        )
        ok = cur.rowcount > 0
        row = db.execute(
            "SELECT customer_id, note FROM reminders WHERE id = ?", (reminder_id,)
        ).fetchone() if ok else None
    if ok and row:
        add_touch(row["customer_id"], "nhac_xong", row["note"] or "")
    return ok


def reminders_due(today: str = "") -> list:
    """Hôm nay section E: manual reminders due today or overdue."""
    today = today or today_vn()
    with _connect() as db:
        rows = db.execute(
            "SELECT r.*, c.name AS customer_name, c.phone, c.zalo_phone "
            "FROM reminders r JOIN customers c ON c.id = r.customer_id "
            "WHERE r.done_at IS NULL AND r.due_date <= ? ORDER BY r.due_date",
            (today,),
        ).fetchall()
    return [dict(r) for r in rows]


def reminders_for_customer(customer_id: int) -> list:
    with _connect() as db:
        rows = db.execute(
            "SELECT * FROM reminders WHERE customer_id = ? AND done_at IS NULL ORDER BY due_date",
            (customer_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- dealer debts (công nợ)

def add_debt_entry(customer_id: int, entry_type: str, amount_vnd: int,
                   entry_date: str = "", note: str = "", order_id: Optional[int] = None) -> int:
    """Append-only ledger. Corrections are offsetting entries, like a paper sổ nợ."""
    with _connect() as db:
        cur = db.execute(
            "INSERT INTO debt_entries (customer_id, entry_type, amount_vnd, entry_date, note, order_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (customer_id, entry_type, amount_vnd, entry_date or today_vn(),
             note.strip() or None, order_id),
        )
        return cur.lastrowid


def settle_dealer(customer_id: int) -> int:
    """One-click "Đã thanh toán" for a đại lý: record a payment for the full
    outstanding balance so công nợ clears to 0 (server-computed, mirrors the KH
    order_settle_full). Returns the amount settled (0 if nothing was owed)."""
    bal = dealer_balance(customer_id)
    if bal > 0:
        add_debt_entry(customer_id, "payment", bal, note="Thanh toán đủ")
    return bal if bal > 0 else 0


def delete_debt_entry(entry_id: int, customer_id: Optional[int] = None) -> bool:
    """Remove one sổ nợ line — the correction tool for a mis-entered charge or
    payment (the ledger is otherwise append-only). When customer_id is given the
    delete is scoped to that dealer so an id can't be deleted cross-account."""
    with _connect() as db:
        if customer_id is None:
            cur = db.execute("DELETE FROM debt_entries WHERE id = ?", (entry_id,))
        else:
            cur = db.execute("DELETE FROM debt_entries WHERE id = ? AND customer_id = ?",
                             (entry_id, customer_id))
        return cur.rowcount > 0


def dealer_balances() -> list:
    """All dealers with a positive balance, biggest first (the /cong-no list)."""
    with _connect() as db:
        rows = db.execute(
            """
            SELECT c.id, c.name, c.phone, c.zalo_phone,
                   SUM(CASE WHEN e.entry_type='charge' THEN e.amount_vnd ELSE -e.amount_vnd END) AS balance,
                   MAX(CASE WHEN e.entry_type='payment' THEN e.entry_date END) AS last_payment,
                   MIN(CASE WHEN e.entry_type='charge' THEN e.entry_date END) AS first_charge
            FROM customers c JOIN debt_entries e ON e.customer_id = c.id
            WHERE c.type = 'DL'
            GROUP BY c.id HAVING balance > 0
            ORDER BY balance DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def customer_debts() -> list:
    """All KH (retail) orders still carrying a balance (value − payments > 0) —
    the khách-hàng side of /cong-no, mirroring dealer_balances() for ĐL. Unlike
    orders_debt_due() there is NO install-age gate: every unpaid KH order is
    queued so it can be marked Đã thanh toán from the Nợ tab."""
    with _connect() as db:
        rows = db.execute(
            _ORDER_SELECT + """
            WHERE c.type = 'KH'
              AND COALESCE(o.value_vnd, 0) - (
                    SELECT COALESCE(SUM(amount_vnd), 0) FROM order_payments p WHERE p.order_id = o.id
                  ) > 0
            ORDER BY o.install_date IS NULL, o.install_date
            """
        ).fetchall()
    return [dict(r) for r in rows]


def debts_overdue(today: str = "") -> list:
    """Hôm nay section D: positive balance + no payment for DEBT_OVERDUE_DAYS."""
    today = today or today_vn()
    return [
        d for d in dealer_balances()
        if _days_between(d["last_payment"] or d["first_charge"], today) >= DEBT_OVERDUE_DAYS
    ]


def _days_between(earlier: str, later: str) -> int:
    a = datetime.strptime(earlier, "%Y-%m-%d")
    b = datetime.strptime(later, "%Y-%m-%d")
    return (b - a).days


def dealer_balance(customer_id: int) -> int:
    with _connect() as db:
        r = db.execute(
            "SELECT COALESCE(SUM(CASE WHEN entry_type='charge' THEN amount_vnd ELSE -amount_vnd END), 0) AS b "
            "FROM debt_entries WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
    return r["b"]


def ledger_for_dealer(customer_id: int) -> list:
    """Entries oldest-first with a running balance column (like a paper ledger)."""
    with _connect() as db:
        rows = db.execute(
            "SELECT * FROM debt_entries WHERE customer_id = ? ORDER BY entry_date, id",
            (customer_id,),
        ).fetchall()
    out, running = [], 0
    for r in rows:
        d = dict(r)
        running += d["amount_vnd"] if d["entry_type"] == "charge" else -d["amount_vnd"]
        d["running"] = running
        out.append(d)
    return out


# ---------------------------------------------------------------- reports (báo cáo)

def monthly_report(month: str = "") -> dict:
    """Read-time metrics for /bao-cao. ``month`` is 'YYYY-MM' (VN calendar
    month; defaults to the current VN month). Chốt/mất dates aren't tracked
    separately from sent_date yet, so 'trong tháng' and the gửi→chốt average
    use quotes.updated_at as a stand-in — good enough until touch data
    accumulates enough to derive it from touches instead."""
    month = month or today_vn()[:7]
    with _connect() as db:
        gui = db.execute(
            "SELECT COUNT(*) AS n FROM quotes WHERE strftime('%Y-%m', sent_date) = ?", (month,)
        ).fetchone()["n"]
        won = db.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(value_vnd), 0) AS total FROM quotes "
            "WHERE status = 'won' AND strftime('%Y-%m', updated_at) = ?", (month,)
        ).fetchone()
        lost = db.execute(
            "SELECT COUNT(*) AS n FROM quotes WHERE status = 'lost' AND strftime('%Y-%m', updated_at) = ?",
            (month,),
        ).fetchone()["n"]
        avg_days = db.execute(
            "SELECT AVG(julianday(updated_at) - julianday(sent_date)) AS d FROM quotes "
            "WHERE status = 'won' AND strftime('%Y-%m', updated_at) = ?", (month,)
        ).fetchone()["d"]
        lost_reasons = db.execute(
            "SELECT COALESCE(lost_reason, 'khac') AS reason, COUNT(*) AS n FROM quotes "
            "WHERE status = 'lost' AND strftime('%Y-%m', updated_at) = ? "
            "GROUP BY reason ORDER BY n DESC", (month,),
        ).fetchall()
        by_product = db.execute(
            "SELECT product, COALESCE(SUM(value_vnd), 0) AS total FROM quotes "
            "WHERE status = 'won' AND strftime('%Y-%m', updated_at) = ? "
            "GROUP BY product ORDER BY total DESC", (month,),
        ).fetchall()
        touch_counts = db.execute(
            "SELECT kind, COUNT(*) AS n FROM touches WHERE strftime('%Y-%m', created_at) = ? "
            "GROUP BY kind ORDER BY n DESC", (month,),
        ).fetchall()
    won_n, won_total = won["n"], won["total"]
    close_rate = (won_n / (won_n + lost)) if (won_n + lost) else None
    debt_total = sum(d["balance"] for d in dealer_balances())
    return {
        "month": month,
        "gui": gui, "chot": won_n, "mat": lost,
        "ty_le_chot": close_rate, "gia_tri_chot": won_total,
        "avg_days_to_close": round(avg_days, 1) if avg_days is not None else None,
        "ly_do_mat": [dict(r) for r in lost_reasons],
        "doanh_thu_theo_sp": [dict(r) for r in by_product],
        "cong_no_total": debt_total, "cong_no_qua_han": len(debts_overdue()),
        "cham_soc": [dict(r) for r in touch_counts],
    }


# ---------------------------------------------------------------- Zalo OA linking

def log_zalo_event(zalo_user_id: str, event_name: str, text: str = "") -> None:
    with _connect() as db:
        db.execute(
            "INSERT INTO zalo_events (zalo_user_id, event_name, text) VALUES (?, ?, ?)",
            (zalo_user_id, event_name, text or None),
        )
        # Any customer already linked to this Zalo user gets their inbound
        # timestamp bumped — that's what gates whether the API can message them.
        db.execute(
            "UPDATE customers SET zalo_last_inbound_at = datetime('now') WHERE zalo_user_id = ?",
            (zalo_user_id,),
        )


def recent_unlinked_zalo_events(limit: int = 30) -> list:
    """Most recent event per zalo_user_id that isn't linked to any customer yet —
    what staff use to match an inbound Zalo message to a name in the CRM."""
    with _connect() as db:
        rows = db.execute(
            """
            SELECT e.zalo_user_id, e.event_name, e.text, MAX(e.received_at) AS received_at
            FROM zalo_events e
            WHERE e.zalo_user_id NOT IN (
                SELECT zalo_user_id FROM customers WHERE zalo_user_id IS NOT NULL
            )
            GROUP BY e.zalo_user_id
            ORDER BY received_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def set_customer_zalo_user_id(customer_id: int, zalo_user_id: str) -> bool:
    with _connect() as db:
        cur = db.execute(
            "UPDATE customers SET zalo_user_id = ?, zalo_linked_at = datetime('now'), "
            "updated_at = datetime('now') WHERE id = ?",
            (zalo_user_id.strip(), customer_id),
        )
        return cur.rowcount > 0


def unlink_customer_zalo(customer_id: int) -> bool:
    with _connect() as db:
        cur = db.execute(
            "UPDATE customers SET zalo_user_id = NULL, zalo_linked_at = NULL, "
            "zalo_last_inbound_at = NULL, updated_at = datetime('now') WHERE id = ?",
            (customer_id,),
        )
        return cur.rowcount > 0
