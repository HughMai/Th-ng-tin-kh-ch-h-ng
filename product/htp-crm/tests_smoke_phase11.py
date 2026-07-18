"""Smoke test for Phase 11: Công nợ 'Đã thu' tab + xem theo ngày.

- A fully-paid KH đơn hàng surfaces under /cong-no?loc=da-thu (dated by its
  settling payment) and drops off the outstanding Khách hàng list.
- A ĐL công nợ payment surfaces under Đã thu too.
- store.settlements(day) narrows to a single VN day (the báo-cáo day lens).
- /don-hang?ngay=<ngày chốt> narrows Đơn hàng to that day; a day with no chốt
  renders the empty state instead of everything.

Runs against a throwaway temp SQLite DB — never touches data/htp.db. Env vars
must be set BEFORE `import app` (app.py reads DB_PATH/... at import time).

Run with: .venv/Scripts/python.exe tests_smoke_phase11.py
"""
import os
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

client = TestClient(app.app)

r = client.post("/login", data={"password": "test-pass-123"}, follow_redirects=False)
assert r.status_code == 303, f"login failed: {r.status_code} {r.text}"

today = store.today_vn()

# ---- 1. KH đơn thu đủ → Đã thu (dated by settling payment), off outstanding ----
kh = store.create_customer("Anh Tuấn", "0900000001", "KH")
qid = store.create_quote_header(kh)
store.add_quote_item(qid, "cua_cuon", "Cửa cuốn công nghệ Úc", "Tole màu 5.2 zem blusc",
                     3000, 2200, 12_000_000, True)
r = client.post(f"/bao-gia/{qid}/trang-thai", data={"trang_thai": "won"}, follow_redirects=False)
assert r.status_code == 303
oid = store.get_quote(qid)["order_id"]
val = store.get_order(oid)["value_vnd"]
# Unpaid → not a settlement yet, but IS on the outstanding Khách hàng tab.
assert not any(s["kind"] == "KH" and s["ref"] == oid for s in store.settlements())
assert oid in [o["id"] for o in store.customer_debts()]
# Settle full via the Nợ-tab endpoint.
r = client.post(f"/don-hang/{oid}/thu-du", data={"method": "chuyển khoản"}, follow_redirects=False)
assert r.status_code == 303
match = [s for s in store.settlements() if s["kind"] == "KH" and s["ref"] == oid]
assert match, "paid KH đơn missing from settlements()"
assert match[0]["amount_vnd"] == val and match[0]["pay_date"] == today, match[0]
assert oid not in [o["id"] for o in store.customer_debts()], "paid đơn still on outstanding tab"
page = client.get("/cong-no?loc=da-thu").text
assert "Anh Tuấn" in page and "đã thu đủ" in page, "Đã thu tab did not render the paid đơn"
print(f"1. KH đơn #{oid} thu đủ shows in Đã thu, off the outstanding tab OK ({val} đ)")

# ---- 2. ĐL công nợ payment → Đã thu -------------------------------------------
dl = store.create_customer("Đại lý Minh Long", "0900000002", "DL")
store.add_debt_entry(dl, "charge", 33_000_000, note="10 bộ cửa")
r = client.post(f"/cong-no/{dl}/thanh-toan-du", data={"method": "tiền mặt"}, follow_redirects=False)
assert r.status_code == 303
assert any(s["kind"] == "DL" and s["ref"] == dl and s["amount_vnd"] == 33_000_000
           for s in store.settlements()), "ĐL settlement missing from settlements()"
print("2. ĐL settlement shows in Đã thu OK")

# ---- 3. settlements(day) filters by VN day ------------------------------------
assert len(store.settlements(today)) == len(store.settlements()), "today filter dropped rows"
assert store.settlements("2020-01-01") == [], "stale day should be empty"
print("3. settlements(day) filters by VN day OK")

# ---- 4. /don-hang?ngay= narrows to that ngày chốt -----------------------------
assert "Anh Tuấn" in client.get(f"/don-hang?ngay={today}").text, "day filter hid a same-day đơn"
assert "Không có đơn nào chốt" in client.get("/don-hang?ngay=2020-01-01").text, "empty day not handled"
print("4. /don-hang?ngay= day filter OK")

print("ALL PHASE 11 SMOKE TESTS PASSED")
