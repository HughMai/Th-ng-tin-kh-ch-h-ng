"""Smoke test for the HTP CRM tracking layer (SIMPLIFY-PLAN.md Phase 1):
touches table, "Lịch sử chăm sóc" timeline + quick note, /bao-cao reports.

Runs against a throwaway temp SQLite DB — never touches data/htp.db. Env vars
must be set BEFORE `import app` (app.py reads DB_PATH/FAMILY_PASSWORD/
SESSION_SECRET at import time and calls store.configure(DB_PATH) eagerly).

Run with: .venv/Scripts/python.exe tests_smoke.py
"""
import os
import tempfile
from pathlib import Path

_tmp_dir = tempfile.mkdtemp(prefix="htp_crm_test_")
os.environ["DB_PATH"] = str(Path(_tmp_dir) / "test.db")
os.environ["FAMILY_PASSWORD"] = "test-pass-123"
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["COOKIE_INSECURE"] = "1"  # TestClient talks plain http://testserver

from fastapi.testclient import TestClient

import app
import store

client = TestClient(app.app)

# ---- login ------------------------------------------------------------------
r = client.post("/login", data={"password": "test-pass-123"}, follow_redirects=False)
assert r.status_code == 303, f"login failed: {r.status_code} {r.text}"
assert "session" in client.cookies, "no session cookie set after login"

# ---- seed a customer + quote via store functions -----------------------------
cid = store.create_customer("Anh Test — Trần Phú", "0909999999", "KH", "khac", "1 Trần Phú")
qid = store.create_quote(cid, "cua_cuon", "Cửa cuốn test", 10_000_000)
print(f"seeded customer #{cid}, quote #{qid}")

# ---- GET / --------------------------------------------------------------------
r = client.get("/")
assert r.status_code == 200, f"GET / -> {r.status_code}"

# ---- GET /khach/{id} ------------------------------------------------------------
r = client.get(f"/khach/{cid}")
assert r.status_code == 200, f"GET /khach/{cid} -> {r.status_code}"
assert "Lịch sử" in r.text, "customer detail page missing 'Lịch sử' timeline section"

# ---- POST a quick note, then re-GET and check it shows up in the timeline -------
note_text = "gọi rồi, hẹn tuần sau"
r = client.post(f"/khach/{cid}/cham-soc", data={"note": note_text}, follow_redirects=False)
assert r.status_code == 303, f"POST cham-soc -> {r.status_code} {r.text}"
r = client.get(f"/khach/{cid}")
assert r.status_code == 200
assert note_text in r.text, "quick note not shown in timeline after POST"

# ---- GET /bao-cao ------------------------------------------------------------------
r = client.get("/bao-cao")
assert r.status_code == 200, f"GET /bao-cao -> {r.status_code}"
assert "Tỷ lệ chốt" in r.text, "reports page missing 'Tỷ lệ chốt' metric"

print("ALL SMOKE TESTS PASSED")
