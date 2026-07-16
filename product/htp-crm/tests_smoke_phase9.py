"""Smoke test for Phase 9: sổ đơn hàng view — category filter chips (?loai=)
+ ngày-chốt timeline (newest day first, 🔥 Gấp pinned) + paid/unpaid chip.

Runs against a throwaway temp SQLite DB — never touches data/htp.db.

Run with: .venv/Scripts/python.exe tests_smoke_phase9.py
"""
import os
import re
import sqlite3
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

# ---- seed: 4 active orders (2 cửa cuốn, 1 cửa kéo gấp, 1 nhôm kính) + 1 done ----
kh_cuong = store.create_customer("Anh Cường", "0901000001", "KH", "gioi_thieu")
kh_hoa = store.create_customer("Chị Hoa", "0901000002", "KH", "facebook")
kh_nam = store.create_customer("Anh Nam", "0901000003", "KH", "gioi_thieu")
kh_sang = store.create_customer("Chú Sang", "0901000004", "KH", "gioi_thieu")
kh_binh = store.create_customer("Cô Bình", "0901000005", "KH", "gioi_thieu")

o_today = store.create_order(kh_cuong, "cua_cuon", "Cửa cuốn nhà chính", value_vnd=11_000_000)
o_old = store.create_order(kh_hoa, "cua_cuon", "Cửa cuốn gara", value_vnd=9_000_000)
o_nk = store.create_order(kh_nam, "nhom_kinh", "Bộ cửa nhôm kính", value_vnd=25_000_000)
o_urgent = store.create_order(kh_sang, "cua_keo", "Cửa kéo kho", value_vnd=7_000_000)
o_done = store.create_order(kh_binh, "cua_cuon", "Cửa cuốn cũ", value_vnd=5_000_000)
store.set_order_urgent(o_urgent, True)
store.set_order_stage(o_done, "hoan_thanh")

# backdate one order 4 days so the timeline has more than one date group
db = sqlite3.connect(os.environ["DB_PATH"])
db.execute("UPDATE orders SET created_at = datetime('now', '-4 days') WHERE id = ?", (o_old,))
db.commit()
db.close()

# ---- chips + counts (Khác hidden when no khác orders exist) ----------------------
r = client.get("/don-hang")
assert r.status_code == 200
html = r.text
for chunk in ("Tất cả (4)", "Cửa cuốn (2)", "Cửa kéo (1)", "Nhôm kính (1)"):
    assert chunk in html, f"missing chip {chunk!r}"
assert "Khác (" not in html, "Khác chip should be hidden when no khác orders exist"
assert 'class="on" href="/don-hang"' in html, "Tất cả chip should be active by default"

# ---- timeline: newest day first, 🔥 Gấp pinned above, urgent card only once ------
assert "· Hôm nay" in html, "today's group header missing"
assert "· 4 ngày trước" in html, "backdated group header missing"
assert html.index("· Hôm nay") < html.index("· 4 ngày trước"), "newest group must come first"
assert "Gấp (1)" in html, "urgent section header missing"
assert html.index("Gấp (1)") < html.index("· Hôm nay"), "urgent section must be pinned on top"
assert html.count("Chú Sang") == 1, "urgent order must not also appear in the date groups"

# ---- ?loai= narrows both active list and archive ---------------------------------
r = client.get("/don-hang?loai=cua_cuon")
assert r.status_code == 200
html = r.text
assert "Anh Cường" in html and "Chị Hoa" in html, "cửa cuốn orders missing under filter"
assert "Cô Bình" in html and "Đã hoàn thành (1)" in html, "archive should be filtered too"
assert "Anh Nam" not in html and "Chú Sang" not in html, "other products must be filtered out"
assert 'class="on" href="/don-hang?loai=cua_cuon"' in html, "active chip should be Cửa cuốn"

# ---- valid-but-empty category shows its own empty state --------------------------
r = client.get("/don-hang?loai=khac")
assert r.status_code == 200
assert "Chưa có đơn sản phẩm nào đang làm." in r.text, "per-category empty state missing"
assert "Đã hoàn thành" not in r.text, "archive block should be omitted when empty"

# ---- bogus param coerces to Tất cả ------------------------------------------------
r = client.get("/don-hang?loai=xyz")
assert r.status_code == 200
for name in ("Anh Cường", "Chị Hoa", "Anh Nam", "Chú Sang"):
    assert name in r.text, f"bogus loai should show everything, missing {name}"

# ---- quick-pay keeps the filter (hidden next input) -------------------------------
r = client.get("/don-hang?loai=cua_keo")
assert 'value="/don-hang?loai=cua_keo"' in r.text, "quick-pay next should keep the filter"

# ---- paid chip: còn nợ → Đã thu đủ after thu-du -----------------------------------
r = client.get("/don-hang")
assert "còn nợ" in r.text, "unpaid order should show còn nợ chip"
r = client.post(f"/don-hang/{o_today}/thu-du", data={"next": "/don-hang"}, follow_redirects=False)
assert r.status_code == 303, f"thu-du failed: {r.status_code} {r.text}"
r = client.get("/don-hang")
assert "Đã thu đủ" in r.text, "settled order should show đã thu đủ chip"

# ---- regressions ------------------------------------------------------------------
rows = store.orders_active()
assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", o["chot_date"]) for o in rows), \
    f"chot_date malformed: {[o.get('chot_date') for o in rows]}"
r = client.get(f"/don-hang/{o_today}")
assert r.status_code == 200, "order detail page broken"

print("ALL PHASE 9 SMOKE TESTS PASSED")
