"""Smoke test for GET /api/thu — the money-in feed Sổ Thu Chi reads.

Covers the token gate, the date-range filter, and the one thing that silently
corrupts a day's Thu total if it regresses: a dealer's order payments must NOT
appear (ĐL money is counted once, from debt_entries) while KH cọc + thanh toán
and ĐL công nợ payments must all appear.

Runs against a throwaway temp SQLite DB — never touches data/htp.db.

Run with: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe tests_smoke_api_thu.py
"""
import os
import tempfile
from pathlib import Path

_tmp_dir = tempfile.mkdtemp(prefix="htp_crm_thu_test_")
os.environ["DB_PATH"] = str(Path(_tmp_dir) / "test.db")
os.environ["FAMILY_PASSWORD"] = "test-pass-123"
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["COOKIE_INSECURE"] = "1"
os.environ["THUCHI_TOKEN"] = "test-thuchi-token"

from fastapi.testclient import TestClient

import app
import store

client = TestClient(app.app)
HDR = {"X-Thuchi-Token": "test-thuchi-token"}

DAY = "2026-07-20"
NEXT = "2026-07-21"
OLD = "2026-07-01"

# ---- seed: one KH with cọc + thanh toán, one ĐL with a công nợ payment -------
kh = store.create_customer("Chị Lan — Test KH", "0909000001", "KH")
kh_order = store.create_order(kh, "cua_cuon", "Cửa cuốn test", 20_000_000)
store.add_order_payment(kh_order, "coc", 5_000_000, DAY, "tiền mặt")
store.add_order_payment(kh_order, "thanh_toan", 15_000_000, DAY, "chuyển khoản")

dl = store.create_customer("Đại lý Test", "0909000002", "DL")
store.add_debt_entry(dl, "charge", 30_000_000, OLD, "Lấy hàng")
store.add_debt_entry(dl, "payment", 12_000_000, DAY, "Trả nợ", method="tiền mặt")

# A dealer order paid the same day: its order_payments row must be excluded, or
# dealer money would count twice (once here, once in debt_entries).
dl_order = store.create_order(dl, "cua_keo", "Cửa kéo đại lý", 8_000_000)
store.add_order_payment(dl_order, "thanh_toan", 8_000_000, DAY, "tiền mặt")

# Money on a different day, to prove the range filter bites.
store.add_debt_entry(dl, "payment", 3_000_000, NEXT, "Trả tiếp", method="tiền mặt")

# ---- token gate --------------------------------------------------------------
r = client.get(f"/api/thu?tu={DAY}&den={DAY}")
assert r.status_code == 403, f"no token should be 403, got {r.status_code}"
r = client.get(f"/api/thu?tu={DAY}&den={DAY}", headers={"X-Thuchi-Token": "wrong"})
assert r.status_code == 403, f"bad token should be 403, got {r.status_code}"

# ---- bad dates ---------------------------------------------------------------
r = client.get("/api/thu?tu=20-07-2026&den=2026-07-20", headers=HDR)
assert r.status_code == 400, f"malformed date should be 400, got {r.status_code}"
r = client.get("/api/thu", headers=HDR)
assert r.status_code == 400, f"missing dates should be 400, got {r.status_code}"

# ---- one day -----------------------------------------------------------------
r = client.get(f"/api/thu?tu={DAY}&den={DAY}", headers=HDR)
assert r.status_code == 200, f"GET /api/thu -> {r.status_code} {r.text}"
rows = r.json()["thu"]
total = sum(x["amount_vnd"] for x in rows)
assert total == 32_000_000, f"expected 5tr cọc + 15tr thanh toán + 12tr ĐL = 32tr, got {total:,}"
assert len(rows) == 3, f"expected 3 rows, got {len(rows)}: {rows}"

kinds = sorted(x["kind"] for x in rows)
assert kinds == ["coc", "thanh_toan", "thanh_toan"], f"unexpected kinds: {kinds}"
assert all(x["date"] == DAY for x in rows), "a row escaped the day filter"
assert {"date", "name", "kind", "method", "amount_vnd"} == set(rows[0]), \
    f"row shape changed: {sorted(rows[0])}"

# the dealer's own order payment must be absent (double-count guard)
assert not any(x["amount_vnd"] == 8_000_000 for x in rows), \
    "dealer order payment leaked into /api/thu — ĐL money would count twice"

# ---- range spanning both days -------------------------------------------------
r = client.get(f"/api/thu?tu={DAY}&den={NEXT}", headers=HDR)
rows = r.json()["thu"]
assert sum(x["amount_vnd"] for x in rows) == 35_000_000, "range should pick up the 3tr on day 2"
assert [x["date"] for x in rows] == sorted(x["date"] for x in rows), "rows should be date-ordered"

# ---- a day with no money ------------------------------------------------------
r = client.get("/api/thu?tu=2026-07-15&den=2026-07-15", headers=HDR)
assert r.json()["thu"] == [], "quiet day should return an empty list, not an error"

print("ALL SMOKE TESTS PASSED")
