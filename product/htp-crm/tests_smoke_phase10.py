"""Smoke test for Phase 10: chốt-ing a Đại lý's quote into an order now
auto-charges the dealer's sổ nợ (debt_entries) for the order's VAT-inclusive
value, with an offsetting 'payment' entry if a cọc was already recorded.
Regression guard for the 2026-07-17 bug where Công nợ silently showed 0đ for
real dealer orders because nothing ever called add_debt_entry() on chốt —
only the manual "+ Ghi nợ" form did.

Runs against a throwaway temp SQLite DB — never touches data/htp.db. Env vars
must be set BEFORE `import app` (app.py reads DB_PATH/... at import time and
calls store.configure(DB_PATH) eagerly).

Run with: .venv/Scripts/python.exe tests_smoke_phase10.py
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

# ---- 1. chốt (no cọc) auto-charges the dealer for the full order value --------
dl1 = store.create_customer("Đại lý Xingfa", "0944555666", "DL")
qid1 = store.create_quote_header(dl1)
store.add_quote_item(qid1, "cua_cuon", "Cửa cuốn công nghệ Úc", "Tole màu 5.2 zem blusc",
                     3000, 2200, 10_000_000, True)
assert store.dealer_balance(dl1) == 0, "no charge should exist before chốt"
r = client.post(f"/bao-gia/{qid1}/trang-thai", data={"trang_thai": "won"}, follow_redirects=False)
assert r.status_code == 303, f"chốt failed: {r.status_code} {r.text[:300]}"
q1 = store.get_quote(qid1)
oid1 = q1["order_id"]
order1 = store.get_order(oid1)
assert store.dealer_balance(dl1) == order1["value_vnd"] > 0, (
    f"dealer not auto-charged on chốt: balance={store.dealer_balance(dl1)}, order value={order1['value_vnd']}")
assert dl1 in [d["id"] for d in store.dealer_balances()], "dealer missing from /cong-no dealer_balances() list"
print(f"1. chốt auto-charges dealer for full VAT-inclusive order value OK "
     f"(đơn #{oid1}: {order1['value_vnd']} đ)")

# ---- 2. chốt WITH a cọc records charge + offsetting payment, net balance -------
dl2 = store.create_customer("Đại lý Có Cọc", "0955666777", "DL")
qid2 = store.create_quote_header(dl2, deposit_vnd=2_000_000)
store.add_quote_item(qid2, "cua_cuon", "Cửa cuốn công nghệ Úc", "Tole màu 5.2 zem blusc",
                     3000, 2200, 10_000_000, True)
r = client.post(f"/bao-gia/{qid2}/trang-thai", data={"trang_thai": "won"}, follow_redirects=False)
assert r.status_code == 303
q2 = store.get_quote(qid2)
oid2 = q2["order_id"]
order2 = store.get_order(oid2)
expected_balance = order2["value_vnd"] - 2_000_000
assert store.dealer_balance(dl2) == expected_balance, (
    f"cọc not netted against auto-charge: balance={store.dealer_balance(dl2)}, expected={expected_balance}")
ledger = store.ledger_for_dealer(dl2)
kinds = sorted(e["entry_type"] for e in ledger)
assert kinds == ["charge", "payment"], f"expected one charge + one payment entry, got {kinds}"
print(f"2. chốt with cọc nets charge - cọc = {expected_balance} đ (charge+payment both recorded) OK")

# ---- 3. KH customers never get debt_entries rows (dealer-only behavior) -------
kh = store.create_customer("Anh KH Thường", "0966777888", "KH")
qid3 = store.create_quote_header(kh)
store.add_quote_item(qid3, "cua_cuon", "Cửa cuốn công nghệ Úc", "Tole màu 5.2 zem blusc",
                     3000, 2200, 8_000_000, True)
r = client.post(f"/bao-gia/{qid3}/trang-thai", data={"trang_thai": "won"}, follow_redirects=False)
assert r.status_code == 303
assert store.dealer_balance(kh) == 0, "KH customer must never get a debt_entries charge"
assert store.ledger_for_dealer(kh) == [], "KH customer must have an empty sổ nợ ledger"
print("3. KH chốt does NOT touch debt_entries (dealer-only auto-charge) OK")

# ---- 4. manual "+ Ghi nợ" still works on top of the auto-charge -----------------
r = client.post(f"/cong-no/{dl1}/them", data={"entry_type": "payment", "amount_vnd": "1.000.000"},
                follow_redirects=False)
assert r.status_code == 303
assert store.dealer_balance(dl1) == order1["value_vnd"] - 1_000_000, "manual payment on top of auto-charge broken"
print("4. manual + Ghi nợ still works on top of the auto-charge OK")

# ---- 5. daily_report counts a dealer cọc exactly once ---------------------------
# Regression: the ĐL cọc used to land in BOTH order_payments (carried onto the
# order) and debt_entries (offsetting payment), and daily_report summed both —
# inflating tiền thu trong ngày by every dealer cọc.
assert store.order_payments_for(oid2) == [], (
    "ĐL cọc must not be written to order_payments (dealer money lives in debt_entries)")
store.add_order_payment(oid3 := store.get_quote(qid3)["order_id"], "thanh_toan", 4_000_000)
report = store.daily_report(store.today_vn())
coc_rows = [p for p in report["thu_list"] if p["name"] == "Đại lý Có Cọc"]
assert len(coc_rows) == 1 and coc_rows[0]["amount_vnd"] == 2_000_000, (
    f"dealer cọc must appear exactly once in the daily report, got {coc_rows}")
# 2M dl2 cọc + 1M dl1 manual payment (test 4) + 4M KH payment just recorded
assert report["thu_total"] == 7_000_000, (
    f"thu_total must count each real payment once: expected 7.000.000, got {report['thu_total']}")
print("5. daily_report counts dealer cọc once (no order_payments phantom) OK")

print("ALL PHASE 10 SMOKE TESTS PASSED")
