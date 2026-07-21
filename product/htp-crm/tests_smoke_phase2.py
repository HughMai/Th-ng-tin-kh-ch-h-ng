"""Smoke test for the HTP CRM UX de-clunk (SIMPLIFY-PLAN.md Phase 2):
static CSS/JS extraction + caching, quote-builder single-screen add-item loop,
data-ajax progressive enhancement on the Hôm nay page.

Runs against a throwaway temp SQLite DB — never touches data/htp.db. Env vars
must be set BEFORE `import app` (app.py reads DB_PATH/FAMILY_PASSWORD/
SESSION_SECRET at import time and calls store.configure(DB_PATH) eagerly).

Run with: .venv/Scripts/python.exe tests_smoke_phase2.py
"""
import os
import tempfile
from pathlib import Path

_tmp_dir = tempfile.mkdtemp(prefix="htp_crm_test_phase2_")
os.environ["DB_PATH"] = str(Path(_tmp_dir) / "test.db")
os.environ["FAMILY_PASSWORD"] = "test-pass-123"
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["COOKIE_INSECURE"] = "1"  # TestClient talks plain http://testserver

from fastapi.testclient import TestClient

import app
import store
import views

client = TestClient(app.app)

# ---- login ------------------------------------------------------------------
r = client.post("/login", data={"password": "test-pass-123"}, follow_redirects=False)
assert r.status_code == 303, f"login failed: {r.status_code} {r.text}"
assert "session" in client.cookies, "no session cookie set after login"

# ---- static/app.css: 200, cached by whether the URL is fingerprinted --------
# A bare URL must stay short-lived: the flat max-age=86400 this used to assert is
# what let pre-board CSS outlive a deploy on the family's phones for a day.
r = client.get("/static/app.css")
assert r.status_code == 200, f"GET /static/app.css -> {r.status_code}"
assert r.headers.get("cache-control") == "public, max-age=300", \
    f"unexpected Cache-Control on bare app.css: {r.headers.get('cache-control')!r}"

r = client.get(f"/static/app.css?v={views.ASSET_V}")
assert r.status_code == 200, f"GET fingerprinted app.css -> {r.status_code}"
assert r.headers.get("cache-control") == "public, max-age=31536000, immutable", \
    f"fingerprinted app.css should be immutable: {r.headers.get('cache-control')!r}"

# ---- static/app.js: 200 -----------------------------------------------------
r = client.get("/static/app.js")
assert r.status_code == 200, f"GET /static/app.js -> {r.status_code}"

# ---- pages link the fingerprinted URLs, and sw.js agrees on the hash --------
# These moving together is the whole fix: new bytes => new URL => guaranteed miss
# in both the HTTP cache and the service worker cache.
r = client.get("/")
assert f"/static/app.css?v={views.ASSET_V}" in r.text, "page does not link fingerprinted CSS"
sw = client.get("/sw.js")
assert sw.status_code == 200, f"GET /sw.js -> {sw.status_code}"
assert "__ASSET_V__" not in sw.text, "sw.js placeholder was not substituted"
assert f"'{views.ASSET_V}'" in sw.text, "sw.js does not carry the current asset hash"

# ---- seed a customer + multi-item quote header ------------------------------
cid = store.create_customer("Anh Test — Trần Phú", "0909999999", "KH", "khac", "1 Trần Phú")
qid = store.create_quote_header(cid)
print(f"seeded customer #{cid}, quote header #{qid}")

# ---- quote build page GET contains "Thêm cửa" (collapsible add-item form) --
r = client.get(f"/bao-gia/{qid}")
assert r.status_code == 200, f"GET /bao-gia/{qid} -> {r.status_code}"
assert "Thêm cửa" in r.text, "quote build page missing '➕ Thêm cửa' collapsible"

# ---- POST a new line item from the build page (one round trip) -------------
r = client.post(
    f"/bao-gia/{qid}/hang-muc/moi",
    data={"loai_cua": "cua_cuon", "cong_nghe": "", "mau": "",
          "ngang": "3000", "cao": "2200", "gia_thu_cong": "1.000.000"},
    follow_redirects=False,
)
assert r.status_code == 303, f"POST hang-muc/moi -> {r.status_code} {r.text}"
assert r.headers["location"] == f"/bao-gia/{qid}", \
    f"expected redirect back to build page, got {r.headers['location']}"
r = client.get(r.headers["location"])
assert r.status_code == 200
# gia_thu_cong is a đơn giá (đ/m²) since the 2026-07-18 manual-price rework:
# 1.000.000 đ/m² × 3.0m × 2.2m = 6.600.000đ line total
assert "6.600.000" in r.text, \
    "new line item's price not shown on the re-rendered build page"

# ---- GET / still 200 and carries a data-ajax attribute ----------------------
# Seed a reminder due today so Hôm nay renders at least one one-tap action
# (the _post_btn(..., data_ajax="remove") calls only emit the attribute when
# there's a card to show).
store.create_reminder(cid, store.today_vn(), "gọi lại")
r = client.get("/")
assert r.status_code == 200, f"GET / -> {r.status_code}"
assert "data-ajax" in r.text, "Hôm nay page missing a data-ajax attribute"

print("ALL PHASE 2 SMOKE TESTS PASSED")
