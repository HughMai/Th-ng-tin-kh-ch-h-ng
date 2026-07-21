"""Smoke test for Phase 4: formal báo giá numbers (BG-2026-0042) + order
payments (order_payments table, deposit carryover on chốt, "Khách lẻ còn nợ"
Hôm nay section).

Runs against a throwaway temp SQLite DB — never touches data/htp.db. Env vars
must be set BEFORE `import app` (app.py reads DB_PATH/... at import time and
calls store.configure(DB_PATH) eagerly).

Run with: .venv/Scripts/python.exe tests_smoke_phase4.py
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

YEAR = store.today_vn()[:4]

# ---- 1. báo giá number: format, on build page, in export filename -------------
cid = store.create_customer("Anh Số — Trần Phú", "0901112233", "KH")
qid = store.create_quote_header(cid)
store.add_quote_item(qid, "cua_cuon", "Cửa cuốn công nghệ Đức", "KV thường",
                     4000, 3000, 12_000_000, True, "ghi sần")
so = store.bao_gia_so(qid, store.today_vn())
assert so == f"BG-{YEAR}-{qid:04d}", f"unexpected số báo giá: {so}"
r = client.get(f"/bao-gia/{qid}")
assert r.status_code == 200 and so in r.text, f"số báo giá not on build page: {so}"
r = client.get(f"/bao-gia/{qid}/xuat")
assert r.status_code == 200, f"export failed: {r.status_code}"
assert so in r.headers.get("content-disposition", ""), "số báo giá not in export filename"
print(f"1. bao gia number OK ({so})")

# ---- 2. deposit carries onto the order as a 'coc' payment on chốt --------------
qid2 = store.create_quote_header(cid, accessories="", deposit_vnd=2_000_000)
store.add_quote_item(qid2, "cua_keo", "Có lá", "6zem", 3000, 2500, 8_000_000, True)
def vat_incl(v):  # an order's value_vnd is VAT-inclusive (subtotal + 10%)
    return v + round(v * 0.1)
qval = vat_incl(store.get_quote(qid2)["value_vnd"])  # order carries VAT; quote is pre-VAT
r = client.post(f"/bao-gia/{qid2}/trang-thai", data={"trang_thai": "won"},
                follow_redirects=False)
assert r.status_code == 303, f"chốt failed: {r.status_code} {r.text}"
oid = store.get_quote(qid2)["order_id"]
pays = store.order_payments_for(oid)
assert len(pays) == 1 and pays[0]["kind"] == "coc" and pays[0]["amount_vnd"] == 2_000_000, \
    f"deposit not carried as coc payment: {pays}"
o = store.get_order(oid)
assert o["paid_vnd"] == 2_000_000, f"paid_vnd wrong: {o['paid_vnd']}"
assert o["balance_vnd"] == qval - 2_000_000, f"balance_vnd wrong: {o['balance_vnd']} (qval={qval})"
print(f"2. deposit carryover OK (don #{oid}: paid 2.000.000, balance {o['balance_vnd']})")

# ---- 3. partial payment updates balance (get_order carries paid/balance) -------
store.add_order_payment(oid, "thanh_toan", 1_000_000)
o = store.get_order(oid)
assert o["paid_vnd"] == 3_000_000 and o["balance_vnd"] == qval - 3_000_000, \
    f"balance not updated after payment: paid={o['paid_vnd']} bal={o['balance_vnd']}"
print("3. partial payment recompute OK")

# ---- 4. "Khách lẻ còn nợ": KH order installed long ago, unpaid -> surfaces ------
kh = store.create_customer("Chị Nợ — Nguyễn Trãi", "0902223344", "KH")
oid_kh = store.create_order(kh, "cua_cuon", "Cửa cuốn 3m", 10_000_000, "2020-01-01")
dl = store.create_customer("Đại lý Nợ", "0903334455", "DL")
oid_dl = store.create_order(dl, "cua_cuon", "Đơn đại lý", 10_000_000, "2020-01-01")

due_ids = [o["id"] for o in store.orders_debt_due()]
assert oid_kh in due_ids, "KH unpaid order not in orders_debt_due"
assert oid_dl not in due_ids, "DL order must NOT be in KH orders_debt_due (uses công nợ)"

def _board_col(html: str, label: str) -> str:
    """The one Hôm nay board column headed `label`. A khách can legitimately
    appear in more than one column (a đại lý owes nothing on the KH ledger but
    its cửa still has to be giao), so "name absent from the page" is too coarse
    an assertion — scope it to the column under test."""
    hits = [c for c in html.split('<div class="board-col ') if f">{label}<" in c]
    assert len(hits) == 1, f"expected exactly one '{label}' column, found {len(hits)}"
    return hits[0]


r = client.get("/")
assert "Khách lẻ còn nợ" in r.text, "KH debt section missing on Hôm nay"
kh_col = _board_col(r.text, "Khách lẻ còn nợ")
assert "Chị Nợ" in kh_col, "KH debt section missing the khách"
assert "Đại lý Nợ" not in kh_col, "DL order leaked into Hôm nay KH debt section"
# ...but the đại lý's cửa is 6 years past its ngày giao, so it IS overdue work.
assert "Đại lý Nợ" in _board_col(r.text, "Lắp hôm nay"), "overdue DL order missing from Lắp hôm nay"
print(f"4. Khach le con no surfaces OK (don #{oid_kh}, DL #{oid_dl} excluded)")

# ---- 5. "Đã thu đủ" clears the balance and drops the section -------------------
# oid_kh's value is VAT-inclusive (10.000.000 + 10% = 11.000.000), so settling in
# full means paying 11.000.000.
r = client.post(f"/don-hang/{oid_kh}/thanh-toan",
                data={"amount_vnd": "11.000.000", "kind": "thanh_toan", "next": "/"},
                follow_redirects=False)
assert r.status_code == 303, f"thu đủ failed: {r.status_code} {r.text}"
assert store.get_order(oid_kh)["balance_vnd"] == 0, "balance not zero after full payment"
assert oid_kh not in [o["id"] for o in store.orders_debt_due()], "paid order still due"
r = client.get("/")
assert "Khách lẻ còn nợ" not in r.text, "KH debt section should be gone after full payment"
print("5. thu du clears balance + section OK")

print("ALL PHASE 4 SMOKE TESTS PASSED")
