"""Smoke test: đơn hàng scheduled to install today/overdue must show up as work
on the Hôm nay page.

The Hôm nay board was assembled purely from care + debt follow-ups (nhắc báo
giá, bảo trì, bảo hành, công nợ, xin đánh giá). A đơn hàng with ngày lắp = hôm
nay sat on /don-hang under its "HÔM NAY" day header while Hôm nay itself said
"không có việc cần làm" and counted 0 việc — the one job the family actually had
to do that day was the one the dashboard hid.

Runs against a throwaway temp SQLite DB — never touches data/htp.db.

Run with: .venv/Scripts/python.exe tests_smoke_install_today.py
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


def _yesterday() -> str:
    import datetime
    return (datetime.date.fromisoformat(today) - datetime.timedelta(days=1)).isoformat()


# ---- 1. baseline: empty CRM -> Hôm nay is genuinely empty ---------------------
body = client.get("/").text
assert "Hôm nay không có việc cần làm" in body, "fresh CRM should show the empty state"
print("1. empty Hôm nay OK")

# ---- 2. đơn hàng lắp hôm nay, still chờ sản xuất -> lands on Hôm nay ----------
kh = store.create_customer("Mai Hugh", "0905111222", "KH", "gioi_thieu")
oid = store.create_order(kh, "cua_keo", "Cửa kéo 6zem", value_vnd=6_132_081)
store.set_order_install(oid, today)
assert store.get_order(oid)["stage"] == "cho_san_xuat"

due = [o["id"] for o in store.orders_install_due(today)]
assert oid in due, f"install due today missing from the query: {due}"

body = client.get("/").text
assert "Hôm nay không có việc cần làm" not in body, "Hôm nay still claims there's nothing to do"
assert "Lắp hôm nay" in body, "no 'Lắp hôm nay' column on the board"
assert "Mai Hugh" in body, "the đơn hàng itself is not on the page"
print("2. install-due order shows on Hôm nay OK")

# ---- 3. it counts in the "Việc cần làm hôm nay" stat --------------------------
# The stat is derived from the same lists the board renders, so a card on the
# page that doesn't move the number would be the original bug wearing a hat.
assert '<div class="n">1</div>' in body, "task count did not include the install"
print("3. task count includes the install OK")

# ---- 4. overdue install is flagged, not silently dropped ---------------------
kh2 = store.create_customer("Anh Đạt", "0905111333", "KH", "facebook")
oid2 = store.create_order(kh2, "cua_cuon", "Cửa cuốn nhà chính", value_vnd=12_000_000)
store.set_order_install(oid2, _yesterday())
body = client.get("/").text
assert "Quá hẹn" in body, "overdue install not flagged on the card"
assert "Anh Đạt" in body, "overdue install missing from the board"
print("4. overdue install flagged OK")

# ---- 5. "Đã lắp xong" closes it from Hôm nay and it leaves the column ---------
r = client.post(f"/don-hang/{oid}/giai-doan", data={"stage": "dang_lap", "next": "/"},
                follow_redirects=False)
assert r.status_code == 303, f"stage POST failed: {r.status_code}"
assert r.headers["location"] == "/", f"next= ignored, went to {r.headers['location']}"
assert store.get_order(oid)["stage"] == "dang_lap"
assert oid not in [o["id"] for o in store.orders_install_due(today)], "đã lắp đặt still queued"
print("5. 'Đã lắp xong' closes the card OK")

# ---- 6. an undated đơn hàng never enters this column -------------------------
# It's still chờ sản xuất work (the Zalo "viec" list owns it), but with no ngày
# lắp there is nothing to do about it *today*.
kh3 = store.create_customer("Chị Mai", "0905111444", "KH", "google")
oid3 = store.create_order(kh3, "cua_keo", "Cửa kéo kho", value_vnd=6_000_000)
assert oid3 not in [o["id"] for o in store.orders_install_due(today)], "undated order queued"
assert oid3 in [o["id"] for o in store.orders_for_digest(today)], "undated order missing from viec"
print("6. undated order stays out of Hôm nay, stays in viec OK")

print("ALL INSTALL-TODAY SMOKE TESTS PASSED")
