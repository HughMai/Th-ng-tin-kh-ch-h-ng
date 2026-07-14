"""Smoke test for Phase 3: customer email + order-level invoice line items
(order_items table, chốt snapshot, hạng mục routes, printable hóa đơn).

Runs against a throwaway temp SQLite DB — never touches data/htp.db. Env vars
must be set BEFORE `import app` (app.py reads DB_PATH/FAMILY_PASSWORD/
SESSION_SECRET at import time and calls store.configure(DB_PATH) eagerly).

Run with: .venv/Scripts/python.exe tests_smoke_phase3.py
"""
import os
import tempfile
from pathlib import Path

_tmp_dir = tempfile.mkdtemp(prefix="htp_crm_test_")
os.environ["DB_PATH"] = str(Path(_tmp_dir) / "test.db")
os.environ["FAMILY_PASSWORD"] = "test-pass-123"
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["COOKIE_INSECURE"] = "1"  # TestClient talks plain http://testserver

from fastapi.testclient import TestClient

import app
import store

client = TestClient(app.app)

r = client.post("/login", data={"password": "test-pass-123"}, follow_redirects=False)
assert r.status_code == 303, f"login failed: {r.status_code} {r.text}"

# ---- 1. customer email: create via form, shows on detail, survives edit --------
r = client.post("/khach/moi", data={
    "name": "Anh Email — Trần Phú", "phone": "0901112233",
    "type": "KH", "source": "khac", "email": "anh.email@gmail.com",
}, follow_redirects=False)
assert r.status_code == 303, f"POST /khach/moi -> {r.status_code} {r.text}"
cid = int(r.headers["location"].rsplit("/", 1)[-1])
r = client.get(f"/khach/{cid}")
assert "anh.email@gmail.com" in r.text, "email not shown on customer detail page"
r = client.post(f"/khach/{cid}/sua", data={
    "name": "Anh Email — Trần Phú", "phone": "0901112233",
    "type": "KH", "source": "khac", "email": "moi@gmail.com",
}, follow_redirects=False)
assert r.status_code == 303, f"POST /khach/{cid}/sua -> {r.status_code}"
assert store.get_customer(cid)["email"] == "moi@gmail.com", "email lost on edit"
print(f"1. email round-trip OK (khach #{cid})")

# ---- 2. multi-item quote + accessories -> won -> order_items snapshot -----------
qid = store.create_quote_header(cid, accessories="Bình Tích Điện x1")
store.add_quote_item(qid, "cua_cuon", "Cửa cuốn công nghệ Đức", "KV thường",
                     4000, 3000, 12_000_000, True, "ghi sần")
store.add_quote_item(qid, "cua_keo", "Có lá", "6zem", 3000, 2500, 8_000_000, True)
quote_value = store.get_quote(qid)["value_vnd"]
r = client.post(f"/bao-gia/{qid}/trang-thai", data={"trang_thai": "won"},
                follow_redirects=False)
assert r.status_code == 303, f"chốt failed: {r.status_code} {r.text}"
oid = store.get_quote(qid)["order_id"]
assert oid, "won quote has no linked order"
items = store.order_items_for(oid)
assert len(items) == 3, f"expected 2 doors + 1 accessory, got {len(items)}: {items}"
order_value = store.get_order(oid)["value_vnd"]
assert order_value == sum(i["thanh_tien"] for i in items), "value_vnd != SUM(items)"
assert order_value == quote_value, f"order {order_value} != quote {quote_value}"
print(f"2. won-snapshot OK (don #{oid}: {len(items)} items, {order_value} VND)")

# ---- 3. quick order (lump value, no quote) -> one generic line ------------------
r = client.post("/don-hang/moi", data={
    "customer_id": cid, "product": "khac", "value_vnd": "5.000.000",
    "description": "Sửa cửa lẻ",
}, follow_redirects=False)
assert r.status_code == 303, f"POST /don-hang/moi -> {r.status_code} {r.text}"
oid2 = int(r.headers["location"].rsplit("/", 1)[-1])
items2 = store.order_items_for(oid2)
assert len(items2) == 1, f"quick order should have 1 generic line, got {len(items2)}"
assert items2[0]["thanh_tien"] == 5_000_000 and items2[0]["description"] == "Sửa cửa lẻ"
print(f"3. quick-order generic line OK (don #{oid2})")

# ---- 4. printable hóa đơn -------------------------------------------------------
r = client.get(f"/don-hang/{oid}/hoa-don")
assert r.status_code == 200, f"GET hoa-don -> {r.status_code}"
assert "Tổng tiền" in r.text and "Anh Email" in r.text, "invoice missing totals/customer"
assert "Bình Tích Điện" in r.text, "invoice missing accessory line"
print("4. invoice page OK")

# ---- 5. add / edit / delete hạng mục -> value_vnd recomputed --------------------
r = client.post(f"/don-hang/{oid2}/hang-muc/moi", data={
    "description": "Phí vận chuyển", "so_luong": 1, "thanh_tien": "300.000",
}, follow_redirects=False)
assert r.status_code == 303, f"add item -> {r.status_code} {r.text}"
assert store.get_order(oid2)["value_vnd"] == 5_300_000, "value not recomputed on add"
new_item = store.order_items_for(oid2)[-1]
r = client.post(f"/don-hang/{oid2}/hang-muc/{new_item['id']}/sua", data={
    "description": "Phí vận chuyển xa", "so_luong": 1, "thanh_tien": "400.000",
}, follow_redirects=False)
assert r.status_code == 303, f"edit item -> {r.status_code} {r.text}"
assert store.get_order(oid2)["value_vnd"] == 5_400_000, "value not recomputed on edit"
r = client.post(f"/don-hang/{oid2}/hang-muc/{new_item['id']}/xoa", follow_redirects=False)
assert r.status_code == 303, f"delete item -> {r.status_code}"
assert store.get_order(oid2)["value_vnd"] == 5_000_000, "value not recomputed on delete"
print("5. line-item add/edit/delete + recompute OK")

# ---- 6. order detail shows hạng mục card ----------------------------------------
r = client.get(f"/don-hang/{oid}")
assert r.status_code == 200
assert "Hạng mục" in r.text and "Xem hóa đơn" in r.text, "order page missing hạng mục card"
print("6. order detail line-items card OK")

print("ALL PHASE 3 SMOKE TESTS PASSED")
