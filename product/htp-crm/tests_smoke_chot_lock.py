"""Smoke test: a báo giá with a đơn hàng can't be dragged out of Chốt (BG-0041).

1. A chốt quote with a live đơn refuses Đã gửi / chasing / Mất with
   "Xóa đơn hàng trước nếu muốn mở lại", and stays won with its đơn.
2. Deleting the đơn first drops the quote to Mất — then it re-opens normally.
3. Quotes without a đơn still move freely (Đã gửi ↔ Mất).

Runs against a throwaway temp SQLite DB — never touches data/htp.db.
Run with: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe tests_smoke_chot_lock.py
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
import store

client = TestClient(app.app)
r = client.post("/login", data={"password": "test-pass-123"}, follow_redirects=False)
assert r.status_code == 303, f"login failed: {r.status_code}"

cid = store.create_customer("Chú Ánh — Lê Lợi", "0905111222", "KH")


def quote_with_item() -> int:
    qid = store.create_quote_header(cid)
    store.add_quote_item(qid, "cua_cuon", "Cửa cuốn công nghệ Đức", "KV 380",
                         3000, 2800, 12_000_000, False)
    return qid


def move(qid: int, status: str):
    data = {"trang_thai": status}
    if status == "lost":
        data["ly_do"] = "khac"
    return client.post(f"/bao-gia/{qid}/trang-thai", data=data, follow_redirects=False)


# ---- 1. chốt + đơn hàng is locked -------------------------------------------------
q = quote_with_item()
assert move(q, "won").status_code == 303
oid = store.get_quote(q)["order_id"]
assert oid, "chốt should create a đơn hàng"

for status in ("sent", "chasing", "lost"):
    r = move(q, status)
    assert r.status_code == 400, f"{status}: expected 400, got {r.status_code}"
    assert r.json()["detail"] == "Xóa đơn hàng trước nếu muốn mở lại", r.text
    after = store.get_quote(q)
    assert after["status"] == "won" and after["order_id"] == oid, after
assert store.get_order(oid), "đơn hàng must survive"
print("1. chốt with a đơn refuses Đã gửi / chasing / Mất OK")

# ---- 2. delete the đơn first, then it re-opens ------------------------------------
assert store.delete_order(oid)
after = store.get_quote(q)
assert after["status"] == "lost" and not after["order_id"], after
assert move(q, "sent").status_code == 303
assert store.get_quote(q)["status"] == "sent"
print("2. after xóa đơn hàng the quote re-opens OK")

# ---- 3. no đơn → free movement ----------------------------------------------------
q2 = quote_with_item()
assert move(q2, "lost").status_code == 303
assert store.get_quote(q2)["status"] == "lost"
assert move(q2, "sent").status_code == 303
assert store.get_quote(q2)["status"] == "sent"
print("3. quotes without a đơn still move freely OK")

print("ALL CHOT LOCK SMOKE TESTS PASSED")
