"""HTP CRM — FastAPI app (Hưng Thành Phát family door business).

Sổ khách hàng: customers, quote follow-up, warranty check-ins, dealer công nợ.
Server-rendered Vietnamese HTML (views.py), SQLite (store.py), no JS framework.

Auth: ONE shared family password -> signed HMAC-SHA256 session cookie (pattern
copied from product/qr-menu/app.py, simplified to a single role). Per-IP login
throttle. 30-day sessions so the parents don't retype the password.
"""
import base64
import hashlib
import hmac
import json
import os
import re
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

import baogia
import pricing
import store
import views
import zalo_client

# ---- config (from environment) ----------------------------------------------
_db_path_env = os.environ.get("DB_PATH", "data/htp.db")
DB_PATH = _db_path_env if os.path.isabs(_db_path_env) else str(HERE / _db_path_env)
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
FAMILY_PASSWORD = os.environ.get("FAMILY_PASSWORD", "")
SESSION_TTL = 60 * 60 * 24 * 30  # 30 days — a family phone app, not a bank
# Secure session cookies by default. Set COOKIE_INSECURE=1 ONLY for http local dev.
COOKIE_SECURE = os.environ.get("COOKIE_INSECURE", "").strip().lower() not in ("1", "true", "yes")
ZALO_APP_ID = os.environ.get("ZALO_APP_ID", "")
ZALO_APP_SECRET = os.environ.get("ZALO_APP_SECRET", "")
PUBLIC_HOSTNAME = os.environ.get("PUBLIC_HOSTNAME", "")
ZALO_REDIRECT_URI = f"https://{PUBLIC_HOSTNAME}/zalo/oauth/callback"
# Zalo group bot sidecar (zca-js) — optional. Every bot feature below no-ops
# when either is blank, so the app runs unchanged without the bot container.
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
BOT_URL = os.environ.get("BOT_URL", "")
# Shared secret for the hungthanhphat.vn website's form → /api/yeu-cau forward.
# Blank = the JSON endpoint is disabled (503); the HTML /yeu-cau form is unaffected.
WEB_LEAD_TOKEN = os.environ.get("WEB_LEAD_TOKEN", "")
# Shared secret for Sổ Thu Chi's read-only money-in pull (/api/thu).
# Blank = the endpoint is disabled (403), like every other token API here.
THUCHI_TOKEN = os.environ.get("THUCHI_TOKEN", "")
# Company header printed on the exported Báo Giá. Defaults are HTP's real brand
# details (from the quote template); any field can be overridden via env, and a
# field left blank is simply omitted from the sheet.
COMPANY = {
    "name": os.environ.get("COMPANY_NAME", "HƯNG THÀNH PHÁT DOOR"),
    "tagline": os.environ.get("COMPANY_TAGLINE", "Cửa Cuốn · Cửa Kéo · Cửa Nhôm Kính"),
    "phone": os.environ.get("COMPANY_PHONE", "0945 042 345 | 0913 574 077"),
    "address": os.environ.get("COMPANY_ADDRESS", "235 - 237 (281 Cũ) Võ Văn Kiệt, Bình Thủy, Cần Thơ"),
    "email": os.environ.get("COMPANY_EMAIL", "hungthanhphat6688@gmail.com"),
    "website": os.environ.get("COMPANY_WEBSITE", "hungthanhphat.vn"),
    "signer": os.environ.get("COMPANY_SIGNER", ""),  # "Người báo giá" — printed name, blank = sign by hand
    # VietQR scan-to-pay on the báo giá. bank_bin is the NAPAS code (Vietcombank
    # = 970436) and bank_account must be the real account number — bank
    # nicknames/aliases aren't part of the VietQR standard and won't resolve.
    "bank_name": os.environ.get("COMPANY_BANK_NAME", "Vietcombank"),
    "bank_bin": os.environ.get("COMPANY_BANK_BIN", "970436"),
    "bank_account": os.environ.get("COMPANY_BANK_ACCOUNT", "0111000327986"),
    "bank_holder": os.environ.get("COMPANY_BANK_HOLDER", "Phan Thi Hong"),
}

store.configure(DB_PATH)
zalo_client.configure(str(Path(DB_PATH).parent / "zalo_tokens.json"))

app = FastAPI(title="HTP CRM")
app.add_middleware(GZipMiddleware, minimum_size=500)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


@app.middleware("http")
async def _static_cache_headers(request: Request, call_next):
    """Cache /static/* by whether the URL is fingerprinted.

    A ?v=<hash> URL is safe to cache forever: new bytes ship under a new URL, so
    a stale copy can never be served. Un-fingerprinted URLs (fonts, icons, and
    any old link still pointing at bare /static/app.css) get a short TTL instead
    — that's what let pre-board CSS survive a deploy for a day on the family's
    phones, and a 5-minute window keeps the blast radius small if it recurs."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        if request.query_params.get("v"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "public, max-age=300"
    return response


# ---- PWA: manifest + service worker (installable "app" on iOS/Android/desktop) --
# sw.js is served from the origin root so its scope is the whole site. no-cache so
# a redeploy pushes the new worker instead of a day-stale one. The manifest gets
# the correct content-type browsers expect.
@app.get("/manifest.webmanifest", include_in_schema=False)
async def pwa_manifest():
    return FileResponse(
        HERE / "static" / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


@app.get("/sw.js", include_in_schema=False)
async def pwa_service_worker():
    # Substitute the asset fingerprint so the worker's cache name and precache
    # URLs track app.css/app.js content. The body changes whenever they do, which
    # is also what makes the browser treat this as a new worker and re-install.
    body = (HERE / "static" / "sw.js").read_text(encoding="utf-8").replace(
        "__ASSET_V__", views.ASSET_V
    )
    return Response(
        body,
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


# ---- signed session cookie (copied from qr-menu/app.py) ----------------------
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


def _authed(request: Request) -> bool:
    token = request.cookies.get("session", "")
    return _unsign(token) is not None


def _guard(request: Request) -> RedirectResponse | None:
    """HTML-route guard: redirect to /login when not authed."""
    return None if _authed(request) else RedirectResponse("/login", status_code=303)


# ---- in-process login throttle (copied from qr-menu/app.py) -------------------
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


def _parse_dim(*vals: str) -> tuple:
    """Parse door dimensions (mm) from the hạng-mục form. The client input is
    inputmode=numeric but that's cosmetic — a non-numeric/blank value must yield
    a clean 400, not an int() ValueError -> 500. Requires a positive integer so
    a 0×0 (or negative) '0đ' junk line can't be created."""
    out = []
    for v in vals:
        v = (v or "").strip()
        if not v.isdigit() or int(v) <= 0:
            raise HTTPException(status_code=400, detail="Kích thước phải là số mm lớn hơn 0")
        out.append(int(v))
    return tuple(out)


def _reject_if_ordered(q: dict) -> None:
    """A báo giá that's been chốt (linked to an đơn hàng) is READ-ONLY. Its line
    items/deposit/value are snapshotted onto the order at chốt, and the đơn hàng
    is the editable surface from then on. Without this guard, editing the quote
    afterward silently desyncs the order (order keeps the stale snapshot + a
    now-wrong công nợ). Keyed on order_id, so a won card dragged back to 'sent'
    stays locked while its order lives, and unlocks once the order is deleted
    (delete_order clears quotes.order_id)."""
    if q and q.get("order_id"):
        raise HTTPException(status_code=400,
                            detail=f"Báo giá đã chốt — sửa trên đơn hàng #{q['order_id']}")


def _safe_next(nxt: str, fallback: str) -> str:
    """Only allow same-app relative redirects."""
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else fallback


def _valid_month(s: str) -> bool:
    """'2026-07' shaped month string, as produced by <input type=month>."""
    return len(s) == 7 and s[4] == "-" and s[:4].isdigit() and s[5:].isdigit()


def _valid_day(s: str) -> bool:
    """'2026-07-17' shaped date string, as produced by <input type=date>."""
    return len(s) == 10 and s[4] == "-" and s[7] == "-" and _valid_month(s[:7]) and s[8:].isdigit()


# ---- login / logout -----------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
def login_form(error: str = ""):
    return views.login_page(error)


@app.post("/login")
def login_submit(request: Request, password: str = Form(...)):
    ip = request.client.host if request.client else "?"
    if _rate_check(f"login:{ip}", 5, 60):  # max 5 failed attempts / minute / IP
        raise HTTPException(status_code=429, detail="Thử lại sau một phút nhé.")
    if not (FAMILY_PASSWORD and secrets.compare_digest(password, FAMILY_PASSWORD)):
        _rate_mark(f"login:{ip}")
        return RedirectResponse("/login?error=1", status_code=303)
    _buckets.pop(f"login:{ip}", None)
    payload = {"role": "family", "exp": int(time.time()) + SESSION_TTL}
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


# ---- Zalo domain-ownership verification (Xác thực quyền sở hữu, developers.zalo.me) --
# Zalo requires this exact static file served at this exact path before the
# redirect_uri domain (crm.187-77-133-39.sslip.io) can be registered on the app.
# Hardcoded (not read from disk) — no path traversal surface, and this is a
# public unauthenticated route since Zalo's crawler doesn't have our cookie.
_ZALO_DOMAIN_VERIFY_FILE = "zalo_verifierPeRY2UM9Ip0UXk4EoOvL23M_-mdPoI5LDJWs.html"
_ZALO_DOMAIN_VERIFY_HTML = """<!DOCTYPE html>
<html lang="en">

<head>
    <meta property="zalo-platform-site-verification" content="PeRY2UM9Ip0UXk4EoOvL23M_-mdPoI5LDJWs" />
</head>

<body>
There Is No Limit To What You Can Accomplish Using Zalo!
</body>

</html>"""


@app.get("/zalo/oauth/callback/{filename}", response_class=HTMLResponse)
def zalo_domain_verify(filename: str):
    if filename != _ZALO_DOMAIN_VERIFY_FILE:
        raise HTTPException(status_code=404)
    return _ZALO_DOMAIN_VERIFY_HTML


# Zalo's separate "Xác thực domain" flow wants the same file at the domain
# root instead. A literal exact path (not a `/{filename}` catch-all) so it
# can't shadow the many other single-segment routes (/khach, /zalo, etc.).
@app.get(f"/{_ZALO_DOMAIN_VERIFY_FILE}", response_class=HTMLResponse)
def zalo_domain_verify_root():
    return _ZALO_DOMAIN_VERIFY_HTML


# ---- public lead form (/yeu-cau) ------------------------------------------------
# Unauthenticated on purpose (like /login and the Zalo verify files): this is
# the link the family hands out on Zalo/Facebook/Chợ Tốt so a customer can
# leave a quote request after hours. Spam guards: honeypot field + per-IP
# hourly throttle + hard length caps. A submission lands as a stage='lead'
# customer plus a due-today reminder, so it surfaces in Hôm nay's Nhắc việc
# with zero new UI. (/bao-gia was taken by the internal quotes tab.)
_NEED_LABELS = dict(views.NEED_OPTIONS)


@app.get("/yeu-cau", response_class=HTMLResponse)
def lead_form_public():
    return views.baogia_public_page(COMPANY)


@app.post("/yeu-cau")
def lead_form_submit(request: Request, ten: str = Form(""), sdt: str = Form(""),
                     khu_vuc: str = Form(""), nhu_cau: str = Form("khac"),
                     kich_thuoc: str = Form(""), ghi_chu: str = Form(""),
                     website: str = Form("")):
    if website.strip():  # honeypot — bots fill it, humans never see it
        return RedirectResponse("/yeu-cau/cam-on", status_code=303)
    ip = request.client.host if request.client else "?"
    if _rate_check(f"lead:{ip}", 5, 3600):  # max 5 accepted submissions / hour / IP
        raise HTTPException(status_code=429, detail="Gửi nhiều quá, anh/chị thử lại sau nhé.")
    ten, khu_vuc = ten.strip()[:80], khu_vuc.strip()[:120]
    kich_thuoc, ghi_chu = kich_thuoc.strip()[:60], ghi_chu.strip()[:500]
    digits = re.sub(r"\D", "", sdt)
    if len(ten) < 2 or not (9 <= len(digits) <= 12):
        return HTMLResponse(views.baogia_public_page(
            COMPANY,
            vals={"ten": ten, "sdt": sdt, "khu_vuc": khu_vuc, "nhu_cau": nhu_cau,
                  "kich_thuoc": kich_thuoc, "ghi_chu": ghi_chu},
            error="Anh/chị kiểm tra lại họ tên và số điện thoại giúp em nhé."))
    _rate_mark(f"lead:{ip}")
    store.add_web_lead(ten, digits, khu_vuc, _NEED_LABELS.get(nhu_cau, "Khác"),
                       kich_thuoc, ghi_chu)
    _ping_group_new_lead(ten, digits, _NEED_LABELS.get(nhu_cau, "Khác"), kich_thuoc)
    return RedirectResponse("/yeu-cau/cam-on", status_code=303)


@app.get("/yeu-cau/cam-on", response_class=HTMLResponse)
def lead_form_thanks():
    return views.baogia_thanks_page(COMPANY)


def _ping_group_new_lead(name: str, phone: str, need: str, size: str) -> None:
    """Instant Zalo-group heads-up for a web lead. Name/phone/need/size ONLY —
    the no-VND-in-group rule means the website's price estimate never rides
    along (it still lands in the CRM reminder note, which is family-only)."""
    parts = [f"📥 KHÁCH MỚI TỪ WEBSITE", f"👤 {name} — 📞 {phone}"]
    detail = " · ".join(x for x in (need, size) if x)
    if detail:
        parts.append(detail)
    parts.append("→ Gọi lại trong 15 phút nha!")
    _bot_send("\n".join(parts))


@app.post("/api/yeu-cau")
def lead_api_submit(request: Request, payload: dict):
    """JSON forward from hungthanhphat.vn's contact form (route.ts notifyViaCRM).
    Token-gated so random internet POSTs can't write leads; the public HTML
    form above stays the unauthenticated path with its own honeypot/throttle."""
    if not WEB_LEAD_TOKEN:
        raise HTTPException(status_code=503, detail="web lead forward not configured")
    if not secrets.compare_digest(request.headers.get("X-Web-Token", ""), WEB_LEAD_TOKEN):
        raise HTTPException(status_code=401, detail="bad token")
    name = str(payload.get("name", "")).strip()[:80]
    digits = re.sub(r"\D", "", str(payload.get("phone", "")))
    need = str(payload.get("need", "")).strip()[:120]
    size = str(payload.get("size", "")).strip()[:60]
    note = str(payload.get("note", "")).strip()[:500]
    if len(name) < 2 or not (9 <= len(digits) <= 12):
        raise HTTPException(status_code=422, detail="ten/sdt")
    store.add_web_lead(name, digits, "", need or "Từ website", size, note)
    _ping_group_new_lead(name, digits, need, size)
    return {"ok": True}


# ---- Hôm nay -------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def today_page(request: Request):
    if r := _guard(request):
        return r
    today = store.today_vn()
    body = views.today_page(
        chase=store.quotes_to_chase(today),
        installs=store.orders_install_due(today),
        checkins=store.orders_checkin_due(today),
        expiring=store.orders_expiring(today),
        debts=store.debts_overdue(today),
        reminders=store.reminders_due(today),
        reviews=store.orders_review_due(today),
        kh_debts=store.orders_debt_due(today),
        summary=store.open_quotes_summary(),
        debt_total=store.debt_outstanding_total(),
        bot_ready=bool(BOT_URL and BOT_TOKEN),
        today=today,
    )
    return views.page("Hôm nay", body, active="/")


# ---- khách hàng ----------------------------------------------------------------
@app.get("/khach", response_class=HTMLResponse)
def customers(request: Request, q: str = "", loai: str = ""):
    if r := _guard(request):
        return r
    # No stage filter — the list shows every khách. Filtering by stage here is
    # what made manually-added customers vanish from the only page that lists them.
    rows = store.list_customers(q=q, type_filter=loai)
    return views.page("Khách hàng", views.customers_page(rows, q, loai), active="/khach")


@app.get("/khach/moi", response_class=HTMLResponse)
def customer_new_form(request: Request, next: str = ""):
    if r := _guard(request):
        return r
    return views.page("Thêm khách", views.customer_form_page(next_to=next), active="/khach")


@app.post("/khach/moi")
def customer_new(request: Request, name: str = Form(...), phone: str = Form(...),
                 type: str = Form("KH"), source: str = Form("khac"),
                 address: str = Form(""), note: str = Form(""),
                 zalo_phone: str = Form(""), email: str = Form(""), next: str = Form("")):
    if r := _guard(request):
        return r
    cid = store.create_customer(name, phone, type, source, address, note, zalo_phone, email=email)
    if next == "bao-gia-moi":
        return RedirectResponse(f"/bao-gia/moi?khach={cid}", status_code=303)
    return RedirectResponse(f"/khach/{cid}", status_code=303)


# ---- tiếp nhận khách (3-step intake wizard: customer + doors + deposit) --------
# Declared BEFORE /khach/{customer_id} so "tiep-nhan" isn't parsed as an int id.
@app.get("/khach/tiep-nhan", response_class=HTMLResponse)
def intake_form(request: Request):
    if r := _guard(request):
        return r
    return views.page("Khách hàng mới", views.intake_wizard_page(store.today_vn()),
                      active="/khach", wrap_class="form")


@app.post("/khach/tiep-nhan")
def intake_submit(request: Request, name: str = Form(...), phone: str = Form(...),
                  type: str = Form("KH"), source: str = Form("khac"),
                  address: str = Form(""), note: str = Form(""), zalo_phone: str = Form(""),
                  email: str = Form(""), units: str = Form("[]"), accessories: str = Form(""),
                  deposit: str = Form(""), install_date: str = Form(""),
                  quote_note: str = Form("")):
    if r := _guard(request):
        return r
    cid = store.create_customer(name, phone, type, source, address, note, zalo_phone, email=email)
    try:
        raw_units = json.loads(units) if units else []
    except (ValueError, TypeError):
        raw_units = []
    priced = []
    for u in raw_units if isinstance(raw_units, list) else []:
        if not isinstance(u, dict) or u.get("product") not in (*pricing.DOOR_CONFIG, "khac"):
            continue
        try:
            ngang, cao = int(u.get("ngang") or 0), int(u.get("cao") or 0)
        except (ValueError, TypeError):
            continue
        if not ngang or not cao:
            continue
        product = u["product"]
        cong_nghe = (u.get("cong_nghe") or "").strip()
        if product == "khac":  # free-form item: its name rides in cong_nghe (no catalog fields)
            cong_nghe = (u.get("ten") or "").strip()[:120]
        mau = (u.get("mau") or "").strip()
        manual_on = bool(u.get("manual"))
        table_price = pricing.get_price(product, cong_nghe, mau, type)
        if table_price is not None and not manual_on:
            per_sqm, flat = pricing.get_surcharges(product, cong_nghe, pricing.area_m2(ngang, cao))
            thanh_tien, is_manual = pricing.line_total(table_price, ngang, cao, per_sqm, flat), False
        else:
            dongia = _parse_vnd(u.get("gia_manual", "")) or 0
            thanh_tien, is_manual = pricing.manual_line_total(dongia, ngang, cao), True
        if not thanh_tien:
            continue  # no table match and no manual price — skip junk
        priced.append((product, cong_nghe, mau, (u.get("mau_sac") or "").strip(),
                       (u.get("ghi_chu") or "").strip(), ngang, cao, thanh_tien, is_manual))
    # Anything the family typed on step 3 is a real báo giá, even with no cửa —
    # a phụ-kiện-only job (khóa, bình tích điện, chi phí khác) is ordinary work.
    # Gating solely on `priced` silently dropped the phụ kiện, đặt cọc, ngày lắp
    # and ghi chú, leaving a bare customer and no order anywhere.
    deposit_vnd = _parse_vnd(deposit)
    has_header_data = bool(accessories.strip() or deposit_vnd or install_date.strip()
                           or quote_note.strip())
    if priced or has_header_data:
        qid = store.create_quote_header(cid, accessories, deposit_vnd, install_date, quote_note)
        for product, cong_nghe, mau, mau_sac, ghi_chu, ngang, cao, thanh_tien, is_manual in priced:
            store.add_quote_item(qid, product, cong_nghe, mau, ngang, cao,
                                 thanh_tien, is_manual, mau_sac, ghi_chu)
        return RedirectResponse(f"/bao-gia/{qid}", status_code=303)
    return RedirectResponse(f"/khach/{cid}", status_code=303)


@app.get("/khach/{customer_id}", response_class=HTMLResponse)
def customer_detail(request: Request, customer_id: int):
    if r := _guard(request):
        return r
    c = store.get_customer(customer_id)
    if not c:
        raise HTTPException(status_code=404)
    quotes = store.quotes_for_customer(customer_id)
    for q in quotes:  # attach bộ cửa line items so the detail page can show them
        q["items"] = store.quote_items_for(q["id"])
    body = views.customer_detail_page(
        c,
        quotes=quotes,
        orders=store.orders_for_customer(customer_id),
        reminders=store.reminders_for_customer(customer_id),
        balance=store.dealer_balance(customer_id) if c["type"] == "DL" else 0,
        today=store.today_vn(),
        touches=store.list_touches(customer_id),
        phone_dups=store.customers_sharing_phone(customer_id),
    )
    return views.page(c["name"], body, active="/khach")


@app.get("/khach/{customer_id}/sua", response_class=HTMLResponse)
def customer_edit_form(request: Request, customer_id: int, next: str = ""):
    if r := _guard(request):
        return r
    c = store.get_customer(customer_id)
    if not c:
        raise HTTPException(status_code=404)
    return views.page("Sửa thông tin", views.customer_form_page(c, next_to=next), active="/khach")


@app.post("/khach/{customer_id}/sua")
def customer_edit(request: Request, customer_id: int, name: str = Form(...),
                  phone: str = Form(...), type: str = Form("KH"), source: str = Form("khac"),
                  address: str = Form(""), note: str = Form(""), zalo_phone: str = Form(""),
                  email: str = Form(""), next: str = Form("")):
    if r := _guard(request):
        return r
    store.update_customer(customer_id, name, phone, type, source, address, note, zalo_phone, email)
    return RedirectResponse(_safe_next(next, f"/khach/{customer_id}"), status_code=303)


@app.post("/khach/{customer_id}/xoa")
def customer_delete(request: Request, customer_id: int):
    if r := _guard(request):
        return r
    if not store.get_customer(customer_id):
        raise HTTPException(status_code=404)
    store.delete_customer(customer_id)
    return RedirectResponse("/khach", status_code=303)


@app.post("/khach/{customer_id}/cham-soc")
def customer_add_touch(request: Request, customer_id: int, note: str = Form(...)):
    if r := _guard(request):
        return r
    if not store.get_customer(customer_id):
        raise HTTPException(status_code=404)
    if note.strip():
        store.add_touch(customer_id, "ghi_chu", note)
    return RedirectResponse(f"/khach/{customer_id}", status_code=303)


@app.post("/khach/{customer_id}/goi")
def customer_log_call(request: Request, customer_id: int):
    """Beacon target: tapping Gọi dials (tel:) and fires this to log a 'goi'
    touch. JS-off falls back to just dialing — the call simply isn't logged."""
    if r := _guard(request):
        return r
    if not store.get_customer(customer_id):
        raise HTTPException(status_code=404)
    store.add_touch(customer_id, "goi")
    return Response(status_code=204)


@app.post("/khach/{customer_id}/nhac")
def reminder_add(request: Request, customer_id: int,
                 due_date: str = Form(...), note: str = Form(...)):
    if r := _guard(request):
        return r
    store.create_reminder(customer_id, due_date, note)
    return RedirectResponse(f"/khach/{customer_id}", status_code=303)


@app.post("/nhac/{reminder_id}/xong")
def reminder_done(request: Request, reminder_id: int, next: str = Form("/")):
    if r := _guard(request):
        return r
    store.mark_reminder_done(reminder_id)
    return RedirectResponse(_safe_next(next, "/"), status_code=303)


# ---- nhập danh bạ (bulk import) --------------------------------------------------
def _parse_import(raw: str) -> list:
    """Each line: 'Tên, SĐT, KH|ĐL, địa chỉ?' -> preview dicts (ok/dup/bad)."""
    seen_phones = set()
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        row = {"line": line, "name": "", "phone": "", "type": "KH", "address": "",
               "status": "bad", "why": ""}
        if len(parts) < 2 or not parts[0]:
            row["why"] = "Thiếu tên hoặc SĐT"
            out.append(row)
            continue
        row["name"] = parts[0]
        row["phone"] = store.normalize_phone(parts[1])
        if not row["phone"]:
            row["why"] = "SĐT không hợp lệ"
            out.append(row)
            continue
        if len(parts) >= 3:
            t = store.strip_diacritics(parts[2]).upper()
            row["type"] = "DL" if t in ("DL", "ĐL", "DAI LY", "DAILY") else "KH"
        if len(parts) >= 4:
            row["address"] = ", ".join(parts[3:])
        if row["phone"] in seen_phones or store.find_customer_by_phone(row["phone"]):
            row["status"], row["why"] = "dup", "Trùng SĐT — bỏ qua"
        else:
            row["status"], row["why"] = "ok", "Sẽ thêm"
            seen_phones.add(row["phone"])
        out.append(row)
    return out


@app.get("/nhap", response_class=HTMLResponse)
def import_form(request: Request):
    if r := _guard(request):
        return r
    return views.page("Nhập danh bạ", views.import_page(), active="/khach")


@app.post("/nhap", response_class=HTMLResponse)
def import_submit(request: Request, action: str = Form("preview"), raw: str = Form("")):
    if r := _guard(request):
        return r
    preview = _parse_import(raw)
    if action != "confirm":
        return views.page("Nhập danh bạ", views.import_page(preview, raw), active="/khach")
    n = 0
    for p in preview:
        if p["status"] == "ok":
            store.create_customer(p["name"], p["phone"], p["type"], "khac", p["address"])
            n += 1
    return RedirectResponse(f"/khach?q=&loai=", status_code=303)


# ---- báo giá ---------------------------------------------------------------------
@app.get("/bao-gia", response_class=HTMLResponse)
def quotes(request: Request):
    if r := _guard(request):
        return r
    body = views.quotes_page(store.list_quotes("all"), store.quotes_archived(), store.open_quotes_summary())
    return views.page("Báo giá", body, active="/bao-gia")


@app.get("/bao-gia/moi", response_class=HTMLResponse)
def quote_new_form(request: Request, khach: int = 0, q: str = ""):
    if r := _guard(request):
        return r
    if not khach:
        rows = store.list_customers(q=q)
        return views.page("Thêm báo giá",
                          views.pick_customer_page(rows, q, "/bao-gia/moi", "Báo giá cho khách nào?"),
                          active="/bao-gia")
    c = store.get_customer(khach)
    if not c:
        raise HTTPException(status_code=404)
    return views.page("Thêm báo giá", views.quote_form_page(c, store.today_vn()), active="/bao-gia")


@app.post("/bao-gia/moi")
def quote_new(request: Request, customer_id: int = Form(...), product: str = Form(...),
              value_vnd: str = Form(""), description: str = Form(""), sent_date: str = Form("")):
    if r := _guard(request):
        return r
    store.create_quote(customer_id, product, description, _parse_vnd(value_vnd), sent_date)
    return RedirectResponse("/bao-gia", status_code=303)


@app.post("/bao-gia/{quote_id}/da-nhan")
def quote_contacted(request: Request, quote_id: int, next: str = Form("/bao-gia")):
    if r := _guard(request):
        return r
    store.mark_quote_contacted(quote_id)
    return RedirectResponse(_safe_next(next, "/bao-gia"), status_code=303)


@app.post("/bao-gia/{quote_id}/trang-thai")
def quote_status(request: Request, quote_id: int,
                 trang_thai: str = Form(...), ly_do: str = Form("")):
    if r := _guard(request):
        return r
    if trang_thai == "won":
        # An empty báo giá (no hạng mục and no legacy lump value) would chốt into
        # a 0đ zombie order sitting in Tiến độ — block it. quote_finish guards the
        # "Xong" button the same way; this covers the kanban/Chốt path too.
        q = store.get_quote(quote_id)
        if not q:
            raise HTTPException(status_code=404)
        if not store.quote_items_for(quote_id) and not q.get("value_vnd"):
            raise HTTPException(status_code=400,
                                detail="Báo giá chưa có hạng mục — thêm hạng mục trước khi chốt")
        store.set_quote_status(quote_id, "won")
        # Chốt → tự tạo đơn sản xuất (vào tab Đơn hàng) và báo nhóm Zalo tự động
        # qua bot. Ở lại danh sách báo giá — thẻ tự chuyển Đã gửi → Chốt, không
        # nhảy vào chi tiết đơn.
        oid = store.create_order_from_quote(quote_id)
        if oid:
            _new_order_ping(oid)
        return RedirectResponse("/bao-gia", status_code=303)
    if trang_thai == "lost":
        store.set_quote_status(quote_id, "lost", ly_do)
        return RedirectResponse("/bao-gia", status_code=303)
    if trang_thai in ("sent", "chasing"):
        # Kanban drag between open columns, or pulling a card back out of
        # Chốt/Mất (re-opening clears lost_reason; an existing order is kept).
        store.set_quote_status(quote_id, trang_thai)
        return RedirectResponse("/bao-gia", status_code=303)
    raise HTTPException(status_code=400, detail="Trạng thái không hợp lệ")


# ---- báo giá nhiều hạng mục (calculator-backed multi-item quotes) -----------------
# Phụ kiện vocabulary is the priced set shared with the intake wizard and the
# price math — see pricing.PHUKIEN_CATALOG.
_ACCESSORY_LABELS = dict(pricing.PHUKIEN_CATALOG)


def _encode_extras(raw: str) -> list[str]:
    """Free-form 'chi phí khác' textarea → accessories segments. Each non-empty
    line is 'Tên - <số tiền>' or 'Tên x<qty> - <số tiền>' (dots/spaces in the
    amount are ignored) and is encoded as '<tên> x<qty> =<amount>' so pricing
    prices it and it survives a re-save. The name is stripped of the ', ' /
    ' x<digit>' / ' =' delimiters the accessories string relies on."""
    out = []
    for line in (raw or "").splitlines():
        m = re.match(r"^\s*(.+?)(?:\s+x(\d+))?[\s:–-]+([\d.,]+)\s*$", line)
        if not m:
            continue
        name = re.sub(r",|\sx(?=\d)|\s=", " ", m.group(1)).strip()
        qty = int(m.group(2)) if m.group(2) else 1
        amount = int(re.sub(r"\D", "", m.group(3)) or 0)
        if name and amount:
            out.append(f"{name} x{qty} ={amount}")
    return out


def _accessories_from_form(form) -> str:
    """quotes.accessories from a phụ-kiện form: catalog checkboxes ('<label>
    x<qty>') plus free-form 'chi phí khác' lines ('<name> x1 =<amount>')."""
    parts = [f"{label} x{form.get(f'{key}_qty') or 1}"
             for key, label in pricing.PHUKIEN_CATALOG if form.get(f"{key}_chk") == "1"]
    parts += _encode_extras(form.get("extras", ""))
    return ", ".join(parts)


@app.get("/bao-gia/nhieu-hang-muc", response_class=HTMLResponse)
def quote_multi_new_form(request: Request, khach: int = 0, q: str = ""):
    if r := _guard(request):
        return r
    if not khach:
        rows = store.list_customers(q=q)
        return views.page("Báo giá nhiều hạng mục",
                          views.pick_customer_page(rows, q, "/bao-gia/nhieu-hang-muc",
                                                   "Báo giá cho khách nào?", mode="multi"),
                          active="/bao-gia")
    c = store.get_customer(khach)
    if not c:
        raise HTTPException(status_code=404)
    return views.page("Thông tin chung", views.quote_header_form_page(c, store.today_vn()), active="/bao-gia")


@app.post("/bao-gia/nhieu-hang-muc")
async def quote_multi_new(request: Request, customer_id: int = Form(...),
                          deposit: str = Form(""), install_date: str = Form(""), note: str = Form("")):
    if r := _guard(request):
        return r
    form = await request.form()
    accessories = _accessories_from_form(form)
    quote_id = store.create_quote_header(customer_id, accessories, _parse_vnd(deposit), install_date, note)
    return RedirectResponse(f"/bao-gia/{quote_id}", status_code=303)


@app.get("/bao-gia/{quote_id}/hang-muc/moi", response_class=HTMLResponse)
def quote_item_new_form(request: Request, quote_id: int):
    if r := _guard(request):
        return r
    q = store.get_quote(quote_id)
    if not q:
        raise HTTPException(status_code=404)
    if q.get("order_id"):  # locked (đã chốt) — bounce back to the read-only build page
        return RedirectResponse(f"/bao-gia/{quote_id}", status_code=303)
    return views.page("Thêm hạng mục", views.quote_item_form_page(q), active="/bao-gia")


def _price_quote_item(loai_cua: str, cong_nghe: str, mau: str, ngang_mm: int, cao_mm: int,
                      customer_type: str, gia_thu_cong: str, manual_override: bool = False) -> tuple:
    """Table price if one matches, else the submitted manual đơn giá (đ/m² × area).
    Server always owns the price when the table has an authoritative answer — same
    principle as product/qr-menu/app.py's api_order re-pricing. ``manual_override``
    forces the manual đơn giá even when the catalog has a price (giá đặc biệt for
    old customers). Shared by add and edit so both paths price identically."""
    price = pricing.get_price(loai_cua, cong_nghe, mau, customer_type)
    if price is not None and not manual_override:
        per_sqm, flat = pricing.get_surcharges(loai_cua, cong_nghe, pricing.area_m2(ngang_mm, cao_mm))
        return pricing.line_total(price, ngang_mm, cao_mm, per_sqm, flat), False
    thanh_tien = pricing.manual_line_total(_parse_vnd(gia_thu_cong) or 0, ngang_mm, cao_mm)
    if not thanh_tien:
        raise HTTPException(status_code=400, detail="Không có bảng giá cho lựa chọn này — cần nhập giá tay")
    return thanh_tien, True


@app.post("/bao-gia/{quote_id}/hang-muc/moi")
def quote_item_new(request: Request, quote_id: int, loai_cua: str = Form(...),
                   cong_nghe: str = Form(""), mau: str = Form(""),
                   ngang: str = Form(...), cao: str = Form(...),
                   gia_thu_cong: str = Form(""), ghi_chu: str = Form(""),
                   manual: str = Form("")):
    if r := _guard(request):
        return r
    q = store.get_quote(quote_id)
    if not q:
        raise HTTPException(status_code=404)
    _reject_if_ordered(q)
    ngang_mm, cao_mm = _parse_dim(ngang, cao)
    thanh_tien, is_manual = _price_quote_item(loai_cua, cong_nghe, mau, ngang_mm, cao_mm,
                                              q["customer_type"], gia_thu_cong, bool(manual))
    store.add_quote_item(quote_id, loai_cua, cong_nghe, mau, ngang_mm, cao_mm, thanh_tien, is_manual,
                         ghi_chu=ghi_chu)
    return RedirectResponse(f"/bao-gia/{quote_id}", status_code=303)


@app.get("/bao-gia/{quote_id}/hang-muc/{item_id}/sua", response_class=HTMLResponse)
def quote_item_edit_form(request: Request, quote_id: int, item_id: int):
    if r := _guard(request):
        return r
    q = store.get_quote(quote_id)
    item = store.get_quote_item(item_id)
    if not q or not item or item["quote_id"] != quote_id:
        raise HTTPException(status_code=404)
    if q.get("order_id"):  # locked (đã chốt) — bounce back to the read-only build page
        return RedirectResponse(f"/bao-gia/{quote_id}", status_code=303)
    return views.page("Sửa hạng mục", views.quote_item_form_page(q, item), active="/bao-gia")


@app.post("/bao-gia/{quote_id}/hang-muc/{item_id}/sua")
def quote_item_edit(request: Request, quote_id: int, item_id: int, loai_cua: str = Form(...),
                    cong_nghe: str = Form(""), mau: str = Form(""),
                    ngang: str = Form(...), cao: str = Form(...),
                    gia_thu_cong: str = Form(""), ghi_chu: str = Form(""),
                    manual: str = Form("")):
    if r := _guard(request):
        return r
    q = store.get_quote(quote_id)
    item = store.get_quote_item(item_id)
    if not q or not item or item["quote_id"] != quote_id:
        raise HTTPException(status_code=404)
    _reject_if_ordered(q)
    ngang_mm, cao_mm = _parse_dim(ngang, cao)
    thanh_tien, is_manual = _price_quote_item(loai_cua, cong_nghe, mau, ngang_mm, cao_mm,
                                              q["customer_type"], gia_thu_cong, bool(manual))
    store.update_quote_item(item_id, loai_cua, cong_nghe, mau, ngang_mm, cao_mm, thanh_tien, is_manual,
                            ghi_chu=ghi_chu)
    return RedirectResponse(f"/bao-gia/{quote_id}", status_code=303)


@app.post("/bao-gia/{quote_id}/hang-muc/{item_id}/xoa")
def quote_item_delete(request: Request, quote_id: int, item_id: int):
    if r := _guard(request):
        return r
    _reject_if_ordered(store.get_quote(quote_id))
    store.delete_quote_item(item_id)
    return RedirectResponse(f"/bao-gia/{quote_id}", status_code=303)


@app.post("/bao-gia/{quote_id}/hoan-tat")
def quote_finish(request: Request, quote_id: int):
    if r := _guard(request):
        return r
    if not store.quote_items_for(quote_id):
        raise HTTPException(status_code=400, detail="Chưa có hạng mục nào")
    return RedirectResponse("/bao-gia", status_code=303)


@app.post("/bao-gia/{quote_id}/dat-coc")
def quote_set_deposit(request: Request, quote_id: int, deposit: str = Form("")):
    if r := _guard(request):
        return r
    q = store.get_quote(quote_id)
    if not q:
        raise HTTPException(status_code=404)
    _reject_if_ordered(q)
    store.set_quote_deposit(quote_id, _parse_vnd(deposit))
    return RedirectResponse(f"/bao-gia/{quote_id}", status_code=303)


@app.post("/bao-gia/{quote_id}/ghi-chu")
def quote_set_notes(request: Request, quote_id: int, install_date: str = Form(""), note: str = Form("")):
    if r := _guard(request):
        return r
    q = store.get_quote(quote_id)
    if not q:
        raise HTTPException(status_code=404)
    _reject_if_ordered(q)
    store.update_quote_notes(quote_id, install_date, note)
    return RedirectResponse(f"/bao-gia/{quote_id}", status_code=303)


@app.post("/bao-gia/{quote_id}/phu-kien")
async def quote_set_accessories(request: Request, quote_id: int):
    if r := _guard(request):
        return r
    q = store.get_quote(quote_id)
    if not q:
        raise HTTPException(status_code=404)
    _reject_if_ordered(q)
    form = await request.form()
    store.update_quote_accessories(quote_id, _accessories_from_form(form))
    return RedirectResponse(f"/bao-gia/{quote_id}", status_code=303)


@app.get("/bao-gia/{quote_id}/xuat")
def quote_export(request: Request, quote_id: int):
    """Download the báo giá as an .xlsx laid out like the family's Google-Sheet
    quote (door lines → phụ kiện → Tổng cộng / VAT 10% / Tổng tiền)."""
    if r := _guard(request):
        return r
    q = store.get_quote(quote_id)
    if not q:
        raise HTTPException(status_code=404)
    items = store.quote_items_for(quote_id)
    # Phụ kiện count as hạng mục — a job that's only khóa/bình tích điện/chi phí
    # khác still needs a báo giá to send the customer. Block only a truly empty one.
    if not items and not q.get("accessories"):
        raise HTTPException(status_code=400, detail="Chưa có hạng mục nào để xuất")
    customer = store.get_customer(q["customer_id"])
    data, filename = baogia.build_baogia_xlsx(q, customer, items, COMPANY)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/bao-gia/{quote_id}", response_class=HTMLResponse)
def quote_build(request: Request, quote_id: int):
    if r := _guard(request):
        return r
    q = store.get_quote(quote_id)
    if not q:
        raise HTTPException(status_code=404)
    body = views.quote_build_page(q, store.quote_items_for(quote_id))
    return views.page(f"Báo giá {store.bao_gia_so(quote_id, q.get('sent_date'))}", body, active="/bao-gia")


# ---- đơn hàng ---------------------------------------------------------------------
# Orders are only ever created by chốt-ing a báo giá (store.create_order_from_quote,
# triggered from /bao-gia/{id}/trang-thai) — there is no manual "+ Đơn hàng" path.
@app.get("/don-hang", response_class=HTMLResponse)
def orders(request: Request, loai: str = "", ngay: str = ""):
    if r := _guard(request):
        return r
    if loai not in ("cua_cuon", "cua_keo", "nhom_kinh", "khac"):
        loai = ""
    day = ngay if _valid_day(ngay) else ""
    body = views.orders_page(store.orders_active(), store.orders_completed(),
                             store.today_vn(), loai=loai, ngay=day)
    return views.page("Đơn hàng", body, active="/don-hang")


@app.post("/don-hang/{order_id}/xoa")
def order_delete(request: Request, order_id: int):
    """Xóa đơn hàng: purges hạng mục/thanh toán/sửa chữa and marks the
    originating báo giá 'Mất' (out of the active pipeline; drag back to re-open).
    Customer lịch sử chăm sóc (touches) is kept — see store.delete_order."""
    if r := _guard(request):
        return r
    if not store.delete_order(order_id):
        raise HTTPException(status_code=404)
    return RedirectResponse("/don-hang", status_code=303)


@app.get("/don-hang/{order_id}", response_class=HTMLResponse)
def order_detail(request: Request, order_id: int):
    if r := _guard(request):
        return r
    o = store.get_order(order_id)
    if not o:
        raise HTTPException(status_code=404)
    # Hạng mục live on the order itself (snapshotted from the báo giá on chốt);
    # quote items are only a fallback for orders that predate the backfill.
    items = store.order_items_for(order_id)
    if not items and o.get("quote_id"):
        items = store.quote_items_for(o["quote_id"])
    body = views.order_detail_page(o, store.service_calls_for_order(order_id), store.today_vn(),
                                   items=items, payments=store.order_payments_for(order_id))
    return views.page(f"Đơn hàng #{order_id}", body, active="/don-hang")


@app.post("/don-hang/{order_id}/gap")
def order_toggle_urgent(request: Request, order_id: int, urgent: str = Form("1")):
    if r := _guard(request):
        return r
    if not store.get_order(order_id):
        raise HTTPException(status_code=404)
    store.set_order_urgent(order_id, urgent == "1")
    return RedirectResponse(f"/don-hang/{order_id}", status_code=303)


@app.post("/don-hang/{order_id}/giai-doan")
def order_set_stage(request: Request, order_id: int, stage: str = Form(...),
                    next: str = Form("")):
    if r := _guard(request):
        return r
    if not store.get_order(order_id):
        raise HTTPException(status_code=404)
    try:
        store.set_order_stage(order_id, stage)
    except ValueError:
        raise HTTPException(status_code=400, detail="Giai đoạn không hợp lệ")
    return RedirectResponse(_safe_next(next, f"/don-hang/{order_id}"), status_code=303)


@app.post("/don-hang/{order_id}/ngay-lap")
def order_set_install(request: Request, order_id: int, install_date: str = Form(...)):
    if r := _guard(request):
        return r
    store.set_order_install(order_id, install_date)
    return RedirectResponse(f"/don-hang/{order_id}", status_code=303)


@app.post("/don-hang/{order_id}/ghi-chu")
def order_set_note(request: Request, order_id: int, note: str = Form("")):
    if r := _guard(request):
        return r
    if not store.get_order(order_id):
        raise HTTPException(status_code=404)
    store.set_order_note(order_id, note)
    return RedirectResponse(f"/don-hang/{order_id}", status_code=303)


@app.post("/don-hang/{order_id}/sua-chua")
def order_service(request: Request, order_id: int,
                  issue: str = Form(...), resolution: str = Form("")):
    if r := _guard(request):
        return r
    store.add_service_call(order_id, issue, resolution)
    return RedirectResponse(f"/don-hang/{order_id}", status_code=303)


@app.post("/don-hang/{order_id}/thanh-toan")
def order_add_payment(request: Request, order_id: int, amount_vnd: str = Form(...),
                      kind: str = Form("thanh_toan"), method: str = Form(""),
                      pay_date: str = Form(""), note: str = Form(""), next: str = Form("")):
    """Ghi cọc/thanh toán cho đơn. 'Đã thu đủ' on Hôm nay posts the full balance
    here; the order page posts partial amounts."""
    if r := _guard(request):
        return r
    if not store.get_order(order_id):
        raise HTTPException(status_code=404)
    amount = _parse_vnd(amount_vnd)
    if not amount:
        raise HTTPException(status_code=400, detail="Số tiền không hợp lệ")
    store.add_order_payment(order_id, kind if kind in ("coc", "thanh_toan") else "thanh_toan",
                            amount, pay_date, method, note)
    return RedirectResponse(_safe_next(next, f"/don-hang/{order_id}"), status_code=303)


@app.post("/don-hang/{order_id}/thu-du")
def order_settle_full(request: Request, order_id: int, next: str = Form(""),
                      method: str = Form(""), note: str = Form("")):
    """Mark a KH order Đã thanh toán — record a payment for the full remaining
    balance (computed server-side to avoid stale amounts). Powers the 'Đã thanh
    toán' button in the Công nợ / Nợ tab, now carrying hình thức + ghi chú."""
    if r := _guard(request):
        return r
    o = store.get_order(order_id)
    if not o:
        raise HTTPException(status_code=404)
    bal = o.get("balance_vnd", 0)
    if bal > 0:
        store.add_order_payment(order_id, "thanh_toan", bal, method=method, note=note)
    return RedirectResponse(_safe_next(next, "/cong-no"), status_code=303)


@app.post("/don-hang/{order_id}/thanh-toan/{pay_id}/xoa")
def order_payment_delete(request: Request, order_id: int, pay_id: int, next: str = Form("")):
    """Reopen an order marked paid by mistake — remove one cọc/thanh toán entry."""
    if r := _guard(request):
        return r
    store.delete_order_payment(pay_id)
    return RedirectResponse(_safe_next(next, f"/don-hang/{order_id}"), status_code=303)


# ---- hạng mục hóa đơn (invoice line items) ------------------------------------
@app.post("/don-hang/{order_id}/hang-muc/moi")
def order_item_new(request: Request, order_id: int, description: str = Form(...),
                   so_luong: int = Form(1), thanh_tien: str = Form(...),
                   don_gia: str = Form("")):
    if r := _guard(request):
        return r
    if not store.get_order(order_id):
        raise HTTPException(status_code=404)
    total = _parse_vnd(thanh_tien)
    if total is None:
        raise HTTPException(status_code=400, detail="Thành tiền không hợp lệ")
    store.add_order_item(order_id, description=description, thanh_tien=total,
                         so_luong=max(so_luong, 1), don_gia=_parse_vnd(don_gia))
    return RedirectResponse(f"/don-hang/{order_id}", status_code=303)


@app.post("/don-hang/{order_id}/hang-muc/{item_id}/sua")
def order_item_edit(request: Request, order_id: int, item_id: int,
                    description: str = Form(""), so_luong: int = Form(1),
                    thanh_tien: str = Form(...), don_gia: str = Form("")):
    if r := _guard(request):
        return r
    total = _parse_vnd(thanh_tien)
    if total is None:
        raise HTTPException(status_code=400, detail="Thành tiền không hợp lệ")
    if not store.update_order_item(item_id, description, max(so_luong, 1), total,
                                   _parse_vnd(don_gia)):
        raise HTTPException(status_code=404)
    return RedirectResponse(f"/don-hang/{order_id}", status_code=303)


@app.post("/don-hang/{order_id}/hang-muc/{item_id}/xoa")
def order_item_delete(request: Request, order_id: int, item_id: int):
    if r := _guard(request):
        return r
    store.delete_order_item(item_id)
    return RedirectResponse(f"/don-hang/{order_id}", status_code=303)


@app.get("/don-hang/{order_id}/hoa-don", response_class=HTMLResponse)
def order_invoice(request: Request, order_id: int):
    if r := _guard(request):
        return r
    o = store.get_order(order_id)
    if not o:
        raise HTTPException(status_code=404)
    items = store.order_items_for(order_id)
    body = views.invoice_page(o, items, COMPANY, store.today_vn())
    return views.page(f"Hóa đơn #{order_id}", body, show_nav=False)


def _order_flag_route(order_id: int, flag: str, next: str, request: Request):
    if r := _guard(request):
        return r
    store.set_order_flag(order_id, flag)
    return RedirectResponse(_safe_next(next, f"/don-hang/{order_id}"), status_code=303)


@app.post("/don-hang/{order_id}/da-bao-tri")
def order_checkin_done(request: Request, order_id: int, next: str = Form("")):
    return _order_flag_route(order_id, "checkin_done_at", next, request)


@app.post("/don-hang/{order_id}/da-nhan-bh")
def order_expiry_notified(request: Request, order_id: int, next: str = Form("")):
    return _order_flag_route(order_id, "expiry_notified_at", next, request)


@app.post("/don-hang/{order_id}/da-xin-danh-gia")
def order_review_requested(request: Request, order_id: int, next: str = Form("")):
    return _order_flag_route(order_id, "review_requested_at", next, request)


# ---- one-tap Zalo DM from a Hôm nay care card (bot must be friends w/ number) --
@app.post("/bao-gia/{quote_id}/gui-zalo-bot")
def quote_send_zalo_bot(request: Request, quote_id: int, text: str = Form(...), next: str = Form("")):
    """DM the follow-up draft to the customer via the bot, then close the loop
    (Đã nhắn) exactly as the manual button would. Loud 502 on failure so the
    card stays and the family forwards the draft from the group instead."""
    if r := _guard(request):
        return r
    q = store.get_quote(quote_id)
    if not q:
        raise HTTPException(status_code=404)
    ok, err = _bot_send_dm(q.get("zalo_phone") or q.get("phone"), text)
    if not ok:
        raise HTTPException(status_code=502, detail=f"Gửi Zalo thất bại: {err}. Chuyển tiếp tay trong nhóm nhé.")
    store.mark_quote_contacted(quote_id)
    return RedirectResponse(_safe_next(next, "/"), status_code=303)


@app.post("/don-hang/{order_id}/gui-zalo-bot")
def order_review_send_zalo_bot(request: Request, order_id: int, text: str = Form(...), next: str = Form("")):
    """DM the review-ask draft to the customer via the bot, then mark review
    requested. Loud 502 on failure (same fall-back contract as above)."""
    if r := _guard(request):
        return r
    o = store.get_order(order_id)
    if not o:
        raise HTTPException(status_code=404)
    ok, err = _bot_send_dm(o.get("zalo_phone") or o.get("phone"), text)
    if not ok:
        raise HTTPException(status_code=502, detail=f"Gửi Zalo thất bại: {err}. Chuyển tiếp tay trong nhóm nhé.")
    store.set_order_flag(order_id, "review_requested_at")
    return RedirectResponse(_safe_next(next, f"/don-hang/{order_id}"), status_code=303)


# ---- công nợ ------------------------------------------------------------------------
@app.get("/cong-no", response_class=HTMLResponse)
def debts(request: Request, loc: str = "tat-ca", ngay: str = ""):
    if r := _guard(request):
        return r
    if loc not in ("tat-ca", "kh", "dl", "da-thu"):
        loc = "tat-ca"
    today = store.today_vn()
    if loc == "da-thu":
        day = ngay if _valid_day(ngay) else ""
        body = views.debts_page([], [], loc, today, settlements=store.settlements(day), day=day)
    else:
        body = views.debts_page(store.dealer_balances(), store.customer_debts(), loc, today)
    return views.page("Công nợ", body, active="/cong-no")


@app.get("/cong-no/{customer_id}", response_class=HTMLResponse)
def ledger(request: Request, customer_id: int):
    if r := _guard(request):
        return r
    c = store.get_customer(customer_id)
    if not c or c["type"] != "DL":
        raise HTTPException(status_code=404)
    body = views.ledger_page(c, store.ledger_for_dealer(customer_id),
                             store.dealer_balance(customer_id))
    return views.page(f"Sổ nợ — {c['name']}", body, active="/cong-no")


@app.get("/cong-no/{customer_id}/them", response_class=HTMLResponse)
def debt_entry_form(request: Request, customer_id: int, loai: str = "charge",
                    don: str = "", tien: str = ""):
    if r := _guard(request):
        return r
    c = store.get_customer(customer_id)
    if not c or c["type"] != "DL":
        raise HTTPException(status_code=404)
    body = views.debt_entry_form_page(c, loai, store.today_vn(), don, tien)
    return views.page("Ghi sổ nợ", body, active="/cong-no")


@app.post("/cong-no/{customer_id}/them")
def debt_entry_add(request: Request, customer_id: int, entry_type: str = Form(...),
                   amount_vnd: str = Form(...), entry_date: str = Form(""),
                   note: str = Form(""), order_id: int = Form(0)):
    if r := _guard(request):
        return r
    c = store.get_customer(customer_id)
    if not c or c["type"] != "DL":
        raise HTTPException(status_code=400, detail="Chỉ ghi nợ cho đại lý")
    amount = _parse_vnd(amount_vnd)
    if not amount or entry_type not in ("charge", "payment"):
        raise HTTPException(status_code=400, detail="Số tiền hoặc loại không hợp lệ")
    store.add_debt_entry(customer_id, entry_type, amount, entry_date, note, order_id or None)
    return RedirectResponse(f"/cong-no/{customer_id}", status_code=303)


@app.post("/cong-no/{customer_id}/thanh-toan-du")
def debt_settle_full(request: Request, customer_id: int, next: str = Form(""),
                     method: str = Form(""), note: str = Form("")):
    """One-click 'Đã thanh toán' for a đại lý — records a payment for the full
    outstanding balance (server-computed), mirroring the KH order_settle_full.
    Captures hình thức (tiền mặt / chuyển khoản) + ghi chú from the Nợ-tab form."""
    if r := _guard(request):
        return r
    c = store.get_customer(customer_id)
    if not c or c["type"] != "DL":
        raise HTTPException(status_code=404)
    store.settle_dealer(customer_id, method, note)
    return RedirectResponse(_safe_next(next, f"/cong-no/{customer_id}"), status_code=303)


@app.post("/cong-no/{customer_id}/xoa/{entry_id}")
def debt_entry_delete(request: Request, customer_id: int, entry_id: int):
    """Xóa một dòng sổ nợ — remove a mis-entered charge/payment. Scoped to the
    dealer so an entry_id can't be deleted from another account's ledger."""
    if r := _guard(request):
        return r
    c = store.get_customer(customer_id)
    if not c or c["type"] != "DL":
        raise HTTPException(status_code=404)
    store.delete_debt_entry(entry_id, customer_id)
    return RedirectResponse(f"/cong-no/{customer_id}", status_code=303)


# ---- báo cáo ------------------------------------------------------------------------
@app.get("/bao-cao", response_class=HTMLResponse)
def reports(request: Request, che_do: str = "ngay", ngay: str = "", thang: str = ""):
    """Daily by default (end-of-day reconciliation — quan sát mỗi ngày); the
    Theo tháng toggle keeps the monthly rollup."""
    if r := _guard(request):
        return r
    if che_do == "thang":
        month = thang if _valid_month(thang) else store.today_vn()[:7]
        report = store.monthly_report(month)
    else:
        day = ngay if _valid_day(ngay) else store.today_vn()
        report = store.daily_report(day)
    return views.page("Báo cáo", views.reports_page(report), active="/bao-cao")


# ---- Zalo OA -----------------------------------------------------------------
@app.get("/zalo", response_class=HTMLResponse)
def zalo_admin(request: Request):
    if r := _guard(request):
        return r
    body = views.zalo_admin_page(
        connected=zalo_client.is_connected(),
        configured=bool(ZALO_APP_ID and ZALO_APP_SECRET),
        unlinked=store.recent_unlinked_zalo_events(),
        bot_configured=bool(BOT_URL and BOT_TOKEN),
        bot_health=_bot_health(),
    )
    return views.page("Zalo OA", body, active="/khach")


@app.get("/zalo/oauth/start")
def zalo_oauth_start(request: Request):
    if r := _guard(request):
        return r
    if not (ZALO_APP_ID and ZALO_APP_SECRET):
        raise HTTPException(status_code=400, detail="Chưa cấu hình ZALO_APP_ID/ZALO_APP_SECRET")
    state = secrets.token_urlsafe(16)
    resp = RedirectResponse(zalo_client.auth_url(ZALO_APP_ID, ZALO_REDIRECT_URI, state))
    resp.set_cookie("zalo_oauth_state", state, httponly=True, samesite="lax",
                    max_age=600, secure=COOKIE_SECURE)
    return resp


@app.get("/zalo/oauth/callback")
def zalo_oauth_callback(request: Request, code: str = "", state: str = ""):
    if r := _guard(request):
        return r
    expected_state = request.cookies.get("zalo_oauth_state", "")
    if not code or not state or not secrets.compare_digest(state, expected_state):
        raise HTTPException(status_code=400, detail="Liên kết Zalo thất bại (state không khớp) — thử lại từ /zalo")
    zalo_client.exchange_code(ZALO_APP_ID, ZALO_APP_SECRET, code, ZALO_REDIRECT_URI)
    resp = RedirectResponse("/zalo", status_code=303)
    resp.delete_cookie("zalo_oauth_state")
    return resp


@app.post("/zalo/webhook")
async def zalo_webhook(request: Request):
    """No cookie auth here — Zalo posts server-to-server. See ZALO_WEBHOOK_TOKEN
    check below; this is a shared-secret query param, not Zalo's own payload
    signature (verify the current signing scheme in Zalo's docs if that matters
    more than the query-param check for this use case)."""
    expected = os.environ.get("ZALO_WEBHOOK_TOKEN", "")
    if not expected or request.query_params.get("token") != expected:
        raise HTTPException(status_code=403)
    payload = await request.json()
    event_name = payload.get("event_name", "")
    zalo_user_id = (payload.get("sender") or payload.get("follower") or {}).get("id", "")
    text = (payload.get("message") or {}).get("text", "")
    if zalo_user_id:
        store.log_zalo_event(zalo_user_id, event_name, text)
    return {"status": "ok"}


@app.post("/khach/{customer_id}/lien-ket-zalo")
def customer_link_zalo(request: Request, customer_id: int, zalo_user_id: str = Form(...)):
    if r := _guard(request):
        return r
    if not store.get_customer(customer_id):
        raise HTTPException(status_code=404)
    store.set_customer_zalo_user_id(customer_id, zalo_user_id)
    return RedirectResponse(f"/khach/{customer_id}", status_code=303)


@app.post("/khach/{customer_id}/huy-lien-ket-zalo")
def customer_unlink_zalo(request: Request, customer_id: int):
    if r := _guard(request):
        return r
    store.unlink_customer_zalo(customer_id)
    return RedirectResponse(f"/khach/{customer_id}", status_code=303)


@app.post("/khach/{customer_id}/gop")
def customer_merge(request: Request, customer_id: int, dup_id: int = Form(...)):
    """Fold a same-phone duplicate (``dup_id``) into this customer, then land
    back on this customer's page. Powers the "⚠️ Trùng SĐT" merge button."""
    if r := _guard(request):
        return r
    if not store.get_customer(customer_id) or not store.get_customer(dup_id):
        raise HTTPException(status_code=404)
    store.merge_customers(customer_id, dup_id)
    return RedirectResponse(f"/khach/{customer_id}", status_code=303)


@app.post("/khach/{customer_id}/gui-zalo")
def customer_send_zalo(request: Request, customer_id: int, text: str = Form(...)):
    if r := _guard(request):
        return r
    c = store.get_customer(customer_id)
    if not c:
        raise HTTPException(status_code=404)
    if not c.get("zalo_user_id"):
        raise HTTPException(status_code=400, detail="Khách chưa gắn Zalo — vào /zalo để gắn trước")
    token = zalo_client.get_valid_access_token(ZALO_APP_ID, ZALO_APP_SECRET)
    if not token:
        raise HTTPException(status_code=400, detail="Chưa kết nối Zalo OA — vào /zalo để kết nối")
    try:
        zalo_client.send_text(token, c["zalo_user_id"], text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gửi Zalo thất bại: {e}")
    store.add_touch(customer_id, "zalo_api", text)
    return RedirectResponse(f"/khach/{customer_id}", status_code=303)


# ---- Zalo group bot (zca-js sidecar) ------------------------------------------
# The bot lives in its own container and talks to the CRM over a tiny internal
# HTTP API, authenticated with a shared secret (X-Bot-Token). A dead/unconfigured
# bot must never break a CRM request, so every outbound call is best-effort.
_XONG_RE = re.compile(r"(?i)^\s*xong\s+(\d+)\s*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _bot_send(text: str) -> None:
    if not (BOT_URL and BOT_TOKEN and text):
        return
    try:
        requests.post(f"{BOT_URL}/send", json={"text": text},
                      headers={"X-Bot-Token": BOT_TOKEN}, timeout=3)
    except Exception:
        pass


def _bot_send_dm(phone: str, text: str) -> tuple[bool, str]:
    """One-tap customer DM via the personal-account bot (zca-js findUser +
    sendMessage). Requires the bot configured, logged in, and already FRIENDS
    with the number (Hughie sends the friend request first). Returns
    (ok, error_vi) — fails loudly so the family falls back to forwarding the
    draft from the group instead of a silent no-op."""
    if not (BOT_URL and BOT_TOKEN):
        return False, "Bot chưa cấu hình"
    if not phone:
        return False, "Khách chưa có số Zalo"
    try:
        r = requests.post(f"{BOT_URL}/send-dm", json={"phone": phone, "text": text},
                          headers={"X-Bot-Token": BOT_TOKEN}, timeout=10)
    except Exception as e:
        return False, f"Không gọi được bot ({e})"
    if r.status_code == 200:
        return True, ""
    try:
        detail = r.json().get("error", "")
    except Exception:
        detail = ""
    return False, detail or f"Bot lỗi {r.status_code}"


def _bot_health() -> dict | None:
    if not BOT_URL:
        return None
    try:
        r = requests.get(f"{BOT_URL}/health", timeout=1.5)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _job_desc(o: dict) -> str:
    return o.get("description") or views.PRODUCT_LABELS.get(o["product"], "")


# Zalo group messages are plain text — no bold, no colour. Emoji prefixes and a
# rule between jobs are the only tools for making a field scannable, so each
# field the xưởng needs gets its own glyph and its own line.
_DIGEST_RULE = "━━━━━━━━━━━━━━━━"


def _digest_when(o: dict, today: str) -> str:
    """Urgency line — the first thing the xưởng should read about a job."""
    inst = o.get("install_date")
    bits = ["⚡ GẤP"] if o.get("urgent") else []
    if inst and inst < today:
        bits.append(f"🔴 QUÁ HẸN {views.fmt_date(inst)}")
    elif inst == today:
        bits.append("⏰ LẮP HÔM NAY")
    elif inst:
        bits.append(f"📅 Lắp {views.fmt_date(inst)}")
    else:
        bits.append("📅 Chưa hẹn ngày lắp")
    return "  ".join(bits)


def _digest_items(o: dict) -> list:
    """Hạng mục lines — what to actually make. Doors carry kích thước + màu sắc
    on their own lines; phụ kiện/generic lines still get a line each, so a
    phụ-kiện-only đơn hàng never renders as a job with nothing in it. Orders
    with no snapshot rows at all fall back to the order's own description.
    Never prints a price — the group stays VND-free."""
    lines = []
    for i in store.order_items_for(o["id"]):
        sl = i.get("so_luong") or 1
        qty = f" ×{sl}" if sl > 1 else ""
        if i.get("ngang_mm") and i.get("cao_mm"):
            lines.append(f"🚪 {views._door_desc(i)}{qty}")
            lines.append(f"     ↔ {i['ngang_mm']} × {i['cao_mm']}mm")
            if i.get("mau_sac"):
                lines.append(f"     🎨 Màu: {i['mau_sac']}")
        else:
            lines.append(f"🔧 {views._door_desc(i)}{qty}")
        if i.get("ghi_chu"):
            lines.append(f"     ✏️ {i['ghi_chu']}")
    return lines or [f"🚪 {_job_desc(o)}"]


def _digest_text(today: str) -> str:
    """Numbered chờ-sản-xuất list for the Zalo group. One visually separated
    block per job carrying everything the xưởng needs to cut metal — kích
    thước, màu sắc, ghi chú — so nobody has to open the CRM to start work.
    Every call re-saves the numbering (store.save_digest) so "xong <N>" always
    resolves against the freshest message sent to the group."""
    rows = store.orders_for_digest(today)
    store.save_digest(today, [o["id"] for o in rows])
    if not rows:
        return ""
    lines = [f"🔨 CHỜ SẢN XUẤT · {views.fmt_date(today)}",
             f"{len(rows)} đơn cần làm"]
    for idx, o in enumerate(rows, 1):
        lines.append(_DIGEST_RULE)
        lines.append(f"【{idx}】 {o['customer_name']}")
        lines.append(_digest_when(o, today))
        lines.extend(_digest_items(o))
        if o.get("address"):
            lines.append(f"📍 {o['address']}")
        if o.get("phone"):
            lines.append(f"📞 {o['phone']}")
        if o.get("note"):
            lines.append(f"📝 {o['note']}")
    lines.append(_DIGEST_RULE)
    lines.append("👉 Sản xuất xong nhắn: xong <số>")
    return "\n".join(lines)


def _care_lists(today: str) -> tuple[list, list]:
    """Sales-side follow-ups: báo giá past the chase window + KH installs
    awaiting a review ask. Same store queries as the Hôm nay page, so chat
    and web can never disagree."""
    return store.quotes_to_chase(today), store.orders_review_due(today)


def _care_summary_text(chase: list, reviews: list, hint: bool) -> str:
    """💬 CHĂM SÓC KHÁCH section — names/product/days only, never VND."""
    if not (chase or reviews):
        return ""
    lines = ["💬 CHĂM SÓC KHÁCH"]
    if chase:
        lines.append(f"Nhắc báo giá ({len(chase)}):")
        for q in chase:
            lan = " — nhắc lần 2" if q["nudge_level"] == 2 else ""
            phone = q["zalo_phone"] or q["phone"] or ""
            lines.append(f"• {q['customer_name']} — {_job_desc(q)} — gửi {q['days_sent']} ngày{lan}"
                         + (f" — {phone}" if phone else ""))
    if reviews:
        lines.append(f"Xin đánh giá ({len(reviews)}):")
        for o in reviews:
            phone = o["zalo_phone"] or o["phone"] or ""
            lines.append(f"• {o['customer_name']} — {_job_desc(o)} — lắp {views.fmt_date(o['install_date'])}"
                         + (f" — {phone}" if phone else ""))
    if hint:
        lines.append("Lấy tin nhắn mẫu, gõ: nhac")
    return "\n".join(lines)


def _care_replies(today: str) -> list:
    """'nhac' command: summary first, then one pure-draft message per khách —
    no headers, so the family can long-press → chuyển tiếp a draft straight
    into the customer chat without editing."""
    chase, reviews = _care_lists(today)
    if not (chase or reviews):
        return ["Không có khách nào cần nhắc hôm nay 🎉"]
    summary = _care_summary_text(chase, reviews, hint=False)
    replies = [summary + "\nGiữ từng tin bên dưới để chuyển tiếp cho khách:"]
    for q in chase:
        tpl = "quote_followup_2" if q["nudge_level"] == 2 else "quote_followup_1"
        replies.append(views.render(tpl, ten=q["customer_name"],
                                    san_pham=views.PRODUCT_LABELS.get(q["product"], "sản phẩm")))
    for o in reviews:
        replies.append(views.render("review_request", ten=o["customer_name"],
                                    san_pham=views.PRODUCT_LABELS.get(o["product"], "sản phẩm")))
    return replies


def _new_order_ping(order_id: int) -> None:
    """Instant group heads-up the moment a báo giá is chốt, so the xưởng sees the
    new job without waiting for the morning digest. Every hạng mục goes in, not
    just cửa — a phụ-kiện-only job (khóa, bình tích điện) and a legacy lump quote
    are ordinary work, and filtering on kích thước made both chốt in silence."""
    o = store.get_order(order_id)
    if not o:
        return
    items = store.order_items_for(order_id)
    if not items:
        return
    _bot_send(views.production_message_no_price(o, items))


def _check_bot_token(request: Request) -> None:
    token = request.headers.get("x-bot-token", "")
    if not BOT_TOKEN or not secrets.compare_digest(token, BOT_TOKEN):
        raise HTTPException(status_code=403)


@app.post("/bot/inbound")
async def bot_inbound(request: Request):
    """Zalo group message, forwarded by the bot sidecar. No cookie auth —
    server-to-server, same shared-secret pattern as /zalo/webhook."""
    _check_bot_token(request)
    payload = await request.json()
    text = (payload.get("text") or "").strip()
    name = payload.get("name") or ""
    today = store.today_vn()

    m = _XONG_RE.match(text)
    if m:
        order_id = store.get_digest_order(today, int(m.group(1)))
        o = store.get_order(order_id) if order_id else None
        if not o:
            return {"reply": f"Không thấy số {m.group(1)} trong bảng hôm nay. Gõ: viec để xem bảng mới."}
        # "xong <N>" closes the sản xuất step only — đã lắp đặt/đã giao stays a
        # web-CRM action, so a stale digest number can never skip a stage.
        if o["stage"] != "cho_san_xuat":
            label = views.STAGE_LABELS.get(o["stage"], o["stage"])
            return {"reply": f"{o['customer_name']} — đã xong rồi ({label})."}
        store.set_order_stage(order_id, "dang_san_xuat")
        who = f" ({name} báo)" if name else ""
        return {"reply": f"✅ {o['customer_name']} — {_job_desc(o)}: SẢN XUẤT XONG{who}"}

    if text.lower() in ("viec", "việc"):
        return {"reply": _digest_text(today) or "Không có cửa nào chờ sản xuất 🎉"}

    if text.lower() in ("nhac", "nhắc"):
        replies = _care_replies(today)
        if len(replies) == 1:
            return {"reply": replies[0]}
        return {"replies": replies}

    if text.lower().startswith("xong"):
        return {"reply": "Gõ: xong <số> (số trong bảng chờ sản xuất)"}

    return {}


@app.get("/bot/digest")
def bot_digest_pull(request: Request):
    """Pulled once a day by the bot sidecar's morning schedule (DIGEST_HOUR/
    DIGEST_MINUTE) — chờ-sản-xuất work list + chăm sóc khách summary (quote
    nudges / review asks due). The work list carries kích thước/màu/ghi chú per
    hạng mục, so the xưởng needs nothing else in the morning message."""
    _check_bot_token(request)
    today = store.today_vn()
    chase, reviews = _care_lists(today)
    parts = [t for t in (_digest_text(today),
                         _care_summary_text(chase, reviews, hint=True)) if t]
    return {"text": "\n\n".join(parts)}


@app.get("/api/thu")
def api_thu(request: Request, tu: str = "", den: str = ""):
    """Money-in feed for Sổ Thu Chi (server-to-server, X-Thuchi-Token). Read-only:
    the Thu app shows these rows badged 'CRM' and never edits them — công nợ
    corrections stay a CRM action, and this is fetched live so they show up at once."""
    token = request.headers.get("x-thuchi-token", "")
    if not THUCHI_TOKEN or not secrets.compare_digest(token, THUCHI_TOKEN):
        raise HTTPException(status_code=403)
    if not (_DATE_RE.match(tu) and _DATE_RE.match(den)):
        raise HTTPException(status_code=400, detail="tu/den phải là YYYY-MM-DD")
    return {"thu": store.thu_between(tu, den)}


@app.get("/zalo/bot/qr")
def zalo_bot_qr(request: Request):
    if r := _guard(request):
        return r
    if not BOT_URL:
        raise HTTPException(status_code=404)
    try:
        resp = requests.get(f"{BOT_URL}/qr", timeout=3)
        resp.raise_for_status()
    except Exception:
        raise HTTPException(status_code=404)
    return Response(content=resp.content, media_type="image/png")


@app.post("/zalo/bot/gui")
def zalo_bot_send_digest(request: Request):
    if r := _guard(request):
        return r
    _bot_send(_digest_text(store.today_vn()))
    return RedirectResponse("/zalo", status_code=303)
