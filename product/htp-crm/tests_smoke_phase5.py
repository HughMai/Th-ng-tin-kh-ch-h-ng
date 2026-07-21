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
assert "Cô Lan" in reply and "【1】" in reply, f"digest missing order: {reply}"
assert store.get_digest_order(TODAY, 1) == oid, "bot_digest not written for item 1"
print("2. viec command OK")

# ---- 2b. every job carries kích thước / màu sắc / ghi chú ----------------------
# The digest used to be one line per job ending in "1 hạng mục", which told the
# xưởng nothing about what to actually make — they had to open the CRM anyway.
cid_d = store.create_customer("Anh Kích Thước", "0905999888", "KH")
oid_d = store.create_order(cid_d, "cua_cuon", "", 20_000_000, TODAY, note="Hẻm nhỏ, gọi trước")
store.set_order_urgent(oid_d, True)
store.add_order_item(oid_d, product="cua_cuon", cong_nghe="Cửa cuốn công nghệ Đức",
                     mau="KV 468 R", ngang_mm=3200, cao_mm=4500, mau_sac="kem",
                     ghi_chu="khung nhôm dày", thanh_tien=20_000_000)
reply = client.post("/bot/inbound", json={"uid": "1", "name": "", "text": "viec"},
                    headers=HDR).json()["reply"]
for needle in ("Anh Kích Thước", "3200 × 4500mm", "Màu: kem", "khung nhôm dày",
               "Hẻm nhỏ, gọi trước", "KV 468 R", "⚡ GẤP"):
    assert needle in reply, f"digest missing {needle!r}:\n{reply}"
assert "₫" not in reply and "20.000.000" not in reply, f"price leaked into viec:\n{reply}"
print("2b. viec carries kích thước / màu sắc / ghi chú OK")

# ---- 3. "xong <N>" -> order dang_san_xuat, reply confirms ----------------------
# No install_date and not urgent: "viec" lists every chờ-sản-xuất đơn hàng, so
# an undated job must still be numbered. The old digest gated on "urgent or due
# within a day", which made jobs like this one invisible to the xưởng entirely.
oid2 = store.create_order(cid, "cua_keo", "Cửa kéo 6zem", 8_000_000)  # no install_date
r = client.post("/bot/inbound", json={"uid": "1", "name": "Thợ Tùng", "text": "viec"}, headers=HDR)
assert r.status_code == 200, r.text
assert "Cửa kéo 6zem" in r.json()["reply"], f"undated job missing from viec: {r.json()['reply']}"
n2 = next(n for n in range(1, 10) if store.get_digest_order(TODAY, n) == oid2)
r = client.post("/bot/inbound", json={"uid": "1", "name": "Thợ Tùng", "text": f"xong {n2}"}, headers=HDR)
assert r.status_code == 200, r.text
reply = r.json()["reply"]
assert "SẢN XUẤT XONG" in reply and "Thợ Tùng" in reply, f"unexpected reply: {reply}"
o2 = store.get_order(oid2)
assert o2["stage"] == "dang_san_xuat", f"stage not flipped: {o2['stage']}"
# "sản xuất xong" says nothing about when it goes on the wall — đã lắp đặt (and
# the ngày lắp that comes with it) stays a web-CRM action.
assert o2["install_date"] is None, f"install_date must not be backfilled: {o2['install_date']}"
print(f"3. xong <N> finishes production OK (don #{oid2})")

# ---- 4. wrong number -> help reply; chatter ignored; malformed xong -> help ----
r = client.post("/bot/inbound", json={"uid": "1", "name": "", "text": "xong 99"}, headers=HDR)
assert "Không thấy số 99" in r.json()["reply"], f"unexpected: {r.json()}"
r = client.post("/bot/inbound", json={"uid": "1", "name": "", "text": "xin chào cả nhà"}, headers=HDR)
assert r.json() == {}, f"chatter should be ignored, got {r.json()}"
r = client.post("/bot/inbound", json={"uid": "1", "name": "", "text": "xong abc"}, headers=HDR)
assert "Gõ: xong" in r.json()["reply"], f"malformed xong should get help reply: {r.json()}"
print("4. wrong number / chatter / malformed xong OK")

# ---- 5. "xong <N>" twice -> second reply says already done, stage unchanged ----
# The job left chờ sản xuất, so a stale digest number can't walk it a stage further.
r = client.post("/bot/inbound", json={"uid": "1", "name": "Thợ Tùng", "text": f"xong {n2}"}, headers=HDR)
assert "đã xong rồi" in r.json()["reply"], f"unexpected: {r.json()}"
assert store.get_order(oid2)["stage"] == "dang_san_xuat"
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

# ---- 7. "cua"/"cửa" is retired — it must read as chatter, not a command -------
# The list it printed duplicated "viec" once viec started carrying kích thước,
# màu sắc and ghi chú per hạng mục. Two near-identical walls of text in the same
# group is how the family stops reading either.
for txt in ("cua", "cửa", "CUA", "Cửa"):
    r = client.post("/bot/inbound", json={"uid": "1", "name": "", "text": txt}, headers=HDR)
    assert r.status_code == 200, r.text
    assert r.json() == {}, f"retired 'cua' command still replies to {txt!r}: {r.json()}"
print("7. retired cua/cửa command is silent OK")

# ---- 8. /bot/digest is the work list + chăm sóc khách, nothing else -----------
r = client.get("/bot/digest", headers=HDR)
combined = r.json()["text"]
assert "CHỜ SẢN XUẤT" in combined, f"digest missing the work list: {combined}"
assert "ĐANG SẢN XUẤT" not in combined, f"retired production section still in digest: {combined}"
print("8. /bot/digest carries the work list only OK")

# ---- 9. BOT_TOKEN unset -> /bot/inbound 403; rest of the app still works -------
app.BOT_TOKEN = ""
r = client.post("/bot/inbound", json={"uid": "1", "name": "", "text": "viec"}, headers=HDR)
assert r.status_code == 403, f"unset BOT_TOKEN should still 403: {r.status_code}"
r = client.get("/login")
assert r.status_code == 200, f"app broken with BOT_TOKEN unset: {r.status_code}"
print("9. bot fully optional (BOT_TOKEN unset) OK")

print("ALL PHASE 5 SMOKE TESTS PASSED")
