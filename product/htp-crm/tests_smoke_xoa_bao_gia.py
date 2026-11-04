"""Smoke test: xóa báo giá from the Mất column + 4 new công nghệ Đức models.

1. A Mất card carries "Xóa báo giá"; deleting it purges the quote and its hạng
   mục but keeps the customer's lịch sử chăm sóc (quote_id detached).
2. Only Mất quotes can go, and never one a đơn hàng still points at.
3. B7 / B5 / NO1 / AL75 khe thoáng are listed under Cửa cuốn công nghệ Đức with
   no bảng giá yet — they price by hand, and without a đơn giá the server says so.

Runs against a throwaway temp SQLite DB — never touches data/htp.db.
Run with: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe tests_smoke_xoa_bao_gia.py
"""
import os
import tempfile
from pathlib import Path

_tmp_dir = tempfile.mkdtemp(prefix="htp_crm_test_")
os.environ["DB_PATH"] = str(Path(_tmp_dir) / "test.db")
os.environ["FAMILY_PASSWORD"] = "test-pass-123"
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["COOKIE_INSECURE"] = "1"
os.environ["BOT_URL"] = ""

from fastapi.testclient import TestClient

import app
import pricing
import store

client = TestClient(app.app)
r = client.post("/login", data={"password": "test-pass-123"}, follow_redirects=False)
assert r.status_code == 303, f"login failed: {r.status_code}"

DUC = "Cửa cuốn công nghệ Đức"
cid = store.create_customer("Anh Tư — Hùng Vương", "0905777888", "KH")


def quote_with_item() -> int:
    qid = store.create_quote_header(cid)
    store.add_quote_item(qid, "cua_cuon", DUC, "KV 380", 3000, 2800, 12_000_000, False)
    return qid


# ---- 1. delete from Mất ----------------------------------------------------------
q_lost = quote_with_item()
store.set_quote_status(q_lost, "lost", "gia_cao")
touches_before = len(store.list_touches(cid, limit=100))
board = client.get("/bao-gia").text
assert f'action="/bao-gia/{q_lost}/xoa"' in board, "Mất card should offer Xóa báo giá"

r = client.post(f"/bao-gia/{q_lost}/xoa", follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == "/bao-gia", r.status_code
assert store.get_quote(q_lost) is None, "quote should be gone"
assert store.quote_items_for(q_lost) == [], "its hạng mục should be gone"
assert len(store.list_touches(cid, limit=100)) == touches_before, "care history must be kept"
assert f'action="/bao-gia/{q_lost}/xoa"' not in client.get("/bao-gia").text
print("1. xóa báo giá from Mất OK")

# ---- 2. guards ---------------------------------------------------------------------
q_open = quote_with_item()
assert client.post(f"/bao-gia/{q_open}/xoa").status_code == 400, "an open quote must not be deletable"
assert f'action="/bao-gia/{q_open}/xoa"' not in client.get("/bao-gia").text
assert store.get_quote(q_open), "open quote must survive"

q_won = quote_with_item()
store.set_quote_status(q_won, "won")
oid = store.create_order_from_quote(q_won)
store.set_quote_status(q_won, "lost", "khac")  # dragged back to Mất, đơn hàng still exists
assert client.post(f"/bao-gia/{q_won}/xoa").status_code == 400, "a quote backing a đơn must stay"
assert store.get_quote(q_won) and store.get_order(oid)
assert client.post("/bao-gia/999999/xoa").status_code == 400
print("2. delete guards OK")

# ---- 3. new công nghệ Đức models, hand-priced ------------------------------------
duc = pricing.DOOR_CONFIG["cua_cuon"]["fields"][1]["option_map"][DUC]
for m in ("B7", "B5", "NO1", "AL75 khe thoáng"):
    assert m in duc, f"{m} missing from Chọn loại"
    assert pricing.get_price("cua_cuon", DUC, m, "KH") is None, f"{m} should have no price yet"
    assert pricing.get_price("cua_cuon", DUC, m, "DL") is None
assert "AL75 khe thoáng" in client.get("/khach/tiep-nhan").text, "wizard must list the new models"

q = store.create_quote_header(cid)
r = client.post(f"/bao-gia/{q}/hang-muc/moi", data={
    "loai_cua": "cua_cuon", "cong_nghe": DUC, "mau": "B7", "ngang": "3000", "cao": "2800"})
assert r.status_code == 400, "no bảng giá and no đơn giá must be refused"
r = client.post(f"/bao-gia/{q}/hang-muc/moi", data={
    "loai_cua": "cua_cuon", "cong_nghe": DUC, "mau": "B7", "ngang": "3000", "cao": "2800",
    "gia_thu_cong": "1.500.000"}, follow_redirects=False)
assert r.status_code == 303, r.status_code
item = store.quote_items_for(q)[0]
assert item["mau"] == "B7" and item["is_manual_price"], item
print("3. B7 / B5 / NO1 / AL75 khe thoáng OK")

print("ALL XOA BAO GIA SMOKE TESTS PASSED")
