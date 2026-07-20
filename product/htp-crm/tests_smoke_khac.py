"""Smoke test: "Khác" free-form quote items + 10-digit phone field.

The intake wizard's step 2 now has a "Khác" row: custom product name (rides in
quote_items.cong_nghe), kích thước, and a hand-entered đơn giá (đ/m²) — always
manual-priced, no catalog. Covers: the one-time CHECK-constraint rebuild
migration (legacy DBs only allowed nhom_kinh/cua_cuon/cua_keo/xingfa), the
wizard POST, display via _door_desc/baogia._describe, the add/edit hạng mục
form round-trip (an edit must NOT silently convert khac to cua_cuon), and the
chốt snapshot onto order_items.

Runs against a throwaway temp SQLite DB — never touches data/htp.db. Env vars
must be set BEFORE `import app`. The DB is pre-seeded with the LEGACY
quote_items DDL so the startup migration path (what runs on the VPS on deploy)
is exercised for real.

Run with: .venv/Scripts/python.exe tests_smoke_khac.py
"""
import json
import os
import sqlite3
import tempfile
from pathlib import Path

_tmp_dir = tempfile.mkdtemp(prefix="htp_crm_test_")
_db = str(Path(_tmp_dir) / "test.db")
os.environ["DB_PATH"] = _db
os.environ["FAMILY_PASSWORD"] = "test-pass-123"
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["COOKIE_INSECURE"] = "1"

# ---- 0. pre-seed the LEGACY quote_items schema (no 'khac', no mau_sac/ghi_chu)
# so importing app exercises the real column + CHECK-rebuild migrations.
_pre = sqlite3.connect(_db)
_pre.execute(
    """
    CREATE TABLE quote_items (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        quote_id        INTEGER NOT NULL REFERENCES quotes(id),
        product         TEXT NOT NULL CHECK (product IN ('nhom_kinh','cua_cuon','cua_keo','xingfa')),
        cong_nghe       TEXT,
        mau             TEXT,
        ngang_mm        INTEGER NOT NULL,
        cao_mm          INTEGER NOT NULL,
        thanh_tien      INTEGER NOT NULL,
        is_manual_price INTEGER NOT NULL DEFAULT 0,
        sort_order      INTEGER NOT NULL DEFAULT 0
    )
    """
)
_pre.execute(
    "INSERT INTO quote_items (quote_id, product, cong_nghe, mau, ngang_mm, cao_mm, thanh_tien) "
    "VALUES (99, 'cua_cuon', 'Cửa cuốn công nghệ Đức', 'KV 380', 3000, 2200, 9702000)"
)
_pre.commit()
_pre.close()

from fastapi.testclient import TestClient

import app
import store
import views

# migration ran at import: CHECK now includes 'khac', legacy row survived
_sql = sqlite3.connect(_db).execute(
    "SELECT sql FROM sqlite_master WHERE type='table' AND name='quote_items'"
).fetchone()[0]
assert "'khac'" in _sql, f"CHECK rebuild missing 'khac': {_sql}"
_legacy = store.quote_items_for(99)
assert len(_legacy) == 1 and _legacy[0]["mau"] == "KV 380" and _legacy[0]["thanh_tien"] == 9702000, \
    f"legacy row lost/corrupted by rebuild: {_legacy}"
print("0. legacy-DB rebuild migration: CHECK includes 'khac', old row intact OK")

client = TestClient(app.app)
r = client.post("/login", data={"password": "test-pass-123"}, follow_redirects=False)
assert r.status_code == 303, f"login failed: {r.status_code}"

# ---- 1. wizard intake with a Khác unit: 500.000 đ/m² × 3.0m × 2.2m = 3.300.000
TEN = "Mái tôn chống nóng"
unit = {"product": "khac", "ten": TEN, "cong_nghe": "", "mau": "", "mau_sac": "",
        "ghi_chu": "lắp thêm phía sau", "ngang": 3000, "cao": 2200,
        "manual": "1", "gia_manual": "500.000"}
r = client.post("/khach/tiep-nhan", data={
    "name": "Khách Khác", "phone": "0912345678", "type": "KH",
    "units": json.dumps([unit])}, follow_redirects=False)
assert r.status_code == 303, f"intake failed: {r.status_code} {r.text[:300]}"
qid = int(r.headers["location"].rsplit("/", 1)[1])
items = store.quote_items_for(qid)
assert len(items) == 1, f"expected 1 item, got {len(items)}"
it = items[0]
assert it["product"] == "khac" and it["cong_nghe"] == TEN, f"khac item wrong: {it}"
assert it["thanh_tien"] == 3_300_000 and it["is_manual_price"] == 1, \
    f"khac pricing wrong: thanh_tien={it['thanh_tien']} is_manual={it['is_manual_price']}"
print(f"1. wizard Khác unit → product=khac, tên='{TEN}', 3.300.000đ manual OK")

# ---- 2. display: custom name (not the raw 'khac' key) everywhere
assert views._door_desc(it) == TEN, f"_door_desc: {views._door_desc(it)!r}"
import baogia
assert baogia._describe(it) == TEN, f"baogia._describe: {baogia._describe(it)!r}"
r = client.get(f"/bao-gia/{qid}")
assert r.status_code == 200 and TEN in r.text, "build page missing custom name"
print("2. _door_desc / baogia._describe / build page show the custom name OK")

# ---- 3. add-item form accepts khac (name posts as cong_nghe, manual price required)
r = client.post(f"/bao-gia/{qid}/hang-muc/moi", data={
    "loai_cua": "khac", "cong_nghe": "Lưới an toàn ban công", "mau": "",
    "ngang": "2000", "cao": "1500", "gia_thu_cong": "400.000"}, follow_redirects=False)
assert r.status_code == 303, f"add khac item failed: {r.status_code} {r.text[:300]}"
items = store.quote_items_for(qid)
assert len(items) == 2 and items[1]["product"] == "khac" \
    and items[1]["thanh_tien"] == 1_200_000, f"add-item khac wrong: {items[1]}"
print("3. hạng mục form: add loai_cua=khac → 1.200.000đ manual OK")

# ---- 4. edit round-trip keeps product=khac (regression: must not become cua_cuon)
iid = items[1]["id"]
r = client.post(f"/bao-gia/{qid}/hang-muc/{iid}/sua", data={
    "loai_cua": "khac", "cong_nghe": "Lưới an toàn (sửa)", "mau": "",
    "ngang": "2000", "cao": "1500", "gia_thu_cong": "450.000"}, follow_redirects=False)
assert r.status_code == 303, f"edit khac item failed: {r.status_code} {r.text[:300]}"
edited = store.get_quote_item(iid)
assert edited["product"] == "khac" and edited["cong_nghe"] == "Lưới an toàn (sửa)" \
    and edited["thanh_tien"] == 1_350_000, f"edit round-trip wrong: {edited}"
# edit form page renders (prefill path)
r = client.get(f"/bao-gia/{qid}/hang-muc/{iid}/sua")
assert r.status_code == 200, f"edit form failed: {r.status_code}"
print("4. edit round-trip: stays khac, name+price updated, form renders OK")

# ---- 5. chốt → order_items snapshot keeps the khac lines
r = client.post(f"/bao-gia/{qid}/trang-thai", data={"trang_thai": "won"}, follow_redirects=False)
assert r.status_code == 303, f"chốt failed: {r.status_code} {r.text[:300]}"
oid = store.get_quote(qid)["order_id"]
assert oid, "no order created on chốt"
o_items = store.order_items_for(oid)
khac_lines = [i for i in o_items if i["product"] == "khac" and i["cong_nghe"]]
assert len(khac_lines) == 2, f"khac lines lost in snapshot: {o_items}"
assert views._door_desc(khac_lines[0]) == TEN
print("5. chốt snapshot: khac lines on order, named correctly OK")

print("ALL KHAC SMOKE TESTS PASSED")
