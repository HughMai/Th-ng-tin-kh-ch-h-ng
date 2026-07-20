"""Smoke test for PWA installability plumbing: manifest, service worker, icons,
and the <head> tags that wire them into every page. Throwaway temp DB — never
touches data/htp.db. Run: .venv/Scripts/python.exe tests_smoke_pwa.py
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

client = TestClient(app.app)

# ---- manifest ----------------------------------------------------------------
r = client.get("/manifest.webmanifest")
assert r.status_code == 200, f"manifest -> {r.status_code}"
assert r.headers["content-type"].startswith("application/manifest+json"), r.headers["content-type"]
m = json.loads(r.text)
assert m["display"] == "standalone", "manifest must be display:standalone to install"
assert m["start_url"] == "/" and m["scope"] == "/"
sizes = {i["sizes"] for i in m["icons"]}
assert {"192x192", "512x512"} <= sizes, f"missing required icon sizes: {sizes}"
assert any(i.get("purpose") == "maskable" for i in m["icons"]), "need a maskable icon"

# ---- service worker (root scope + no-cache) ----------------------------------
r = client.get("/sw.js")
assert r.status_code == 200, f"sw.js -> {r.status_code}"
assert "javascript" in r.headers["content-type"], r.headers["content-type"]
assert r.headers.get("service-worker-allowed") == "/", "sw needs root scope header"
assert r.headers.get("cache-control") == "no-cache"
assert "addEventListener('fetch'" in r.text, "sw missing fetch handler (install criterion)"

# ---- icons + offline page actually served ------------------------------------
for path in ("/static/icons/icon-192.png", "/static/icons/icon-512.png",
             "/static/icons/icon-512-maskable.png",
             "/static/icons/apple-touch-icon.png", "/static/offline.html"):
    rr = client.get(path)
    assert rr.status_code == 200, f"{path} -> {rr.status_code}"

# ---- head tags present on login page AND an authed page ----------------------
def assert_pwa_head(html, where):
    assert '<link rel="manifest" href="/manifest.webmanifest">' in html, f"{where}: no manifest link"
    assert 'name="theme-color" content="#0f4c81"' in html, f"{where}: no theme-color"
    assert 'rel="apple-touch-icon"' in html, f"{where}: no apple-touch-icon"
    assert "serviceWorker.register('/sw.js')" in html, f"{where}: no SW registration"

r = client.get("/login")
assert r.status_code == 200
assert_pwa_head(r.text, "login page")

client.post("/login", data={"password": "test-pass-123"}, follow_redirects=False)
r = client.get("/")
assert r.status_code == 200
assert_pwa_head(r.text, "home page")

print("ALL PWA SMOKE TESTS PASSED")
