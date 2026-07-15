"""Smoke test for Phase 6: unified Công nợ tab — retail (KH) order-debt and
dealer (ĐL) ledger-debt in one /cong-no view with a Khách hàng/Đại lý filter,
a "Đã thanh toán" (thu-du) button that settles the full balance, and a
delete-payment reopen path.

Runs against a throwaway temp SQLite DB — never touches data/htp.db. Env vars
must be set BEFORE `import app` (app.py reads DB_PATH/... at import time and
calls store.configure(DB_PATH) eagerly).

Run with: .venv/Scripts/python.exe tests_smoke_phase6.py
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

# ---- 1. every unpaid KH order queues, regardless of install age ----------------
# No install_date at all -> orders_debt_due (age-gated) skips it, but customer_debts
# must still queue it so it can be marked paid from the Nợ tab.
kh = store.create_customer("Chị KH — Nguyễn Trãi", "0902223344", "KH")
oid_kh = store.create_order(kh, "cua_cuon", "Cửa cuốn 3m", 10_000_000)  # no install_date
assert oid_kh not in [o["id"] for o in store.orders_debt_due()], "un-installed order shouldn't be age-due"
assert oid_kh in [o["id"] for o in store.customer_debts()], "KH unpaid order missing from customer_debts"
print(f"1. customer_debts queues unpaid KH order regardless of install age OK (đơn #{oid_kh})")

# ---- 2. ĐL ledger debt still lives on the dealer side --------------------------
dl = store.create_customer("Đại lý Nợ", "0903334455", "DL")
store.add_debt_entry(dl, "charge", 5_000_000, note="5 bộ cửa")
assert dl in [d["id"] for d in store.dealer_balances()], "dealer not in dealer_balances"
print("2. dealer ledger debt present OK")

# ---- 3. /cong-no filter scopes each side correctly ----------------------------
def body(loc):
    resp = client.get(f"/cong-no?loc={loc}")
    assert resp.status_code == 200, f"/cong-no?loc={loc} -> {resp.status_code}"
    return resp.text

all_b, kh_b, dl_b = body("tat-ca"), body("kh"), body("dl")
assert f"/don-hang/{oid_kh}/thu-du" in kh_b and "Đại lý Nợ" not in kh_b, "loc=kh should show KH only"
assert "Đại lý Nợ" in dl_b and f"/don-hang/{oid_kh}/thu-du" not in dl_b, "loc=dl should show ĐL only"
assert f"/don-hang/{oid_kh}/thu-du" in all_b and "Đại lý Nợ" in all_b, "loc=tat-ca should show both"
print("3. /cong-no?loc kh/dl/tat-ca scoping OK")

# ---- 4. "Đã thanh toán" (thu-du) settles the full balance ----------------------
r = client.post(f"/don-hang/{oid_kh}/thu-du", data={"next": "/cong-no?loc=kh"},
                follow_redirects=False)
assert r.status_code == 303, f"thu-du failed: {r.status_code} {r.text}"
assert store.get_order(oid_kh)["balance_vnd"] == 0, "balance not zero after thu-du"
assert oid_kh not in [o["id"] for o in store.customer_debts()], "settled order still queued"
assert f"/don-hang/{oid_kh}/thu-du" not in body("kh"), "settled order still rendered in loc=kh"
print("4. thu-du settles full balance + drops from queue OK")

# ---- 5. delete-payment reopens the order --------------------------------------
pay_id = store.order_payments_for(oid_kh)[-1]["id"]
r = client.post(f"/don-hang/{oid_kh}/thanh-toan/{pay_id}/xoa",
                data={"next": f"/don-hang/{oid_kh}"}, follow_redirects=False)
assert r.status_code == 303, f"delete payment failed: {r.status_code} {r.text}"
# value is VAT-inclusive: 10.000.000 + 10% = 11.000.000
assert store.get_order(oid_kh)["balance_vnd"] == 11_000_000, "balance not restored after reopen"
assert oid_kh in [o["id"] for o in store.customer_debts()], "reopened order not back in queue"
print("5. delete-payment reopen restores balance + queue OK")

print("ALL PHASE 6 SMOKE TESTS PASSED")
