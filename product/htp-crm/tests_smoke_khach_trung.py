"""Smoke test: repeat khách/đại lý are picked, not retyped — plus the two new
Cửa Kéo khung-U colours.

The intake wizard's step 1 now has a type-ahead (/api/khach/tim). Picking a hit
sends customer_id along with the POST, and the server attaches the báo giá to
that card. Even without a pick, an identical name + phone reuses the existing
card instead of minting a duplicate. Contact details only ever backfill blanks;
ghi chú appends. On a reuse the STORED KH/ĐL type drives the price table, so a
radio left on "Khách lẻ" can't quote a dealer at retail.

Runs against a throwaway temp SQLite DB — never touches data/htp.db. Env vars
must be set BEFORE `import app`.

Run with: .venv/Scripts/python.exe tests_smoke_khach_trung.py
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
import store
import views

client = TestClient(app.app)
r = client.post("/login", data={"password": "test-pass-123"}, follow_redirects=False)
assert r.status_code == 303, f"login failed: {r.status_code}"

NAME, PHONE = "Anh Ba Đại Lý", "0901112233"
dl_id = store.create_customer(NAME, PHONE, "DL", "gioi_thieu")
store.create_customer("Chị Tư Khách Lẻ", "0907778899", "KH", "facebook")
BASE_COUNT = len(store.list_customers())

# Cửa Kéo "Có lá - 1ly" 3000×2200 (6.6m² -> +20k/m² small-door surcharge):
#   KH 760 -> 5.016.000 + 132.000 = 5.148.000
#   ĐL 680 -> 4.488.000 + 132.000 = 4.620.000
KH_TOTAL, DL_TOTAL = 5_148_000, 4_620_000
UNIT = {"product": "cua_keo", "cong_nghe": "Có lá", "mau": "1ly",
        "mau_sac": "U: trắng · Nhíp: ghi · Lá: xám bình thường", "ghi_chu": "",
        "ngang": 3000, "cao": 2200, "manual": "", "gia_manual": ""}

# ---- 1. type-ahead API: phone fragment, folded name, too-short, unauthenticated
hits = client.get("/api/khach/tim", params={"q": "0901"}).json()["hits"]
assert [h["id"] for h in hits] == [dl_id], f"phone search: {hits}"
assert hits[0]["type"] == "DL" and hits[0]["name"] == NAME, f"hit payload wrong: {hits[0]}"
hits = client.get("/api/khach/tim", params={"q": "anh ba"}).json()["hits"]
assert [h["id"] for h in hits] == [dl_id], f"folded-name search: {hits}"
assert client.get("/api/khach/tim", params={"q": "ab"}).json()["hits"] == [], "2 chars must not search"
anon = TestClient(app.app)
assert anon.get("/api/khach/tim", params={"q": "0901"}).status_code == 401, "search not session-gated"
print("1. /api/khach/tim: phone + folded name hit, <3 chars empty, 401 when logged out OK")

# ---- 2. picked customer_id -> same card, stored ĐL type wins over the KH radio
r = client.post("/khach/tiep-nhan", data={
    "name": NAME, "phone": PHONE, "type": "KH", "customer_id": str(dl_id),
    "address": "12 Trần Phú", "units": json.dumps([UNIT])}, follow_redirects=False)
assert r.status_code == 303, f"intake failed: {r.status_code} {r.text[:300]}"
qid = int(r.headers["location"].rsplit("/", 1)[1])
assert store.get_quote(qid)["customer_id"] == dl_id, "báo giá landed on a different customer"
assert len(store.list_customers()) == BASE_COUNT, "picked customer still created a duplicate"
total = store.quote_items_for(qid)[0]["thanh_tien"]
assert total == DL_TOTAL, f"expected ĐL price {DL_TOTAL}, got {total} (KH would be {KH_TOTAL})"
assert store.get_customer(dl_id)["address"] == "12 Trần Phú", "blank address not backfilled"
print(f"2. pick -> reuse card #{dl_id}, priced at ĐL {DL_TOTAL:,}đ, address backfilled OK")

# ---- 3. no pick, same name + phone retyped -> reuse; address kept, ghi chú appended
r = client.post("/khach/tiep-nhan", data={
    "name": "anh ba đại lý", "phone": "090 111 2233", "type": "DL",
    "address": "99 Nguyễn Trãi", "note": "giao gấp", "units": json.dumps([UNIT])},
    follow_redirects=False)
assert r.status_code == 303, f"retype intake failed: {r.status_code} {r.text[:300]}"
qid2 = int(r.headers["location"].rsplit("/", 1)[1])
assert store.get_quote(qid2)["customer_id"] == dl_id, "retyped name+phone made a new card"
assert len(store.list_customers()) == BASE_COUNT, f"duplicate created: {len(store.list_customers())}"
c = store.get_customer(dl_id)
assert c["address"] == "12 Trần Phú", f"existing address overwritten: {c['address']!r}"
assert c["note"] == "giao gấp", f"note not captured: {c['note']!r}"
print("3. retyped name+phone -> same card, address untouched, ghi chú kept OK")

# ---- 4. different name on the same phone is a different person (warn-and-merge)
r = client.post("/khach/tiep-nhan", data={
    "name": "Chị Ba Vợ Anh Ba", "phone": PHONE, "type": "KH",
    "units": json.dumps([UNIT])}, follow_redirects=False)
assert r.status_code == 303, f"same-phone intake failed: {r.status_code}"
assert len(store.list_customers()) == BASE_COUNT + 1, "different name on same phone must be a new card"
assert [d["id"] for d in store.customers_sharing_phone(dl_id)], "duplicate-phone warning lost"
print("4. same phone + different name -> new card, trùng-SĐT warning intact OK")

# ---- 5. ghi chú appends rather than replacing on the next repeat job
r = client.post("/khach/tiep-nhan", data={
    "name": NAME, "phone": PHONE, "type": "DL", "note": "gọi trước khi giao"},
    follow_redirects=False)
assert r.status_code == 303, f"note-only intake failed: {r.status_code}"
note = store.get_customer(dl_id)["note"]
assert "giao gấp" in note and "gọi trước khi giao" in note, f"note not appended: {note!r}"
print("5. repeat ghi chú appended, nothing lost OK")

# ---- 6. Thêm khách form dedupes the same way
before = len(store.list_customers())
r = client.post("/khach/moi", data={
    "name": NAME, "phone": PHONE, "type": "DL", "source": "khac",
    "email": "bagiadl@gmail.com"}, follow_redirects=False)
assert r.status_code == 303, f"/khach/moi failed: {r.status_code}"
assert r.headers["location"] == f"/khach/{dl_id}", f"redirected elsewhere: {r.headers['location']}"
assert len(store.list_customers()) == before, "Thêm khách created a duplicate"
assert store.get_customer(dl_id)["email"] == "bagiadl@gmail.com", "blank email not backfilled"
print("6. Thêm khách with an existing name+phone -> that card, email backfilled OK")

# ---- 7. Cửa Kéo khung U: trắng + xám bình thường, wired into the wizard page
u_opts = views._WIZARD_COLORS["cua_keo"]["components"][0]["options"]
assert views._WIZARD_COLORS["cua_keo"]["components"][0]["key"] == "u", "first component is not khung U"
for c_ in ("trắng", "xám bình thường", "xanh", "kem", "ghi", "xám xingfa"):
    assert c_ in u_opts, f"khung U missing {c_!r}: {u_opts}"
page = client.get("/khach/tiep-nhan").text
assert "xám bình thường" in page and 'id="customer_id"' in page and "/api/khach/tim" in page, \
    "wizard page missing the new colours or the khách-cũ picker"
print(f"7. khung U options = {u_opts} — rendered in the wizard OK")

print("ALL KHÁCH-TRÙNG SMOKE TESTS PASSED")
