"""Server-rendered HTML for Sổ Thu Chi (same pattern as htp-crm/views.py: inline
HTML strings, no template engine). 100% Vietnamese, phone-first: single column,
>=48px touch targets, fixed bottom nav.

North star is "nhập 10 giây" — the quick-entry form sits at the top of the day
view, opens focused on the amount, and needs one tap to save. Everything else on
the page is reading, not writing.
"""
import hashlib
import html
from datetime import datetime, timedelta
from pathlib import Path

from store import METHOD_LABELS, METHODS


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


# ---- asset fingerprint (same trick as the CRM) --------------------------------
# New bytes => new URL => guaranteed miss in both the HTTP cache and the service
# worker cache, so a deploy can never leave a phone on stale CSS.
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

# Inline SVG favicon — teal "TC" tile, matching the PWA icons. Data URI so
# /favicon.ico never 404s and no extra request is made.
FAVICON = (
    "<link rel=\"icon\" href=\"data:image/svg+xml,"
    "%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2032%2032'%3E"
    "%3Crect%20width='32'%20height='32'%20rx='6'%20fill='%230e6866'/%3E"
    "%3Ctext%20x='16'%20y='23'%20font-family='Arial,sans-serif'%20font-size='15'"
    "%20font-weight='bold'%20fill='%23fff'%20text-anchor='middle'%3ETC%3C/text%3E"
    "%3C/svg%3E\">"
)

PWA_HEAD = (
    '<link rel="manifest" href="/manifest.webmanifest">'
    '<meta name="theme-color" content="#0e6866">'
    '<meta name="mobile-web-app-capable" content="yes">'
    '<meta name="apple-mobile-web-app-capable" content="yes">'
    '<meta name="apple-mobile-web-app-status-bar-style" content="default">'
    '<meta name="apple-mobile-web-app-title" content="Thu Chi">'
    '<link rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png">'
    "<script>if('serviceWorker' in navigator){"
    "addEventListener('load',function(){navigator.serviceWorker.register('/sw.js')})}</script>"
)

_FONT_PRELOAD = (
    '<link rel="preload" href="/static/fonts/be-vietnam-pro-v12-latin_vietnamese-regular.woff2"'
    ' as="font" type="font/woff2" crossorigin>'
    '<link rel="preload" href="/static/fonts/be-vietnam-pro-v12-latin_vietnamese-600.woff2"'
    ' as="font" type="font/woff2" crossorigin>'
)

_MONTHS_VI = ["", "tháng 1", "tháng 2", "tháng 3", "tháng 4", "tháng 5", "tháng 6",
              "tháng 7", "tháng 8", "tháng 9", "tháng 10", "tháng 11", "tháng 12"]
_WEEKDAYS_VI = ["Thứ hai", "Thứ ba", "Thứ tư", "Thứ năm", "Thứ sáu", "Thứ bảy", "Chủ nhật"]

# CRM payment kinds -> what the family calls them.
_CRM_KIND_LABELS = {"coc": "đặt cọc", "thanh_toan": "thanh toán"}


def fmt_vnd(n) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}".replace(",", ".") + "đ"


def fmt_date(d) -> str:
    """'2026-07-23' -> '23/07/2026'."""
    if not d:
        return "—"
    p = str(d)[:10].split("-")
    return f"{p[2]}/{p[1]}/{p[0]}" if len(p) == 3 else esc(d)


def _shift_day(day: str, delta: int) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=delta)).strftime("%Y-%m-%d")


def _shift_month(month: str, delta: int) -> str:
    y, m = int(month[:4]), int(month[5:7])
    total = y * 12 + (m - 1) + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _day_title(day: str) -> str:
    d = datetime.strptime(day, "%Y-%m-%d")
    return f"{_WEEKDAYS_VI[d.weekday()]}, {d.day} {_MONTHS_VI[d.month]} {d.year}"


def page(title: str, body: str, active: str = "", user: str = "", doc_title: str = "") -> str:
    """``title`` is the top-bar label; ``doc_title`` is the browser/tab title when
    the two should differ (the day view brands the bar "Sổ Thu Chi" but the tab
    should say which screen it is)."""
    tabs = [("/ngay", "Hôm nay"), ("/thang", "Tháng")]
    links = "".join(
        f'<a href="{href}" class="{"on" if href == active else ""}">{label}</a>'
        for href, label in tabs
    )
    who = f'<span class="who">{esc(user)}</span>' if user else ""
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(doc_title or title)} — Sổ Thu Chi</title>
{_FONT_PRELOAD}
<link rel="stylesheet" href="{CSS_URL}">{FAVICON}{PWA_HEAD}</head>
<body>
<div class="top">{esc(title)}<span>{who} <a href="/logout" onclick="event.preventDefault();document.getElementById('lo').submit()">Thoát</a></span></div>
<form id="lo" method="post" action="/logout" hidden></form>
<div class="wrap">{body}</div>
<nav class="nav">{links}</nav>
<script src="{JS_URL}"></script>
</body></html>"""


def login_page(error: str = "") -> str:
    """Đăng nhập. The password field turns off every keyboard "helper" on purpose:
    the family types on phones with a Vietnamese IME, and autocapitalize/autocorrect
    silently mangling the first character is indistinguishable from a wrong password.
    autocomplete=current-password lets the phone remember it so they stop retyping."""
    messages = {
        "1": "Sai mật khẩu, thử lại nhé.",
        "cho": "Sai nhiều lần quá — đợi một phút rồi thử lại nhé.",
    }
    msg = messages.get(error, "")
    err = f'<div class="callout callout-danger">{msg}</div>' if msg else ""
    body = f"""
<div class="card login-card">
  <div class="login-brand">Hưng Thành Phát</div>
  <div class="login-sub">Sổ Thu Chi</div>
  {err}
  <form method="post" action="/login">
    <label>Mật khẩu của bạn</label>
    <input name="password" type="password" autofocus
           autocapitalize="none" autocorrect="off" spellcheck="false"
           autocomplete="current-password">
    <button class="btn big mt-4">Đăng nhập</button>
  </form>
</div>"""
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Đăng nhập — Sổ Thu Chi</title>
{_FONT_PRELOAD}
<link rel="stylesheet" href="{CSS_URL}">{FAVICON}{PWA_HEAD}</head>
<body><div class="wrap">{body}</div></body></html>"""


def _seg_kind(name: str, current: str, small: bool = False) -> str:
    """Thu/Chi toggle. Defaults to Chi: tiền khách trả already arrives from the
    CRM, so what the family actually types in all day is money going out."""
    cls = "seg money small" if small else "seg money"
    out = [f'<div class="{cls}">']
    for value, label in (("chi", "Chi ↑"), ("thu", "Thu ↓")):
        checked = " checked" if current == value else ""
        out.append(
            f'<label><input type="radio" name="{name}" value="{value}"{checked}>'
            f"<span>{label}</span></label>"
        )
    out.append("</div>")
    return "".join(out)


def _seg_method(name: str, current: str) -> str:
    out = ['<div class="seg small">']
    for value in METHODS:
        checked = " checked" if current == value else ""
        out.append(
            f'<label><input type="radio" name="{name}" value="{value}"{checked}>'
            f"<span>{esc(METHOD_LABELS[value])}</span></label>"
        )
    out.append("</div>")
    return "".join(out)


def _quick_form(day: str, error: str = "") -> str:
    """The hero. Amount first and autofocused, one tap to save."""
    err = ('<div class="callout callout-danger">Chưa nhập số tiền — gõ số rồi lưu nhé.</div>'
           if error == "so-tien" else "")
    return f"""
<form class="quick" method="post" action="/them">
  <input type="hidden" name="ngay" value="{esc(day)}">
  {err}
  <input class="amount money" name="so_tien" inputmode="numeric" autocomplete="off"
         placeholder="0" autofocus aria-label="Số tiền">
  {_seg_kind("kind", "chi")}
  <label for="mo_ta">Nội dung</label>
  <input id="mo_ta" name="mo_ta" autocomplete="off" placeholder="Mua tôn, đổ xăng, ứng lương…">
  <label>Hình thức</label>
  {_seg_method("hinh_thuc", "tien_mat")}
  <button class="btn big mt-4">Lưu vào sổ</button>
  <div class="hint">Tiền khách trả (cọc, thanh toán, công nợ đại lý) tự chảy vào Thu
  từ CRM — không cần nhập lại ở đây.</div>
</form>"""


def _totals_html(totals: dict) -> str:
    return f"""
<div class="totals">
  <div class="t thu"><div class="n">{fmt_vnd(totals['thu'])}</div><div class="lbl">Thu</div></div>
  <div class="t chi"><div class="n">{fmt_vnd(totals['chi'])}</div><div class="lbl">Chi</div></div>
  <div class="t net"><div class="n">{fmt_vnd(totals['con_lai'])}</div><div class="lbl">Còn lại</div></div>
</div>"""


def _methods_html(methods: list) -> str:
    if not methods:
        return ""
    chips = "".join(
        f'<span class="m">{esc(m["label"])}: {fmt_vnd(m["total"])}</span>' for m in methods
    )
    return f'<div class="methods">{chips}</div>'


def _entry_line(e: dict) -> str:
    """A manual row: description, who wrote it, amount, and a sửa link."""
    who = e.get("updated_by") or e.get("created_by") or ""
    stamp = f" · {esc(who)}" if who else ""
    edited = " · đã sửa" if e.get("updated_by") else ""
    desc = esc(e["description"]) or '<span class="dim">(không ghi nội dung)</span>'
    sign = "+" if e["kind"] == "thu" else "−"
    return f"""
<div class="line {e['kind']}">
  <div class="body">
    <div class="desc">{desc}</div>
    <div class="meta">{esc(METHOD_LABELS.get(e['method'], ''))}{stamp}{edited}</div>
  </div>
  <div class="amt">{sign}{fmt_vnd(e['amount_vnd'])}</div>
  <a class="edit" href="/sua/{e['id']}">Sửa</a>
</div>"""


def _crm_line(p: dict) -> str:
    """A CRM row: read-only here. Corrections happen in the CRM (đơn hàng / công
    nợ), and this app re-reads live, so there is deliberately no Sửa link."""
    kind = _CRM_KIND_LABELS.get(p.get("kind"), "thanh toán")
    name = esc(p.get("name") or "Khách")
    return f"""
<div class="line thu">
  <div class="body">
    <div class="desc"><span class="tag">CRM</span>{name}</div>
    <div class="meta">{esc(kind)}</div>
  </div>
  <div class="amt">+{fmt_vnd(p['amount_vnd'])}</div>
</div>"""


def day_page(day: str, entries: list, crm_rows: list, crm_error: str, totals: dict,
             methods: list, user: str, error: str = "") -> str:
    prev_d, next_d = _shift_day(day, -1), _shift_day(day, 1)
    nav = f"""
<div class="daynav">
  <a href="/ngay?ngay={prev_d}" aria-label="Ngày trước">‹</a>
  <form method="get" action="/ngay">
    <input type="date" name="ngay" value="{esc(day)}" onchange="this.form.submit()">
  </form>
  <a href="/ngay?ngay={next_d}" aria-label="Ngày sau">›</a>
</div>"""
    warn = f'<div class="callout callout-warn">{esc(crm_error)}</div>' if crm_error else ""
    # CRM rows first: they're the money that already came in, and the family reads
    # the day top-down. Manual entries follow, newest first.
    lines = "".join(_crm_line(p) for p in crm_rows) + "".join(_entry_line(e) for e in entries)
    if not lines:
        lines = '<div class="empty">Chưa ghi gì cho ngày này.</div>'
    body = f"""
{_quick_form(day, error)}
{nav}
<h2>{esc(_day_title(day))}</h2>
{_totals_html(totals)}
{_methods_html(methods)}
{warn}
{lines}"""
    return page("Sổ Thu Chi", body, active="/ngay", user=user, doc_title="Hôm nay")


def month_page(month: str, rows: list, totals: dict, methods: list, crm_error: str,
               user: str) -> str:
    from store import today_vn

    today = today_vn()
    prev_m, next_m = _shift_month(month, -1), _shift_month(month, 1)
    nav = f"""
<div class="daynav">
  <a href="/thang?thang={prev_m}" aria-label="Tháng trước">‹</a>
  <form method="get" action="/thang">
    <input type="month" name="thang" value="{esc(month)}" onchange="this.form.submit()">
  </form>
  <a href="/thang?thang={next_m}" aria-label="Tháng sau">›</a>
</div>"""
    warn = f'<div class="callout callout-warn">{esc(crm_error)}</div>' if crm_error else ""
    trs = []
    for r in rows:
        d = datetime.strptime(r["day"], "%Y-%m-%d")
        thu = f'<td class="thu">{fmt_vnd(r["thu"])}</td>' if r["thu"] else '<td class="zero">—</td>'
        chi = f'<td class="chi">{fmt_vnd(r["chi"])}</td>' if r["chi"] else '<td class="zero">—</td>'
        net = fmt_vnd(r["con_lai"]) if (r["thu"] or r["chi"]) else "—"
        net_cls = "" if (r["thu"] or r["chi"]) else ' class="zero"'
        today_cls = ' class="today"' if r["day"] == today else ""
        trs.append(
            f'<tr{today_cls}><td><a href="/ngay?ngay={r["day"]}">{d.day}/{d.month}</a></td>'
            f'{thu}{chi}<td{net_cls}>{net}</td></tr>'
        )
    table = f"""
<table class="ledger">
  <tr><th>Ngày</th><th>Thu</th><th>Chi</th><th>Còn lại</th></tr>
  {''.join(trs)}
</table>"""
    body = f"""
{nav}
<h2>Tháng {int(month[5:7])}/{month[:4]}</h2>
{_totals_html(totals)}
{_methods_html(methods)}
{warn}
{table}"""
    return page("Theo tháng", body, active="/thang", user=user)


def edit_page(entry: dict, user: str) -> str:
    amount = f"{int(entry['amount_vnd']):,}".replace(",", ".")
    who = entry.get("created_by") or "—"
    stamp = f"Ghi bởi {esc(who)} · {fmt_date(entry.get('created_at'))}"
    if entry.get("updated_by"):
        stamp += f" · sửa lần cuối bởi {esc(entry['updated_by'])}"
    body = f"""
<form class="quick" method="post" action="/sua/{entry['id']}">
  <input class="amount money" name="so_tien" inputmode="numeric" autocomplete="off"
         value="{esc(amount)}" autofocus aria-label="Số tiền">
  {_seg_kind("kind", entry["kind"])}
  <label for="mo_ta">Nội dung</label>
  <input id="mo_ta" name="mo_ta" autocomplete="off" value="{esc(entry['description'])}">
  <label for="ngay">Ngày</label>
  <input id="ngay" type="date" name="ngay" value="{esc(entry['entry_date'])}">
  <label>Hình thức</label>
  {_seg_method("hinh_thuc", entry["method"])}
  <button class="btn big mt-4">Lưu thay đổi</button>
  <div class="hint">{stamp}</div>
</form>
<div class="row">
  <a class="btn done" href="/ngay?ngay={esc(entry['entry_date'])}">Quay lại</a>
  <form method="post" action="/xoa/{entry['id']}" style="flex:1;display:flex"
        data-confirm="Xoá dòng này khỏi sổ?">
    <button class="btn danger">Xoá</button>
  </form>
</div>"""
    return page("Sửa dòng", body, active="/ngay", user=user)
