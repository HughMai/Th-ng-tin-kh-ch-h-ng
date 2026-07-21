"""Smoke test: a customer you add is a customer you can find.

Regression guard for the bug where adding a khách appeared to do nothing. The
row saved fine — but create_customer() defaulted to stage='lead' while /khach
lists stage='customer' and had no control to switch, so every manual add landed
on a list with no link pointing at it. From the family's side the form just ate
the entry.

Two halves, and both matter:
  - manual adds (Thêm khách + the tiếp-nhận wizard) land on the DEFAULT list
  - web-form leads still record as 'lead', and the Lead tab can actually reach
    them (that's the half that keeps the split meaningful instead of collapsing
    everything into one bucket)

Runs against a throwaway temp SQLite DB — never touches data/htp.db. Env vars
must be set BEFORE `import app`.

Run with: .venv/Scripts/python.exe tests_smoke_customer_visibility.py
"""
import os
import tempfile
from pathlib import Path

_tmp_dir = tempfile.mkdtemp(prefix="htp_crm_test_vis_")
os.environ["DB_PATH"] = str(Path(_tmp_dir) / "test.db")
os.environ["FAMILY_PASSWORD"] = "test-pass-123"
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["COOKIE_INSECURE"] = "1"

from fastapi.testclient import TestClient

import app
import store

client = TestClient(app.app)
r = client.post("/login", data={"password": "test-pass-123"}, follow_redirects=False)
assert r.status_code == 303, f"login failed: {r.status_code}"

# ---- 1. "Thêm khách" form -> visible on the default list --------------------
r = client.post("/khach/moi", data={"name": "Chú Bảy Thêm Tay", "phone": "0901234567",
                                    "type": "KH", "source": "gioi_thieu"},
                follow_redirects=False)
assert r.status_code == 303, f"POST /khach/moi -> {r.status_code}"
cid = int(r.headers["location"].rsplit("/", 1)[1])
assert store.get_customer(cid)["stage"] == "customer", "manual add must not be filed as a lead"

r = client.get("/khach")
assert "Chú Bảy Thêm Tay" in r.text, "manually added khách missing from the default /khach list"

# ---- 2. tiếp-nhận wizard (no doors) -> same ---------------------------------
r = client.post("/khach/tiep-nhan", data={"name": "Cô Tám Wizard", "phone": "0907654321",
                                          "type": "KH", "source": "khac", "units": "[]"},
                follow_redirects=False)
assert r.status_code == 303, f"POST /khach/tiep-nhan -> {r.status_code}"
wid = int(r.headers["location"].rsplit("/", 1)[1])
assert store.get_customer(wid)["stage"] == "customer", "wizard add must not be filed as a lead"

r = client.get("/khach")
assert "Cô Tám Wizard" in r.text, "wizard-added khách missing from the default /khach list"

# ---- 3. web leads still record as leads, but the list shows them too --------
# The stage column still means something (add_web_lead uses it, and chốt promotes),
# but /khach no longer filters on it — one list, everyone on it.
store.add_web_lead("Anh Web Lead", "0912000333", "Bình Thủy", "Cửa cuốn mới")
wl = store.find_customer_by_phone("0912000333")
assert wl and wl["stage"] == "lead", f"web lead should stay a lead: {wl}"

r = client.get("/khach")
assert "Anh Web Lead" in r.text, "web lead missing from /khach — the list must show everyone"

# ---- 4. every customer in the DB is on the page, whatever its stage ---------
missing = [c["name"] for c in store.list_customers() if c["name"] not in r.text]
assert not missing, f"customers missing from /khach: {missing}"

# ---- 5. search still works, and narrows ------------------------------------
r = client.get("/khach?q=Wizard")
assert "Cô Tám Wizard" in r.text and "Chú Bảy Thêm Tay" not in r.text, "search filter broken"

print("OK — tests_smoke_customer_visibility: manual adds land on the list, "
      "every customer is listed regardless of stage, web leads still record as leads")
