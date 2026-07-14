"""Smoke test for Phase 7: đơn hàng lifecycle overhaul.

- No manual "+ Đơn hàng" route — orders only ever come from a báo giá đã chốt.
- /don-hang splits into đang làm (active) vs đã hoàn thành (archived, collapsed),
  with quick payment-adjust / Đã thanh toán actions on outstanding KH cards.
- /bao-gia auto-archives a won quote once its order reaches hoàn thành.
- Xóa đơn hàng: purges order-owned data, reverts the báo giá to 'sent', keeps
  the customer's lịch sử chăm sóc (touches) and công nợ ledger history.

Runs against a throwaway temp SQLite DB — never touches data/htp.db. Env vars
must be set BEFORE `import app` (app.py reads DB_PATH/... at import time and
calls store.configure(DB_PATH) eagerly).

Run with: .venv/Scripts/python.exe tests_smoke_phase7.py
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

# ---- 1. manual order-creation route is gone -------------------------------------
cid = store.create_customer("Anh Đơn — Trần Phú", "0904445566", "KH")
r = client.get("/don-hang/moi")
# No literal /don-hang/moi route left — falls through to /don-hang/{order_id}
# (int-typed) and "moi" fails validation, so FastAPI returns 422, not 404.
assert r.status_code == 422, f"GET /don-hang/moi should be gone, got {r.status_code}"
r = client.post("/don-hang/moi", data={"customer_id": cid, "product": "khac", "value_vnd": "1.000.000"})
# Same reasoning: path matches /don-hang/{order_id} (GET-only) -> 405, not 404.
assert r.status_code == 405, f"POST /don-hang/moi should be gone, got {r.status_code}"
print("1. manual order-creation route removed OK")

# ---- 2. đơn hàng split: active vs hoàn thành (archived, collapsed) --------------
qid = store.create_quote(cid, "cua_cuon", "Cửa cuốn test", 10_000_000)
r = client.post(f"/bao-gia/{qid}/trang-thai", data={"trang_thai": "won"}, follow_redirects=False)
assert r.status_code == 303, f"chốt failed: {r.status_code} {r.text}"
oid = store.get_quote(qid)["order_id"]
assert oid in [o["id"] for o in store.orders_active()], "new order should be active"
assert oid not in [o["id"] for o in store.orders_completed()], "new order shouldn't be completed yet"

r = client.get("/don-hang")
assert f"/don-hang/{oid}" in r.text and "Đã hoàn thành" not in r.text, \
    "active order should render without an archive section yet"

store.set_order_stage(oid, "hoan_thanh")
assert oid in [o["id"] for o in store.orders_completed()], "order should move to completed"
assert oid not in [o["id"] for o in store.orders_active()], "order should leave active"
r = client.get("/don-hang")
assert "Đã hoàn thành (1)" in r.text, "archive section should show the completed order"
print(f"2. đơn hàng split OK (đơn #{oid})")

# ---- 3. quick payment actions on the đơn hàng list for outstanding KH orders ----
qid2 = store.create_quote(cid, "cua_cuon", "Cửa cuốn 2", 8_000_000)
client.post(f"/bao-gia/{qid2}/trang-thai", data={"trang_thai": "won"}, follow_redirects=False)
oid2 = store.get_quote(qid2)["order_id"]
r = client.get("/don-hang")
assert f"còn nợ" in r.text and f"/don-hang/{oid2}/thu-du" in r.text, \
    "outstanding KH order should show a còn nợ chip + Đã thanh toán action on /don-hang"
r = client.post(f"/don-hang/{oid2}/thu-du", data={"next": "/don-hang"}, follow_redirects=False)
assert r.status_code == 303
assert store.get_order(oid2)["balance_vnd"] == 0, "thu-du from /don-hang should settle the balance"
print(f"3. quick Đã thanh toán from /don-hang list OK (đơn #{oid2})")

# ---- 4. báo giá auto-archives once its order reaches hoàn thành -----------------
r = client.get("/bao-gia")
assert f"/bao-gia/{qid}" not in r.text or "Đã hoàn thành (1)" in r.text, \
    "won quote with a hoàn thành order should be archived, not on the open board"
all_ids = [q["id"] for q in store.list_quotes("all")]
assert qid not in all_ids, "archived quote must not appear in list_quotes('all')"
archived_ids = [q["id"] for q in store.quotes_archived()]
assert qid in archived_ids, "quote should appear in quotes_archived()"
assert qid2 not in all_ids or qid2 in [q["id"] for q in store.list_quotes("all")], \
    "qid2's order isn't hoàn thành yet — sanity: qid2 should still be in list_quotes('all')"
assert qid2 in [q["id"] for q in store.list_quotes("all")], "qid2 shouldn't be archived (order not hoàn thành)"
print("4. báo giá auto-archive on hoàn thành OK")

# ---- 5. xóa đơn hàng: purge order data, revert quote, keep touches --------------
store.add_service_call(oid2, "Cửa kêu", "Tra dầu")
store.add_touch(cid, "ghi_chu", "gọi hỏi thăm", order_id=oid2)
touches_before = len(store.list_touches(cid, limit=50))

r = client.post(f"/don-hang/{oid2}/xoa", follow_redirects=False)
assert r.status_code == 303, f"xóa đơn hàng failed: {r.status_code} {r.text}"
assert store.get_order(oid2) is None, "order should be gone after xóa"
assert store.order_items_for(oid2) == [], "order_items should be purged"
assert store.order_payments_for(oid2) == [], "order_payments should be purged"
assert store.service_calls_for_order(oid2) == [], "service_calls should be purged"

q2 = store.get_quote(qid2)
assert q2["status"] == "sent" and q2["order_id"] is None, \
    f"quote should revert to sent/unlinked, got status={q2['status']} order_id={q2['order_id']}"

touches_after = store.list_touches(cid, limit=50)
assert len(touches_after) == touches_before, "customer lịch sử chăm sóc must survive xóa đơn hàng"
assert all(t["order_id"] != oid2 for t in touches_after), "surviving touches should drop the dead order_id link"

r = client.post(f"/don-hang/{oid2}/xoa", follow_redirects=False)
assert r.status_code == 404, "deleting an already-gone order should 404"
print(f"5. xóa đơn hàng OK — order purged, báo giá reverted to sent, {touches_after and touches_after[0]['kind']} touch kept")

print("ALL PHASE 7 SMOKE TESTS PASSED")
