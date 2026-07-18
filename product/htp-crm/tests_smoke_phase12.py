"""Smoke test for Phase 12: chăm sóc khách over the Zalo bot — the "nhac"
group command (quote follow-up + post-install review-ask drafts, one
forward-ready message per khách), the 💬 CHĂM SÓC KHÁCH digest section, and
the one-tap "Gửi Zalo" direct-message buttons on the Hôm nay care cards
(/bao-gia/{id}/gui-zalo-bot, /don-hang/{id}/gui-zalo-bot).

CRM side only — the Node bot just relays {reply}/{replies} and does the
findUser+DM; here _bot_send_dm is monkeypatched. Runs against a throwaway
temp SQLite DB — never touches data/htp.db. Env vars must be set BEFORE
`import app`.

Run with: .venv/Scripts/python.exe tests_smoke_phase12.py
"""
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

_tmp_dir = tempfile.mkdtemp(prefix="htp_crm_test_")
os.environ["DB_PATH"] = str(Path(_tmp_dir) / "test.db")
os.environ["FAMILY_PASSWORD"] = "test-pass-123"
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["COOKIE_INSECURE"] = "1"
os.environ["BOT_TOKEN"] = "test-bot-token"
os.environ["BOT_URL"] = ""  # no live bot in tests — outbound _bot_send stays a no-op

from fastapi.testclient import TestClient

import app
import store
import views

_ORIG_SEND_DM = app._bot_send_dm  # capture before section 7 monkeypatches it

client = TestClient(app.app)
r = client.post("/login", data={"password": "test-pass-123"}, follow_redirects=False)
assert r.status_code == 303, f"login failed: {r.status_code} {r.text}"

TODAY = store.today_vn()
HDR = {"X-Bot-Token": "test-bot-token"}


def days_ago(n: int) -> str:
    return (date.fromisoformat(TODAY) - timedelta(days=n)).isoformat()


def nhac(text: str = "nhac") -> dict:
    r = client.post("/bot/inbound", json={"uid": "1", "name": "Mẹ", "text": text}, headers=HDR)
    assert r.status_code == 200, r.text
    return r.json()


# ---- 1. nothing due -> friendly single reply -----------------------------------
data = nhac()
assert "Không có khách nào cần nhắc" in data["reply"], data
print("1. empty state OK")

# ---- 2. stale quote + fresh KH install -> summary + one pure draft per khách ---
cid = store.create_customer("Anh Tuấn", "0905111222", "KH")
qid = store.create_quote(cid, "cua_cuon", "", 15_000_000, sent_date=days_ago(3))
cid2 = store.create_customer("Cô Hoa", "0905333444", "KH")
oid = store.create_order(cid2, "nhom_kinh", "", 20_000_000, days_ago(2))
store.set_order_stage(oid, "hoan_thanh")

data = nhac()
replies = data["replies"]
assert len(replies) == 3, f"want summary + 2 drafts, got {len(replies)}: {replies}"
summary = replies[0]
assert "CHĂM SÓC KHÁCH" in summary and "Anh Tuấn" in summary and "Cô Hoa" in summary, summary
assert "gửi 3 ngày" in summary and "0905111222" in summary, summary
draft_q, draft_r = replies[1], replies[2]
assert draft_q.startswith("Chào Anh Tuấn") and "báo giá" in draft_q and "cửa cuốn" in draft_q, draft_q
assert draft_r.startswith("Chào Cô Hoa") and "Google" in draft_r and "/review" in draft_r, draft_r
for t in replies:
    assert "₫" not in t and "15.000" not in t and "20.000" not in t, f"VND leaked: {t}"
print("2. nhac summary + pure forward-ready drafts OK")

# ---- 3. accented "nhắc" is equivalent ------------------------------------------
assert nhac("nhắc") == data, "nhac/nhắc should be equivalent"
print("3. nhac/nhắc equivalence OK")

# ---- 4. /bot/digest gains the care section (+ hint), still VND-free ------------
r = client.get("/bot/digest", headers=HDR)
assert r.status_code == 200, r.text
digest = r.json()["text"]
assert "CHĂM SÓC KHÁCH" in digest and "gõ: nhac" in digest, digest
assert "₫" not in digest, "digest must not contain VND"
print("4. digest care section OK")

# ---- 5. ĐL / old installs excluded; >= 5 days sent -> lần-2 template -----------
cid3 = store.create_customer("Đại lý Minh", "0905999888", "DL")
oid_dl = store.create_order(cid3, "cua_cuon", "", 30_000_000, days_ago(1))
store.set_order_stage(oid_dl, "hoan_thanh")
cid4 = store.create_customer("Chú Bảy", "0905777666", "KH")
oid_old = store.create_order(cid4, "cua_keo", "", 9_000_000, days_ago(20))
store.set_order_stage(oid_old, "hoan_thanh")
qid2 = store.create_quote(cid4, "cua_keo", "", 5_000_000, sent_date=days_ago(6))

data = nhac()
summary = data["replies"][0]
assert "Đại lý Minh" not in summary, f"ĐL should never get a review ask: {summary}"
assert "Nhắc báo giá (2):" in summary and "Xin đánh giá (1):" in summary, summary
assert "nhắc lần 2" in summary, summary
assert len(data["replies"]) == 4, data["replies"]
assert "quyết định" in data["replies"][2], f"lần-2 draft should use quote_followup_2: {data['replies'][2]}"
print("5. ĐL/old-install excluded + lần-2 template OK")

# ---- 6. Đã nhắn / Đã xin clears the list; digest drops the section -------------
store.mark_quote_contacted(qid)
store.mark_quote_contacted(qid2)
store.set_order_flag(oid, "review_requested_at")
data = nhac()
assert "Không có khách nào cần nhắc" in data["reply"], data
r = client.get("/bot/digest", headers=HDR)
assert "CHĂM SÓC" not in r.json()["text"], "care section should disappear once handled"
print("6. đã nhắn / đã xin clears the list OK")

# ---- 7. one-tap "Gửi Zalo": bot send succeeds -> loop closes -------------------
# Fresh stale quote + fresh review-due order to fire the buttons at.
cidz = store.create_customer("Bác Sơn", "0912000111", "KH")
qidz = store.create_quote(cidz, "cua_cuon", "", 12_000_000, sent_date=days_ago(3))
oidz = store.create_order(cidz, "nhom_kinh", "", 18_000_000, days_ago(1))
store.set_order_stage(oidz, "hoan_thanh")

sent = []
app._bot_send_dm = lambda phone, text: (sent.append((phone, text)) or (True, ""))

r = client.post(f"/bao-gia/{qidz}/gui-zalo-bot",
                data={"text": "Chào Bác Sơn, follow-up draft", "next": "/"},
                follow_redirects=False)
assert r.status_code == 303, f"quote send should redirect: {r.status_code} {r.text}"
assert sent and sent[-1][0] == "0912000111", f"bot not called with phone: {sent}"
assert store.get_quote(qidz)["last_contact_date"] == TODAY, "quote not marked contacted after send"
assert all(q["id"] != qidz for q in store.quotes_to_chase(TODAY)), "quote should drop from chase"

r = client.post(f"/don-hang/{oidz}/gui-zalo-bot",
                data={"text": "Chào Bác Sơn, review draft", "next": "/"},
                follow_redirects=False)
assert r.status_code == 303, f"review send should redirect: {r.status_code} {r.text}"
assert store.get_order(oidz)["review_requested_at"], "review flag not set after send"
print("7. one-tap Gửi Zalo closes the loop OK")

# ---- 8. bot send fails -> 502, loop NOT closed (card stays for manual forward) -
cidf = store.create_customer("Cô Mai", "0912999888", "KH")
qidf = store.create_quote(cidf, "cua_keo", "", 7_000_000, sent_date=days_ago(3))
app._bot_send_dm = lambda phone, text: (False, "không tìm thấy Zalo cho số này (chưa kết bạn?)")
r = client.post(f"/bao-gia/{qidf}/gui-zalo-bot",
                data={"text": "draft", "next": "/"}, follow_redirects=False)
assert r.status_code == 502, f"failed send should 502, got {r.status_code}"
assert store.get_quote(qidf)["last_contact_date"] is None, "quote must NOT be marked contacted on failure"
assert any(q["id"] == qidf for q in store.quotes_to_chase(TODAY)), "quote must stay in chase on failure"
print("8. failed send is loud + non-destructive OK")

# ---- 9. genuine helper fails loudly when the bot isn't configured -------------
# BOT_URL is "" in this test env, so the real helper never calls out — it must
# return a loud (False, reason) instead of a silent success.
ok, reason = _ORIG_SEND_DM("0912000111", "hi")
assert ok is False and "cấu hình" in reason, f"unconfigured bot should fail loudly: {(ok, reason)}"
ok, reason = _ORIG_SEND_DM("", "hi")
assert ok is False, "empty phone should fail"
print("9. helper fails loudly when bot unconfigured OK")

# ---- 10. care card renders the Gửi Zalo button only when bot_ready ------------
html_on = views.today_page(chase=store.quotes_to_chase(TODAY), checkins=[], expiring=[],
                           debts=[], reminders=[], reviews=store.orders_review_due(TODAY),
                           summary=store.open_quotes_summary(), kh_debts=[], bot_ready=True)
assert "gui-zalo-bot" in html_on, "Gửi Zalo button missing when bot_ready=True"
html_off = views.today_page(chase=store.quotes_to_chase(TODAY), checkins=[], expiring=[],
                            debts=[], reminders=[], reviews=store.orders_review_due(TODAY),
                            summary=store.open_quotes_summary(), kh_debts=[], bot_ready=False)
assert "gui-zalo-bot" not in html_off, "Gửi Zalo button must be hidden when bot_ready=False"
print("10. card button gates on bot_ready OK")

print("ALL PHASE 12 SMOKE TESTS PASSED")
