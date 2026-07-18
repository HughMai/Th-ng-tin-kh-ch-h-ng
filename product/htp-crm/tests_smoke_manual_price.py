"""Smoke test: "Giá đặc biệt" manual đơn-giá override.

A door with a valid catalog match can now be force-priced by a hand-entered
đơn giá (đ/m²) — width × height × đơn giá, no surcharges — via the "Giá đặc
biệt (nhập tay)" toggle. Covers both entry points: the new-customer intake
wizard (/khach/tiep-nhan) and the add-item form on an existing quote
(/bao-gia/{id}/hang-muc/moi). Regression guard that the override actually beats
the catalog price (server owns the total).

Runs against a throwaway temp SQLite DB — never touches data/htp.db. Env vars
must be set BEFORE `import app`.

Run with: .venv/Scripts/python.exe tests_smoke_manual_price.py
"""
import json
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
import pricing
import store

client = TestClient(app.app)
r = client.post("/login", data={"password": "test-pass-123"}, follow_redirects=False)
assert r.status_code == 303, f"login failed: {r.status_code} {r.text}"

# A KH catalog match: Cửa cuốn công nghệ Đức | KV 380 = 1450 (nghìn đ/m²).
# 3000×2200 door → catalog 9,702,000đ (base 9,570,000 + small-door 132,000).
# Manual đơn giá 1,300,000 đ/m² → 8,580,000đ. Different ⇒ override took effect.
CN, MAU = "Cửa cuốn công nghệ Đức", "KV 380"
NGANG, CAO = 3000, 2200
DONGIA = 1_300_000
MANUAL_TOTAL = 8_580_000

# ---- 1. unit math ---------------------------------------------------------------
assert pricing.manual_line_total(DONGIA, NGANG, CAO) == MANUAL_TOTAL, \
    f"manual_line_total wrong: {pricing.manual_line_total(DONGIA, NGANG, CAO)}"
CATALOG_TOTAL = pricing.line_total(1450, NGANG, CAO, 20_000, 0)
assert CATALOG_TOTAL == 9_702_000 and CATALOG_TOTAL != MANUAL_TOTAL, \
    f"catalog baseline unexpected: {CATALOG_TOTAL}"
print(f"1. manual_line_total({DONGIA},{NGANG},{CAO}) == {MANUAL_TOTAL}đ "
      f"(catalog would be {CATALOG_TOTAL}đ) OK")


def _submit_intake(name, phone, unit_extra):
    unit = {"product": "cua_cuon", "cong_nghe": CN, "mau": MAU,
            "ngang": NGANG, "cao": CAO, "mau_sac": "", "ghi_chu": ""}
    unit.update(unit_extra)
    r = client.post("/khach/tiep-nhan", data={
        "name": name, "phone": phone, "type": "KH",
        "units": json.dumps([unit])}, follow_redirects=False)
    assert r.status_code == 303, f"intake failed: {r.status_code} {r.text[:300]}"
    qid = int(r.headers["location"].rsplit("/", 1)[1])
    items = store.quote_items_for(qid)
    assert len(items) == 1, f"expected 1 item, got {len(items)}"
    return qid, items[0]

# ---- 2. wizard with the override ON — manual đơn giá beats the catalog ----------
_, item = _submit_intake("Khách Quen", "0900000001",
                         {"manual": "1", "gia_manual": "1.300.000"})
assert item["thanh_tien"] == MANUAL_TOTAL and item["is_manual_price"] == 1, \
    f"wizard override failed: thanh_tien={item['thanh_tien']} is_manual={item['is_manual_price']}"
print(f"2. wizard 'Giá đặc biệt' → {item['thanh_tien']}đ, is_manual=1 (beat catalog) OK")

# ---- 3. wizard with the override OFF — catalog price is untouched ---------------
_, item = _submit_intake("Khách Thường", "0900000002", {})
assert item["thanh_tien"] == CATALOG_TOTAL and item["is_manual_price"] == 0, \
    f"catalog path regressed: thanh_tien={item['thanh_tien']} is_manual={item['is_manual_price']}"
print(f"3. wizard no toggle → catalog {item['thanh_tien']}đ, is_manual=0 (unchanged) OK")

# ---- 4. add-item form on an existing quote honors manual=1 ----------------------
cid = store.create_customer("Khách Sửa Giá", "0900000003", "KH")
qid = store.create_quote_header(cid)
r = client.post(f"/bao-gia/{qid}/hang-muc/moi", data={
    "loai_cua": "cua_cuon", "cong_nghe": CN, "mau": MAU,
    "ngang": str(NGANG), "cao": str(CAO),
    "gia_thu_cong": "1.300.000", "manual": "1"}, follow_redirects=False)
assert r.status_code == 303, f"add-item failed: {r.status_code} {r.text[:300]}"
item = store.quote_items_for(qid)[0]
assert item["thanh_tien"] == MANUAL_TOTAL and item["is_manual_price"] == 1, \
    f"add-item override failed: thanh_tien={item['thanh_tien']} is_manual={item['is_manual_price']}"
print(f"4. add-item form manual=1 → {item['thanh_tien']}đ, is_manual=1 OK")

print("ALL MANUAL-PRICE SMOKE TESTS PASSED")
