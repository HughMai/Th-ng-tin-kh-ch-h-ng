"""Server-rendered HTML for the HTP CRM (STL dashboard pattern: inline HTML
strings, no template engine). 100% Vietnamese, phone-first: single column,
17px base, >=48px touch targets, fixed bottom nav, one-tap Zalo/gọi actions.
"""
import hashlib
import html
import json
import re
from collections import Counter
from itertools import groupby
from pathlib import Path

from templates_vi import (
    LOST_REASON_LABELS,
    PRODUCT_LABELS,
    RETIRED_STAGE_LABELS,
    SOURCE_LABELS,
    STAGE_LABELS,
    render,
    zalo_link,
)
import pricing
from pricing import DOOR_CONFIG, PHUKIEN_CATALOG, _PRICES_DL, _PRICES_KH
from store import bao_gia_so


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


# ---- asset fingerprint ---------------------------------------------------------
# app.css/app.js live at stable filenames, so a browser that cached them (HTTP
# max-age) or a service worker that precached them keeps serving the old bytes
# after a deploy — the family's phones rendered new board markup against stale
# pre-board CSS. Bumping the SW's CACHE name alone can't fix that: the new worker
# re-fetches /static/app.css and the browser answers from its own still-fresh HTTP
# cache, so the new cache gets repopulated with the old file.
#
# Fingerprinting the URL is what actually breaks the tie: new bytes => new URL =>
# guaranteed miss in both caches. Computed once at import from the file contents,
# so a deploy is the only thing that can change it and nobody has to remember to
# bump a version by hand.
def _asset_fingerprint() -> str:
    h = hashlib.sha256()
    static = Path(__file__).parent / "static"
    for name in ("app.css", "app.js"):
        try:
            h.update((static / name).read_bytes())
        except OSError:  # missing file shouldn't take the app down
            h.update(name.encode())
    return h.hexdigest()[:10]


ASSET_V = _asset_fingerprint()
CSS_URL = f"/static/app.css?v={ASSET_V}"
JS_URL = f"/static/app.js?v={ASSET_V}"


# Inline SVG favicon (navy "H" monogram, brand #0f4c81) served as a data URI so
# browsers stop requesting /favicon.ico — which has no route/file and 404s on
# every page load — and the tab shows a brand icon. No extra file or HTTP request.
FAVICON = (
    "<link rel=\"icon\" href=\"data:image/svg+xml,"
    "%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2032%2032'%3E"
    "%3Crect%20width='32'%20height='32'%20rx='6'%20fill='%230f4c81'/%3E"
    "%3Ctext%20x='16'%20y='23'%20font-family='Arial,sans-serif'%20font-size='18'"
    "%20font-weight='bold'%20fill='%23fff'%20text-anchor='middle'%3EH%3C/text%3E"
    "%3C/svg%3E\">"
)


# PWA plumbing shared by every page: links the manifest, sets the theme/status-bar
# colour, points iOS at the home-screen icon, and registers the service worker so
# the CRM installs as a standalone app on iOS/Android/desktop. Injected right after
# FAVICON in each <head>.
PWA_HEAD = (
    '<link rel="manifest" href="/manifest.webmanifest">'
    '<meta name="theme-color" content="#0f4c81">'
    '<meta name="mobile-web-app-capable" content="yes">'
    '<meta name="apple-mobile-web-app-capable" content="yes">'
    '<meta name="apple-mobile-web-app-status-bar-style" content="default">'
    '<meta name="apple-mobile-web-app-title" content="HTP CRM">'
    '<link rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png">'
    "<script>if('serviceWorker' in navigator){"
    "addEventListener('load',function(){navigator.serviceWorker.register('/sw.js')})}</script>"
)


def fmt_vnd(n) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}".replace(",", ".") + "đ"


def fmt_vnd_short(n) -> str:
    """340_000_000 -> '340tr'; 1_200_000_000 -> '1,2 tỷ' (pipeline header)."""
    if not n:
        return "0đ"
    n = int(n)
    if n >= 1_000_000_000:
        v = n / 1_000_000_000
        s = f"{v:.1f}".rstrip("0").rstrip(".").replace(".", ",")
        return f"{s} tỷ"
    if n >= 1_000_000:
        return f"{round(n / 1_000_000)}tr"
    return fmt_vnd(n)


def fmt_date(d) -> str:
    """'2026-07-12' -> '12/07/2026'."""
    if not d:
        return "—"
    p = str(d)[:10].split("-")
    return f"{p[2]}/{p[1]}/{p[0]}" if len(p) == 3 else esc(d)


_JS = """
function fmtMoney(inp){
  // Keep the field as plain digits WHILE typing — do NOT re-insert grouping dots
  // on every keystroke. Rewriting .value mid-keystroke makes phone keyboards
  // (GBoard) re-commit the digit just typed, which multiplied "3000000" into
  // "3333000000" on both the đặt-cọc and phụ-kiện price fields. Grouping into
  // 3.000.000 is applied once on blur instead — when the keyboard is dismissing,
  // so there is nothing left to re-commit.
  var d = inp.value.replace(/\\D/g, '');
  if (d !== inp.value) inp.value = d;
  if (!inp._grp){
    inp._grp = true;
    inp.addEventListener('blur', function(){
      var v = inp.value.replace(/\\D/g, '');
      inp.value = v ? v.replace(/\\B(?=(\\d{3})+(?!\\d))/g, '.') : '';
    });
  }
}
function logGoi(id){ if(navigator.sendBeacon) navigator.sendBeacon('/khach/'+id+'/goi'); }
"""

_TABS = [
    ("/", "Hôm nay"),
    ("/bao-gia", "Báo giá"),
    ("/khach", "Khách"),
    ("/don-hang", "Đơn hàng"),
    ("/cong-no", "Công nợ"),
    ("/bao-cao", "Báo cáo"),
]


def _sidebar_html(active: str) -> str:
    """Desktop-only GHL-style nav rail (hidden <900px via CSS). Reads the same
    _TABS data as the bottom nav below, without touching the bottom nav's own
    tested rendering loop."""
    links = "".join(
        f'<a href="{href}" class="{"on" if href == active else ""}">{label}</a>'
        for href, label in _TABS
    )
    return f'<nav class="sidebar"><div class="brand">HTP</div>{links}</nav>'


def page(title: str, body: str, active: str = "", show_nav: bool = True,
         wrap_class: str = "") -> str:
    nav = ""
    sidebar = ""
    if show_nav:
        links = "".join(
            f'<a href="{href}" class="{"on" if href == active else ""}">{label}</a>'
            for href, label in _TABS
        )
        nav = f'<nav class="nav">{links}</nav>'
        sidebar = _sidebar_html(active)
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} — HTP</title>
<link rel="preload" href="/static/fonts/be-vietnam-pro-v12-latin_vietnamese-regular.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/static/fonts/be-vietnam-pro-v12-latin_vietnamese-600.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="{CSS_URL}">{FAVICON}{PWA_HEAD}</head>
<body>
{sidebar}
<div class="top">{esc(title)}<a href="/logout" onclick="event.preventDefault();document.getElementById('lo').submit()">Thoát</a></div>
<form id="lo" method="post" action="/logout" hidden></form>
<div class="wrap{(' ' + wrap_class) if wrap_class else ''}">{body}</div>
{nav}
<script>{_JS}</script>
<script src="{JS_URL}"></script>
</body></html>"""


def login_page(error: str = "") -> str:
    err = '<div class="callout callout-danger">Sai mật khẩu, thử lại nhé.</div>' if error else ""
    body = f"""
<div class="card login-card">
  <div class="login-brand">Hưng Thành Phát</div>
  <div class="login-sub">Sổ khách hàng</div>
  {err}
  <form method="post" action="/login">
    <label>Mật khẩu gia đình</label>
    <input name="password" type="password" autofocus>
    <button class="btn big mt-4">Đăng nhập</button>
  </form>
</div>"""
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Đăng nhập — HTP</title>
<link rel="preload" href="/static/fonts/be-vietnam-pro-v12-latin_vietnamese-regular.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/static/fonts/be-vietnam-pro-v12-latin_vietnamese-600.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="{CSS_URL}">{FAVICON}{PWA_HEAD}</head>
<body><div class="wrap">{body}</div></body></html>"""


NEED_OPTIONS = [
    ("cua_cuon", "Cửa cuốn mới"),
    ("cua_keo", "Cửa kéo mới"),
    ("nhom_kinh", "Cửa nhôm kính"),
    ("sua_chua", "Sửa chữa / bảo trì"),
    ("khac", "Khác"),
]


def _public_page(title: str, body: str) -> str:
    """Bare public page (no nav, no logout) — same skeleton as login_page."""
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} — Hưng Thành Phát Door</title>
<link rel="preload" href="/static/fonts/be-vietnam-pro-v12-latin_vietnamese-regular.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/static/fonts/be-vietnam-pro-v12-latin_vietnamese-600.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="{CSS_URL}">{FAVICON}{PWA_HEAD}</head>
<body><div class="wrap">{body}</div></body></html>"""


def baogia_public_page(company: dict, vals: dict | None = None, error: str = "") -> str:
    """Public quote-request form (/yeu-cau) — the link handed out on
    Zalo/Facebook/Chợ Tốt. Unauthenticated; keep it phone-first and short."""
    v = vals or {}
    err = f'<div class="callout callout-danger">{esc(error)}</div>' if error else ""
    opts = "".join(
        f'<option value="{val}"{" selected" if v.get("nhu_cau") == val else ""}>{label}</option>'
        for val, label in NEED_OPTIONS
    )
    body = f"""
<div class="card login-card">
  <div class="login-brand">{esc(company["name"].title())}</div>
  <div class="login-sub">{esc(company["tagline"])} — Cần Thơ, từ 2005</div>
  <p class="mt-4">Anh/chị để lại thông tin, bên em <b>gọi lại báo giá trong 30 phút</b>
  (giờ làm việc 8:00–17:30, T2–T7). Khảo sát tận nơi miễn phí ·
  Lắp xong mới thanh toán · Sai vật tư đền 200%.</p>
  {err}
  <form method="post" action="/yeu-cau">
    <label>Họ tên anh/chị</label>
    <input name="ten" value="{esc(v.get("ten", ""))}" autofocus>
    <label>Số điện thoại (có Zalo càng tốt)</label>
    <input name="sdt" type="tel" value="{esc(v.get("sdt", ""))}">
    <label>Khu vực / địa chỉ công trình</label>
    <input name="khu_vuc" value="{esc(v.get("khu_vuc", ""))}" placeholder="VD: Bình Thủy, Cần Thơ">
    <label>Anh/chị cần gì?</label>
    <select name="nhu_cau">{opts}</select>
    <label>Kích thước ngang × cao (mét — nếu biết)</label>
    <input name="kich_thuoc" value="{esc(v.get("kich_thuoc", ""))}" placeholder="VD: 3m x 2.5m">
    <label>Ghi chú thêm</label>
    <textarea name="ghi_chu" rows="2">{esc(v.get("ghi_chu", ""))}</textarea>
    <input name="website" value="" tabindex="-1" autocomplete="off" aria-hidden="true"
           style="position:absolute;left:-9999px;height:0;width:0;border:0;padding:0">
    <button class="btn big mt-4">Gửi yêu cầu báo giá</button>
  </form>
  <p class="mt-4">Cần gấp? Gọi/Zalo <a href="tel:0945042345"><b>0945 042 345</b></a></p>
</div>"""
    return _public_page("Yêu cầu báo giá", body)


def baogia_thanks_page(company: dict) -> str:
    body = f"""
<div class="card login-card">
  <div class="login-brand">Đã nhận yêu cầu của anh/chị! ✅</div>
  <p class="mt-4">Bên em sẽ <b>gọi lại trong 30 phút</b> trong giờ làm việc
  (8:00–17:30, T2–T7). Ngoài giờ, bên em gọi sớm sáng hôm sau.</p>
  <p class="mt-4">Cần gấp anh/chị gọi <a href="tel:0945042345"><b>0945 042 345</b></a>
  hoặc nhắn Zalo số này nha!</p>
  <p class="mt-4">{esc(company["address"])} · {esc(company["website"])}</p>
</div>"""
    return _public_page("Cảm ơn anh/chị", body)


# ---------------------------------------------------------------- shared bits

def contact_buttons(phone: str, zalo_phone: str = "", msg: str = "",
                    customer_id: int = 0, zalo_user_id: str = "") -> str:
    """Zalo / Gọi button row. ``msg`` is no longer copied to the clipboard —
    the family sends through the bot or the API instead, so a "Chép tin nhắn"
    button was one tap of dead weight on every card. It survives only as the
    body of the API direct-send below.
    When the customer is linked to a Zalo OA user id, add a direct-send button
    that posts through the API instead of opening the Zalo app — still a person
    tapping "send", just via the API rather than a deep link."""
    z = zalo_phone or phone
    zalo = f'<a class="btn zalo" href="{esc(zalo_link(z))}">Zalo</a>' if z else ""
    call = (f'<a class="btn call" href="tel:{esc(phone)}"'
            + (f' onclick="logGoi({customer_id})"' if customer_id else "")
            + '>Gọi</a>') if phone else ""
    api_send = ""
    if msg and customer_id and zalo_user_id:
        api_send = (
            f'<form method="post" action="/khach/{customer_id}/gui-zalo" class="flex grow">'
            f'<input type="hidden" name="text" value="{esc(msg)}">'
            f'<button class="btn zalo w-full">Gửi qua API</button></form>'
        )
    return f'<div class="row">{zalo}{call}{api_send}</div>'


def _post_btn(action: str, label: str, cls: str = "done", next_url: str = "", data_ajax: str = "") -> str:
    nxt = f'<input type="hidden" name="next" value="{esc(next_url)}">' if next_url else ""
    ajax = f' data-ajax="{esc(data_ajax)}"' if data_ajax else ""
    return (
        f'<form method="post" action="{esc(action)}" class="flex grow"{ajax}>{nxt}'
        f'<button class="btn {cls} w-full">{esc(label)}</button></form>'
    )


def type_chip(t: str) -> str:
    return '<span class="chip dl">Đại lý</span>' if t == "DL" else '<span class="chip kh">Khách lẻ</span>'


_STATUS_LABEL = {"sent": "Đã gửi", "chasing": "Đang theo dõi", "won": "Đã chốt", "lost": "Mất"}


def status_chip(s: str) -> str:
    return f'<span class="chip {esc(s)}">{_STATUS_LABEL.get(s, s)}</span>'


# ---------------------------------------------------------------- bộ cửa / tiến độ

def _door_desc(item: dict) -> str:
    """'Cửa Cuốn Đức KV 380' — door label + short technology + model. Order
    items carry an explicit description for phụ kiện/generic lines — it wins."""
    if item.get("description"):
        return item["description"]
    if item["product"] == "khac":  # free-form item — its name rides in cong_nghe
        return item.get("cong_nghe") or "Sản phẩm khác"
    label = DOOR_CONFIG.get(item["product"], {}).get("label", item["product"])
    parts = [label]
    cong_nghe = item.get("cong_nghe") or ""
    if cong_nghe:
        parts.append(cong_nghe.replace("Cửa cuốn công nghệ ", ""))
    if item.get("mau"):
        parts.append(item["mau"])
    return " ".join(p for p in parts if p)


def bo_cua_html(items: list) -> str:
    """Compact bộ-cửa list (each door: mô tả — kích thước · màu — thành tiền).
    Order items without kích thước (phụ kiện/generic invoice lines) render as
    mô tả ×SL — thành tiền."""
    rows = []
    for i in items:
        mau_sac = f' · {esc(i["mau_sac"])}' if i.get("mau_sac") else ""
        kich = (f' — {i["ngang_mm"]}×{i["cao_mm"]}mm'
                if i.get("ngang_mm") and i.get("cao_mm") else "")
        qty = f' ×{i["so_luong"]}' if i.get("so_luong", 1) > 1 else ""
        note = (f'<div class="sub" style="opacity:.7">Ghi chú: {esc(i["ghi_chu"])}</div>'
                if i.get("ghi_chu") else "")
        rows.append(
            f'<div class="sub between">'
            f'<span>{esc(_door_desc(i))}{kich}{mau_sac}{qty}</span>'
            f'<b class="amt">{fmt_vnd(i["thanh_tien"])}</b></div>'
            f'{note}'
        )
    return "".join(rows)


def production_message_no_price(order: dict, items: list) -> str:
    """Plain-text work-order stripped of every VND figure — used for the Zalo
    group auto-ping on chốt (money never goes to the group, see
    ZALO-BOT-PLAN.md). Every hạng mục rides along: lines without kích thước
    (phụ kiện, generic invoice lines) print mô tả ×SL, same as bo_cua_html."""
    L = []
    if order.get("urgent"):
        L.append("🔥 GẤP — ưu tiên làm trước")
    L.append("🔨 ĐƠN SẢN XUẤT — Hưng Thành Phát")
    L.append(f"Khách: {order.get('customer_name', '')}")
    if order.get("phone"):
        L.append(f"SĐT: {order['phone']}")
    if order.get("address"):
        L.append(f"Địa chỉ: {order['address']}")
    if order.get("install_date"):
        L.append(f"Ngày lắp: {fmt_date(order['install_date'])}")
    L.append("— Bộ cửa —" if any(i.get("ngang_mm") and i.get("cao_mm") for i in items)
             else "— Hạng mục —")
    for idx, i in enumerate(items, 1):
        kich = (f" — {i['ngang_mm']}×{i['cao_mm']}mm"
                if i.get("ngang_mm") and i.get("cao_mm") else "")
        mau_sac = f" · {i['mau_sac']}" if i.get("mau_sac") else ""
        qty = f" ×{i['so_luong']}" if (i.get("so_luong") or 1) > 1 else ""
        L.append(f"{idx}. {_door_desc(i)}{kich}{mau_sac}{qty}")
        if i.get("ghi_chu"):
            L.append(f"   Ghi chú: {i['ghi_chu']}")
    if order.get("note"):
        L.append(f"Ghi chú: {order['note']}")
    return "\n".join(L)


def stage_badge(stage: str) -> str:
    return f'<span class="chip stage-{esc(stage)}">{esc(STAGE_LABELS.get(stage, stage))}</span>'


# ---------------------------------------------------------------- touches (chăm sóc)

_TOUCH_KIND_LABELS = {
    "goi": "Gọi điện",
    "zalo": "Nhắn Zalo",
    "zalo_api": "Gửi qua API",
    "trang_thai": "Đổi trạng thái",
    "ghi_chu": "Ghi chú",
    "nhac_xong": "Nhắc xong",
    "bao_tri": "Bảo trì",
    "danh_gia": "Xin đánh giá",
}


def _state_labels_vi(detail: str) -> str:
    """Old trang_thai touches stored raw codes ('sent → won',
    'cho_san_xuat → hoan_thanh'); translate known tokens at render so the
    family never sees enum names. Unknown words (e.g. a lost_reason) pass
    through untouched."""
    words = {**_STATUS_LABEL, **STAGE_LABELS, **RETIRED_STAGE_LABELS, **LOST_REASON_LABELS}
    return re.sub(r"[a-z_]+", lambda m: words.get(m.group(0), m.group(0)), detail)


def _touch_timeline_html(touches: list) -> str:
    """Newest-first care log, label per kind, relative day count. Rendered as a
    connected vertical rail (a dot per entry) rather than separate cards."""
    if not touches:
        return '<div class="empty">Chưa có lượt chăm sóc nào.</div>'
    rows = []
    for t in touches:
        label = _TOUCH_KIND_LABELS.get(t["kind"], t["kind"])
        days = t.get("days_ago")
        when = "Hôm nay" if days == 0 else (f"{days} ngày trước" if days and days > 0 else fmt_date(t["created_at"]))
        detail_text = t.get("detail") or ""
        if detail_text and t["kind"] == "trang_thai":
            detail_text = _state_labels_vi(detail_text)
        detail = f" — {esc(detail_text)}" if detail_text else ""
        rows.append(f'<div class="tl-item"><span class="tl-dot"></span><div class="sub">{label}{detail} — {when}</div></div>')
    return f'<div class="timeline">{"".join(rows)}</div>'


# ---------------------------------------------------------------- Hôm nay

def _zalo_bot_btn(action: str, msg: str) -> str:
    """One-tap: DM this draft to the customer via the personal-account bot.
    On 200 the AJAX layer removes the card (loop already closed server-side);
    on failure it falls back to a normal POST that shows a loud error and
    keeps the card, so the family can forward the draft from the group."""
    return (
        f'<form method="post" action="{esc(action)}" class="flex grow" data-ajax="remove">'
        f'<input type="hidden" name="text" value="{esc(msg)}">'
        f'<input type="hidden" name="next" value="/">'
        f'<button class="btn zalo w-full">Gửi Zalo</button></form>'
    )


def _board_html(columns: list, extra_class: str = "") -> str:
    """Trello-style board. ``columns`` is a list of dicts:
      label  — column heading
      cards  — list of already-rendered card-body HTML strings
      accent — token name for the 4px stripe on the column head (colour-codes
               the list the way Trello's label strip does)
      meta   — optional small right-aligned figure in the head (a money total)
      attrs  — optional extra attributes on the column (drag-drop wiring)

    Renders at every viewport, unlike the old desktop-only .kanban: on a phone
    the columns are ~84vw and scroll-snap so each swipe lands one list square
    in view, which is exactly how Trello's own mobile board behaves. Callers
    drop empty columns before calling — an all-empty board is the caller's
    empty state, not a row of blank lists."""
    cols = []
    for c in columns:
        cards = "".join(f'<div class="board-card">{b}</div>' for b in c["cards"])
        meta = f'<span class="board-col-meta">{c["meta"]}</span>' if c.get("meta") else ""
        cols.append(f"""
<div class="board-col accent-{esc(c.get("accent", "brand"))}"{c.get("attrs", "")}>
  <div class="board-col-h"><span class="board-col-t">{esc(c["label"])}</span>
    <span class="board-col-n">{len(c["cards"])}</span>{meta}</div>
  <div class="board-col-body">{cards}</div>
</div>""")
    return f'<div class="board {extra_class}">{"".join(cols)}</div>'


def today_page(chase: list, checkins: list, expiring: list, debts: list,
               reminders: list, reviews: list, summary: dict, kh_debts: list = None,
               debt_total: int = 0, bot_ready: bool = False,
               installs: list = None, today: str = "") -> str:
    kh_debts = kh_debts or []
    installs = installs or []
    # Stat-card row (GHL-style dashboard convention) — visible on all
    # viewports. The pipeline total reuses the already-existing
    # open_quotes_summary(); the task count is derived from the SAME lists
    # rendered below, so it can never disagree with what's listed on the
    # page. debt_total is the one exception: it comes from
    # store.debt_outstanding_total() (the /cong-no grand total, dealer +
    # every unpaid KH order) rather than from `debts`/`kh_debts` alone,
    # because those two lists are gated to "actionable today" — an order
    # that's unpaid but not yet past its nag grace period must still count
    # here, or the headline number quietly disagrees with /cong-no.
    task_count = (len(chase) + len(installs) + len(checkins) + len(expiring) + len(debts)
                  + len(reminders) + len(reviews) + len(kh_debts))
    stat_row = f"""
<div class="stat-row">
  <div class="stat-card"><div class="n">{task_count}</div><div class="lbl">Việc cần làm hôm nay</div></div>
  <div class="stat-card"><div class="n">{fmt_vnd_short(summary["total"])}</div><div class="lbl">Báo giá đang theo dõi</div></div>
  <div class="stat-card"><div class="n">{fmt_vnd_short(debt_total)}</div><div class="lbl">Công nợ quá hạn</div></div>
</div>"""
    if task_count == 0:
        return (stat_row + '<div class="empty" style="padding-top:70px;font-size:18px">'
                "Hôm nay không có việc cần làm.</div>")

    # One board column per kind of work. Columns are lists of things to DO, not
    # stages a card moves between — nothing is dragged here; each card carries
    # the button that closes its own loop and then removes itself (data-ajax).
    cols = []

    def col(label: str, accent: str, cards: list) -> None:
        if cards:
            cols.append({"label": label, "accent": accent, "cards": cards})

    # Leads the board: a cửa due on the wall today outranks every follow-up.
    # "Đã lắp xong" is the only button here that moves a production stage from
    # this page — it walks the đơn hàng straight to đã lắp đặt/đã giao, which is
    # what then feeds Xin đánh giá and Khách lẻ còn nợ.
    install_cards = []
    for o in installs:
        label = PRODUCT_LABELS.get(o["product"], "sản phẩm")
        overdue = bool(today and o["install_date"] < today)
        when = (f'<span class="chip warn">Quá hẹn {fmt_date(o["install_date"])}</span>'
                if overdue else '<span class="chip">Hôm nay</span>')
        gap = '<span class="chip warn">Gấp</span>' if o.get("urgent") else ""
        install_cards.append(f"""
  <div class="name">{esc(o["customer_name"])} {when} {gap}</div>
  <div class="sub">{esc(label.capitalize())} — {esc(STAGE_LABELS.get(o["stage"], o["stage"]))}{
      f' — {esc(o["address"])}' if o.get("address") else ""}</div>
  {contact_buttons(o["phone"], o["zalo_phone"], customer_id=o["customer_id"])}
  <div class="row">
    <form method="post" action="/don-hang/{o["id"]}/giai-doan" class="flex grow" data-ajax="remove">
      <input type="hidden" name="stage" value="dang_lap">
      <input type="hidden" name="next" value="/">
      <button class="btn done w-full">Đã lắp xong</button></form>
    <a class="btn done" href="/don-hang/{o["id"]}">Xem đơn</a>
  </div>""")
    col("Lắp hôm nay", "danger", install_cards)

    chase_cards = []
    for q in chase:
        label = PRODUCT_LABELS.get(q["product"], "sản phẩm")
        msg = render("quote_followup_2" if q["nudge_level"] == 2 else "quote_followup_1",
                     ten=q["customer_name"], san_pham=label)
        badge = '<span class="badge">Lần 2</span>' if q["nudge_level"] == 2 else ""
        bot_row = (f'<div class="row">{_zalo_bot_btn("/bao-gia/" + str(q["id"]) + "/gui-zalo-bot", msg)}</div>'
                   if bot_ready and (q["zalo_phone"] or q["phone"]) else "")
        chase_cards.append(f"""
  <div class="name">{esc(q["customer_name"])} {badge}</div>
  <div class="sub">{esc(label.capitalize())} — {fmt_vnd(q["value_vnd"])} — gửi {q["days_sent"]} ngày trước</div>
  {contact_buttons(q["phone"], q["zalo_phone"], msg, customer_id=q["customer_id"])}
  {bot_row}
  <div class="row">{_post_btn(f'/bao-gia/{q["id"]}/da-nhan', "Đã nhắn", next_url="/", data_ajax="remove")}
  <a class="btn done" href="/bao-gia">Xem</a></div>""")
    col("Cần nhắc báo giá", "warn", chase_cards)

    col("Nhắc hôm nay", "brand", [f"""
  <div class="name">{esc(r["customer_name"])}</div>
  <div class="sub">{esc(r["note"])} — hẹn {fmt_date(r["due_date"])}</div>
  {contact_buttons(r["phone"], r["zalo_phone"], customer_id=r["customer_id"])}
  <div class="row">{_post_btn(f'/nhac/{r["id"]}/xong', "Xong", next_url="/", data_ajax="remove")}</div>""" for r in reminders])

    # Only reaches this queue once the đơn hàng is đã lắp đặt/đã giao —
    # store.orders_review_due() gates on stage, not just an install date.
    review_cards = []
    for o in reviews:
        label = PRODUCT_LABELS.get(o["product"], "sản phẩm")
        msg = render("review_request", ten=o["customer_name"], san_pham=label)
        bot_row = (f'<div class="row">{_zalo_bot_btn("/don-hang/" + str(o["id"]) + "/gui-zalo-bot", msg)}</div>'
                   if bot_ready and (o["zalo_phone"] or o["phone"]) else "")
        review_cards.append(f"""
  <div class="name">{esc(o["customer_name"])}</div>
  <div class="sub">{esc(label.capitalize())} — lắp {fmt_date(o["install_date"])}</div>
  {contact_buttons(o["phone"], o["zalo_phone"], msg, customer_id=o["customer_id"])}
  {bot_row}
  <div class="row">{_post_btn(f'/don-hang/{o["id"]}/da-xin-danh-gia', "Đã xin", next_url="/", data_ajax="remove")}</div>""")
    col("Xin đánh giá", "ok", review_cards)

    col("Bảo trì 6 tháng", "info", [f"""
  <div class="name">{esc(o["customer_name"])}</div>
  <div class="sub">{esc(PRODUCT_LABELS.get(o["product"], "sản phẩm").capitalize())} — lắp {fmt_date(o["install_date"])}</div>
  {contact_buttons(o["phone"], o["zalo_phone"],
                   render("checkin_6m", ten=o["customer_name"],
                          san_pham=PRODUCT_LABELS.get(o["product"], "sản phẩm")),
                   customer_id=o["customer_id"])}
  <div class="row">{_post_btn(f'/don-hang/{o["id"]}/da-bao-tri', "Đã bảo trì", next_url="/", data_ajax="remove")}</div>""" for o in checkins])

    col("Bảo hành sắp hết", "warn", [f"""
  <div class="name">{esc(o["customer_name"])}</div>
  <div class="sub">{esc(PRODUCT_LABELS.get(o["product"], "sản phẩm").capitalize())} — hết BH {fmt_date(o["expiry_date"])}</div>
  {contact_buttons(o["phone"], o["zalo_phone"],
                   render("warranty_expiring", ten=o["customer_name"],
                          san_pham=PRODUCT_LABELS.get(o["product"], "sản phẩm"),
                          ngay=fmt_date(o["expiry_date"])),
                   customer_id=o["customer_id"])}
  <div class="row">{_post_btn(f'/don-hang/{o["id"]}/da-nhan-bh', "Đã nhắn", next_url="/", data_ajax="remove")}</div>""" for o in expiring])

    col("Công nợ đại lý", "danger", [f"""
  <div class="name">{esc(d["name"])} <span class="chip warn">{fmt_vnd(d["balance"])}</span></div>
  <div class="sub">{f"trả lần cuối {fmt_date(d['last_payment'])}" if d["last_payment"] else "chưa trả lần nào"}</div>
  {contact_buttons(d["phone"], d["zalo_phone"],
                   render("debt_reminder", ten=d["name"], so_tien=fmt_vnd(d["balance"])),
                   customer_id=d["id"])}
  <div class="row"><a class="btn done" href="/cong-no/{d["id"]}">Xem sổ nợ</a></div>""" for d in debts])

    col("Khách lẻ còn nợ", "danger", [f"""
  <div class="name">{esc(o["customer_name"])} <span class="chip warn">{fmt_vnd(o["balance_vnd"])}</span></div>
  <div class="sub">{esc(PRODUCT_LABELS.get(o["product"], "sản phẩm").capitalize())} — lắp {fmt_date(o["install_date"])} · còn nợ</div>
  {contact_buttons(o["phone"], o["zalo_phone"],
                   render("debt_reminder", ten=o["customer_name"], so_tien=fmt_vnd(o["balance_vnd"])),
                   customer_id=o["customer_id"])}
  <div class="row">
    <form method="post" action="/don-hang/{o["id"]}/thanh-toan" class="flex grow" data-ajax="remove">
      <input type="hidden" name="amount_vnd" value="{o["balance_vnd"]}">
      <input type="hidden" name="kind" value="thanh_toan">
      <input type="hidden" name="next" value="/">
      <button class="btn done w-full">Đã thu đủ</button></form>
    <a class="btn done" href="/don-hang/{o["id"]}">Xem đơn</a>
  </div>""" for o in kh_debts])

    return stat_row + _board_html(cols)


# ---------------------------------------------------------------- customers

def customers_page(rows: list, q: str = "", loai: str = "") -> str:
    """One list, everyone on it. The lead/khách-chính split only ever hid rows
    the family had typed in themselves — they think in "khách", not pipeline
    stages, so the page no longer filters by stage at all."""
    items = "".join(f"""
<div class="card">
  <div class="row between mt-0" style="align-items:flex-start">
    <a href="/khach/{c["id"]}" class="grow" style="min-width:0">
      <div class="name">{esc(c["name"])} {type_chip(c["type"])}</div>
      <div class="sub">{esc(c["phone"] or "—")} · {SOURCE_LABELS.get(c["source"], "")}</div>
    </a>
    <button type="button" class="kebab-btn" onclick="toggleCardMenu(event, this)">⋮</button>
  </div>
  <div class="card-menu" hidden>
    <a href="/khach/{c["id"]}/sua">Sửa thông tin</a>
    {f'<a href="tel:{esc(c["phone"])}">Gọi điện</a>' if c["phone"] else ""}
    <form method="post" action="/khach/{c["id"]}/xoa"
      onsubmit="return confirm('Xóa khách {esc(c["name"])}? Toàn bộ báo giá, đơn hàng, công nợ và nhắc hẹn của khách này sẽ bị xóa theo — không thể hoàn tác.') && confirm('Chắc chắn chứ? Bấm OK để xóa vĩnh viễn khách {esc(c["name"])} và toàn bộ dữ liệu liên quan.')">
      <button type="submit" class="danger">Xóa khách hàng</button>
    </form>
  </div>
</div>""" for c in rows) or (
        '<div class="empty">Chưa tìm thấy khách nào.</div>' if q
        else '<div class="empty">Chưa có khách nào — bấm "Khách hàng mới" để thêm.</div>'
    )
    return f"""
<form class="search" method="get" action="/khach">
  <input name="q" value="{esc(q)}" placeholder="Tìm tên, SĐT hoặc địa chỉ…">
  <input type="hidden" name="loai" value="{esc(loai)}"><button>Tìm</button>
</form>
<div class="cards-grid">{items}</div>
<script>
function toggleCardMenu(ev, btn) {{
  ev.stopPropagation();
  var menu = btn.closest('.card').querySelector('.card-menu');
  var wasOpen = !menu.hidden;
  document.querySelectorAll('.card-menu').forEach(function(m) {{ m.hidden = true; }});
  menu.hidden = wasOpen;
}}
document.addEventListener('click', function() {{
  document.querySelectorAll('.card-menu').forEach(function(m) {{ m.hidden = true; }});
}});
</script>"""


def customer_form_page(c: dict = None, next_to: str = "") -> str:
    c = c or {}
    is_edit = bool(c.get("id"))
    action = f"/khach/{c['id']}/sua" if is_edit else "/khach/moi"
    nxt = f'<input type="hidden" name="next" value="{esc(next_to)}">' if next_to else ""
    cur_type = c.get("type") or "KH"
    src_opts = "".join(
        f'<option value="{v}" {"selected" if c.get("source", "khac") == v else ""}>{label}</option>'
        for v, label in SOURCE_LABELS.items()
    )
    return f"""
<form method="post" action="{action}">{nxt}
  <label>Tên khách *</label>
  <input name="name" required value="{esc(c.get("name", ""))}" placeholder="VD: Anh Hùng — Trần Phú">
  <label>Số điện thoại *</label>
  <input name="phone" required inputmode="tel" value="{esc(c.get("phone", ""))}">
  <label>Email</label>
  <input name="email" type="email" inputmode="email" value="{esc(c.get("email") or "")}" placeholder="VD: khach@gmail.com">
  <label>Loại khách</label>
  <div class="seg">
    <label><input type="radio" name="type" value="KH" {"checked" if cur_type == "KH" else ""}><span>Khách lẻ</span></label>
    <label><input type="radio" name="type" value="DL" {"checked" if cur_type == "DL" else ""}><span>Đại lý</span></label>
  </div>
  <label>Khách biết mình qua đâu?</label>
  <select name="source">{src_opts}</select>
  <label>Địa chỉ</label>
  <input name="address" value="{esc(c.get("address", ""))}">
  <label>Ghi chú</label>
  <textarea name="note">{esc(c.get("note", ""))}</textarea>
  <label>Số Zalo (nếu khác SĐT)</label>
  <input name="zalo_phone" inputmode="tel" value="{esc(c.get("zalo_phone", ""))}">
  <button class="btn big mt-4">{"Lưu thay đổi" if is_edit else "Thêm khách"}</button>
</form>"""


# ---------------------------------------------------------------- intake wizard
# Colour options per door type. Not priced — captured into quote_items.mau_sac,
# kept here (presentation data), not in pricing.py. Three shapes:
#   {"options": [...]}                  -> one fixed palette (nhôm kính)
#   {"depends_on": <field>, "map": {}}  -> palette varies by another selector (cửa cuốn)
#   {"components": [...]}               -> several colour PARTS, all shown at once
#                                           (cửa kéo) — not alternatives to pick between
# Cửa cuốn màu follows Công nghệ (Đức/Úc each have their own; Đài Loan/Inox keep
# the generic list). Cửa kéo carries two colour parts on every unit — khung U and
# nhíp, always shown — plus a third (lá) that only exists when Loại ("cong_nghe")
# is "Có lá"; each part has its own palette. Lá is gated by the existing Có
# lá/Không lá field, not a separate selector.
_CUON_GENERIC = ["ghi sần", "kem", "xanh"]
_WIZARD_COLORS = {
    "cua_cuon": {"depends_on": "cong_nghe", "map": {
        "Cửa cuốn công nghệ Đức": ["trắng", "ghi", "kem"],
        "Cửa cuốn công nghệ Úc": ["xanh", "trắng", "ghi", "kem"],
        "Cửa cuốn công nghệ Đài Loan": _CUON_GENERIC,
        "Inox": _CUON_GENERIC,
    }},
    "cua_keo": {"components": [
        {"key": "u", "tag": "U", "label": "Màu khung U",
         "options": ["xanh", "xám kem", "ghi", "xám xingfa"]},
        {"key": "nhip", "tag": "Nhíp", "label": "Màu nhíp",
         "options": ["trắng", "ghi", "kem", "xám xingfa"]},
        {"key": "la", "tag": "Lá", "label": "Màu lá",
         "options": ["xanh", "kem", "xám xingfa", "ghi"],
         "show_if": {"field": "cong_nghe", "equals": "Có lá"}},
    ]},
    "nhom_kinh": {"options": ["xám xingfa", "trắng", "giả gỗ"]},
}
# Accessory labels for the wizard — the priced set shared with the header form
# and the price math (single source of truth in pricing.PHUKIEN_CATALOG).
_WIZARD_ACCESSORIES = [label for _key, label in PHUKIEN_CATALOG]

# Built once (no f-string → no brace doubling). Uses event delegation on
# #units-container so dynamically-built blocks carry no inline handlers.
_WIZARD_JS = r"""
var STEP_LABELS = {1:'Bước 1/3 — Thông tin khách', 2:'Bước 2/3 — Chọn cửa', 3:'Bước 3/3 — Phụ kiện & cọc'};
function curStep(){ return parseInt(document.querySelector('.wiz-step.active').dataset.step); }
function showStep(n){
  document.querySelectorAll('.wiz-step').forEach(function(s){ s.classList.toggle('active', parseInt(s.dataset.step)===n); });
  document.getElementById('wizbar').style.width = Math.round(n*100/3)+'%';
  document.getElementById('wizlabel').textContent = STEP_LABELS[n];
  var ab=document.getElementById('addbar'); if(ab) ab.hidden = (n!==2);  // sticky add-bar only while choosing cửa
  window.scrollTo(0,0);
}
function firstIncompleteUnit(){
  var bad=null;
  document.querySelectorAll('.unit-block').forEach(function(b){
    if(bad) return;
    var ngang=parseInt(b.querySelector('.u-ngang').value)||0;
    var cao=parseInt(b.querySelector('.u-cao').value)||0;
    var tenEl=unitField(b,'ten');
    if(!ngang || !cao || !unitTotal(b) || (tenEl && !tenEl.value.trim())) bad=b;
  });
  return bad;
}
function phoneOk(){ return /^[0-9]{10}$/.test(document.getElementById('c_phone').value.trim()); }
function wizNext(){
  var s=curStep();
  if(s===1){
    if(!document.getElementById('c_name').value.trim()){
      showStep(1); alert('Cần nhập Tên khách'); return;
    }
    if(!phoneOk()){ showStep(1); alert('Số điện thoại phải gồm đúng 10 chữ số'); return; }
    showStep(2);
  } else if(s===2){
    var bad=firstIncompleteUnit();
    if(bad){ bad.scrollIntoView({behavior:'smooth',block:'center'}); alert('Mỗi cửa cần đủ kích thước và giá (theo bảng giá hoặc nhập tay); mục "Khác" cần thêm tên sản phẩm'); return; }
    showStep(3);
  }
}
function wizBack(){ var s=curStep(); if(s>1) showStep(s-1); }
function customerType(){ return document.querySelector('input[name=type]:checked').value; }
function optionHtml(list){
  return list.map(function(o){ return '<option value="'+o+'">'+(o||'— Chọn —')+'</option>'; }).join('');
}
function quickAdd(type){
  var cont=document.getElementById('units-container');
  var block=buildUnit(type);
  cont.appendChild(block);
  renumber(); updateTotal();
  // bring the new block into view above the sticky bar and start filling it in
  block.scrollIntoView({behavior:'smooth', block:'center'});
  var first=block.querySelector('input, select');
  if(first) first.focus({preventScroll:true});
}
function renumber(){
  var counts={};
  document.querySelectorAll('.unit-block').forEach(function(b){
    var t=b.dataset.type; counts[t]=(counts[t]||0)+1;
    b.querySelector('.uhead .lbl').textContent = (DOOR_CONFIG[t]?DOOR_CONFIG[t].label:'Khác') + ' #' + counts[t];
  });
}
function colorSpec(type){ return COLORS[type]||{}; }
function colorOptionsFor(type, driverVal){
  var spec=colorSpec(type);
  if(spec.options) return spec.options;          // fixed palette (nhôm kính)
  if(spec.map) return spec.map[driverVal]||[];   // varies by cong_nghe (cửa cuốn)
  return [];
}
function buildColorFields(type){
  var spec=colorSpec(type);
  if(spec.components){  // cửa kéo: several always-on colour PARTS (one may be gated)
    return spec.components.map(function(c){
      var hidden = c.show_if ? ' style="display:none"' : '';
      return '<div class="fld color-comp" data-comp="'+c.key+'"'+hidden+'>'
           + '<label>'+c.label+'</label>'
           + '<select data-key="mau_'+c.key+'"><option value="">— Chọn màu —</option>'+optionHtml(c.options)+'</select></div>';
    }).join('');
  }
  var initColors=spec.options||[];  // fixed palette shows now; dynamic (cửa cuốn) waits for its driver
  return '<div class="fld"><label>Màu</label><select data-key="mau_sac"><option value="">— Chọn màu —</option>'+optionHtml(initColors)+'</select></div>';
}
function buildUnit(type){
  var div=document.createElement('div');
  div.className='unit-block'; div.dataset.type=type;
  if(type==='khac'){  // free-form item: name + kích thước + đơn giá tay (no catalog)
    div.innerHTML =
      '<div class="uhead"><span class="lbl">Khác</span><button type="button" class="ux">&times;</button></div>'
      + '<div class="fld"><label>Tên sản phẩm</label><input data-key="ten" placeholder="VD: Mái tôn, lưới an toàn..."></div>'
      + '<div class="field-grid">'
      +   '<div class="fld"><label>Ngang (mm)</label><input class="u-ngang" inputmode="numeric" placeholder="VD: 3000"></div>'
      +   '<div class="fld"><label>Cao (mm)</label><input class="u-cao" inputmode="numeric" placeholder="VD: 2200"></div>'
      + '</div>'
      + '<div class="unit-price manual">Nhập đơn giá tay (đ/m²)</div>'
      + '<label class="u-manual-toggle" style="display:none"><input type="checkbox" class="u-manual-on" checked> Giá đặc biệt (nhập tay)</label>'
      + '<div class="u-manual-wrap"><label>Đơn giá tay (đ/m²)</label><input class="u-manual" inputmode="numeric" placeholder="VD: 1.300.000"></div>'
      + '<div class="fld"><label>Ghi chú</label><textarea data-key="ghi_chu" rows="2" placeholder="VD: khách yêu cầu ray nhôm, lắp mặt trong..."></textarea></div>';
    return div;
  }
  var cfg=DOOR_CONFIG[type];
  var fields='';
  cfg.fields.forEach(function(f){
    var opts = (f.type==='select-dynamic') ? '<option value="">— Chọn —</option>' : optionHtml(f.options);
    fields += '<div class="fld"><label>'+f.label+'</label><select data-key="'+f.key+'">'+opts+'</select></div>';
  });
  fields += buildColorFields(type);
  div.innerHTML =
    '<div class="uhead"><span class="lbl">'+cfg.label+'</span><button type="button" class="ux">&times;</button></div>'
    + '<div class="field-grid">'+fields+'</div>'
    + '<div class="field-grid">'
    +   '<div class="fld"><label>Ngang (mm)</label><input class="u-ngang" inputmode="numeric" placeholder="VD: 3000"></div>'
    +   '<div class="fld"><label>Cao (mm)</label><input class="u-cao" inputmode="numeric" placeholder="VD: 2200"></div>'
    + '</div>'
    + '<div class="unit-price">Chọn đầy đủ để xem giá</div>'
    + '<label class="u-manual-toggle"><input type="checkbox" class="u-manual-on"> Giá đặc biệt (nhập tay)</label>'
    + '<div class="u-manual-wrap" style="display:none"><label>Đơn giá tay (đ/m²)</label><input class="u-manual" inputmode="numeric" placeholder="VD: 1.300.000"></div>'
    + '<div class="fld"><label>Ghi chú</label><textarea data-key="ghi_chu" rows="2" placeholder="VD: khách yêu cầu ray nhôm, lắp mặt trong..."></textarea></div>';
  return div;
}
function unitField(b,key){ return b.querySelector('[data-key="'+key+'"]'); }
function onUnitField(b,key){
  var type=b.dataset.type;
  if(type==='khac'){ priceUnit(b); return; }  // no catalog fields to cascade
  var cfg=DOOR_CONFIG[type];
  cfg.fields.forEach(function(f){
    if(f.type==='select-dynamic' && f.depends_on===key){
      var val=unitField(b,key).value;
      var opts=(f.option_map && f.option_map[val]) || [''];
      unitField(b,f.key).innerHTML=optionHtml(opts);
    }
  });
  var spec=colorSpec(type);
  if(spec.depends_on===key){  // cong_nghe (cửa cuốn) changed -> refresh Màu
    var scEl=unitField(b,'mau_sac');
    if(scEl) scEl.innerHTML='<option value="">— Chọn màu —</option>'+optionHtml(colorOptionsFor(type, unitField(b,key).value));
  }
  if(spec.components){  // cửa kéo: Loại changed -> show/hide the gated Lá colour part
    spec.components.forEach(function(c){
      if(c.show_if && c.show_if.field===key){
        var wrap=b.querySelector('.color-comp[data-comp="'+c.key+'"]');
        if(wrap) wrap.style.display = (unitField(b,key).value===c.show_if.equals) ? '' : 'none';
      }
    });
  }
  priceUnit(b);
}
function priceUnit(b){
  var type=b.dataset.type;
  var cnEl=unitField(b,'cong_nghe'); var mEl=unitField(b,'mau');
  var cn=cnEl?cnEl.value:''; var m=mEl?mEl.value:'';
  var ngang=parseInt(b.querySelector('.u-ngang').value)||0;
  var cao=parseInt(b.querySelector('.u-cao').value)||0;
  var table=(customerType()==='DL')?PRICES_DL:PRICES_KH;
  var key=(type==='cua_keo')?('Cửa Kéo|'+cn+' - '+m):(cn+'|'+m);
  var price=table[key];
  var priceEl=b.querySelector('.unit-price');
  var manualWrap=b.querySelector('.u-manual-wrap');
  var manualOn=b.querySelector('.u-manual-on').checked;
  var manualActive=manualOn || (!price && (cn||m));
  if(manualActive){
    manualWrap.style.display='block'; priceEl.classList.add('manual');
    var dg=parseInt(b.querySelector('.u-manual').value.replace(/[^0-9]/g,''))||0;
    var mtotal=(dg && ngang && cao)?Math.floor(dg*ngang*cao/1000000):0;
    b.dataset.match='0'; b.dataset.manual='1'; b.dataset.total=mtotal;
    if(mtotal) priceEl.textContent='Thành tiền: '+mtotal.toLocaleString('vi-VN')+'đ (giá đặc biệt)';
    else if(manualOn) priceEl.textContent='Nhập đơn giá tay (đ/m²)';
    else priceEl.textContent='Không có bảng giá — nhập đơn giá tay (đ/m²)';
  } else if(price && ngang && cao){
    var total=Math.floor(price*ngang*cao/1000);
    priceEl.textContent='Thành tiền: '+total.toLocaleString('vi-VN')+'đ (theo bảng giá)';
    priceEl.classList.remove('manual'); manualWrap.style.display='none';
    b.dataset.match='1'; b.dataset.manual='0'; b.dataset.total=total;
  } else {
    b.dataset.match='0'; b.dataset.manual='0'; b.dataset.total=0;
    priceEl.textContent='Chọn đầy đủ để xem giá';
    priceEl.classList.remove('manual'); manualWrap.style.display='none';
  }
  updateTotal();
}
function unitTotal(b){ return parseInt(b.dataset.total)||0; }
function updateTotal(){
  var sum=0; var blocks=document.querySelectorAll('.unit-block');
  blocks.forEach(function(b){ sum+=unitTotal(b); });
  var gt=document.getElementById('grandtotal');
  if(gt){ gt.textContent = blocks.length
    ? 'Tổng tạm tính: '+sum.toLocaleString('vi-VN')+'đ ('+blocks.length+' cửa)'
    : 'Chưa có cửa nào — bấm nút để thêm'; }
  var hint=document.getElementById('units-empty');
  if(hint) hint.style.display = blocks.length ? 'none' : 'block';
}
function removeUnit(b){ b.remove(); renumber(); updateTotal(); }
(function(){
  var cont=document.getElementById('units-container');
  cont.addEventListener('change', function(e){
    var b=e.target.closest('.unit-block'); if(!b) return;
    if(e.target.dataset.key){ onUnitField(b, e.target.dataset.key); }
    else if(e.target.classList.contains('u-manual-on')){ priceUnit(b); }
  });
  cont.addEventListener('input', function(e){
    var b=e.target.closest('.unit-block'); if(!b) return;
    if(e.target.classList.contains('u-ngang') || e.target.classList.contains('u-cao')){
      e.target.value=e.target.value.replace(/[^0-9]/g,''); priceUnit(b);
    } else if(e.target.classList.contains('u-manual')){ fmtMoney(e.target); priceUnit(b); }
  });
  cont.addEventListener('click', function(e){
    if(e.target.classList.contains('ux')){ removeUnit(e.target.closest('.unit-block')); }
  });
})();
function addExtraAccRow(){
  var cont=document.getElementById('extra-acc-container');
  var div=document.createElement('div');
  div.className='acc-row-custom';
  div.innerHTML =
    '<input type="text" class="ax-name" placeholder="Tên phụ kiện">'
    + '<input type="number" class="ax-qty" min="1" value="1" inputmode="numeric">'
    + '<input type="text" class="ax-price" inputmode="numeric" placeholder="Giá (VND)" oninput="fmtMoney(this)">'
    + '<button type="button" class="ux">&times;</button>';
  cont.appendChild(div);
}
(function(){
  var cont=document.getElementById('extra-acc-container');
  cont.addEventListener('click', function(e){
    if(e.target.classList.contains('ux')){ e.target.closest('.acc-row-custom').remove(); }
  });
})();
function unitColorText(b){
  var type=b.dataset.type;
  var spec=colorSpec(type);
  if(spec.components){  // cửa kéo: join the visible parts, e.g. "U: xanh · Nhíp: trắng"
    var parts=[];
    spec.components.forEach(function(c){
      var wrap=b.querySelector('.color-comp[data-comp="'+c.key+'"]');
      if(wrap && wrap.style.display==='none') return;  // gated part not active (Không lá) -> skip
      var el=unitField(b,'mau_'+c.key);
      if(el && el.value) parts.push(c.tag+': '+el.value);
    });
    return parts.join(' · ');
  }
  var scEl=unitField(b,'mau_sac');
  return scEl?scEl.value:'';
}
function serializeWizard(){
  var units=[];
  document.querySelectorAll('.unit-block').forEach(function(b){
    var cnEl=unitField(b,'cong_nghe'); var mEl=unitField(b,'mau');
    var tenEl=unitField(b,'ten');
    units.push({
      product:b.dataset.type,
      ten:tenEl?tenEl.value.trim():'',
      cong_nghe:cnEl?cnEl.value:'',
      mau:mEl?mEl.value:'',
      mau_sac:unitColorText(b),
      ghi_chu:unitField(b,'ghi_chu').value.trim(),
      ngang:parseInt(b.querySelector('.u-ngang').value)||0,
      cao:parseInt(b.querySelector('.u-cao').value)||0,
      manual:b.querySelector('.u-manual-on').checked?'1':'',
      gia_manual:b.querySelector('.u-manual').value.replace(/[^0-9]/g,'')
    });
  });
  document.getElementById('units').value=JSON.stringify(units);
  var accs=[];
  document.querySelectorAll('.acc-chk:checked').forEach(function(chk){
    var qty=parseInt(chk.closest('.acc-row').querySelector('.acc-qty').value)||1;
    accs.push(chk.dataset.label+' x'+qty);
  });
  document.querySelectorAll('.acc-row-custom').forEach(function(row){
    var name=row.querySelector('.ax-name').value.trim().replace(/,/g,' ').replace(/\sx(?=\d)/g,' ').replace(/\s=/g,' ').trim();
    var qty=parseInt(row.querySelector('.ax-qty').value)||1;
    var price=parseInt(row.querySelector('.ax-price').value.replace(/[^0-9]/g,''))||0;
    if(name && price) accs.push(name+' x'+qty+' ='+price);
  });
  document.getElementById('accessories').value=accs.join(', ');
}
document.getElementById('wizform').addEventListener('submit', function(e){
  if(!document.getElementById('c_name').value.trim() || !phoneOk()){
    e.preventDefault(); showStep(1); alert('Cần nhập Tên và Số điện thoại (đúng 10 chữ số)'); return;
  }
  var bad=firstIncompleteUnit();
  if(bad){ e.preventDefault(); showStep(2); bad.scrollIntoView({behavior:'smooth',block:'center'}); alert('Mỗi cửa cần đủ kích thước và giá (theo bảng giá hoặc nhập tay); mục "Khác" cần thêm tên sản phẩm'); return; }
  serializeWizard();
});
"""


def intake_wizard_page(today: str) -> str:
    """3-step new-customer intake (customer → doors → accessories/deposit),
    reconstructed from the reference prototype. One POST to /khach/tiep-nhan
    creates the customer and, if any door units were added, a multi-item quote
    (server re-prices authoritatively via pricing.get_price)."""
    src_opts = "".join(f'<option value="{v}">{label}</option>' for v, label in SOURCE_LABELS.items())
    # Sticky add-bar buttons: one per door type (short label, "Cửa " stripped) + Khác.
    add_btns = "".join(
        f'<button type="button" class="addbtn" onclick="quickAdd(\'{key}\')">+ {esc(cfg["label"].replace("Cửa ", ""))}</button>'
        for key, cfg in DOOR_CONFIG.items()
    ) + '<button type="button" class="addbtn khac" onclick="quickAdd(\'khac\')">+ Khác</button>'
    acc_rows = "".join(f'''
    <div class="acc-row">
      <label><input type="checkbox" class="acc-chk" data-label="{esc(label)}"> {esc(label)}</label>
      <input type="number" class="acc-qty" min="1" value="1" inputmode="numeric">
    </div>''' for label in _WIZARD_ACCESSORIES)
    consts = (
        "const DOOR_CONFIG = " + json.dumps(DOOR_CONFIG, ensure_ascii=False) + ";\n"
        "const PRICES_KH = " + json.dumps(_PRICES_KH, ensure_ascii=False) + ";\n"
        "const PRICES_DL = " + json.dumps(_PRICES_DL, ensure_ascii=False) + ";\n"
        "const COLORS = " + json.dumps(_WIZARD_COLORS, ensure_ascii=False) + ";\n"
    )
    script = "<script>\n" + consts + _WIZARD_JS + "</script>"
    return f'''
<div class="wiz-steplabel" id="wizlabel">Bước 1/3 — Thông tin khách</div>
<div class="wiz-progress"><div class="bar" id="wizbar" style="width:33%"></div></div>
<form method="post" action="/khach/tiep-nhan" id="wizform">
  <input type="hidden" name="units" id="units">
  <input type="hidden" name="accessories" id="accessories">

  <div class="wiz-step active" data-step="1">
    <div class="card">
      <div class="card-title">Thông tin khách hàng</div>
      <label>Loại khách</label>
      <div class="seg">
        <label><input type="radio" name="type" value="KH" checked><span>Khách lẻ</span></label>
        <label><input type="radio" name="type" value="DL"><span>Đại lý</span></label>
      </div>
      <div class="field-grid">
        <div class="fld"><label>Tên khách *</label>
          <input name="name" id="c_name" placeholder="VD: Anh Hùng — Trần Phú"></div>
        <div class="fld"><label>Số điện thoại *</label>
          <input name="phone" id="c_phone" inputmode="numeric" maxlength="10"
            oninput="this.value=this.value.replace(/[^0-9]/g,'').slice(0,10)"></div>
        <div class="fld"><label>Email</label><input name="email" type="email" inputmode="email"></div>
        <div class="fld"><label>Địa chỉ</label><input name="address"></div>
        <div class="fld"><label>Khách biết mình qua đâu?</label><select name="source">{src_opts}</select></div>
        <div class="fld"><label>Số Zalo (nếu khác SĐT)</label><input name="zalo_phone" inputmode="numeric" maxlength="10"
          oninput="this.value=this.value.replace(/[^0-9]/g,'').slice(0,10)"></div>
      </div>
      <label>Ghi chú</label>
      <textarea name="note"></textarea>
    </div>
    <div class="wiz-nav">
      <button type="button" class="btn copy" onclick="wizNext()">Tiếp theo →</button>
    </div>
  </div>

  <div class="wiz-step" data-step="2">
    <div class="card-title">Chọn cửa</div>
    <div id="units-empty" class="wiz-hint">Chưa có cửa nào. Dùng thanh bên dưới để thêm cửa — thêm bao nhiêu tùy ý, không cần cuộn lên.</div>
    <div id="units-container"></div>
    <div class="wiz-nav">
      <button type="button" class="btn done" onclick="wizBack()">← Quay lại</button>
      <button type="button" class="btn copy" onclick="wizNext()">Tiếp theo →</button>
    </div>
  </div>

  <div class="wiz-step" data-step="3">
    <div class="card">
      <div class="card-title">Phụ kiện & đặt cọc</div>
      <label>Phụ kiện</label>
      {acc_rows}
      <div id="extra-acc-container"></div>
      <button type="button" class="btn done" onclick="addExtraAccRow()">+ Thêm phụ kiện khác</button>
      <div class="field-grid mt-3">
        <div class="fld"><label>Đã đặt cọc (VND)</label>
          <input id="deposit" name="deposit" inputmode="numeric" oninput="fmtMoney(this)" placeholder="VD: 2.000.000"></div>
        <div class="fld"><label>Ngày lắp đặt dự kiến</label>
          <input type="date" name="install_date" min="{today}"></div>
      </div>
      <label>Ghi chú báo giá</label>
      <textarea name="quote_note"></textarea>
    </div>
    <div class="wiz-nav">
      <button type="button" class="btn done" onclick="wizBack()">← Quay lại</button>
      <button type="submit" class="btn call">Lưu khách &amp; báo giá</button>
    </div>
  </div>
</form>
<div class="addbar" id="addbar" hidden>
  <div class="addbar-inner">
    <div class="addbar-total" id="grandtotal">Chưa có cửa nào — bấm nút để thêm</div>
    <div class="addbar-btns">{add_btns}</div>
  </div>
</div>
{script}'''


def _customer_quote_card(c: dict, q: dict) -> str:
    """One báo giá on the customer page: bộ cửa + status actions + edit link."""
    items = q.get("items") or []
    bo_cua = bo_cua_html(items)
    edit = f'<a class="btn done" href="/bao-gia/{q["id"]}">Sửa báo giá</a>'
    if q["status"] in ("sent", "chasing"):
        lost_opts = "".join(f'<option value="{v}">{label}</option>'
                            for v, label in LOST_REASON_LABELS.items())
        actions = f"""
  <div class="row">
    {_post_btn(f'/bao-gia/{q["id"]}/da-nhan', "Đã gửi", next_url=f'/khach/{c["id"]}')}
    <form method="post" action="/bao-gia/{q["id"]}/trang-thai" class="flex grow">
      <input type="hidden" name="trang_thai" value="won">
      <button class="btn call w-full">Chốt</button></form>
  </div>
  <details class="mt-2"><summary class="btn danger w-full">Mất</summary>
    <form method="post" action="/bao-gia/{q["id"]}/trang-thai" class="mt-2">
      <input type="hidden" name="trang_thai" value="lost">
      <select name="ly_do">{lost_opts}</select>
      <button class="btn danger w-full mt-2">Xác nhận mất</button>
    </form></details>
  <div class="row mt-2">{edit}</div>"""
    elif q["status"] == "won":
        # Đã chốt: the đơn hàng is the editable surface; the báo giá is read-only
        # (see quote_build_page `locked`), so link it as "Xem", not "Sửa".
        prog = (f'<a class="btn copy" href="/don-hang/{q["order_id"]}">Xem tiến độ</a>'
                if q.get("order_id") else "")
        view = f'<a class="btn done" href="/bao-gia/{q["id"]}">Xem báo giá</a>'
        actions = f'<div class="row mt-2">{prog}{view}</div>'
    else:  # lost
        reason = LOST_REASON_LABELS.get(q.get("lost_reason"), "") if q.get("lost_reason") else ""
        actions = (f'<div class="sub">Lý do: {esc(reason)}</div>' if reason else "") + \
                  f'<div class="row mt-2">{edit}</div>'
    return f"""
<div class="card">
  <div class="sub">{status_chip(q["status"])} {bao_gia_so(q["id"], q.get("sent_date"))}</div>
  <div class="sub">{esc(PRODUCT_LABELS.get(q["product"], "").capitalize())}
   — <b class="amt">{fmt_vnd(q["value_vnd"])}</b> — gửi {fmt_date(q["sent_date"])}</div>
  {bo_cua}
  {actions}
</div>"""


def customer_detail_page(c: dict, quotes: list, orders: list, reminders: list,
                         balance: int, today: str, touches: list = None,
                         phone_dups: list = None) -> str:
    qs = "".join(_customer_quote_card(c, q) for q in quotes)
    os_ = "".join(f"""
<a href="/don-hang/{o["id"]}"><div class="card">
  <div class="sub">{esc(PRODUCT_LABELS.get(o["product"], "").capitalize())} — {fmt_vnd(o["value_vnd"])}
   — {"lắp " + fmt_date(o["install_date"]) if o["install_date"] else "Chưa lắp"}
   {f'<span class="chip warn">còn nợ {fmt_vnd(o["balance_vnd"])}</span>' if o["customer_type"] == "KH" and o.get("balance_vnd", 0) > 0 else ""}</div>
</div></a>""" for o in orders)
    rs = "".join(f"""
<div class="card"><div class="sub">{esc(r["note"])} — {fmt_date(r["due_date"])}</div>
<div class="row">{_post_btn(f'/nhac/{r["id"]}/xong', "Xong", next_url=f'/khach/{c["id"]}')}</div></div>"""
        for r in reminders)

    dup_banner = ""
    if phone_dups:
        dup_rows = "".join(
            f'<div class="row mt-2" style="align-items:center">'
            f'<a href="/khach/{d["id"]}" class="grow">{esc(d["name"])} (#{d["id"]})</a>'
            f'<form method="post" action="/khach/{c["id"]}/gop" '
            f'onsubmit="return confirm(\'Gộp khách trùng này vào «{esc(c["name"])}»? '
            f'Mọi báo giá, đơn hàng, chăm sóc của bản trùng sẽ dồn về đây và bản trùng bị xoá — '
            f'không thể hoàn tác.\')">'
            f'<input type="hidden" name="dup_id" value="{d["id"]}">'
            f'<button type="submit" class="btn done">Gộp vào đây</button></form></div>'
            for d in phone_dups)
        dup_banner = (f'<div class="card" style="border-left:3px solid var(--danger-text)">'
                      f'<div class="sub strong text-danger">Trùng số điện thoại</div>'
                      f'<div class="sub">Cùng SĐT với các khách sau — gộp lại nếu là cùng một người:</div>'
                      f'{dup_rows}</div>')

    debt = ""
    if c["type"] == "DL":
        debt = (f'<h2>Công nợ</h2><a class="btn done w-full" href="/cong-no/{c["id"]}">'
                f'Sổ nợ — hiện nợ {fmt_vnd(balance)}</a>')

    if c.get("zalo_user_id"):
        zalo_block = f"""
<div class="card">
  <div class="sub">Đã gắn Zalo (lần nhắn gần nhất: {fmt_date(c.get("zalo_last_inbound_at"))})</div>
  <form method="post" action="/khach/{c["id"]}/gui-zalo" class="mt-2">
    <textarea name="text" placeholder="Nhắn qua Zalo API…" required></textarea>
    <button class="btn zalo w-full mt-2">Gửi qua API</button>
  </form>
  <form method="post" action="/khach/{c["id"]}/huy-lien-ket-zalo" class="mt-2">
    <button class="btn done w-full">Hủy gắn Zalo</button>
  </form>
</div>"""
    else:
        zalo_block = f"""
<details class="card"><summary>Gắn Zalo (dán từ /zalo)</summary>
  <form method="post" action="/khach/{c["id"]}/lien-ket-zalo">
    <label>Zalo user id</label><input name="zalo_user_id" required placeholder="Dán id từ /zalo">
    <button class="btn big mt-3">Gắn</button>
  </form>
</details>"""

    return f"""
<div class="detail-cols">
<div class="detail-main">
{dup_banner}
<div class="card">
  <div class="name">{esc(c["name"])} {type_chip(c["type"])}</div>
  <div class="sub">{esc(c["phone"] or "—")} · {SOURCE_LABELS.get(c["source"], "")}</div>
  {f'<div class="sub">Email: {esc(c["email"])}</div>' if c.get("email") else ""}
  {f'<div class="sub">Đ/c: {esc(c["address"])}</div>' if c.get("address") else ""}
  {f'<div class="sub">Ghi chú: {esc(c["note"])}</div>' if c.get("note") else ""}
  {contact_buttons(c["phone"], c.get("zalo_phone") or "", customer_id=c["id"])}
  <div class="row"><a class="btn done" href="/khach/{c["id"]}/sua">Sửa</a></div>
</div>
{zalo_block}
<details class="card"><summary>+ Nhắc lại (hẹn gọi sau)</summary>
  <form method="post" action="/khach/{c["id"]}/nhac">
    <label>Ngày nhắc</label><input type="date" name="due_date" required min="{today}" value="{today}">
    <label>Nội dung</label><input name="note" required placeholder="VD: gọi lại sau Tết">
    <button class="btn big mt-3">Lưu nhắc</button>
  </form>
</details>
{rs}
{debt}
{f"<h2>Báo giá ({len(quotes)})</h2>{qs}" if quotes else ""}
{f"<h2>Đơn hàng ({len(orders)})</h2>{os_}" if orders else ""}
</div>
<aside class="detail-side">
<h2>Lịch sử chăm sóc</h2>
<div class="card">
  <form method="post" action="/khach/{c["id"]}/cham-soc">
    <label class="mt-0">Ghi chú nhanh</label>
    <div class="row">
      <input name="note" required placeholder="VD: gọi rồi, hẹn tuần sau" class="grow-2">
      <button class="btn done">Lưu</button>
    </div>
  </form>
</div>
{_touch_timeline_html(touches or [])}
</aside>
</div>"""


# ---------------------------------------------------------------- import

def import_page(preview: list = None, raw: str = "") -> str:
    """preview rows: dicts {line, name, phone, type, address, status(ok|dup|bad), why}."""
    intro = """
<div class="card"><div class="sub">Mỗi dòng một khách, theo mẫu:<br>
<b>Tên, SĐT, KH hoặc ĐL, địa chỉ</b> (địa chỉ không bắt buộc)<br><br>
VD:<br>Anh Hùng Trần Phú, 0901234567, KH, 12 Trần Phú<br>Đại lý Minh Phát, 0912345678, ĐL</div></div>"""
    if preview is None:
        return f"""{intro}
<form method="post" action="/nhap">
  <input type="hidden" name="action" value="preview">
  <textarea name="raw" style="min-height:180px" placeholder="Dán danh sách khách vào đây…"></textarea>
  <button class="btn big mt-3">Xem trước</button>
</form>"""

    cls = {"ok": "preview-ok", "dup": "preview-dup", "bad": "preview-bad"}
    rows = "".join(
        f'<tr class="{cls[p["status"]]}"><td>{esc(p["name"] or p["line"])}</td>'
        f'<td>{esc(p["phone"])}</td><td>{esc(p["type"])}</td><td>{esc(p["why"])}</td></tr>'
        for p in preview
    )
    n_ok = sum(1 for p in preview if p["status"] == "ok")
    confirm = f"""
<form method="post" action="/nhap">
  <input type="hidden" name="action" value="confirm">
  <input type="hidden" name="raw" value="{esc(raw)}">
  <button class="btn big mt-3">Thêm {n_ok} khách</button>
</form>""" if n_ok else '<div class="empty">Không có dòng nào hợp lệ để thêm.</div>'
    return f"""{intro}
<div class="card scroll-x">
<table class="ledger"><tr><th>Tên</th><th>SĐT</th><th>Loại</th><th></th></tr>{rows}</table>
</div>
{confirm}
<a class="btn done w-full mt-2" href="/nhap">Sửa lại danh sách</a>"""


# ---------------------------------------------------------------- quotes

def _quote_pipeline_controls(q: dict, next_url: str = "/bao-gia") -> str:
    """Đã gửi / Chốt / Mất controls shared by the báo giá board card and the
    quote detail page, so the two never drift. Chốt posts won → the server
    creates the đơn hàng, promotes the customer, and (303) returns to /bao-gia —
    the card moves Đã gửi → Chốt without leaving the pipeline. next_url only
    steers the 'Đã gửi' redirect; chốt/mất redirects are server-owned.

    Deliberately a plain POST + redirect, not data-ajax="remove": on a board
    the card has to LAND in the Chốt column (and the column counts/totals have
    to follow it). Removing it in place would just make it vanish."""
    if q["status"] in ("sent", "chasing"):
        lost_opts = "".join(f'<option value="{v}">{label}</option>'
                            for v, label in LOST_REASON_LABELS.items())
        return f"""
  <div class="row">
    {_post_btn(f'/bao-gia/{q["id"]}/da-nhan', "Đã gửi", next_url=next_url)}
    <form method="post" action="/bao-gia/{q["id"]}/trang-thai" class="flex grow">
      <input type="hidden" name="trang_thai" value="won">
      <button class="btn call w-full">Chốt</button></form>
  </div>
  <details class="mt-2"><summary class="btn danger w-full">Mất</summary>
    <form method="post" action="/bao-gia/{q["id"]}/trang-thai" class="mt-2">
      <input type="hidden" name="trang_thai" value="lost">
      <select name="ly_do">{lost_opts}</select>
      <button class="btn danger w-full mt-2">Xác nhận mất</button>
    </form></details>"""
    if q["status"] == "won" and q["order_id"]:
        return (f'<div class="row"><a class="btn copy" '
                f'href="/don-hang/{q["order_id"]}">Xem tiến độ</a></div>')
    return ""


def quotes_page(rows: list, archived: list, summary: dict) -> str:
    header = (f'<div class="total">Đang theo dõi: {summary["n"]} báo giá — '
              f'{fmt_vnd_short(summary["total"])}</div>')
    board = _kanban_html(rows) if rows else '<div class="empty">Chưa có báo giá nào.</div>'
    archive = ""
    if archived:
        arch_cards = "".join(f"""
<a href="/don-hang/{q["order_id"]}"><div class="card dim">
  <div class="name">{esc(q["customer_name"])} {status_chip(q["status"])}</div>
  <div class="sub">{bao_gia_so(q["id"], q.get("sent_date"))} · {esc(PRODUCT_LABELS.get(q["product"], "").capitalize())} — {fmt_vnd(q["value_vnd"])}</div>
</div></a>""" for q in archived)
        archive = f"""
<details class="card mt-3">
  <summary>Đã hoàn thành ({len(archived)})</summary>
  <div class="cards-grid mt-2">{arch_cards}</div>
</details>"""
    return f"""
{header}
<a class="btn add" href="/khach/tiep-nhan">+ Khách hàng mới (tạo báo giá)</a>
{board}
{archive}"""


# (statuses in this column, status posted on drop, label). "sent" and
# "chasing" share one visual column — customer's ask: 3 columns matching the
# actual shop workflow (báo giá gửi ra & đang theo dõi → chốt → mất), not the
# internal contacted/not-contacted distinction.
_KANBAN_COLS = ((("sent", "chasing"), "sent", "Đã gửi"),
                (("won",), "won", "Chốt"),
                (("lost",), "lost", "Mất"))


def _kanban_html(rows: list) -> str:
    """Báo giá pipeline board — now the ONLY rendering of the list (the parallel
    mobile card list is gone, so the two can no longer drift). Drag-drop is a
    desktop nicety; on touch the same moves are made with the Đã gửi / Chốt /
    Mất buttons on each card, which is why those controls live on the card
    rather than only in a hover menu.

    Switching the segment-filter chip to "Tất cả" populates every column; the
    default "Đang theo dõi" segment only has sent/chasing cards, which is the
    existing filter behavior, not a board-specific limitation."""
    _accent = {"sent": "brand", "won": "ok", "lost": "danger"}
    cols = []
    for statuses, drop_status, label in _KANBAN_COLS:
        col_rows = [q for q in rows if q["status"] in statuses]
        total = sum(q["value_vnd"] or 0 for q in col_rows)
        cards = []
        for q in col_rows:
            lost = (f' — {LOST_REASON_LABELS.get(q["lost_reason"], "")}'
                    if q["status"] == "lost" and q["lost_reason"] else "")
            cards.append(f"""
  <div class="name"><a href="/bao-gia/{q['id']}" draggable="false">{esc(q["customer_name"])}</a></div>
  <div class="sub">{bao_gia_so(q["id"], q.get("sent_date"))} · {esc(PRODUCT_LABELS.get(q["product"], "").capitalize())} — {fmt_vnd(q["value_vnd"])}
   — gửi {fmt_date(q["sent_date"])} ({q["days_sent"]} ngày){lost}</div>
  {f'<div class="sub">Ghi chú: {esc(q["description"])}</div>' if q.get("description") else ""}
  <div class="acts">
    <a href="/bao-gia/{q['id']}" draggable="false">{"Xem báo giá" if q.get("order_id") else "Sửa thông tin"}</a>
    {'' if q.get("order_id") else f'<a href="/bao-gia/{q["id"]}/hang-muc/moi" draggable="false">+ Hạng mục</a>'}
    {f'<a href="/bao-gia/{q["id"]}/xuat" draggable="false">Xuất</a>' if (q.get("item_count") or q.get("accessories")) else ''}
  </div>
  <div draggable="false">{_quote_pipeline_controls(q, next_url="/bao-gia")}</div>""")
        cols.append({
            "label": label, "accent": _accent[drop_status], "cards": cards,
            "meta": fmt_vnd_short(total),
            "attrs": (f' data-status="{drop_status}" ondragover="kbAllowDrop(event)"'
                      f" ondrop=\"kbDrop(event,'{drop_status}')\""),
        })
    return f"""
{_board_html(cols, extra_class="board-drag")}
<script>
function kbAllowDrop(ev) {{ ev.preventDefault(); }}
// The card wrapper is emitted by the shared board helper, so drag is wired up
// here instead of via inline attributes. The quote id is read back off the
// card's own /bao-gia/<id> link rather than duplicated into a data- attribute.
// No JS -> no drag, and the Đã gửi / Chốt / Mất buttons still do every move.
document.querySelectorAll('.board-drag .board-card').forEach(function (card) {{
  var a = card.querySelector('a[href^="/bao-gia/"]');
  if (!a) return;
  var id = a.getAttribute('href').split('/')[2];
  card.setAttribute('draggable', 'true');
  card.addEventListener('dragstart', function (ev) {{
    ev.dataTransfer.setData('text/plain', id);
  }});
}});
function kbDrop(ev, newStatus) {{
  ev.preventDefault();
  var id = ev.dataTransfer.getData('text/plain');
  if (newStatus === 'won') {{
    var f = document.createElement('form');
    f.method = 'post'; f.action = '/bao-gia/' + id + '/trang-thai';
    f.innerHTML = '<input name="trang_thai" value="won">';
    document.body.appendChild(f); f.submit();
    return;
  }}
  var body = new URLSearchParams();
  body.set('trang_thai', newStatus);
  if (newStatus === 'lost') {{
    var reason = prompt('Lý do mất khách hàng?');
    if (!reason) return;
    body.set('ly_do', reason);
  }}
  fetch('/bao-gia/' + id + '/trang-thai', {{
    method: 'POST', credentials: 'same-origin',
    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body: body.toString()
  }}).then(function() {{ location.reload(); }});
}}
</script>"""


def pick_customer_page(rows: list, q: str, target: str, title: str, mode: str = "single") -> str:
    """Step 1 of quote/order creation: pick the customer first. mode='multi'
    is used by the multi-item báo giá flow (still the same picker UI)."""
    items = "".join(f"""
<a href="{target}?khach={c["id"]}&mode={mode}"><div class="card">
  <div class="name">{esc(c["name"])} {type_chip(c["type"])}</div>
  <div class="sub">{esc(c["phone"] or "—")}</div>
</div></a>""" for c in rows) or '<div class="empty">Không tìm thấy — thêm khách mới nhé.</div>'
    return f"""
<div class="card"><div class="sub">{esc(title)}</div></div>
<form class="search" method="get" action="{target}">
  <input name="q" value="{esc(q)}" placeholder="Tìm tên hoặc SĐT…">
  <input type="hidden" name="mode" value="{esc(mode)}"><button>Tìm</button>
</form>
{items}
<a class="btn done w-full mt-2" href="/khach/moi?next={target.strip("/").replace("/", "-")}">+ Thêm khách mới</a>"""


def quote_form_page(customer: dict, today: str) -> str:
    prods = "".join(
        f'<label><input type="radio" name="product" value="{v}" {"checked" if v == "cua_cuon" else ""}>'
        f'<span>{label.capitalize()}</span></label>'
        for v, label in PRODUCT_LABELS.items() if v != "khac"
    )
    return f"""
<div class="card"><div class="name">{esc(customer["name"])} {type_chip(customer["type"])}</div>
<div class="sub">{esc(customer["phone"] or "")}</div></div>
<form method="post" action="/bao-gia/moi">
  <input type="hidden" name="customer_id" value="{customer["id"]}">
  <label>Sản phẩm</label>
  <div class="seg" style="flex-wrap:wrap">{prods}
    <label><input type="radio" name="product" value="khac"><span>Khác</span></label></div>
  <label>Giá báo (VND)</label>
  <input name="value_vnd" inputmode="numeric" oninput="fmtMoney(this)" placeholder="VD: 12.000.000">
  <label>Mô tả (kích thước, màu, nơi lắp…)</label>
  <textarea name="description"></textarea>
  <label>Ngày gửi báo giá</label>
  <input type="date" name="sent_date" value="{today}" required>
  <button class="btn big mt-4">Lưu báo giá</button>
</form>"""


# ---------------------------------------------------------------- multi-item quotes (calculator)

_ACCESSORIES = PHUKIEN_CATALOG


def _phukien_rows(checked: dict = None) -> str:
    """Phụ kiện checkbox+qty rows, shared by the header-form (add) and the
    quote detail page's editable phụ kiện card. checked = {key: qty} to
    pre-tick/pre-fill; empty/None renders everything unchecked."""
    checked = checked or {}
    return "".join(f"""
<div class="acc-row">
  <label><input type="checkbox" name="{key}_chk" value="1"
    onchange="toggleQty(this,'{key}_qty')" {"checked" if key in checked else ""}> {label}</label>
  <input type="number" name="{key}_qty" id="{key}_qty" min="1" value="{checked.get(key, 1)}"
    {"" if key in checked else "disabled"} inputmode="numeric">
</div>""" for key, label in _ACCESSORIES)


def _parse_accessories_to_keys(accessories: str) -> dict:
    """'Moto + rmoc x2, Khóa ngang (tole) x1' -> {'moto_rmoc': 2, 'khoa_tole': 1}.
    Reverses the "{label} x{qty}" join built by app.py's quote_multi_new /
    quote_set_accessories, so the edit card can pre-tick current selections."""
    label_to_key = {label: key for key, label in _ACCESSORIES}
    out = {}
    for part in (accessories or "").split(", "):
        part = part.strip()
        if " x" not in part:
            continue
        label, _, qty = part.rpartition(" x")
        if label in label_to_key and qty.isdigit():
            out[label_to_key[label]] = int(qty)
    return out


def _extras_text(accessories: str) -> str:
    """Free-form 'chi phí khác' entries as 'Tên - 300.000' lines (or
    'Tên x2 - 300.000' when qty>1) to pre-fill the editable textarea;
    round-trips through app._encode_extras on save."""
    lines = []
    for name, qty, price in pricing.extras_of(accessories or ""):
        qty_part = f" x{qty}" if qty != 1 else ""
        lines.append(f"{esc(name)}{qty_part} - {price:,}".replace(",", "."))
    return "\n".join(lines)


_EXTRAS_HINT = "Chi phí khác (mỗi dòng: tên - số tiền, VD: Lò xo - 300000 hoặc Lò xo x2 - 300000)"


def quote_header_form_page(customer: dict, today: str) -> str:
    rows = _phukien_rows()
    return f"""
<div class="card"><div class="name">{esc(customer["name"])} {type_chip(customer["type"])}</div>
<div class="sub">{esc(customer["phone"] or "")}</div></div>
<form method="post" action="/bao-gia/nhieu-hang-muc/moi">
  <input type="hidden" name="customer_id" value="{customer["id"]}">
  <label>Phụ kiện</label>
  {rows}
  <label>{_EXTRAS_HINT}</label>
  <textarea name="extras" placeholder="Lò xo - 300000"></textarea>
  <label>Đã đặt cọc (VND)</label>
  <input name="deposit" inputmode="numeric" oninput="fmtMoney(this)" placeholder="VD: 2.000.000">
  <label>Ngày lắp đặt dự kiến</label>
  <input type="date" name="install_date" min="{today}">
  <label>Ghi chú</label>
  <textarea name="note"></textarea>
  <button class="btn big mt-4">Tiếp tục — thêm hạng mục</button>
</form>
<script>
function toggleQty(cb, qtyId) {{
  var qty = document.getElementById(qtyId);
  qty.disabled = !cb.checked;
  if (cb.checked) qty.value = qty.value || 1;
}}
</script>"""


def quote_build_page(quote: dict, items: list) -> str:
    # Đã chốt (linked to an đơn hàng) => read-only. Editing here would desync the
    # order's snapshotted lines/value, so the edit controls are hidden and the
    # server rejects mutations too (app._reject_if_ordered). Unlocks if the
    # order is deleted (that clears quotes.order_id).
    locked = bool(quote.get("order_id"))
    item_actions = "" if locked else """
  <div class="row">
    <a class="btn done" href="/bao-gia/{qid}/hang-muc/{iid}/sua">Sửa</a>
    <form method="post" action="/bao-gia/{qid}/hang-muc/{iid}/xoa" class="flex grow">
      <button class="btn danger w-full">Xóa</button>
    </form>
  </div>"""
    item_cards = "".join(f"""
<div class="card">
  <div class="name">{esc(PRODUCT_LABELS.get(i["product"], "").capitalize())}</div>
  <div class="sub">{esc(i["cong_nghe"] or "")} {esc(i["mau"] or "")} — {i["ngang_mm"]}×{i["cao_mm"]}mm
   {"(nhập tay)" if i["is_manual_price"] else ""}</div>
  {f'<div class="sub" style="opacity:.7">Ghi chú: {esc(i["ghi_chu"])}</div>' if i.get("ghi_chu") else ""}
  <div class="sub amt">{fmt_vnd(i["thanh_tien"])}</div>
  {item_actions.format(qid=quote["id"], iid=i["id"])}
</div>""" for i in items)

    locked_banner = (
        f'<div class="card" style="border-left:3px solid var(--brand)">'
        f'<div class="sub strong">Đã chốt — báo giá đã khóa</div>'
        f'<div class="sub">Mọi thay đổi (hạng mục, cọc, ghi chú) làm trên đơn hàng.</div>'
        f'<a class="btn copy w-full mt-2" '
        f'href="/don-hang/{quote["order_id"]}">Mở đơn hàng #{quote["order_id"]}</a></div>'
    ) if locked else ""

    dep = quote.get("deposit_vnd") or 0
    dep_str = f"{dep:,}".replace(",", ".") if dep else ""
    deposit_card = f"""
<form method="post" action="/bao-gia/{quote["id"]}/dat-coc" class="card">
  <label class="mt-0">Đã đặt cọc (VND) — sửa lúc nào cũng được</label>
  <div class="row mt-2">
    <input name="deposit" inputmode="numeric" oninput="fmtMoney(this)" value="{dep_str}"
      placeholder="VD: 2.000.000" class="grow-2">
    <button class="btn done">Lưu cọc</button>
  </div>
  <div class="sub mt-2">Còn lại: <b class="amt">{fmt_vnd((quote.get("value_vnd") or 0) - dep)}</b></div>
</form>""" if items and not locked else ""

    notes_card = f"""
<form method="post" action="/bao-gia/{quote["id"]}/ghi-chu" class="card">
  <label class="mt-0">Ngày lắp đặt dự kiến</label>
  <input type="date" name="install_date" value="{quote.get("install_date") or ""}">
  <label>Ghi chú</label>
  <textarea name="note">{esc(quote.get("note") or "")}</textarea>
  <button class="btn done mt-2">Lưu ghi chú</button>
</form>""" if not locked else ""

    phukien_card = f"""
<form method="post" action="/bao-gia/{quote["id"]}/phu-kien" class="card">
  <label class="mt-0">Phụ kiện — sửa lúc nào cũng được</label>
  {_phukien_rows(_parse_accessories_to_keys(quote.get("accessories") or ""))}
  <label>{_EXTRAS_HINT}</label>
  <textarea name="extras" placeholder="Lò xo - 300000">{_extras_text(quote.get("accessories") or "")}</textarea>
  <button class="btn done mt-2">Lưu phụ kiện</button>
</form>
<script>
function toggleQty(cb, qtyId) {{
  var qty = document.getElementById(qtyId);
  qty.disabled = !cb.checked;
  if (cb.checked) qty.value = qty.value || 1;
}}
</script>""" if not locked else ""

    # Phụ kiện alone is a real báo giá (khóa, bình tích điện, chi phí khác — no
    # cửa), so it gets the same summary, Xuất Excel and "Xong, lưu báo giá" as any
    # other. Gating this on `items` left those jobs with no way to save the đơn
    # hàng or send the customer a quote.
    ctype = quote["customer_type"]
    accessories = quote.get("accessories") or ""
    pk_items = pricing.phukien_line_items(accessories, items, ctype)
    if items or pk_items:
        pk_total = pricing.calc_phukien(accessories, items, ctype)
        door_total = sum(i["thanh_tien"] for i in items)
        tong_cong = quote.get("value_vnd") or (door_total + pk_total)
        vat = round(tong_cong * 0.1)
        tong_tien = tong_cong + vat
        _line = ('<div class="sub between">'
                 '<span>{name}</span><b class="amt">{val}</b></div>')
        pk_rows = "".join(_line.format(name=f'{esc(p["name"])} ×{p["qty"]}', val=fmt_vnd(p["total"]))
                          for p in pk_items)
        pk_block = (f'<div style="margin-top:6px;padding-top:6px;border-top:1px solid var(--line)">'
                    f'<div class="sub strong">Phụ kiện</div>{pk_rows}</div>'
                    if pk_items else "")
        door_line = (_line.format(name=f"Cửa ({len(items)} hạng mục)", val=fmt_vnd(door_total))
                     if items else "")
        summary = f"""
<div class="card">
  {door_line}
  {pk_block}
  <div style="margin-top:6px;padding-top:6px;border-top:1px solid var(--line)">
    {_line.format(name="Tổng cộng", val=fmt_vnd(tong_cong))}
    {_line.format(name="VAT (10%)", val=fmt_vnd(vat))}
  </div>
  <div class="total between mt-2">
    <span>Tổng tiền</span><span>{fmt_vnd(tong_tien)}</span></div>
</div>"""
        export_btn = (f'<a class="btn copy w-full mt-2" '
                      f'href="/bao-gia/{quote["id"]}/xuat">Xuất Báo Giá (Excel)</a>')
        finish = "" if locked else f"""
<form method="post" action="/bao-gia/{quote["id"]}/hoan-tat">
  <button class="btn big mt-3">Xong, lưu báo giá</button>
</form>"""
    else:
        legacy = quote.get("value_vnd") or quote.get("description")
        summary = (f'<div class="card"><div class="sub">{esc(PRODUCT_LABELS.get(quote["product"], "").capitalize())} '
                   f'— {fmt_vnd(quote["value_vnd"])}</div></div>' if legacy
                   else '<div class="empty">Chưa có hạng mục nào.</div>')
        export_btn = ""
        finish = ""

    lost_note = (f' — {esc(LOST_REASON_LABELS.get(quote["lost_reason"], ""))}'
                 if quote["status"] == "lost" and quote.get("lost_reason") else "")
    pipeline_card = f"""
<div class="card">
  <div class="sub strong">Tiến độ: {status_chip(quote["status"])}{lost_note}</div>
  {_quote_pipeline_controls(quote, next_url=f'/bao-gia/{quote["id"]}')}
</div>"""

    add_item_details = "" if locked else f"""
<details class="card">
  <summary class="btn add">+ Thêm cửa</summary>
  <div class="mt-3">{_quote_item_form_body(quote)}</div>
</details>"""

    return f"""
<div class="card"><div class="name">{esc(quote["customer_name"])} {type_chip(quote["customer_type"])}</div>
<div class="sub">{bao_gia_so(quote["id"], quote.get("sent_date"))} — gửi {fmt_date(quote["sent_date"])}</div></div>
{pipeline_card}
{locked_banner}
{summary}
{item_cards}
{deposit_card}
{phukien_card}
{notes_card}
{add_item_details}
{export_btn}
{finish}
<a class="btn done w-full mt-2" href="/bao-gia">Quay lại danh sách</a>"""


def _quote_item_form_body(quote: dict, item: dict = None) -> str:
    """Loại cửa radios + dynamic fields + kích thước/giá form — the add/edit
    hạng mục form, without the customer-name card header. Shared by the
    standalone add/edit page (quote_item_form_page) and the "+ Thêm cửa"
    collapsible on quote_build_page, so adding an item doesn't require
    navigating away from the build page first."""
    customer_type = quote["customer_type"]
    # "khac" = free-form item (custom name in cong_nghe, always manual-priced);
    # the wizard can create these, so this form must round-trip them too.
    products = list(DOOR_CONFIG.items()) + [("khac", {"label": "Khác", "fields": []})]
    radios, blocks = [], []
    for i, (key, cfg) in enumerate(products):
        active = (item["product"] == key) if item else (i == 0)
        radios.append(
            f'<label><input type="radio" name="loai_cua" value="{key}" '
            f'{"checked" if active else ""} onchange="selectProduct(\'{key}\')">'
            f'<span>{esc(cfg["label"])}</span></label>'
        )
        field_html = []
        if key == "khac":
            field_html.append(f"""
    <div class="unit-field">
      <label>Tên sản phẩm</label>
      <input id="f_khac_cong_nghe" name="cong_nghe" {"" if active else "disabled"}
        oninput="updatePreview()" placeholder="VD: Mái tôn, lưới an toàn...">
    </div>""")
        for f in cfg["fields"]:
            is_dynamic = f["type"] == "select-dynamic"
            wrap_style = ' style="display:none"' if is_dynamic else ""
            if is_dynamic:
                opts = '<option value="">— Chọn —</option>'
            else:
                opts = "".join(f'<option value="{esc(o)}">{esc(o) or "— Chọn —"}</option>' for o in f["options"])
            field_html.append(f"""
    <div class="unit-field" id="wrap_{key}_{f['key']}"{wrap_style}>
      <label>{esc(f['label'])}</label>
      <select id="f_{key}_{f['key']}" name="{f['key']}" {"" if active else "disabled"}
        onchange="onFieldChanged('{key}','{f['key']}')">{opts}</select>
    </div>""")
        blocks.append(f'<div class="product-fields" data-product="{key}" '
                      f'style="display:{"block" if active else "none"}">{"".join(field_html)}</div>')

    script = f"""
<script>
const DOOR_CONFIG = {json.dumps(DOOR_CONFIG, ensure_ascii=False)};
const PRICES_KH = {json.dumps(_PRICES_KH, ensure_ascii=False)};
const PRICES_DL = {json.dumps(_PRICES_DL, ensure_ascii=False)};
const CUSTOMER_TYPE = "{customer_type}";

function selectProduct(product) {{
  document.querySelectorAll('.product-fields').forEach(function(div) {{
    var active = div.dataset.product === product;
    div.style.display = active ? 'block' : 'none';
    div.querySelectorAll('select, input').forEach(function(sel) {{ sel.disabled = !active; }});
  }});
  updatePreview();
}}
function onFieldChanged(product, key) {{
  var cfg = DOOR_CONFIG[product];
  cfg.fields.forEach(function(f) {{
    if (f.type === 'select-dynamic' && f.depends_on === key) {{
      var val = document.getElementById('f_' + product + '_' + key).value;
      var wrap = document.getElementById('wrap_' + product + '_' + f.key);
      var sel = document.getElementById('f_' + product + '_' + f.key);
      var opts = (f.option_map && f.option_map[val]) || [];
      sel.innerHTML = opts.map(function(o) {{
        return '<option value="' + o + '">' + (o || '— Chọn —') + '</option>';
      }}).join('');
      if (wrap) wrap.style.display = opts.length > 1 ? 'block' : 'none';
    }}
  }});
  updatePreview();
}}
function updatePreview() {{
  var product = document.querySelector('input[name=loai_cua]:checked').value;
  var cnEl = document.getElementById('f_' + product + '_cong_nghe');
  var mEl = document.getElementById('f_' + product + '_mau');
  var cn = cnEl ? cnEl.value : '';
  var m = mEl ? mEl.value : '';
  var ngang = parseInt(document.getElementById('ngang').value) || 0;
  var cao = parseInt(document.getElementById('cao').value) || 0;
  var table = (CUSTOMER_TYPE === 'DL') ? PRICES_DL : PRICES_KH;
  var key = (product === 'cua_keo') ? ('Cửa Kéo|' + cn + ' - ' + m) : (cn + '|' + m);
  var price = table[key];
  var preview = document.getElementById('price_preview');
  var manualWrap = document.getElementById('manual_price_wrap');
  var manualInput = document.getElementById('gia_thu_cong');
  var manualOn = document.getElementById('manual_on').checked;
  var manualActive = manualOn || !(price && ngang && cao);
  if (manualActive) {{
    manualWrap.style.display = 'block';
    manualInput.required = true;
    var dg = parseInt(manualInput.value.replace(/[^0-9]/g, '')) || 0;
    var mtotal = (dg && ngang && cao) ? Math.floor(dg * ngang * cao / 1000000) : 0;
    if (mtotal) preview.textContent = 'Ước tính: ' + mtotal.toLocaleString('vi-VN') + 'đ (giá đặc biệt)';
    else if (manualOn) preview.textContent = 'Nhập đơn giá tay (đ/m²)';
    else preview.textContent = 'Không có bảng giá cho lựa chọn này — nhập đơn giá tay (đ/m²)';
  }} else {{
    var total = Math.floor(price * ngang * cao / 1000);
    preview.textContent = 'Ước tính: ' + total.toLocaleString('vi-VN') + 'đ (theo bảng giá)';
    manualWrap.style.display = 'none';
    manualInput.required = false;
  }}
}}
function prefillEdit(product, values) {{
  var radio = document.querySelector('input[name=loai_cua][value="' + product + '"]');
  if (radio) radio.checked = true;
  selectProduct(product);
  var cfg = DOOR_CONFIG[product];
  if (cfg) {{
    cfg.fields.filter(function(f) {{ return f.type !== 'select-dynamic'; }}).forEach(function(f) {{
      var el = document.getElementById('f_' + product + '_' + f.key);
      if (el && values[f.key] !== undefined) el.value = values[f.key];
    }});
    cfg.fields.filter(function(f) {{ return f.type === 'select-dynamic'; }}).forEach(function(f) {{
      onFieldChanged(product, f.depends_on);
      var el = document.getElementById('f_' + product + '_' + f.key);
      if (el && values[f.key] !== undefined) el.value = values[f.key];
    }});
  }} else {{
    var tenEl = document.getElementById('f_' + product + '_cong_nghe');
    if (tenEl && values.cong_nghe !== undefined) tenEl.value = values.cong_nghe;
  }}
  document.getElementById('ngang').value = values.ngang || '';
  document.getElementById('cao').value = values.cao || '';
  if (values.is_manual) {{
    document.getElementById('manual_on').checked = true;
    if (values.dongia) document.getElementById('gia_thu_cong').value = values.dongia.toLocaleString('vi-VN');
  }}
  updatePreview();
}}
document.addEventListener('DOMContentLoaded', updatePreview);
</script>"""

    if item:
        _area = (item["ngang_mm"] or 0) * (item["cao_mm"] or 0)
        _dongia = round(item["thanh_tien"] * 1_000_000 / _area) if (item["is_manual_price"] and _area) else 0
        prefill = f"""
<script>document.addEventListener('DOMContentLoaded', function() {{
  prefillEdit({json.dumps(item["product"], ensure_ascii=False)}, {{
    cong_nghe: {json.dumps(item["cong_nghe"] or "", ensure_ascii=False)},
    mau: {json.dumps(item["mau"] or "", ensure_ascii=False)},
    ngang: {item["ngang_mm"]}, cao: {item["cao_mm"]},
    is_manual: {1 if item["is_manual_price"] else 0}, dongia: {_dongia}
  }});
}});</script>"""
        form_action = f'/bao-gia/{quote["id"]}/hang-muc/{item["id"]}/sua'
        submit_label = "Lưu thay đổi"
    else:
        prefill = ""
        form_action = f'/bao-gia/{quote["id"]}/hang-muc/moi'
        submit_label = "Thêm hạng mục"

    return f"""
<form method="post" action="{form_action}">
  <label>Loại cửa</label>
  <div class="seg" style="flex-wrap:wrap">{"".join(radios)}</div>
  {"".join(blocks)}
  <div class="row">
    <div class="unit-field">
      <label>Ngang (mm)</label>
      <input id="ngang" name="ngang" inputmode="numeric"
        oninput="this.value=this.value.replace(/\\D/g,'');updatePreview()" placeholder="VD: 3000">
    </div>
    <div class="unit-field">
      <label>Cao (mm)</label>
      <input id="cao" name="cao" inputmode="numeric"
        oninput="this.value=this.value.replace(/\\D/g,'');updatePreview()" placeholder="VD: 2200">
    </div>
  </div>
  <div class="total" id="price_preview">Chọn đầy đủ để xem giá</div>
  <label class="u-manual-toggle"><input type="checkbox" id="manual_on" name="manual" value="1" onchange="updatePreview()"> Giá đặc biệt (nhập tay)</label>
  <div id="manual_price_wrap" style="display:none">
    <label>Đơn giá tay (đ/m²)</label>
    <input id="gia_thu_cong" name="gia_thu_cong" inputmode="numeric" oninput="fmtMoney(this);updatePreview()" placeholder="VD: 1.300.000">
  </div>
  <label>Ghi chú</label>
  <textarea name="ghi_chu" placeholder="VD: khách yêu cầu ray nhôm, lắp mặt trong...">{esc(item["ghi_chu"] or "") if item else ""}</textarea>
  <button class="btn big mt-4">{submit_label}</button>
</form>
{script}{prefill}"""


def quote_item_form_page(quote: dict, item: dict = None) -> str:
    return (
        f'<div class="card"><div class="name">{esc(quote["customer_name"])} '
        f'{type_chip(quote["customer_type"])}</div></div>'
        + _quote_item_form_body(quote, item)
    )


# ---------------------------------------------------------------- orders

# Sổ đơn hàng category tabs — short labels sized for 390px chips (the
# PRODUCT_LABELS strings are longer, e.g. "cửa nhôm kính").
_ORDER_FILTERS = (("", "Tất cả"), ("cua_cuon", "Cửa cuốn"),
                  ("cua_keo", "Cửa kéo"), ("nhom_kinh", "Nhôm kính"),
                  ("khac", "Khác"))


def orders_page(active: list, completed: list, today: str, loai: str = "",
                ngay: str = "") -> str:
    """Sổ đơn hàng — mirrors the paper order book: category chips (?loai=),
    Gấp pinned on top, then đang-làm orders grouped by ngày chốt (newest
    day first), đã hoàn thành archived below (collapsed). ``ngay`` (blank = mọi
    ngày) narrows to a single ngày-chốt, báo-cáo style. Đơn hàng only ever come
    from a báo giá đã chốt (store.create_order_from_quote); there's no manual
    "+ Đơn hàng" entry point."""
    if ngay:
        active = [o for o in active if (o.get("chot_date") or "") == ngay]
        completed = [o for o in completed if (o.get("chot_date") or "") == ngay]

    def _href(key: str) -> str:
        p = ([f"loai={key}"] if key else []) + ([f"ngay={ngay}"] if ngay else [])
        return "/don-hang" + ("?" + "&".join(p) if p else "")

    next_url = _href(loai)

    def _card(o: dict, dim: bool = False) -> str:
        badges = ""
        if o.get("urgent"):
            badges += '<span class="chip urgent">Gấp</span> '
        badges += stage_badge(o.get("stage") or "cho_san_xuat")
        if o["expiry_date"]:
            if o["expiry_date"] < today:
                badges += ' <span class="chip sent">Hết BH</span>'
            elif o["expiry_date"] <= _add_days(today, 30):
                badges += ' <span class="chip warn">Sắp hết BH</span>'
        bal = o.get("balance_vnd", 0) if o.get("customer_type") == "KH" else 0
        if bal > 0:
            badges += f' <span class="chip warn">còn nợ {fmt_vnd(bal)}</span>'
        elif o.get("customer_type") == "KH" and (o.get("value_vnd") or 0) > 0:
            # Paper book marks paid in red/green — no chip for DL (money lives
            # in /cong-no) or unpriced orders (nothing to have collected).
            badges += ' <span class="chip won">Đã thu đủ</span>'
        inst = f"lắp {fmt_date(o['install_date'])}" if o["install_date"] else "Chưa lắp"
        cls = " dim" if dim else ""
        actions = ""
        if bal > 0:
            actions = f"""
<div class="row mt-2">
  <details class="grow"><summary class="btn done w-full">Điều chỉnh</summary>
    <form method="post" action="/don-hang/{o["id"]}/thanh-toan" class="mt-2">
      <input type="hidden" name="next" value="{next_url}">
      <input name="amount_vnd" required inputmode="numeric" oninput="fmtMoney(this)" placeholder="Số tiền đã thu thêm">
      <button class="btn big mt-2">Ghi thanh toán</button>
    </form>
  </details>
  <form method="post" action="/don-hang/{o["id"]}/thu-du" class="grow"
    onsubmit="return confirm('Đánh dấu đã thu đủ đơn #{o["id"]}?')">
    <input type="hidden" name="next" value="{next_url}">
    <button class="btn call w-full">Đã thanh toán</button>
  </form>
</div>"""
        return f"""
<div class="card{cls}">
  <a href="/don-hang/{o["id"]}" style="display:block">
    <div class="name">{esc(o["customer_name"])}</div>
    <div class="sub">{badges}</div>
    <div class="sub">{esc(PRODUCT_LABELS.get(o["product"], "").capitalize())} — {fmt_vnd(o["value_vnd"])} — {inst}</div>
  </a>
  {actions}
</div>"""

    # Chip counts come from the UNFILTERED active list so the numbers always
    # agree with what each tab shows (same principle as the today_page counts).
    counts = Counter(o["product"] for o in active)
    n_all = len(active)
    has_khac = counts.get("khac", 0) > 0 or any(o["product"] == "khac" for o in completed)
    if loai:
        active = [o for o in active if o["product"] == loai]
        completed = [o for o in completed if o["product"] == loai]
    urgent = [o for o in active if o.get("urgent")]
    normal = [o for o in active if not o.get("urgent")]
    # Stable reverse sort: newest chốt day first; within a day the SQL
    # created_at ASC order survives (entries read top-down like the book).
    normal.sort(key=lambda o: o.get("chot_date") or "", reverse=True)

    chips = "".join(
        f'<a class="{"on" if loai == key else ""}" href="{_href(key)}">'
        f'{lbl} ({counts.get(key, 0) if key else n_all})</a>'
        for key, lbl in _ORDER_FILTERS if key != "khac" or has_khac)
    clear = f'<a class="btn done" href="{f"/don-hang?loai={loai}" if loai else "/don-hang"}">Mọi ngày</a>' if ngay else ""
    picker = f"""
<div class="filters">{chips}</div>
<form method="get" action="/don-hang" class="row mb-3">
  {f'<input type="hidden" name="loai" value="{esc(loai)}">' if loai else ""}
  <input type="date" name="ngay" value="{esc(ngay)}" class="grow-2">
  <button class="btn done">Xem</button>
  {clear}
</form>"""
    out = [picker]

    if urgent:
        out.append(f'<div class="day-h">Gấp ({len(urgent)})</div>'
                   f'<div class="cards-grid">{"".join(_card(o) for o in urgent)}</div>')
    if normal:
        for day, grp in groupby(normal, key=lambda o: o.get("chot_date") or ""):
            cards = "".join(_card(o) for o in grp)
            out.append(f'<div class="day-h">{_order_day_header(day, today)}</div>'
                       f'<div class="cards-grid">{cards}</div>')
    elif not urgent:
        label = PRODUCT_LABELS.get(loai, "")
        if ngay:
            out.append(f'<div class="empty">Không có đơn nào chốt {fmt_date(ngay)}.</div>')
        else:
            out.append(f'<div class="empty">Chưa có đơn {esc(label)} nào đang làm.</div>' if loai
                       else '<div class="empty">Chưa có đơn hàng nào đang làm.</div>')

    if completed:
        out.append(f"""
<details class="card mt-3">
  <summary>Đã hoàn thành ({len(completed)})</summary>
  <div class="cards-grid mt-2">{"".join(_card(o, dim=True) for o in completed)}</div>
</details>""")
    return "".join(out)


def _add_days(d: str, n: int) -> str:
    from datetime import datetime, timedelta
    return (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


def _order_day_header(day: str, today: str) -> str:
    """Sổ-style date header: '10/7 · Hôm nay', '9/7 · Hôm qua',
    '6/7 · 4 ngày trước' (≤7 days), then bare '19/6'; other years show
    '19/6/2025' so old orders stay unambiguous."""
    if not day:
        return "Không rõ ngày chốt"
    dm = f"{int(day[8:10])}/{int(day[5:7])}"
    if day[:4] != today[:4]:
        return f"{dm}/{day[:4]}"
    days = _days_since(day, today)
    if days == 0:
        return f"{dm} · Hôm nay"
    if days == 1:
        return f"{dm} · Hôm qua"
    if 1 < days <= 7:
        return f"{dm} · {days} ngày trước"
    return dm


def order_detail_page(o: dict, calls: list, today: str,
                      items: list = None, payments: list = None) -> str:
    label = PRODUCT_LABELS.get(o["product"], "sản phẩm")
    items = items or []
    payments = payments or []
    stage_now = o.get("stage") or "cho_san_xuat"

    # ── Hạng mục hóa đơn: display + inline edit + printable invoice ────────
    # Quote items only show as a read-only fallback (orders predating the
    # order_items backfill); edit/invoice work on real order items.
    is_order_items = bool(items) and "order_id" in items[0]
    edit_rows = ""
    if is_order_items:
        edit_rows = "".join(f"""
<form method="post" action="/don-hang/{o["id"]}/hang-muc/{i["id"]}/sua" class="row mt-2">
  <input name="description" value="{esc(i.get("description") or _door_desc(i))}" class="grow-3">
  <input name="so_luong" type="number" min="1" value="{i.get("so_luong", 1)}" style="flex:0 0 56px">
  <input name="thanh_tien" inputmode="numeric" value="{i["thanh_tien"]}" class="grow-2">
  <button class="btn done" style="flex:0 0 60px">Lưu</button>
</form>
<form method="post" action="/don-hang/{o["id"]}/hang-muc/{i["id"]}/xoa" class="row mt-1">
  <button class="btn danger w-full" style="font-size:12px">Xóa dòng trên</button>
</form>""" for i in items)
    item_editor = f"""
<details class="card"><summary>Sửa hạng mục / thêm dòng</summary>
{edit_rows}
<form method="post" action="/don-hang/{o["id"]}/hang-muc/moi" style="margin-top:12px;border-top:1px solid var(--line);padding-top:8px">
  <label>Nội dung dòng mới</label><input name="description" required placeholder="VD: Phí vận chuyển, Motor YH 300kg">
  <div class="row">
    <div class="grow"><label>SL</label><input name="so_luong" type="number" min="1" value="1"></div>
    <div class="grow-2"><label>Thành tiền (VND)</label><input name="thanh_tien" required inputmode="numeric" placeholder="VD: 500.000"></div>
  </div>
  <button class="btn big mt-3">+ Thêm dòng</button>
</form></details>"""
    hoa_don_btn = (f'<div class="row mt-2">'
                   f'<a class="btn copy" href="/don-hang/{o["id"]}/hoa-don">Xem hóa đơn</a></div>'
                   if is_order_items else "")
    bo_cua_block = ((f'<div class="card"><div class="sub strong">Hạng mục ({len(items)})</div>'
                     f'{bo_cua_html(items)}{hoa_don_btn}</div>' if items else "")
                    + item_editor)

    # ── Tiến độ: stage stepper + Gấp toggle ───────────────────────────────
    stages_row = "".join(
        f'<form method="post" action="/don-hang/{o["id"]}/giai-doan" class="flex grow">'
        f'<input type="hidden" name="stage" value="{k}">'
        f'<button class="btn {"call" if k == stage_now else "done"} w-full" style="font-size:13px"'
        f'{" disabled" if k == stage_now else ""}>{esc(lbl)}</button></form>'
        for k, lbl in STAGE_LABELS.items()
    )
    if o.get("urgent"):
        gap_btn = (f'<form method="post" action="/don-hang/{o["id"]}/gap" class="flex grow">'
                   f'<input type="hidden" name="urgent" value="0">'
                   f'<button class="btn done w-full">Bỏ đánh dấu Gấp</button></form>')
    else:
        gap_btn = (f'<form method="post" action="/don-hang/{o["id"]}/gap" class="flex grow">'
                   f'<input type="hidden" name="urgent" value="1">'
                   f'<button class="btn danger w-full">Đánh dấu Gấp (làm trước)</button></form>')
    urgent_banner = ('<div class="card" style="background:var(--danger-bg);border-left:3px solid var(--danger-text)">'
                     '<b>ĐƠN GẤP</b> — ưu tiên làm trước</div>' if o.get("urgent") else "")
    tien_do = f"""
<h2>Tiến độ sản xuất</h2>
<div class="card">
  <div class="sub mb-2">Giai đoạn hiện tại: {stage_badge(stage_now)}</div>
  <div class="row">{stages_row}</div>
  <div class="row mt-2">{gap_btn}</div>
</div>"""

    svc = "".join(
        f'<div class="card"><div class="sub">{fmt_date(s["call_date"])} — {esc(s["issue"])}'
        f'{" → " + esc(s["resolution"]) if s["resolution"] else ""}</div></div>'
        for s in calls
    )
    flags = []
    if o["install_date"]:
        if not o["checkin_done_at"]:
            flags.append(_post_btn(f'/don-hang/{o["id"]}/da-bao-tri', "Đã bảo trì 6T",
                                   next_url=f'/don-hang/{o["id"]}'))
        if not o["expiry_notified_at"]:
            flags.append(_post_btn(f'/don-hang/{o["id"]}/da-nhan-bh', "Đã nhắn BH",
                                   next_url=f'/don-hang/{o["id"]}'))
        if not o["review_requested_at"] and o["customer_type"] == "KH":
            flags.append(_post_btn(f'/don-hang/{o["id"]}/da-xin-danh-gia', "Đã xin đánh giá",
                                   next_url=f'/don-hang/{o["id"]}'))
    else:
        flags.append(f"""
<details class="card grow"><summary>Ghi ngày lắp đặt</summary>
<form method="post" action="/don-hang/{o["id"]}/ngay-lap">
  <input type="date" name="install_date" required value="{today}">
  <button class="btn big mt-2">Lưu ngày lắp</button>
</form></details>""")

    debt_link = ""
    if o["customer_type"] == "DL":
        debt_link = (f'<a class="btn done" href="/cong-no/{o["customer_id"]}/them?loai=charge'
                     f'&don={o["id"]}&tien={o["value_vnd"] or ""}">Ghi nợ đơn này</a>')

    # ── Thanh toán / còn nợ (KH only) ─────────────────────────────────────
    # Retail jobs track cọc + thanh toán here; dealers (ĐL) use công nợ (above).
    payment_block = ""
    if o["customer_type"] == "KH":
        paid, bal = o.get("paid_vnd", 0), o.get("balance_vnd", 0)
        kind_lbl = {"coc": "Cọc", "thanh_toan": "Thanh toán"}
        pay_rows = "".join(
            '<div class="sub between">'
            f'<span>{fmt_date(p["pay_date"])} · {kind_lbl.get(p["kind"], p["kind"])}'
            f'{" · " + esc(p["method"]) if p.get("method") else ""}</span>'
            '<span style="display:flex;align-items:center;gap:8px">'
            f'<b class="amt">{fmt_vnd(p["amount_vnd"])}</b>'
            f'<form method="post" action="/don-hang/{o["id"]}/thanh-toan/{p["id"]}/xoa" '
            "onsubmit=\"return confirm('Xóa khoản này? Đơn sẽ trở lại còn nợ.')\">"
            f'<input type="hidden" name="next" value="/don-hang/{o["id"]}">'
            '<button title="Xóa khoản thanh toán" '
            'style="background:none;border:none;color:var(--danger-text);cursor:pointer;font-size:13px;padding:0">Xóa</button>'
            "</form></span></div>"
            for p in payments
        ) or '<div class="sub">Chưa có thanh toán nào.</div>'
        payment_block = f"""
<h2>Thanh toán</h2>
<div class="card">
  <div class="sub between"><span>Giá trị đơn (gồm VAT)</span><b class="amt">{fmt_vnd(o["value_vnd"])}</b></div>
  <div class="sub between"><span>Đã thu</span><b class="amt">{fmt_vnd(paid)}</b></div>
  <div class="total between mt-2">
    <span>Còn lại</span><span class="amt">{fmt_vnd(bal)}</span></div>
  <div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--line)">{pay_rows}</div>
</div>
<details class="card"><summary>+ Ghi thanh toán</summary>
<form method="post" action="/don-hang/{o["id"]}/thanh-toan" class="mt-2">
  <div class="row">
    <div class="grow"><label class="mt-0">Loại</label>
      <select name="kind"><option value="thanh_toan">Thanh toán</option><option value="coc">Cọc</option></select></div>
    <div class="grow"><label class="mt-0">Hình thức</label>
      <select name="method"><option value="Tiền mặt">Tiền mặt</option><option value="Chuyển khoản">Chuyển khoản</option><option value="ZaloPay">ZaloPay</option></select></div>
  </div>
  <label>Số tiền (VND)</label>
  <input name="amount_vnd" required inputmode="numeric" oninput="fmtMoney(this)" placeholder="VD: 5.000.000">
  <label>Ngày</label><input type="date" name="pay_date" value="{today}">
  <button class="btn big mt-3">+ Ghi thanh toán</button>
</form></details>"""

    return f"""
{urgent_banner}
<div class="card">
  <div class="name">{esc(o["customer_name"])} {type_chip(o["customer_type"])} {stage_badge(stage_now)}</div>
  <div class="sub">{esc(label.capitalize())} — {fmt_vnd(o["value_vnd"])}</div>
  <div class="sub">{"Lắp " + fmt_date(o["install_date"]) if o["install_date"] else "Chưa lắp"}
   · BH {o["warranty_months"]} tháng{f" · hết BH {fmt_date(o['expiry_date'])}" if o["expiry_date"] else ""}</div>
  {f'<div class="sub">Đ/c: {esc(o["address"])}</div>' if o.get("address") else ""}
  {f'<div class="sub">Ghi chú: {esc(o["note"])}</div>' if o.get("note") else ""}
  {contact_buttons(o["phone"], o["zalo_phone"])}
  <div class="row"><a class="btn done" href="/khach/{o["customer_id"]}">Xem khách</a>{debt_link}</div>
</div>
<details class="card"><summary>Sửa ghi chú</summary>
<form method="post" action="/don-hang/{o["id"]}/ghi-chu" class="mt-2">
  <textarea name="note" placeholder="VD: nhà trong hẻm, gọi trước khi tới, khách cần lắp buổi sáng">{esc(o.get("note") or "")}</textarea>
  <button class="btn big mt-2">Lưu ghi chú</button>
</form></details>
<details class="card"><summary class="text-danger">Xóa đơn hàng</summary>
  <div class="sub mt-2">Xóa hạng mục, thanh toán, sửa chữa của đơn này. Báo giá gốc quay lại "Đã gửi" để chốt lại nếu cần. Lịch sử chăm sóc của khách không bị mất.</div>
  <form method="post" action="/don-hang/{o["id"]}/xoa" class="mt-2"
    onsubmit="return confirm('Xóa đơn hàng #{o["id"]}? Không thể hoàn tác.')">
    <button class="btn danger w-full">Xóa đơn hàng #{o["id"]}</button>
  </form>
</details>
{bo_cua_block}
{payment_block}
{tien_do}
<h2>Lắp đặt & bảo hành</h2>
<div class="row">{"".join(flags)}</div>
<h2>Sửa chữa / bảo hành ({len(calls)})</h2>
{svc or '<div class="empty">Chưa có lần sửa nào.</div>'}
<details class="card"><summary>+ Ghi sửa chữa</summary>
<form method="post" action="/don-hang/{o["id"]}/sua-chua">
  <label>Vấn đề</label><input name="issue" required placeholder="VD: thay pin remote, cửa kêu">
  <label>Xử lý</label><input name="resolution" placeholder="VD: đã thay pin, tra dầu">
  <button class="btn big mt-3">Lưu</button>
</form></details>"""


def invoice_page(o: dict, items: list, company: dict, today: str) -> str:
    """Printable hóa đơn for an order: company header, customer block, line-item
    table, Tổng cộng / VAT 10% / Tổng tiền (same math as the báo giá summary and
    the báo giá xlsx). Internal document — real VAT e-invoices (hoá đơn đỏ) come
    from the government e-invoice provider."""
    rows = []
    for idx, i in enumerate(items, 1):
        kich = (f' — {i["ngang_mm"]}×{i["cao_mm"]}mm'
                if i.get("ngang_mm") and i.get("cao_mm") else "")
        mau_sac = f' · {esc(i["mau_sac"])}' if i.get("mau_sac") else ""
        note = (f'<br><span style="opacity:.7;font-size:.9em">Ghi chú: {esc(i["ghi_chu"])}</span>'
                if i.get("ghi_chu") else "")
        sl = i.get("so_luong", 1) or 1
        don_gia = i.get("don_gia") if i.get("don_gia") is not None else i["thanh_tien"] // sl
        rows.append(
            f'<tr><td>{idx}</td><td>{esc(_door_desc(i))}{kich}{mau_sac}{note}</td>'
            f'<td class="num">{sl}</td><td class="num">{fmt_vnd(don_gia)}</td>'
            f'<td class="num">{fmt_vnd(i["thanh_tien"])}</td></tr>'
        )
    tong_cong = sum(i["thanh_tien"] for i in items)
    vat = round(tong_cong * 0.1)
    tong_tien = tong_cong + vat
    contact = " · ".join(x for x in (company.get("phone"), company.get("address")) if x)
    kh_lines = "".join(
        f'<div class="sub">{label} {esc(val)}</div>'
        for label, val in (("KH:", o.get("customer_name")), ("SĐT:", o.get("phone")),
                           ("Email:", o.get("email")), ("Đ/c:", o.get("address")))
        if val
    )
    return f"""
<div class="inv">
<div class="card">
  <div style="font-size:20px;font-weight:700;color:var(--brand)">{esc(company.get("name", ""))}</div>
  <div class="sub">{esc(company.get("tagline", ""))}</div>
  {f'<div class="sub">{esc(contact)}</div>' if contact else ""}
  <div class="sub strong mt-2">HÓA ĐƠN #{o["id"]} — ngày {fmt_date(o.get("install_date") or today)}</div>
  {kh_lines}
  <table>
    <tr><th>STT</th><th>Nội dung</th><th class="num">SL</th><th class="num">Đơn giá</th><th class="num">Thành tiền</th></tr>
    {"".join(rows) or '<tr><td colspan="5">Chưa có hạng mục nào.</td></tr>'}
    <tr class="totals"><td colspan="3"></td><td class="num">Tổng cộng</td><td class="num">{fmt_vnd(tong_cong)}</td></tr>
    <tr class="totals"><td colspan="3"></td><td class="num">VAT 10%</td><td class="num">{fmt_vnd(vat)}</td></tr>
    <tr class="totals"><td colspan="3"></td><td class="num"><b>Tổng tiền</b></td><td class="num"><b>{fmt_vnd(tong_tien)}</b></td></tr>
  </table>
  <div class="row no-print mt-3">
    <button type="button" class="btn copy" onclick="window.print()">In hóa đơn</button>
    <a class="btn done" href="/don-hang/{o["id"]}">← Về đơn hàng</a>
  </div>
</div>
</div>"""


# ---------------------------------------------------------------- công nợ

def _settle_form(action: str, next_url: str) -> str:
    """The Nợ-tab 'Đã thanh toán' control: a disclosure that opens a small form
    for hình thức (tiền mặt / chuyển khoản) + ghi chú before recording the
    full-balance payment. Posts to the same settle endpoint as the old button."""
    return f"""
<details class="mt-2">
  <summary class="btn done w-full">Đã thanh toán</summary>
  <form method="post" action="{action}" class="mt-2">
    <input type="hidden" name="next" value="{esc(next_url)}">
    <label class="mt-0">Hình thức thanh toán</label>
    <div class="seg">
      <label><input type="radio" name="method" value="Tiền mặt" checked><span>Tiền mặt</span></label>
      <label><input type="radio" name="method" value="Chuyển khoản"><span>Chuyển khoản</span></label>
    </div>
    <label>Ghi chú</label>
    <input name="note" placeholder="Tùy chọn — VD: thu đủ tại xưởng">
    <button class="btn call w-full mt-3">Xác nhận đã thu đủ</button>
  </form>
</details>"""


def debts_page(dealer_rows: list, customer_rows: list, loc: str, today: str,
               settlements: list = None, day: str = "") -> str:
    """Công nợ tab. ``loc`` filters the view: 'tat-ca' (both), 'kh' (retail
    khách lẻ — orders still owing, from order_payments), 'dl' (đại lý ledger) or
    'da-thu' (Đã thu — the record of settled money, grouped by day). Dealers link
    to their sổ nợ; each unpaid KH order gets a Đã thanh toán button that settles
    the full remaining balance."""
    seg = "".join(
        f'<a class="btn {"call" if loc == key else "done"}" href="/cong-no?loc={key}">{lbl}</a>'
        for key, lbl in (("tat-ca", "Tất cả"), ("kh", "Khách hàng"),
                         ("dl", "Đại lý"), ("da-thu", "Đã thu"))
    )
    out = [f'<div class="row mb-3">{seg}</div>']

    if loc == "da-thu":
        out.append(_settlements_block(settlements or [], day, today))
        return "".join(out)

    if loc in ("tat-ca", "dl"):
        if loc == "tat-ca":
            out.append("<h2>Đại lý (sổ nợ)</h2>")
        cards = []
        for d in dealer_rows:
            days = _days_since(d["last_payment"] or d["first_charge"], today)
            style = ' style="border-left:3px solid var(--danger-text)"' if days >= 30 else ""
            last = f"trả lần cuối {fmt_date(d['last_payment'])}" if d["last_payment"] else "chưa trả lần nào"
            cards.append(f"""
<div class="card"{style}>
  <a href="/cong-no/{d["id"]}">
    <div class="name">{esc(d["name"])} <span class="chip warn">{fmt_vnd(d["balance"])}</span> {type_chip("DL")}</div>
    <div class="sub">{last} ({days} ngày)</div>
  </a>
  {_settle_form(f'/cong-no/{d["id"]}/thanh-toan-du', f'/cong-no?loc={loc}')}
</div>""")
        out.append("".join(cards) or '<div class="empty">Không có đại lý nào đang nợ.</div>')

    if loc in ("tat-ca", "kh"):
        if loc == "tat-ca":
            out.append("<h2>Khách lẻ (đơn chưa thu đủ)</h2>")
        cards = []
        for o in customer_rows:
            when = f"lắp {fmt_date(o['install_date'])}" if o["install_date"] else "chưa lắp"
            cards.append(f"""
<div class="card">
  <a href="/don-hang/{o["id"]}">
    <div class="name">{esc(o["customer_name"])} <span class="chip warn">{fmt_vnd(o["balance_vnd"])}</span> {type_chip("KH")}</div>
    <div class="sub">Đơn #{o["id"]} · {when}</div>
  </a>
  {_settle_form(f'/don-hang/{o["id"]}/thu-du', f'/cong-no?loc={loc}')}
</div>""")
        out.append("".join(cards) or '<div class="empty">Không có khách lẻ nào còn nợ.</div>')

    return "".join(out)


def _settlement_card(r: dict) -> str:
    """One Đã-thu row: a fully-paid KH đơn (links to the order) or a ĐL công nợ
    payment (links to that dealer's sổ nợ)."""
    if r["kind"] == "KH":
        link = f'/don-hang/{r["ref"]}'
        meth = f' · {esc(r["method"])}' if (r.get("method") or "").strip() else ""
        sub = f'Đơn #{r["ref"]} · đã thu đủ{meth}'
        chip = type_chip("KH")
    else:
        link = f'/cong-no/{r["ref"]}'
        meth = f' · {esc(r["method"])}' if (r.get("method") or "").strip() else ""
        note = f' · {esc(r["note"])}' if (r.get("note") or "").strip() else ""
        sub = f'Thanh toán{meth}{note}'
        chip = type_chip("DL")
    return f"""
<div class="card">
  <a href="{link}">
    <div class="name">{esc(r["name"])} <span class="chip won">{fmt_vnd(r["amount_vnd"])}</span> {chip}</div>
    <div class="sub">{sub}</div>
  </a>
</div>"""


def _settlements_block(rows: list, day: str, today: str) -> str:
    """Đã thu tab: a date picker (blank = mọi ngày) over the settled-money record,
    grouped by ngày thu (newest first) with a per-day subtotal — mirrors how Đơn
    hàng groups by ngày chốt and how báo cáo totals tiền thu trong ngày."""
    picker = f"""
<form method="get" action="/cong-no" class="row mb-3">
  <input type="hidden" name="loc" value="da-thu">
  <input type="date" name="ngay" value="{esc(day)}" class="grow-2">
  <button class="btn done">Xem</button>
  {'<a class="btn done" href="/cong-no?loc=da-thu">Mọi ngày</a>' if day else ''}
</form>"""
    if not rows:
        empty = ('<div class="empty">Chưa thu khoản nào ngày này.</div>' if day
                 else '<div class="empty">Chưa có khoản đã thu nào.</div>')
        return picker + empty
    parts = [picker]
    for d, grp in groupby(rows, key=lambda r: r.get("pay_date") or ""):
        grp = list(grp)
        total = sum(r["amount_vnd"] for r in grp)
        parts.append(f'<div class="day-h">{_order_day_header(d, today)} — {fmt_vnd(total)}</div>'
                     f'<div class="cards-grid">{"".join(_settlement_card(r) for r in grp)}</div>')
    return "".join(parts)


def _days_since(d: str, today: str) -> int:
    from datetime import datetime
    return (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(d, "%Y-%m-%d")).days


def ledger_page(c: dict, entries: list, balance: int) -> str:
    rows = "".join(f"""
<tr><td>{fmt_date(e["entry_date"])}</td><td>{esc(e["note"] or ("Đơn #" + str(e["order_id"]) if e["order_id"] else ""))}</td>
<td class="{"pos" if e["entry_type"] == "charge" else "neg"}">
{"+" if e["entry_type"] == "charge" else "−"}{fmt_vnd(e["amount_vnd"])}</td>
<td class="run">{fmt_vnd(e["running"])}</td>
<td><form method="post" action="/cong-no/{c["id"]}/xoa/{e["id"]}" style="display:inline"
  onsubmit="return confirm('Xóa dòng này khỏi sổ nợ? Không thể hoàn tác.')">
  <button class="btn danger" style="padding:2px 8px">Xóa</button></form></td></tr>""" for e in entries)
    msg = render("debt_reminder", ten=c["name"], so_tien=fmt_vnd(balance)) if balance > 0 else ""
    settle = (f"""
<form method="post" action="/cong-no/{c["id"]}/thanh-toan-du" class="mb-3"
  onsubmit="return confirm('{esc(c["name"])} đã trả đủ {fmt_vnd(balance)}? Sổ nợ sẽ về 0.')">
  <button class="btn done w-full">Đã thanh toán đủ</button>
</form>""" if balance > 0 else "")
    return f"""
<div class="total">Hiện nợ: {fmt_vnd(balance)}</div>
<div class="card">
  <div class="name">{esc(c["name"])}</div>
  <div class="sub">{esc(c["phone"] or "")}</div>
  {contact_buttons(c["phone"], c.get("zalo_phone") or "", msg)}
</div>
<div class="row mb-3">
  <a class="btn danger" href="/cong-no/{c["id"]}/them?loai=charge">+ Ghi nợ</a>
  <a class="btn call" href="/cong-no/{c["id"]}/them?loai=payment">+ Thanh toán</a>
</div>
{settle}
<div class="card scroll-x">
<table class="ledger"><tr><th>Ngày</th><th>Nội dung</th><th>Số tiền</th><th style="text-align:right">Còn nợ</th><th></th></tr>
{rows or '<tr><td colspan="5">Chưa có ghi chép nào.</td></tr>'}</table>
</div>"""


def debt_entry_form_page(c: dict, loai: str, today: str, order_id: str = "", tien: str = "") -> str:
    val = fmt_vnd(int(tien))[:-1] if tien.isdigit() else ""
    return f"""
<div class="card"><div class="name">{esc(c["name"])}</div></div>
<form method="post" action="/cong-no/{c["id"]}/them">
  {f'<input type="hidden" name="order_id" value="{esc(order_id)}">' if order_id else ""}
  <label>Loại</label>
  <div class="seg">
    <label><input type="radio" name="entry_type" value="charge" {"checked" if loai != "payment" else ""}><span>Ghi nợ</span></label>
    <label><input type="radio" name="entry_type" value="payment" {"checked" if loai == "payment" else ""}><span>Thanh toán</span></label>
  </div>
  <label>Số tiền (VND) *</label>
  <input name="amount_vnd" required inputmode="numeric" oninput="fmtMoney(this)" value="{esc(val)}">
  <label>Ngày</label>
  <input type="date" name="entry_date" value="{today}" required>
  <label>Nội dung</label>
  <input name="note" placeholder="VD: 5 bộ cửa cuốn / chuyển khoản">
  <button class="btn big mt-4">Lưu</button>
</form>"""


# ---------------------------------------------------------------- báo cáo

def _report_toggle(active: str) -> str:
    """Ngày / Tháng switch shared by both báo cáo modes."""
    return '<div class="row mb-3">' + "".join(
        f'<a class="btn {"call" if active == key else "done"}" href="/bao-cao?che_do={key}">{lbl}</a>'
        for key, lbl in (("ngay", "Theo ngày"), ("thang", "Theo tháng"))
    ) + "</div>"


def reports_page(r: dict) -> str:
    """Daily by default (end-of-day reconciliation); monthly when the toggle is
    set to Theo tháng."""
    return _daily_report_page(r) if r.get("mode") == "day" else _monthly_report_page(r)


def _daily_report_page(r: dict) -> str:
    day = r["day"]
    ty_le = f"{round(r['ty_le_chot'] * 100)}%" if r["ty_le_chot"] is not None else "—"

    thu_cards = "".join(
        f'<div class="stat-card"><div class="n">{fmt_vnd_short(x["total"])}</div>'
        f'<div class="lbl">{esc(x["method"])}</div></div>'
        for x in r["thu_theo_hinh_thuc"]
    ) or '<div class="empty">Chưa thu khoản nào trong ngày.</div>'

    kind_lbl = {"coc": "Cọc", "thanh_toan": "Thanh toán"}
    pay_rows = "".join(
        f'<tr><td>{esc(p["name"])}</td>'
        f'<td>{esc((p["method"] or "").strip() or "—")}</td>'
        f'<td>{kind_lbl.get(p["kind"], p["kind"])}</td>'
        f'<td class="run">{fmt_vnd(p["amount_vnd"])}</td></tr>'
        for p in r["thu_list"]
    )
    pay_block = (
        '<div class="card scroll-x"><table class="ledger">'
        '<tr><th>Khách</th><th>Hình thức</th><th>Loại</th><th style="text-align:right">Số tiền</th></tr>'
        f'{pay_rows}</table></div>'
        if r["thu_list"] else ""
    )

    stat_row = f"""
<div class="stat-row">
  <div class="stat-card"><div class="n">{r["gui"]}</div><div class="lbl">Báo giá đã gửi</div></div>
  <div class="stat-card"><div class="n">{r["chot"]}</div><div class="lbl">Đã chốt</div></div>
  <div class="stat-card"><div class="n">{r["mat"]}</div><div class="lbl">Đã mất</div></div>
  <div class="stat-card"><div class="n">{ty_le}</div><div class="lbl">Tỷ lệ chốt</div></div>
</div>
<div class="total">Giá trị chốt trong ngày: {fmt_vnd(r["gia_tri_chot"])}</div>"""

    sp_rows = "".join(
        f'<tr><td>{esc(PRODUCT_LABELS.get(x["product"], x["product"]))}</td><td>{fmt_vnd(x["total"])}</td></tr>'
        for x in r["doanh_thu_theo_sp"]
    )
    sp_block = (
        f'<h2>Doanh thu theo sản phẩm</h2><div class="card scroll-x">'
        f'<table class="ledger"><tr><th>Sản phẩm</th><th>Doanh thu</th></tr>{sp_rows}</table></div>'
        if r["doanh_thu_theo_sp"] else ""
    )

    return f"""
{_report_toggle("ngay")}
<form method="get" action="/bao-cao" class="row mb-3">
  <input type="hidden" name="che_do" value="ngay">
  <input type="date" name="ngay" value="{esc(day)}" class="grow-2">
  <button class="btn done">Xem</button>
</form>
<div class="card"><div class="name">Báo cáo ngày {fmt_date(day)}</div></div>
<h2>Tiền thu trong ngày — {fmt_vnd(r["thu_total"])}</h2>
<div class="stat-row">{thu_cards}</div>
{pay_block}
<h2>Hoạt động trong ngày</h2>
{stat_row}
{sp_block}"""


def _monthly_report_page(r: dict) -> str:
    month = r["month"]
    display_month = f"{month[5:7]}/{month[:4]}" if len(month) == 7 else esc(month)
    ty_le = f"{round(r['ty_le_chot'] * 100)}%" if r["ty_le_chot"] is not None else "—"
    avg_days = f"{r['avg_days_to_close']} ngày" if r["avg_days_to_close"] is not None else "—"

    stat_row = f"""
<div class="stat-row">
  <div class="stat-card"><div class="n">{r["gui"]}</div><div class="lbl">Báo giá đã gửi</div></div>
  <div class="stat-card"><div class="n">{r["chot"]}</div><div class="lbl">Đã chốt</div></div>
  <div class="stat-card"><div class="n">{r["mat"]}</div><div class="lbl">Đã mất</div></div>
  <div class="stat-card"><div class="n">{ty_le}</div><div class="lbl">Tỷ lệ chốt</div></div>
</div>
<div class="total">Tổng giá trị chốt: {fmt_vnd(r["gia_tri_chot"])} — Trung bình gửi→chốt: {avg_days}</div>"""

    ly_do_rows = "".join(
        f'<tr><td>{esc(LOST_REASON_LABELS.get(x["reason"], x["reason"]))}</td><td>{x["n"]}</td></tr>'
        for x in r["ly_do_mat"]
    )
    ly_do_block = (
        f'<h2>Lý do mất</h2><div class="card scroll-x">'
        f'<table class="ledger"><tr><th>Lý do</th><th>Số lượng</th></tr>{ly_do_rows}</table></div>'
        if r["ly_do_mat"] else
        '<h2>Lý do mất</h2><div class="empty">Không có báo giá mất trong tháng.</div>'
    )

    sp_rows = "".join(
        f'<tr><td>{esc(PRODUCT_LABELS.get(x["product"], x["product"]))}</td><td>{fmt_vnd(x["total"])}</td></tr>'
        for x in r["doanh_thu_theo_sp"]
    )
    sp_block = (
        f'<h2>Doanh thu theo sản phẩm</h2><div class="card scroll-x">'
        f'<table class="ledger"><tr><th>Sản phẩm</th><th>Doanh thu</th></tr>{sp_rows}</table></div>'
        if r["doanh_thu_theo_sp"] else
        '<h2>Doanh thu theo sản phẩm</h2><div class="empty">Chưa có đơn chốt trong tháng.</div>'
    )

    cong_no_block = f"""
<h2>Công nợ</h2>
<div class="stat-row">
  <div class="stat-card"><div class="n">{fmt_vnd_short(r["cong_no_total"])}</div><div class="lbl">Tổng dư nợ hiện tại</div></div>
  <div class="stat-card"><div class="n">{r["cong_no_qua_han"]}</div><div class="lbl">Đại lý quá 30 ngày chưa trả</div></div>
</div>"""

    cs_rows = "".join(
        f'<tr><td>{esc(_TOUCH_KIND_LABELS.get(x["kind"], x["kind"]))}</td><td>{x["n"]}</td></tr>'
        for x in r["cham_soc"]
    )
    cs_block = (
        f'<h2>Chăm sóc khách hàng</h2><div class="card scroll-x">'
        f'<table class="ledger"><tr><th>Loại</th><th>Số lượt</th></tr>{cs_rows}</table></div>'
        if r["cham_soc"] else
        '<h2>Chăm sóc khách hàng</h2><div class="empty">Chưa có lượt chăm sóc nào trong tháng.</div>'
    )

    return f"""
{_report_toggle("thang")}
<form method="get" action="/bao-cao" class="row mb-3">
  <input type="hidden" name="che_do" value="thang">
  <input type="month" name="thang" value="{esc(month)}" class="grow-2">
  <button class="btn done">Xem</button>
</form>
<div class="card"><div class="name">Báo cáo tháng {display_month}</div></div>
{stat_row}
{ly_do_block}
{sp_block}
{cong_no_block}
{cs_block}"""


# ---------------------------------------------------------------- Zalo OA admin

def zalo_admin_page(connected: bool, configured: bool, unlinked: list,
                    bot_configured: bool = False, bot_health: dict = None) -> str:
    if not configured:
        status = '<div class="card"><div class="sub">Chưa có ZALO_APP_ID/ZALO_APP_SECRET trong .env</div></div>'
    elif connected:
        status = ('<div class="card"><div class="sub"><span class="chip won">Đã kết nối</span> Zalo OA</div>'
                  '<a class="btn done w-full mt-2" href="/zalo/oauth/start">'
                  'Kết nối lại</a></div>')
    else:
        status = ('<div class="card"><div class="sub">Chưa kết nối — bấm để đăng nhập Zalo và cấp quyền</div>'
                  '<a class="btn zalo w-full mt-2" href="/zalo/oauth/start">'
                  'Kết nối Zalo OA</a></div>')

    if not bot_configured:
        bot_status = '<div class="card"><div class="sub">Chưa cấu hình BOT_URL/BOT_TOKEN trong .env</div></div>'
    elif bot_health is None:
        bot_status = ('<div class="card"><div class="sub"><span class="chip warn">Không kết nối được</span> '
                      'kiểm tra container htp-crm-bot</div></div>')
    elif bot_health.get("awaitingQR"):
        bot_status = ('<div class="card"><div class="sub">Đang chờ quét mã QR — dùng tài khoản Zalo phụ để quét</div>'
                      '<img src="/zalo/bot/qr" style="max-width:240px;display:block;margin-top:8px" /></div>')
    elif bot_health.get("loggedIn"):
        bot_status = ('<div class="card"><div class="sub"><span class="chip won">Đã kết nối</span> Bot nhóm</div>'
                      '<form method="post" action="/zalo/bot/gui" class="mt-2">'
                      '<button class="btn done w-full" type="submit">'
                      'Gửi bảng công việc vào nhóm</button></form></div>')
    else:
        bot_status = '<div class="card"><div class="sub"><span class="chip warn">Mất kết nối</span> Bot nhóm</div></div>'
    bot_html = f"<h2>Bot nhóm</h2>{bot_status}"

    if unlinked:
        rows = "".join(f"""
<div class="card">
  <div class="sub">{esc(e["event_name"])} — {fmt_date(e["received_at"])}</div>
  {f'<div class="sub">"{esc(e["text"])}"</div>' if e.get("text") else ""}
  <div class="sub" style="word-break:break-all">id: {esc(e["zalo_user_id"])}</div>
</div>""" for e in unlinked)
        unlinked_html = f"""<h2>Tin nhắn Zalo chưa gắn khách ({len(unlinked)})</h2>
<div class="card"><div class="sub">Vào trang khách hàng tương ứng → "Gắn Zalo" → dán id ở trên.</div></div>
{rows}"""
    else:
        unlinked_html = '<h2>Tin nhắn Zalo chưa gắn khách</h2><div class="empty">Chưa có tin nhắn nào.</div>'

    return f"{status}{bot_html}{unlinked_html}"
