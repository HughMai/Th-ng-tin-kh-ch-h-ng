"""Smoke test: public /yeu-cau lead form.

The one unauthenticated customer-facing page: GET renders without a session,
POST creates a stage='lead' customer + a due-today reminder (→ Hôm nay's Nhắc
việc), honeypot submissions are silently dropped, bad input re-renders with the
values kept, and a repeat phone reuses the existing customer instead of
duplicating. Also guards that / itself still redirects to /login unauthed.

Runs against a throwaway temp SQLite DB — never touches data/htp.db. Env vars
must be set BEFORE `import app`.

Run with: .venv/Scripts/python.exe tests_smoke_webleads.py
"""
import os
import tempfile
from pathlib import Path

_tmp_dir = tempfile.mkdtemp(prefix="htp_crm_test_")
os.environ["DB_PATH"] = str(Path(_tmp_dir) / "test.db")
os.environ["FAMILY_PASSWORD"] = "test-pass-123"
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["COOKIE_INSECURE"] = "1"
os.environ["WEB_LEAD_TOKEN"] = "test-web-token"

from fastapi.testclient import TestClient

import app
import store

client = TestClient(app.app)

# 1. Public pages render without any session; / stays guarded.
r = client.get("/", follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == "/login", "/ must stay guarded"
r = client.get("/yeu-cau")
assert r.status_code == 200 and "Gửi yêu cầu báo giá" in r.text, "public form must render"
r = client.get("/yeu-cau/cam-on")
assert r.status_code == 200 and "30 phút" in r.text, "thanks page must render"

# 2. Valid submission → redirect + lead customer + due-today reminder.
r = client.post("/yeu-cau", data={
    "ten": "Anh Bảy Test", "sdt": "0939 123 456", "khu_vuc": "Bình Thủy, Cần Thơ",
    "nhu_cau": "cua_cuon", "kich_thuoc": "3m x 2.5m", "ghi_chu": "Nhà đang xây",
    "website": "",
}, follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == "/yeu-cau/cam-on", r.text
c = store.find_customer_by_phone("0939123456")
assert c and c["stage"] == "lead" and c["type"] == "KH", f"lead customer missing: {c}"
rems = store.reminders_due(store.today_vn())
mine = [x for x in rems if x["customer_id"] == c["id"]]
assert len(mine) == 1 and "Form web" in mine[0]["note"] and "Cửa cuốn mới" in mine[0]["note"], mine

# 3. The reminder actually shows on Hôm nay after login.
r = client.post("/login", data={"password": "test-pass-123"}, follow_redirects=False)
assert r.status_code == 303, f"login failed: {r.status_code}"
r = client.get("/")
assert "Anh Bảy Test" in r.text, "web lead must surface on Hôm nay via Nhắc việc"

# 4. Honeypot filled → silent redirect, nothing stored.
before = len(store.list_customers(stage_filter="lead"))
r = client.post("/yeu-cau", data={
    "ten": "Bot Spam", "sdt": "0111222333", "nhu_cau": "khac", "website": "http://spam",
}, follow_redirects=False)
assert r.status_code == 303, "honeypot must still look like success"
assert len(store.list_customers(stage_filter="lead")) == before, "honeypot row must NOT be stored"

# 5. Bad phone → 200 re-render with error + entered name kept, nothing stored.
r = client.post("/yeu-cau", data={"ten": "Chị Chín", "sdt": "123", "nhu_cau": "khac", "website": ""})
assert r.status_code == 200 and "kiểm tra lại" in r.text and "Chị Chín" in r.text
assert store.find_customer_by_phone("123") is None

# 6. Same phone again → reuses the customer, adds a second reminder.
r = client.post("/yeu-cau", data={
    "ten": "Anh Bảy Test", "sdt": "0939123456", "nhu_cau": "sua_chua", "website": "",
}, follow_redirects=False)
assert r.status_code == 303
leads = [x for x in store.list_customers(stage_filter="lead") if x["phone"] == "0939123456"]
assert len(leads) == 1, f"repeat phone must not duplicate: {leads}"
mine = [x for x in store.reminders_due(store.today_vn()) if x["customer_id"] == c["id"]]
assert len(mine) == 2, f"second request must add a second reminder: {mine}"

# 7. JSON API (/api/yeu-cau — the hungthanhphat.vn website forward).
r = client.post("/api/yeu-cau", json={"name": "Web Khách", "phone": "0987000111"})
assert r.status_code == 401, f"missing token must 401: {r.status_code}"
r = client.post("/api/yeu-cau", json={"name": "Web Khách", "phone": "0987000111"},
                headers={"X-Web-Token": "wrong"})
assert r.status_code == 401, f"wrong token must 401: {r.status_code}"
r = client.post("/api/yeu-cau", headers={"X-Web-Token": "test-web-token"},
                json={"name": "Web Khách", "phone": "0987 000 111",
                      "need": "Cửa Cuốn — CN Đức — KV380", "size": "3m x 2.5m",
                      "note": "Ước tính web: 9.702.000đ | web@test.vn"})
assert r.status_code == 200 and r.json() == {"ok": True}, r.text
wc = store.find_customer_by_phone("0987000111")
assert wc and wc["stage"] == "lead", f"API lead missing: {wc}"
wrem = [x for x in store.reminders_due(store.today_vn()) if x["customer_id"] == wc["id"]]
assert len(wrem) == 1 and "KV380" in wrem[0]["note"] and "9.702.000" in wrem[0]["note"], wrem
r = client.post("/api/yeu-cau", headers={"X-Web-Token": "test-web-token"},
                json={"name": "X", "phone": "123"})
assert r.status_code == 422, f"bad name/phone must 422: {r.status_code}"

print("OK — tests_smoke_webleads: public form renders, lead+reminder stored, "
      "surfaces on Hôm nay, honeypot dropped, bad input re-rendered, phone dedup works, "
      "JSON API token-gated + stores lead")
