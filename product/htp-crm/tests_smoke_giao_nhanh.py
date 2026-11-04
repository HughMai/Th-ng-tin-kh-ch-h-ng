"""Smoke test: closing đơn hàng from the Sổ đơn hàng list, without opening each one.

1. Each active card on /don-hang carries its own next-step button (Sản xuất xong →
   Đã giao) wired to swap just that card in place.
2. Đã giao stamps ngày lắp = today when it's blank (or still in the future), so a
   delivered đơn enters bảo hành / xin đánh giá / bảo trì / còn nợ — before this,
   65% of đã giao đơn in production had no install_date and fell out of all four.
3. Startup does NOT backfill ngày lắp (see section 5 for why).
4. The đơn page wraps Tiến độ in swap targets and renders it above the editors.

Runs against a throwaway temp SQLite DB — never touches data/htp.db.
Run with: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe tests_smoke_giao_nhanh.py
"""
import os
import sqlite3
import tempfile
from pathlib import Path

_tmp_dir = tempfile.mkdtemp(prefix="htp_crm_test_")
os.environ["DB_PATH"] = str(Path(_tmp_dir) / "test.db")
os.environ["FAMILY_PASSWORD"] = "test-pass-123"
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["COOKIE_INSECURE"] = "1"
os.environ["BOT_URL"] = ""

from fastapi.testclient import TestClient

import app
import store

client = TestClient(app.app)
r = client.post("/login", data={"password": "test-pass-123"}, follow_redirects=False)
assert r.status_code == 303, f"login failed: {r.status_code}"

TODAY = store.today_vn()
cid = store.create_customer("Chú Bảy — Lê Lợi", "0905333444", "KH")


def order(desc: str, install: str = None) -> int:
    oid = store.create_order(cid, "cua_cuon", desc, 12_000_000, install)
    store.add_order_item(oid, product="cua_cuon", description=desc, ngang_mm=3000,
                         cao_mm=2800, mau_sac="ghi", thanh_tien=12_000_000)
    return oid


# ---- 1. list cards carry their own next-step button --------------------------
o1, o2 = order("Đơn A"), order("Đơn B")
store.set_order_stage(o2, "dang_san_xuat")
page = client.get("/don-hang").text
assert f'id="don-{o1}"' in page and f'id="don-{o2}"' in page, "cards need swap ids"
assert f'data-swap="don-{o2}"' in page, "card actions must swap their own card"
card1 = page.split(f'id="don-{o1}"')[1].split('id="don-')[0]
card2 = page.split(f'id="don-{o2}"')[1].split('id="don-')[0]
assert "Sản xuất xong</button>" in card1, "chờ sản xuất card should offer Sản xuất xong"
assert "Đã giao</button>" in card2, "sản xuất xong card should offer Đã giao"
print("1. per-card next-step buttons OK")

# ---- 2. Đã giao from the list stays on the list and stamps ngày lắp ----------
assert store.get_order(o2)["install_date"] is None
r = client.post(f"/don-hang/{o2}/giai-doan", data={"stage": "dang_lap", "next": "/don-hang"},
                follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == "/don-hang", "must return to the list"
o = store.get_order(o2)
assert o["stage"] == "dang_lap" and o["install_date"] == TODAY, o
done_card = client.get("/don-hang").text.split(f'id="don-{o2}"')[1].split('id="don-')[0]
assert "Đã giao</button>" not in done_card, "a done card must not offer Đã giao again"
print("2. Đã giao stamps ngày lắp OK")

# ---- 3. a planned date: past is kept, future is pulled back to today ----------
from views import _add_days
past, future = _add_days(TODAY, -3), _add_days(TODAY, 5)
o3, o4 = order("Đơn quá hẹn", past), order("Đơn giao sớm", future)
store.set_order_stage(o3, "dang_lap")
store.set_order_stage(o4, "dang_lap")
assert store.get_order(o3)["install_date"] == past, "a real past ngày lắp must be kept"
assert store.get_order(o4)["install_date"] == TODAY, "can't have been installed in the future"
store.set_order_stage(o1, "dang_san_xuat")
assert store.get_order(o1)["install_date"] is None, "only đã giao stamps a date"
print("3. planned-date handling OK")

# ---- 4. đã giao now feeds the follow-up queues --------------------------------
assert any(x["id"] == o2 for x in store.orders_review_due(TODAY)), "should queue a review ask"
print("4. đã giao enters Xin đánh giá OK")

# ---- 5. a restart leaves a blank ngày lắp blank ------------------------------
# 69 đơn bulk-ticked đã giao on 04/11 were deliberately left without a ngày lắp
# (their tick day isn't their delivery day). A one-off care-log backfill ran once
# in production; nothing at startup may refill them.
o5 = order("Đơn cũ")
with sqlite3.connect(os.environ["DB_PATH"]) as db:
    db.execute("UPDATE orders SET stage='dang_lap', install_date=NULL WHERE id=?", (o5,))
    db.execute("INSERT INTO touches (customer_id, order_id, kind, detail, created_at) "
               "VALUES (?, ?, 'trang_thai', 'dang_san_xuat → dang_lap', '2026-08-09 20:30:00')",
               (cid, o5))
store.configure(os.environ["DB_PATH"])
assert store.get_order(o5)["install_date"] is None, "startup must not backfill ngày lắp"
print("5. no startup backfill OK")

# ---- 6. đơn page: Tiến độ above the editors, wrapped for in-place swaps ------
detail = client.get(f"/don-hang/{o1}").text
for anchor in ('id="don-head"', 'id="tien-do"', 'id="lap-dat"'):
    assert anchor in detail, f"missing swap target {anchor}"
assert detail.index('id="tien-do"') < detail.index("Sửa hạng mục / thêm dòng"), \
    "Tiến độ should sit above the hạng mục editor"
assert 'data-swap="don-head tien-do lap-dat"' in detail, "stepper should swap in place"
print("6. đơn page layout OK")

print("ALL GIAO NHANH SMOKE TESTS PASSED")
