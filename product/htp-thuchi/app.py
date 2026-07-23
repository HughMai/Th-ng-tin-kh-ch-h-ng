"""Sổ Thu Chi — FastAPI app (Hưng Thành Phát family door business).

Sổ thu chi hằng ngày: manual Thu/Chi entries by day and month, plus every đồng
customers paid into the CRM (cọc, thanh toán, công nợ đại lý) pulled live over
/api/thu and shown as read-only "CRM" rows.

Auth: ONE PASSWORD PER PERSON -> signed HMAC-SHA256 session cookie. This is
deliberately different from htp-crm's single shared family password: the kế toán
is an outsider, and every entry is stamped with who wrote it. See CLAUDE.md.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from collections import deque
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")  # load the app-dir .env regardless of CWD (local + Docker)

import store
import views

# ---- config (from environment) ----------------------------------------------
_db_path_env = os.environ.get("DB_PATH", "data/thuchi.db")
DB_PATH = _db_path_env if os.path.isabs(_db_path_env) else str(HERE / _db_path_env)
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
SESSION_TTL = 60 * 60 * 24 * 30  # 30 days — a family phone app, not a bank
COOKIE_SECURE = os.environ.get("COOKIE_INSECURE", "").strip().lower() not in ("1", "true", "yes")
# Ngày bắt đầu dùng sổ. CRM money-in before this is hidden: those months have Thu
# but no Chi (nobody was recording expenses yet), so showing them reads as profit
# that never existed. The CRM keeps that history either way.
START_DATE = os.environ.get("START_DATE", "").strip()
# CRM money-in feed. Either blank -> the app runs fine, just with no "từ CRM" rows.
CRM_URL = os.environ.get("CRM_URL", "").strip().rstrip("/")
THUCHI_TOKEN = os.environ.get("THUCHI_TOKEN", "")


def _parse_users(raw: str) -> dict:
    """USERS="Ba Mẹ:pw1;Kế toán:pw2" -> {"pw1": "Ba Mẹ", ...}. Keyed by password
    because login is a single password field: the password IS the identity."""
    out = {}
    for chunk in (raw or "").split(";"):
        name, sep, pw = chunk.partition(":")
        if sep and name.strip() and pw.strip():
            out[pw.strip()] = name.strip()
    return out


USERS = _parse_users(os.environ.get("USERS", ""))

store.configure(DB_PATH)

app = FastAPI(title="Sổ Thu Chi")
app.add_middleware(GZipMiddleware, minimum_size=500)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


@app.middleware("http")
async def _static_cache_headers(request: Request, call_next):
    """Cache /static/* by whether the URL is fingerprinted (same rule as the CRM):
    ?v=<hash> URLs are immutable, bare URLs get a short TTL so a stale copy can't
    survive a deploy for long on the family's phones."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        if request.query_params.get("v"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "public, max-age=300"
    return response


# ---- PWA: manifest + service worker -------------------------------------------
@app.get("/manifest.webmanifest", include_in_schema=False)
async def pwa_manifest():
    return FileResponse(
        HERE / "static" / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


@app.get("/sw.js", include_in_schema=False)
async def pwa_service_worker():
    body = (HERE / "static" / "sw.js").read_text(encoding="utf-8").replace(
        "__ASSET_V__", views.ASSET_V
    )
    return Response(
        body,
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


# ---- signed session cookie (pattern copied from htp-crm/app.py) ----------------
def _sign(payload: dict) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(SESSION_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def _unsign(token: str) -> dict | None:
    if not SESSION_SECRET:
        return None
    try:
        raw, sig = token.split(".", 1)
    except ValueError:
        return None
    expect = hmac.new(SESSION_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()))
    except Exception:
        return None
    if payload.get("exp", 0) < int(time.time()):
        return None
    return payload


def _user(request: Request) -> str:
    """Logged-in person's name, or '' when not authed. This is what gets stamped
    onto every entry, so it must come from the signed cookie, never a form field."""
    payload = _unsign(request.cookies.get("session", "")) or {}
    return payload.get("user", "")


def _guard(request: Request) -> RedirectResponse | None:
    """HTML-route guard: redirect to /login when not authed."""
    return None if _user(request) else RedirectResponse("/login", status_code=303)


# ---- in-process login throttle (copied from htp-crm/app.py) -------------------
_buckets: dict = {}


def _rate_check(key: str, limit: int, window: int) -> bool:
    dq = _buckets.get(key)
    if not dq:
        return False
    cutoff = time.time() - window
    while dq and dq[0] < cutoff:
        dq.popleft()
    return len(dq) >= limit


def _rate_mark(key: str) -> None:
    _buckets.setdefault(key, deque()).append(time.time())


# ---- helpers ------------------------------------------------------------------
def _parse_vnd(s: str) -> int | None:
    """'12.000.000' / '12,000,000' / '12000000' -> 12000000; blank -> None."""
    digits = "".join(c for c in (s or "") if c.isdigit())
    return int(digits) if digits else None


def _valid_month(s: str) -> bool:
    """'2026-07' shaped month string, as produced by <input type=month>."""
    return len(s) == 7 and s[4] == "-" and s[:4].isdigit() and s[5:].isdigit()


def _valid_day(s: str) -> bool:
    """'2026-07-23' shaped date string, as produced by <input type=date>."""
    return len(s) == 10 and s[4] == "-" and s[7] == "-" and _valid_month(s[:7]) and s[8:].isdigit()


def _clean_kind(s: str) -> str:
    return "thu" if (s or "").strip() == "thu" else "chi"


def _clean_method(s: str) -> str:
    s = (s or "").strip()
    return s if s in store.METHODS else "tien_mat"


def crm_thu(start: str, end: str) -> tuple:
    """Tiền khách trả from the CRM for a VN date range -> (rows, error).

    Read live on every render so a công nợ correction made in the CRM shows up
    here at once — nothing is copied into our db, which is why these rows are
    read-only in this app. A CRM that's down must never take the sổ down with it:
    on any failure we return no rows plus a message the page shows inline, and
    manual Thu/Chi keeps working.
    """
    if not (CRM_URL and THUCHI_TOKEN):
        return [], ""
    # Never ask for anything before the sổ started (see START_DATE).
    if START_DATE and end < START_DATE:
        return [], ""
    if START_DATE and start < START_DATE:
        start = START_DATE
    try:
        resp = requests.get(
            f"{CRM_URL}/api/thu",
            params={"tu": start, "den": end},
            headers={"X-Thuchi-Token": THUCHI_TOKEN},
            timeout=4,
        )
        resp.raise_for_status()
        return resp.json().get("thu", []), ""
    except Exception:
        return [], "Chưa lấy được tiền đã thu bên CRM — phần nhập tay vẫn ghi bình thường."


# ---- login / logout -----------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
def login_form(error: str = ""):
    return views.login_page(error)


@app.post("/login")
def login_submit(request: Request, password: str = Form(...)):
    ip = request.client.host if request.client else "?"
    if _rate_check(f"login:{ip}", 5, 60):  # max 5 failed attempts / minute / IP
        raise HTTPException(status_code=429, detail="Thử lại sau một phút nhé.")
    # Constant-time compare against every configured password so a wrong guess
    # can't be timed to learn which person's password it nearly matched. Compared
    # as bytes because compare_digest rejects non-ASCII str, and a family password
    # with Vietnamese dấu would otherwise 500 instead of logging anyone in.
    name = ""
    given = password.encode("utf-8")
    for pw, who in USERS.items():
        if secrets.compare_digest(given, pw.encode("utf-8")):
            name = who
    if not name:
        _rate_mark(f"login:{ip}")
        return RedirectResponse("/login?error=1", status_code=303)
    _buckets.pop(f"login:{ip}", None)
    payload = {"user": name, "exp": int(time.time()) + SESSION_TTL}
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("session", _sign(payload), httponly=True, samesite="lax",
                    max_age=SESSION_TTL, secure=COOKIE_SECURE)
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session")
    return resp


@app.get("/health")
def health():
    return {"status": "ok"}


# ---- xem sổ -------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if r := _guard(request):
        return r
    return RedirectResponse("/ngay", status_code=303)


@app.get("/ngay", response_class=HTMLResponse)
def day_view(request: Request, ngay: str = "", loi: str = ""):
    """Màn hình chính — nhập nhanh ở trên, sổ của ngày ở dưới."""
    if r := _guard(request):
        return r
    day = ngay if _valid_day(ngay) else store.today_vn()
    entries = store.entries_between(day, day)
    crm_rows, crm_error = crm_thu(day, day)
    return views.day_page(
        day=day,
        entries=entries,
        crm_rows=crm_rows,
        crm_error=crm_error,
        totals=store.totals(entries, crm_rows),
        methods=store.by_method(entries, crm_rows),
        user=_user(request),
        error=loi,
    )


@app.get("/thang", response_class=HTMLResponse)
def month_view(request: Request, thang: str = ""):
    if r := _guard(request):
        return r
    month = thang if _valid_month(thang) else store.today_vn()[:7]
    days = store.days_in_month(month)
    start, end = days[0], days[-1]
    entries = store.entries_between(start, end)
    crm_rows, crm_error = crm_thu(start, end)
    return views.month_page(
        month=month,
        rows=store.month_rows(month, entries, crm_rows),
        totals=store.totals(entries, crm_rows),
        methods=store.by_method(entries, crm_rows),
        crm_error=crm_error,
        user=_user(request),
    )


# ---- ghi sổ -------------------------------------------------------------------
@app.post("/them")
def entry_add(request: Request, kind: str = Form("chi"), so_tien: str = Form(""),
              ngay: str = Form(""), mo_ta: str = Form(""), hinh_thuc: str = Form("tien_mat")):
    if r := _guard(request):
        return r
    day = ngay if _valid_day(ngay) else store.today_vn()
    amount = _parse_vnd(so_tien)
    if not amount:
        # Back to the same day with the message inline — a family phone should
        # never see a raw 400 page for a mistyped number.
        return RedirectResponse(f"/ngay?ngay={day}&loi=so-tien", status_code=303)
    store.add_entry(_clean_kind(kind), amount, day, mo_ta,
                    _clean_method(hinh_thuc), _user(request))
    return RedirectResponse(f"/ngay?ngay={day}", status_code=303)


@app.get("/sua/{entry_id}", response_class=HTMLResponse)
def entry_edit_form(request: Request, entry_id: int):
    if r := _guard(request):
        return r
    entry = store.get_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Không thấy dòng này")
    return views.edit_page(entry=entry, user=_user(request))


@app.post("/sua/{entry_id}")
def entry_edit(request: Request, entry_id: int, kind: str = Form("chi"),
               so_tien: str = Form(""), ngay: str = Form(""), mo_ta: str = Form(""),
               hinh_thuc: str = Form("tien_mat")):
    if r := _guard(request):
        return r
    if not store.get_entry(entry_id):
        raise HTTPException(status_code=404, detail="Không thấy dòng này")
    day = ngay if _valid_day(ngay) else store.today_vn()
    amount = _parse_vnd(so_tien)
    if not amount:
        return RedirectResponse(f"/sua/{entry_id}", status_code=303)
    store.update_entry(entry_id, _clean_kind(kind), amount, day, mo_ta,
                       _clean_method(hinh_thuc), _user(request))
    return RedirectResponse(f"/ngay?ngay={day}", status_code=303)


@app.post("/xoa/{entry_id}")
def entry_delete(request: Request, entry_id: int):
    if r := _guard(request):
        return r
    entry = store.get_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Không thấy dòng này")
    store.delete_entry(entry_id)
    return RedirectResponse(f"/ngay?ngay={entry['entry_date']}", status_code=303)
