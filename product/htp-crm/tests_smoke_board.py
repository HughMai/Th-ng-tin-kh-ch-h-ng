"""Smoke test for the Trello-style board rework:

  1. Xin đánh giá only enters the Hôm nay queue once the đơn hàng reaches
     'dang_lap' (đã lắp đặt/đã giao) — an install_date alone is not enough.
  2. Stages collapsed 4 -> 3; legacy 'hoan_thanh' rows migrate to 'dang_lap'.
  3. Hôm nay and Báo giá render as boards, and "Chép tin nhắn" is gone.

Runs against a throwaway temp SQLite DB — never touches data/htp.db.

Run with: .venv/Scripts/python.exe tests_smoke_board.py
"""
import os
import sqlite3
import tempfile
from pathlib import Path

_tmp_dir = tempfile.mkdtemp(prefix="htp_crm_test_")
os.environ["DB_PATH"] = str(Path(_tmp_dir) / "test.db")
os.environ["FAMILY_PASSWORD"] = "test-pass-123"
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["COOKIE_INSECURE"] = "1"

from fastapi.testclient import TestClient

import app
import store
from templates_vi import STAGE_LABELS

client = TestClient(app.app)
r = client.post("/login", data={"password": "test-pass-123"}, follow_redirects=False)
assert r.status_code == 303, f"login failed: {r.status_code} {r.text}"

today = store.today_vn()

# ---- 1. review nudge is stage-gated, not install-date-gated -----------------
kh = store.create_customer("Anh Đạt", "0902000001", "KH", "gioi_thieu")
oid = store.create_order(kh, "cua_cuon", "Cửa cuốn nhà chính", value_vnd=12_000_000)
store.set_order_install(oid, today)

# installed today on paper, but still sitting in the xưởng
assert store.get_order(oid)["stage"] == "cho_san_xuat"
due = [o["id"] for o in store.orders_review_due(today)]
assert oid not in due, f"review asked while still chờ sản xuất: {due}"

store.set_order_stage(oid, "dang_san_xuat")
due = [o["id"] for o in store.orders_review_due(today)]
assert oid not in due, f"review asked while still sản xuất xong: {due}"

store.set_order_stage(oid, "dang_lap")
due = [o["id"] for o in store.orders_review_due(today)]
assert oid in due, f"review NOT queued after đã lắp đặt/đã giao: {due}"
print("1. Xin đánh giá queues only at 'đã lắp đặt/đã giao' OK")

# ---- 2. three stages; legacy hoan_thanh rows migrate ------------------------
assert list(STAGE_LABELS) == ["cho_san_xuat", "dang_san_xuat", "dang_lap"], STAGE_LABELS
assert STAGE_LABELS["dang_san_xuat"] == "Sản xuất xong"
assert STAGE_LABELS["dang_lap"] == "Đã lắp đặt/đã giao"
try:
    store.set_order_stage(oid, "hoan_thanh")
    raise AssertionError("retired stage still accepted")
except ValueError:
    pass

# a row written by the OLD build, then re-opened by the current one
kh2 = store.create_customer("Chị Mai", "0902000002", "KH", "facebook")
oid_old = store.create_order(kh2, "cua_keo", "Cửa kéo kho", value_vnd=6_000_000)
db = sqlite3.connect(os.environ["DB_PATH"])
db.execute("UPDATE orders SET stage = 'hoan_thanh' WHERE id = ?", (oid_old,))
db.commit()
db.close()
store.configure(os.environ["DB_PATH"])  # re-runs the migrations, as a restart would
assert store.get_order(oid_old)["stage"] == "dang_lap", "hoan_thanh row not migrated"
assert oid_old in [o["id"] for o in store.orders_completed()]
assert oid_old not in [o["id"] for o in store.orders_active()]
print("2. 4 stages -> 3, legacy 'hoan_thanh' migrated to 'dang_lap' OK")

# ---- 3. board markup on Hôm nay + Báo giá, no copy button -------------------
home = client.get("/").text
assert 'class="board' in home, "Hôm nay is not a board"
assert "board-col" in home and "board-card" in home
assert "Xin đánh giá" in home, "review column missing for the đã-lắp order"

quote_id = store.create_quote(kh2, "cua_keo", "Cửa kéo kho", value_vnd=6_000_000)
bao_gia = client.get("/bao-gia").text
assert 'class="board' in bao_gia, "Báo giá is not a board"
assert "board-drag" in bao_gia, "Báo giá board lost its drag wiring"
assert "quotes-list-mobile" not in bao_gia, "duplicate mobile list still rendered"
assert str(quote_id) in bao_gia

for page in (home, bao_gia, client.get(f"/khach/{kh}").text):
    assert "Chép tin nhắn" not in page, "copy button still rendered"
    assert "copyMsg" not in page, "copy JS still shipped"
    assert "showToast" not in page, "orphaned toast JS still shipped"
print("3. Hôm nay + Báo giá render as boards, Chép tin nhắn removed OK")

print("\nAll board smoke tests passed.")
