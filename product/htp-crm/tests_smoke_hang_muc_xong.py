"""Smoke test: per-hạng-mục completion in the Zalo group work list.

A đơn hàng with many cửa used to be all-or-nothing — "xong 3" closed the whole
job, so a xưởng that finished 2 of 5 cửa had nothing to report. Each hạng mục
now carries its own number ("3.2"), the digest goes out one message per job, and
the đơn only moves to đang sản xuất when the last hạng mục is ticked. The web
tick list on /don-hang/<id> writes the same state (and is how a mis-typed number
gets undone).

Runs against a throwaway temp SQLite DB — never touches data/htp.db. Env vars
must be set BEFORE `import app`.

Run with: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe tests_smoke_hang_muc_xong.py
"""
import os
import tempfile
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

client = TestClient(app.app)

TODAY = store.today_vn()
HDR = {"X-Bot-Token": "test-bot-token"}


def say(text: str) -> dict:
    r = client.post("/bot/inbound", json={"uid": "1", "name": "Thợ Tùng", "text": text},
                    headers=HDR)
    assert r.status_code == 200, r.text
    return r.json()


def viec() -> list:
    body = say("viec")
    return body.get("replies") or ([body["reply"]] if body.get("reply") else [])


def door(order_id: int, desc: str, ngang: int = 3000, cao: int = 2800) -> int:
    return store.add_order_item(order_id, product="cua_cuon", description=desc,
                                ngang_mm=ngang, cao_mm=cao, mau_sac="ghi",
                                thanh_tien=10_000_000)


# ---- 1. a đơn with 3 hạng mục gets sub-numbers -----------------------------------
cid = store.create_customer("Cô Lan — Trần Phú", "0905111222", "KH")
oid = store.create_order(cid, "cua_cuon", "Bộ 3 cửa", None, TODAY)
i1, i2, i3 = door(oid, "Cửa A"), door(oid, "Cửa B"), door(oid, "Cửa C")

msgs = viec()
assert len(msgs) == 1, f"one job should be one message, got {len(msgs)}"
block = msgs[0]
for needle in ("【1】", "1.1 ", "1.2 ", "1.3 ", "Cửa A", "Cửa B", "Cửa C", "3000 × 2800mm"):
    assert needle in block, f"digest missing {needle!r}:\n{block}"
assert "xong 1.2" in block, f"digest missing the sub-number hint:\n{block}"
assert store.get_digest_item(TODAY, 1, 2)["id"] == i2, "sub-number 1.2 must map to hạng mục 2"
print("1. hạng mục sub-numbering OK")

# ---- 2. "xong 1.2" closes one hạng mục, not the đơn -------------------------------
reply = say("xong 1.2")["reply"]
assert "Cửa B" in reply and "Còn 2 hạng mục" in reply, f"unexpected reply: {reply}"
assert store.get_order(oid)["stage"] == "cho_san_xuat", "one hạng mục must not move the đơn"
assert store.item_make_counts(oid) == (1, 3), store.item_make_counts(oid)
print("2. xong <N>.<M> closes one hạng mục OK")

# ---- 3. the next list drops it, renumbers, and shows progress ---------------------
block = viec()[0]
assert "Cửa B" not in block, f"made hạng mục still listed:\n{block}"
assert "đã xong 1/3" in block, f"progress counter missing:\n{block}"
assert "1.1 " in block and "1.2 " in block and "1.3 " not in block, f"not renumbered:\n{block}"
assert store.get_digest_item(TODAY, 1, 2)["id"] == i3, "renumbering must follow the printed list"
print("3. renumbering after a partial xong OK")

# ---- 4. the last hạng mục moves the đơn to đang sản xuất --------------------------
assert "Còn 1 hạng mục" in say("xong 1.1")["reply"]  # Cửa A
# Numbers only shift when a new list is sent, so Cửa C is still 1.2 here — and
# re-reporting 1.1 says so rather than closing something else by surprise.
assert "đã xong rồi" in say("xong 1.1")["reply"], "a repeated number must not close another cửa"
reply = say("xong 1.2")["reply"]  # Cửa C — the last one
assert "Xong cả đơn" in reply, f"last hạng mục should close the đơn: {reply}"
assert store.get_order(oid)["stage"] == "dang_san_xuat", "đơn should be đang sản xuất"
assert store.items_to_make(oid) == [], "nothing should be left to make"
print("4. last hạng mục closes the đơn OK")

# ---- 5. bare "xong <N>" still closes the whole đơn, tick list included ------------
oid2 = store.create_order(cid, "cua_keo", "Bộ 2 cửa kéo", None, TODAY)
door(oid2, "Kéo A"), door(oid2, "Kéo B")
viec()
n2 = next(n for n in range(1, 10) if store.get_digest_order(TODAY, n) == oid2)
reply = say(f"xong {n2}")["reply"]
assert "SẢN XUẤT XONG" in reply and "Thợ Tùng" in reply, f"unexpected reply: {reply}"
assert store.get_order(oid2)["stage"] == "dang_san_xuat"
assert store.item_make_counts(oid2) == (2, 2), "bare xong must clear the whole tick list"
print("5. bare xong <N> still closes the đơn OK")

# ---- 6. loose separators, bad sub-numbers, stale numbers --------------------------
oid3 = store.create_order(cid, "cua_cuon", "Bộ 2 cửa", None, TODAY)
door(oid3, "Sep A"), door(oid3, "Sep B")
viec()
n3 = next(n for n in range(1, 10) if store.get_digest_order(TODAY, n) == oid3)
assert "Không thấy hạng mục" in say(f"xong {n3}.9")["reply"], "bad sub-number should say so"
assert "Sep A" in say(f"xong {n3} 1")["reply"], "space separator should work"  # "xong N 1"
viec()
assert "Sep B" in say(f"xong {n3}-1")["reply"], "dash separator should work"
assert store.get_order(oid3)["stage"] == "dang_san_xuat"
assert "Gõ: xong" in say("xong abc")["reply"], "malformed xong should get help"
print("6. separators / bad sub-number / help OK")

# ---- 7. no VND anywhere in the group messages ------------------------------------
oid4 = store.create_order(cid, "cua_cuon", "Đơn có giá", 25_000_000, TODAY)
door(oid4, "Giá A")
all_text = "\n".join(viec()) + "\n" + "\n".join(client.get("/bot/digest", headers=HDR).json()["texts"])
for leak in ("₫", "25.000.000", "10.000.000"):
    assert leak not in all_text, f"price leaked into the group: {leak}\n{all_text}"
print("7. no VND leakage OK")

# ---- 8. one message per job, in the same order as the numbering -------------------
oid4b = store.create_order(cid, "cua_keo", "Đơn thứ hai", None, TODAY)
door(oid4b, "Hai A")
msgs = viec()
assert len(msgs) == len(store.orders_for_digest(TODAY)) >= 2, \
    f"expected one message per job, got {len(msgs)}"
assert "CHỜ SẢN XUẤT" in msgs[0], "header belongs on the first message"
assert "xong 1.2" in msgs[-1], "sub-number hint belongs on the last message"
print("8. one message per job OK")

# ---- 9. web tick list writes the same state and undoes a mis-typed number ---------
r = client.post("/login", data={"password": "test-pass-123"}, follow_redirects=False)
assert r.status_code == 303, f"login failed: {r.status_code}"
oid5 = store.create_order(cid, "cua_cuon", "Bộ 2 cửa web", None, TODAY)
w1, w2 = door(oid5, "Web A"), door(oid5, "Web B")

page = client.get(f"/don-hang/{oid5}").text
assert "Hạng mục đã sản xuất (0/2)" in page, "tick list missing from the đơn hàng page"

client.post(f"/don-hang/{oid5}/hang-muc/{w1}/da-lam", data={"made": "1"}, follow_redirects=False)
assert store.item_make_counts(oid5) == (1, 2)
assert store.get_order(oid5)["stage"] == "cho_san_xuat", "one tick must not move the đơn"

client.post(f"/don-hang/{oid5}/hang-muc/{w2}/da-lam", data={"made": "1"}, follow_redirects=False)
assert store.get_order(oid5)["stage"] == "dang_san_xuat", "last tick should close the đơn"

# Un-tick (the fix for a wrong "xong" in the group): the hạng mục reopens, and the
# stage stays put — walking a đơn backwards is the stepper's job, not a side effect.
client.post(f"/don-hang/{oid5}/hang-muc/{w2}/da-lam", data={"made": "0"}, follow_redirects=False)
assert store.item_make_counts(oid5) == (1, 2), "un-tick should reopen the hạng mục"
assert store.get_order(oid5)["stage"] == "dang_san_xuat", "un-tick must not move the stage"
print("9. web tick list + undo OK")

# ---- 10. single-hạng-mục đơn: no tick list, bare xong still works ------------------
oid6 = store.create_order(cid, "cua_cuon", "Đơn 1 cửa", 9_000_000, TODAY)
page = client.get(f"/don-hang/{oid6}").text
assert "Hạng mục đã sản xuất" not in page, "single-item đơn should not show a tick list"
viec()
n6 = next(n for n in range(1, 20) if store.get_digest_order(TODAY, n) == oid6)
assert "SẢN XUẤT XONG" in say(f"xong {n6}")["reply"]
assert store.get_order(oid6)["stage"] == "dang_san_xuat"
print("10. single-hạng-mục đơn unchanged OK")

print("ALL HẠNG MỤC XONG SMOKE TESTS PASSED")
