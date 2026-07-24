"""Smoke test for Sổ Thu Chi.

Covers the things that would quietly corrupt the family's book: per-person login
and the stamp it writes, day/month totals, edit/delete, START_DATE gating, and
the CRM feed (including a CRM that's down — the sổ must keep working).

The CRM feed is stubbed at app.crm_thu so this test never needs a live CRM; the
real CRM endpoint has its own test (htp-crm/tests_smoke_api_thu.py).

Runs against a throwaway temp SQLite DB — never touches data/thuchi.db.

Run with: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe tests_smoke.py
"""
import os
import tempfile
from pathlib import Path

_tmp_dir = tempfile.mkdtemp(prefix="htp_thuchi_test_")
os.environ["DB_PATH"] = str(Path(_tmp_dir) / "test.db")
os.environ["SESSION_SECRET"] = "test-session-secret"
# "Bà Nội" carries a deliberately accented password — compare_digest rejects
# non-ASCII str, so this is the case that 500s if the byte-compare regresses.
os.environ["USERS"] = "Ba Mẹ:pw-bame;Kế toán:pw-ketoan;Hughie:pw-hughie;Bà Nội:mật-khẩu-có-dấu"
os.environ["COOKIE_INSECURE"] = "1"  # TestClient talks plain http://testserver
os.environ["START_DATE"] = "2026-07-01"
os.environ["CRM_URL"] = ""  # no live CRM in tests; crm_thu is stubbed below

from fastapi.testclient import TestClient

import app
import store
import views

client = TestClient(app.app)

# Kept aside before the stub below replaces it — the real one is exercised at the
# end of this file (START_DATE window + CRM-outage path).
_REAL_CRM_THU = app.crm_thu

DAY = "2026-07-20"
MONTH = "2026-07"


def login(password: str) -> None:
    client.cookies.clear()
    r = client.post("/login", data={"password": password}, follow_redirects=False)
    assert r.status_code == 303, f"login failed: {r.status_code} {r.text}"
    assert "session" in client.cookies, "no session cookie set after login"


# ---- auth --------------------------------------------------------------------
r = client.get("/ngay", follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == "/login", \
    "day view must not be reachable without logging in"

r = client.post("/login", data={"password": "sai-mat-khau"}, follow_redirects=False)
assert r.status_code == 303 and "error=1" in r.headers["location"], "wrong password should bounce"
assert "session" not in client.cookies, "a failed login must not set a session"

# a wrong password with Vietnamese dấu must fail cleanly, not blow up
r = client.post("/login", data={"password": "sai-mật-khẩu"}, follow_redirects=False)
assert r.status_code == 303 and "error=1" in r.headers["location"], \
    "an accented wrong password should bounce, not 500"

# the login field must not let a phone keyboard "help" — a Vietnamese IME
# autocapitalising the first letter is indistinguishable from a wrong password
r = client.get("/login")
for attr in ('type="password"', 'autocapitalize="none"', 'autocorrect="off"',
             'spellcheck="false"', 'autocomplete="current-password"'):
    assert attr in r.text, f"login input is missing {attr}"

# too many wrong tries -> a Vietnamese "wait a minute" page, never a raw JSON 429
for _ in range(6):
    r = client.post("/login", data={"password": "sai"}, follow_redirects=False)
assert r.status_code == 303 and "error=cho" in r.headers["location"], \
    "throttled login should redirect with a readable message, not raise 429"
assert "đợi một phút" in client.get("/login?error=cho").text, \
    "the throttle message should render in Vietnamese on the login page"
app._buckets.clear()  # reset the throttle for the tests that follow

# every configured person can get in, and lands as themselves
for pw, who in (("pw-bame", "Ba Mẹ"), ("pw-ketoan", "Kế toán"), ("pw-hughie", "Hughie"),
                ("mật-khẩu-có-dấu", "Bà Nội")):
    login(pw)
    r = client.get("/ngay")
    assert r.status_code == 200, f"{who} could not open the day view"
    assert who in r.text, f"top bar should show who is logged in ({who})"

# ---- ghi sổ: the stamp is the point ------------------------------------------
login("pw-bame")
r = client.post("/them", data={"kind": "chi", "so_tien": "1.250.000", "ngay": DAY,
                               "mo_ta": "Mua tôn", "hinh_thuc": "tien_mat"},
                follow_redirects=False)
assert r.status_code == 303, f"POST /them -> {r.status_code} {r.text}"

rows = store.entries_between(DAY, DAY)
assert len(rows) == 1, f"expected 1 entry, got {rows}"
e = rows[0]
assert e["amount_vnd"] == 1_250_000, f"'1.250.000' should parse to 1250000, got {e['amount_vnd']}"
assert e["kind"] == "chi" and e["method"] == "tien_mat"
assert e["created_by"] == "Ba Mẹ", f"entry not stamped with who wrote it: {e}"
assert e["updated_by"] == "", "a fresh entry should have no updated_by"

# a blank/garbage amount must not create a 0đ ghost row
r = client.post("/them", data={"kind": "chi", "so_tien": "", "ngay": DAY, "mo_ta": "trống"},
                follow_redirects=False)
assert r.status_code == 303 and "loi=so-tien" in r.headers["location"], \
    "empty amount should bounce back with a message"
assert len(store.entries_between(DAY, DAY)) == 1, "a blank amount created a row"

# ---- sửa: created_by stays, updated_by records who touched it ------------------
login("pw-ketoan")
eid = e["id"]
r = client.post(f"/sua/{eid}", data={"kind": "chi", "so_tien": "1.300.000", "ngay": DAY,
                                     "mo_ta": "Mua tôn (sửa)", "hinh_thuc": "chuyen_khoan"},
                follow_redirects=False)
assert r.status_code == 303, f"POST /sua -> {r.status_code} {r.text}"
e2 = store.get_entry(eid)
assert e2["amount_vnd"] == 1_300_000 and e2["method"] == "chuyen_khoan"
assert e2["created_by"] == "Ba Mẹ", "sửa must not rewrite who originally entered it"
assert e2["updated_by"] == "Kế toán", f"sửa must record who changed it, got {e2['updated_by']}"

r = client.get(f"/sua/{eid}")
assert r.status_code == 200 and "1.300.000" in r.text, "edit form should show the grouped amount"
r = client.get("/sua/999999")
assert r.status_code == 404, "editing a row that doesn't exist should 404, not 500"

# ---- totals + CRM feed --------------------------------------------------------
login("pw-bame")
store.add_entry("thu", 2_000_000, DAY, "Bán lẻ ngoài", "tien_mat", "Ba Mẹ")

CRM_ROWS = [
    {"date": DAY, "name": "Chị Lan", "kind": "coc", "method": "tiền mặt", "amount_vnd": 5_000_000},
    {"date": DAY, "name": "Đại lý A", "kind": "thanh_toan", "method": "chuyển khoản",
     "amount_vnd": 12_000_000},
]
app.crm_thu = lambda start, end: (CRM_ROWS, "")

r = client.get(f"/ngay?ngay={DAY}")
assert r.status_code == 200
# thu = 2tr manual + 5tr cọc + 12tr công nợ = 19tr; chi = 1,3tr; còn lại = 17,7tr
assert "19.000.000đ" in r.text, "day Thu total should include the CRM money-in"
assert "1.300.000đ" in r.text, "day Chi total missing"
assert "17.700.000đ" in r.text, "còn lại should be Thu − Chi"
assert "Chị Lan" in r.text and "Đại lý A" in r.text, "CRM rows should be listed"
assert "đặt cọc" in r.text, "a cọc row should be labelled as such"
assert r.text.count('href="/sua/') == 2, "CRM rows must be read-only (no Sửa link)"

# hình thức split folds the CRM's free-text methods onto our three buckets
assert "Tiền mặt: 7.000.000đ" in r.text, "tiền mặt split wrong (2tr manual + 5tr CRM)"
assert "Chuyển khoản: 12.000.000đ" in r.text, "chuyển khoản split wrong"

# ---- tháng --------------------------------------------------------------------
r = client.get(f"/thang?thang={MONTH}")
assert r.status_code == 200
assert "19.000.000đ" in r.text and "17.700.000đ" in r.text, "month totals should match the day"
assert "20/7" in r.text, "month ledger should list the day that has movement"
assert "31/7" in r.text, "month ledger should show every day, not just active ones"

# ---- CRM down: the sổ keeps working -------------------------------------------
app.crm_thu = lambda start, end: ([], "Chưa lấy được tiền đã thu bên CRM — phần nhập tay vẫn ghi bình thường.")
r = client.get(f"/ngay?ngay={DAY}")
assert r.status_code == 200, "a CRM outage must not take the sổ down"
assert "Chưa lấy được" in r.text, "a CRM outage should say so on the page"
assert "2.000.000đ" in r.text, "manual Thu should still show while the CRM is down"

r = client.post("/them", data={"kind": "chi", "so_tien": "500000", "ngay": DAY, "mo_ta": "Xăng"},
                follow_redirects=False)
assert r.status_code == 303, "must still be able to write while the CRM is down"

# ---- xoá ----------------------------------------------------------------------
before = len(store.entries_between(DAY, DAY))
r = client.post(f"/xoa/{eid}", follow_redirects=False)
assert r.status_code == 303, f"POST /xoa -> {r.status_code}"
assert len(store.entries_between(DAY, DAY)) == before - 1, "xoá did not remove the row"
assert store.get_entry(eid) == {}, "row should be gone"
r = client.post("/xoa/999999", follow_redirects=False)
assert r.status_code == 404, "deleting a row that doesn't exist should 404, not 500"

# ---- the real crm_thu: START_DATE gating + outage handling --------------------
# Everything above stubbed crm_thu; this exercises the real one against a fake
# requests.get so the START_DATE window and the failure path are both covered.
app.crm_thu = _REAL_CRM_THU
assert app.crm_thu("2026-07-20", "2026-07-20") == ([], ""), \
    "with no CRM configured the app should stay quiet, not error"

app.CRM_URL, app.THUCHI_TOKEN = "http://crm.test", "tok"
asked: dict = {}


class _FakeResp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"thu": CRM_ROWS}


def _fake_get(url, params=None, headers=None, timeout=None):
    asked.update(params or {})
    assert headers.get("X-Thuchi-Token") == "tok", "the CRM call must carry the token"
    return _FakeResp()


app.requests.get = _fake_get

rows, err = app.crm_thu(DAY, DAY)
assert rows == CRM_ROWS and err == "", "a healthy CRM should hand back its rows"

# START_DATE is 2026-07-01: a range starting earlier gets clamped, not truncated
# server-side, so old months can't show Thu with no Chi beside it.
app.crm_thu("2026-06-01", "2026-07-20")
assert asked["tu"] == "2026-07-01", f"start should clamp to START_DATE, asked {asked['tu']}"

# a range entirely before the sổ started shouldn't even hit the CRM
asked.clear()
assert app.crm_thu("2026-05-01", "2026-06-30") == ([], "")
assert not asked, "no CRM call should be made for dates before START_DATE"


def _boom(*a, **k):
    raise RuntimeError("CRM down")


app.requests.get = _boom
rows, err = app.crm_thu(DAY, DAY)
assert rows == [] and "Chưa lấy được" in err, "a CRM outage should degrade to a message"

# ---- helpers ------------------------------------------------------------------
assert app._parse_vnd("12.000.000") == 12_000_000
assert app._parse_vnd("12,000,000") == 12_000_000
assert app._parse_vnd("abc") is None
assert app._valid_day("2026-07-20") and not app._valid_day("20-07-2026")
assert app._valid_month("2026-07") and not app._valid_month("2026-7")
assert app._clean_kind("thu") == "thu" and app._clean_kind("junk") == "chi"
assert app._clean_method("junk") == "tien_mat"
assert app._parse_users("A:1;B:2") == {"1": "A", "2": "B"}
assert app._parse_users("") == {}, "no USERS configured should lock everyone out, not crash"

assert store._fold_method("Tiền mặt") == "tien_mat"
assert store._fold_method("chuyển khoản") == "chuyen_khoan"
assert store._fold_method("CK") == "chuyen_khoan"
assert store._fold_method("") == "khac"
assert len(store.days_in_month("2026-02")) == 28, "Feb 2026 has 28 days"
assert len(store.days_in_month("2024-02")) == 29, "Feb 2024 is a leap year"
assert len(store.days_in_month("2026-12")) == 31, "December rollover"
assert store.days_in_month("2026-12")[-1] == "2026-12-31"

assert views.fmt_vnd(1_250_000) == "1.250.000đ"
assert views._shift_day("2026-01-01", -1) == "2025-12-31"
assert views._shift_month("2026-01", -1) == "2025-12"
assert views._shift_month("2026-12", 1) == "2027-01"

# ---- health -------------------------------------------------------------------
client.cookies.clear()
r = client.get("/health")
assert r.status_code == 200 and r.json()["status"] == "ok"

print("ALL SMOKE TESTS PASSED")
