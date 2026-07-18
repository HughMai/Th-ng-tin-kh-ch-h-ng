"""Smoke test for Phase 5: Zalo group bot sidecar CRM endpoints (/bot/inbound,
/bot/digest) — digest numbering, "xong <N>"/"viec" commands, shared-secret auth,
and the bot-fully-optional contract (BOT_TOKEN unset -> app still works fine).

The Node bot (zalo-bot/) is not under test here — only the CRM side of the
bridge. Runs against a throwaway temp SQLite DB — never touches data/htp.db.
Env vars must be set BEFORE `import app` (app.py reads DB_PATH/... at import
time and calls store.configure(DB_PATH) eagerly).

Run with: .venv/Scripts/python.exe tests_smoke_phase5.py
"""
import os
import tempfile
from pathlib import Path

_tmp_dir = tempfile.mkdtemp(prefix="htp_crm_test_")
os.environ["DB_PATH"] = str(Path(_tmp_dir) / "test.db")
os.environ["FAMILY_PASSWORD"] = "test-pass-123"
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["COOKIE_INSECURE"] = "1"  # TestClient talks plain http://testserver
os.environ["BOT_TOKEN"] = "test-bot-token"
os.environ["BOT_URL"] = ""  # no live bot in tests — outbound _bot_send stays a no-op

from fastapi.testclient import TestClient

import app
import store

client = TestClient(app.app)

TODAY = store.today_vn()
HDR = {"X-Bot-Token": "test-bot-token"}

# ---- 1. wrong/missing token -> 403 ---------------------------------------------
r = client.post("/bot/inbound", json={"uid": "1", "name": "Thợ", "text": "viec"})
assert r.status_code == 403, f"missing token should 403, got {r.status_code}"
r = client.post("/bot/inbound", json={"uid": "1", "name": "Thợ", "text": "viec"},
                headers={"X-Bot-Token": "wrong"})
assert r.status_code == 403, f"wrong token should 403, got {r.status_code}"
print("1. bot token auth OK")

# ---- 2. "viec" -> numbered reply, bot_digest rows written ----------------------
cid = store.create_customer("Cô Lan — Trần Phú", "0905111222", "KH")
oid = store.create_order(cid, "cua_cuon", "Cửa cuốn khe thoáng", 15_000_000, TODAY)
r = client.post("/bot/inbound", json={"uid": "1", "name": "Thợ Tùng", "text": "viec"}, headers=HDR)
assert r.status_code == 200, r.text
reply = r.json()["reply"]
assert "Cô Lan" in reply and "1." in reply, f"digest missing order: {reply}"
assert store.get_digest_order(TODAY, 1) == oid, "bot_digest not written for item 1"
print("2. viec command OK")

# ---- 3. "xong <N>" -> order hoan_thanh, install_date backfilled, reply confirms
oid2 = store.create_order(cid, "cua_keo", "Cửa kéo 6zem", 8_000_000)  # no install_date
store.set_order_urgent(oid2, True)  # so it appears in the digest despite no install_date
r = client.post("/bot/inbound", json={"uid": "1", "name": "Thợ Tùng", "text": "viec"}, headers=HDR)
assert r.status_code == 200, r.text
n2 = next(n for n in range(1, 10) if store.get_digest_order(TODAY, n) == oid2)
r = client.post("/bot/inbound", json={"uid": "1", "name": "Thợ Tùng", "text": f"xong {n2}"}, headers=HDR)
assert r.status_code == 200, r.text
reply = r.json()["reply"]
assert "LẮP XONG" in reply and "Thợ Tùng" in reply, f"unexpected reply: {reply}"
o2 = store.get_order(oid2)
assert o2["stage"] == "hoan_thanh", f"stage not flipped: {o2['stage']}"
assert o2["install_date"] == TODAY, f"install_date not backfilled: {o2['install_date']}"
print(f"3. xong <N> completes order OK (don #{oid2})")

# ---- 4. wrong number -> help reply; chatter ignored; malformed xong -> help ----
r = client.post("/bot/inbound", json={"uid": "1", "name": "", "text": "xong 99"}, headers=HDR)
assert "Không thấy số 99" in r.json()["reply"], f"unexpected: {r.json()}"
r = client.post("/bot/inbound", json={"uid": "1", "name": "", "text": "xin chào cả nhà"}, headers=HDR)
assert r.json() == {}, f"chatter should be ignored, got {r.json()}"
r = client.post("/bot/inbound", json={"uid": "1", "name": "", "text": "xong abc"}, headers=HDR)
assert "Gõ: xong" in r.json()["reply"], f"malformed xong should get help reply: {r.json()}"
print("4. wrong number / chatter / malformed xong OK")

# ---- 5. "xong <N>" twice -> second reply says already done, stage unchanged ----
r = client.post("/bot/inbound", json={"uid": "1", "name": "Thợ Tùng", "text": f"xong {n2}"}, headers=HDR)
assert "đã xong rồi" in r.json()["reply"], f"unexpected: {r.json()}"
assert store.get_order(oid2)["stage"] == "hoan_thanh"
print("5. double xong is a no-op OK")

# ---- 6. /bot/digest matches "viec"; no VND leaks into either reply -------------
r = client.get("/bot/digest", headers=HDR)
assert r.status_code == 200, r.text
digest_text = r.json()["text"]
assert "₫" not in digest_text, "digest must not contain VND"
r_inbound = client.post("/bot/inbound", json={"uid": "1", "name": "", "text": "viec"}, headers=HDR)
inbound_text = r_inbound.json()["reply"]
assert "₫" not in inbound_text, "viec reply must not contain VND"
print("6. /bot/digest + no VND leakage OK")

# ---- 7. "cua"/"cửa" -> production queue reply (chờ/đang sản xuất only) --------
r = client.post("/bot/inbound", json={"uid": "1", "name": "", "text": "cua"}, headers=HDR)
assert r.status_code == 200, r.text
cua_reply = r.json()["reply"]
assert "Cô Lan" in cua_reply and "Chờ sản xuất" in cua_reply, f"production list missing order: {cua_reply}"
assert "₫" not in cua_reply, "cua reply must not contain VND"
r = client.post("/bot/inbound", json={"uid": "1", "name": "", "text": "cửa"}, headers=HDR)
assert r.json()["reply"] == cua_reply, "cua/cửa should be equivalent"
print("7. cua/cua-accented command OK")

# ---- 8. /bot/digest combines production queue + lắp đặt work list -------------
r = client.get("/bot/digest", headers=HDR)
combined = r.json()["text"]
assert "ĐANG SẢN XUẤT" in combined and "CÔNG VIỆC" in combined, f"digest missing a section: {combined}"
print("8. /bot/digest combines production + job list OK")

# ---- 9. BOT_TOKEN unset -> /bot/inbound 403; rest of the app still works -------
app.BOT_TOKEN = ""
r = client.post("/bot/inbound", json={"uid": "1", "name": "", "text": "viec"}, headers=HDR)
assert r.status_code == 403, f"unset BOT_TOKEN should still 403: {r.status_code}"
r = client.get("/login")
assert r.status_code == 200, f"app broken with BOT_TOKEN unset: {r.status_code}"
print("9. bot fully optional (BOT_TOKEN unset) OK")

print("ALL PHASE 5 SMOKE TESTS PASSED")
