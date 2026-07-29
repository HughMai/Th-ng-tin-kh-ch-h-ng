"""Smoke test: a mixed đơn hàng files under EVERY loại cửa it contains.

Reproduces the anh Dậu case — 1 cửa kéo + 2 cửa nhôm kính on one đơn. The
single orders.product column becomes 'khac' for any mixed job, so the Sổ đơn
hàng chips used to hide that đơn from both Cửa kéo and Nhôm kính. Filtering now
goes through the hạng mục (store._with_products).

Runs against a throwaway temp SQLite DB — never touches data/htp.db.

Run with: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe tests_smoke_don_hang_mixed.py
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

# ---- anh Dậu: một báo giá, 1 cửa kéo + 2 cửa nhôm kính --------------------------
kh_dau = store.create_customer("Anh Dậu", "0901000009", "KH", "gioi_thieu")
q_dau = store.create_quote_header(kh_dau)
store.add_quote_item(q_dau, "cua_keo", "Có lá", "8zem", 3000, 2200, 4_620_000, False)
store.add_quote_item(q_dau, "nhom_kinh", "Nhôm Việt", "Cửa đi 1.4ly", 1200, 2200, 6_336_000, False)
store.add_quote_item(q_dau, "nhom_kinh", "Nhôm Việt", "Cửa sổ 1.4ly", 1000, 1200, 2_760_000, False)
o_dau = store.create_order_from_quote(q_dau)
assert o_dau, "chốt should create an đơn hàng"

# The single column still collapses to 'khac' — that's the summary, untouched.
assert store.get_order(o_dau)["product"] == "khac"

# ...but the đơn now knows its real mix.
active = store.orders_active()
dau = next(o for o in active if o["id"] == o_dau)
assert dau["products"] == ["cua_keo", "nhom_kinh"], dau["products"]

# ---- a single-type đơn keeps exactly one loại (phụ kiện must not add 'khac') ----
kh_nam = store.create_customer("Anh Nam", "0901000010", "KH", "gioi_thieu")
q_nam = store.create_quote_header(kh_nam, accessories="Bình Tích Điện x1")
store.add_quote_item(q_nam, "cua_cuon", "Cửa cuốn công nghệ Đức", "KV 380", 3000, 2200,
                     9_570_000, False)
o_nam = store.create_order_from_quote(q_nam)
nam = next(o for o in store.orders_active() if o["id"] == o_nam)
assert nam["products"] == ["cua_cuon"], f"phụ kiện leaked into the loại mix: {nam['products']}"

# ---- a legacy lump đơn (no hạng mục) falls back to orders.product --------------
kh_cu = store.create_customer("Cô Cũ", "0901000011", "KH", "gioi_thieu")
o_cu = store.create_order(kh_cu, "cua_keo", "Cửa kéo cũ", value_vnd=5_000_000)
cu = next(o for o in store.orders_active() if o["id"] == o_cu)
assert cu["products"] == ["cua_keo"], cu["products"]

# ---- the chips: anh Dậu shows under Cửa kéo AND Nhôm kính ----------------------
for loai in ("cua_keo", "nhom_kinh"):
    r = client.get(f"/don-hang?loai={loai}")
    assert r.status_code == 200
    assert "Anh Dậu" in r.text, f"mixed đơn missing from the {loai} chip"

# ...and not under Cửa cuốn.
r = client.get("/don-hang?loai=cua_cuon")
assert "Anh Dậu" not in r.text, "mixed đơn leaked into a loại it has no hạng mục for"
assert "Anh Nam" in r.text

# ---- Khác chip stays empty: phụ kiện lines are not a loại cửa ------------------
r = client.get("/don-hang?loai=khac")
assert "Anh Nam" not in r.text, "a phụ kiện line put a cửa cuốn đơn under Khác"
assert "Anh Dậu" not in r.text, "a mixed đơn should file under its real loại, not Khác"

# ---- chip counts follow the same rule -----------------------------------------
r = client.get("/don-hang")
assert "Cửa kéo (2)" in r.text, "chip count should include the mixed đơn"   # Dậu + Cô Cũ
assert "Nhôm kính (1)" in r.text
assert "Cửa cuốn (1)" in r.text
assert "Tất cả (3)" in r.text, "Tất cả still counts đơn, not hạng mục"

# ---- the card sub-line names the real mix -------------------------------------
assert "Cửa kéo + cửa nhôm kính" in r.text, "mixed đơn card should list both loại"

print("OK — tests_smoke_don_hang_mixed")
