"""Smoke test for Phase 8: merge same-phone duplicate customers.

Two customers share a phone (the "⚠️ Trùng SĐT" case). Merging the dup into the
survivor must reassign every related row (quote / order / reminder / debt entry /
touch), backfill the survivor's missing contact fields (Zalo link, email, …),
promote the survivor to "khách chính" if the dup was one, and delete the dup.

Runs against a throwaway temp SQLite DB — never touches data/htp.db.

Run with: .venv/Scripts/python.exe tests_smoke_phase8.py
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

PHONE = "0945042938"

# ---- survivor: a plain lead with one quote --------------------------------------
surv = store.create_customer("Mai Hugh", PHONE, "KH", "gioi_thieu")
qid = store.create_quote_header(surv)
store.add_quote_item(qid, "cua_cuon", "Cửa cuốn công nghệ Đức", "KV thường",
                     4000, 3000, 12_000_000, True)

# ---- dup: same phone, but the richer record (Zalo link, email, an order) --------
dup = store.create_customer("vaihug", PHONE, "KH", "gioi_thieu",
                            address="12 Trần Phú", email="hug@example.com")
store.set_customer_zalo_user_id(dup, "zalo-uid-xyz")
store.add_touch(dup, "zalo", "nhắn hỏi giá")
store.create_reminder(dup, store.today_vn(), "gọi lại")
oid = store.create_order(dup, "cua_cuon", "Cửa cuốn 4x3", value_vnd=12_000_000)
assert store.get_customer(dup)["stage"] == "customer", "dup should be khách chính after an order"

# sanity: the dup is surfaced as a phone duplicate of the survivor
dups = store.customers_sharing_phone(surv)
assert [d["id"] for d in dups] == [dup], f"phone-dup detection wrong: {dups}"

# ---- merge via the HTTP route (survivor <- dup) ---------------------------------
r = client.post(f"/khach/{surv}/gop", data={"dup_id": dup}, follow_redirects=False)
assert r.status_code == 303, f"merge route failed: {r.status_code} {r.text}"

# ---- dup gone, everything reassigned to survivor --------------------------------
assert store.get_customer(dup) is None, "dup customer should be deleted"
assert not store.customers_sharing_phone(surv), "no duplicates should remain"

sc = store.get_customer(surv)
assert store.quotes_for_customer(surv), "survivor should keep its own quote"
assert [o["id"] for o in store.orders_for_customer(surv)] == [oid], "dup's order not reassigned"
assert store.reminders_for_customer(surv), "dup's reminder not reassigned"
assert any(t["detail"] == "nhắn hỏi giá" for t in store.list_touches(surv)), "dup's touch not reassigned"

# ---- survivor backfilled from the dup + promoted --------------------------------
assert sc["zalo_user_id"] == "zalo-uid-xyz", "survivor should inherit the Zalo link"
assert sc["email"] == "hug@example.com", "survivor should inherit the email"
assert sc["address"] == "12 Trần Phú", "survivor should inherit the address"
assert sc["stage"] == "customer", "survivor should be promoted to khách chính"
assert sc["name"] == "Mai Hugh", "survivor's own name/phone must be kept"

# ---- guards ---------------------------------------------------------------------
assert store.merge_customers(surv, surv) is False, "self-merge must be rejected"
assert store.merge_customers(surv, 999999) is False, "unknown dup must be rejected"

print("ALL PHASE 8 SMOKE TESTS PASSED")
