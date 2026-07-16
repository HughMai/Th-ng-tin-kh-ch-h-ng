"""Server-rendered HTML for the HTP CRM (STL dashboard pattern: inline HTML
strings, no template engine). 100% Vietnamese, phone-first: single column,
17px base, >=48px touch targets, fixed bottom nav, tap-to-copy Zalo messages.
"""
import html
import json

from templates_vi import (
    LOST_REASON_LABELS,
    PRODUCT_LABELS,
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
function copyMsg(btn){
  var msg = btn.getAttribute('data-msg');
  function done(){ showToast('Đã chép ✓'); }
  if (navigator.clipboard && window.isSecureContext){
    navigator.clipboard.writeText(msg).then(done);
  } else {
    var ta = document.createElement('textarea');
    ta.value = msg; ta.style.position='fixed'; ta.style.opacity='0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); done(); } catch(e){}
    ta.remove();
  }
}
function showToast(t){
  var el = document.getElementById('toast');
  el.textContent = t; el.classList.add('show');
  setTimeout(function(){ el.classList.remove('show'); }, 1600);
}
function fmtMoney(inp){
  var v = inp.value.replace(/\\D/g, '');
  inp.value = v.replace(/\\B(?=(\\d{3})+(?!\\d))/g, '.');
}
function logGoi(id){ if(navigator.sendBeacon) navigator.sendBeacon('/khach/'+id+'/goi'); }
"""

_TABS = [
    ("/", "🏠", "Hôm nay"),
    ("/bao-gia", "📋", "Báo giá"),
    ("/khach", "👤", "Khách"),
    ("/don-hang", "🚪", "Đơn hàng"),
    ("/cong-no", "💰", "Công nợ"),
    ("/bao-cao", "📊", "Báo cáo"),
]


def _sidebar_html(active: str) -> str:
    """Desktop-only GHL-style nav rail (hidden <900px via CSS). Reads the same
    _TABS data as the bottom nav below, without touching the bottom nav's own
    tested rendering loop."""
    links = "".join(
        f'<a href="{href}" class="{"on" if href == active else ""}">'
        f'<span class="ico">{ico}</span>{label}</a>'
        for href, ico, label in _TABS
    )
    return f'<nav class="sidebar"><div class="brand">HTP</div>{links}</nav>'


def page(title: str, body: str, active: str = "", show_nav: bool = True,
         wrap_class: str = "") -> str:
    nav = ""
    sidebar = ""
    if show_nav:
        links = "".join(
            f'<a href="{href}" class="{"on" if href == active else ""}">'
            f'<span class="ico">{ico}</span>{label}</a>'
            for href, ico, label in _TABS
        )
        nav = f'<nav class="nav">{links}</nav>'
        sidebar = _sidebar_html(active)
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} — HTP</title>
<link rel="stylesheet" href="/static/app.css">{FAVICON}</head>
<body>
{sidebar}
<div class="top">{esc(title)}<a href="/logout" onclick="event.preventDefault();document.getElementById('lo').submit()">Thoát</a></div>
<form id="lo" method="post" action="/logout" style="display:none"></form>
<div class="wrap{(' ' + wrap_class) if wrap_class else ''}">{body}</div>
{nav}
<div class="toast" id="toast"></div>
<script>{_JS}</script>
<script src="/static/app.js"></script>
</body></html>"""


def login_page(error: str = "") -> str:
    err = '<div style="color:#b91c1c;margin-bottom:12px">Sai mật khẩu, thử lại nhé.</div>' if error else ""
    body = f"""
<div class="card" style="margin-top:40px">
  <div style="font-size:22px;font-weight:800;color:#0f4c81;margin-bottom:4px">Hưng Thành Phát</div>
  <div class="sub" style="margin-bottom:14px">Sổ khách hàng</div>
  {err}
  <form method="post" action="/login">
    <label>Mật khẩu gia đình</label>
    <input name="password" type="password" autofocus>
    <button class="btn big" style="margin-top:16px">Đăng nhập</button>
  </form>
</div>"""
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Đăng nhập — HTP</title><link rel="stylesheet" href="/static/app.css">{FAVICON}</head>
<body><div class="wrap">{body}</div></body></html>"""


# ---------------------------------------------------------------- shared bits

def contact_buttons(phone: str, zalo_phone: str = "", msg: str = "",
                    customer_id: int = 0, zalo_user_id: str = "") -> str:
    """Copy / Zalo / Gọi button row. ``msg`` empty -> no copy button.
    When the customer is linked to a Zalo OA user id, add a direct-send button
    that posts through the API instead of opening the Zalo app — still a person
    tapping "send", just via the API rather than a deep link."""
    z = zalo_phone or phone
    copy = (
        f'<button type="button" class="btn copy" data-msg="{esc(msg)}" '
        f'onclick="copyMsg(this)">📋 Chép tin nhắn</button>'
    ) if msg else ""
    zalo = f'<a class="btn zalo" href="{esc(zalo_link(z))}">Zalo</a>' if z else ""
    call = (f'<a class="btn call" href="tel:{esc(phone)}"'
            + (f' onclick="logGoi({customer_id})"' if customer_id else "")
            + '>Gọi</a>') if phone else ""
    api_send = ""
    if msg and customer_id and zalo_user_id:
        api_send = (
            f'<form method="post" action="/khach/{customer_id}/gui-zalo" style="flex:1;display:flex">'
            f'<input type="hidden" name="text" value="{esc(msg)}">'
            f'<button class="btn zalo" style="width:100%">📨 Gửi qua API</button></form>'
        )
    return f'<div class="row">{copy}{zalo}{call}{api_send}</div>'


def _post_btn(action: str, label: str, cls: str = "done", next_url: str = "", data_ajax: str = "") -> str:
    nxt = f'<input type="hidden" name="next" value="{esc(next_url)}">' if next_url else ""
    ajax = f' data-ajax="{esc(data_ajax)}"' if data_ajax else ""
    return (
        f'<form method="post" action="{esc(action)}" style="flex:1;display:flex"{ajax}>{nxt}'
        f'<button class="btn {cls}" style="width:100%">{esc(label)}</button></form>'
    )


def type_chip(t: str) -> str:
    return '<span class="chip dl">Đại lý</span>' if t == "DL" else '<span class="chip kh">Khách lẻ</span>'


_STATUS_LABEL = {"sent": "Đã gửi", "chasing": "Đang theo dõi", "won": "Chốt ✓", "lost": "Mất"}


def status_chip(s: str) -> str:
    return f'<span class="chip {esc(s)}">{_STATUS_LABEL.get(s, s)}</span>'


# ---------------------------------------------------------------- bộ cửa / tiến độ

def _door_desc(item: dict) -> str:
    """'Cửa Cuốn Đức KV 380' — door label + short technology + model. Order
    items carry an explicit description for phụ kiện/generic lines — it wins."""
    if item.get("description"):
        return item["description"]
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
        ico = "🚪" if kich else "•"
        rows.append(
            f'<div class="sub" style="display:flex;justify-content:space-between;gap:8px">'
            f'<span>{ico} {esc(_door_desc(i))}{kich}{mau_sac}{qty}</span>'
            f'<b>{fmt_vnd(i["thanh_tien"])}</b></div>'
        )
    return "".join(rows)


def production_message(order: dict, quote: dict, items: list) -> str:
    """Plain-text bộ-cửa handoff to paste into the staff Zalo group when a deal
    is chốt (or flagged gấp). Copied via the shared copyMsg() button."""
    ctype = (quote or {}).get("customer_type") or order.get("customer_type") or "KH"
    acc = (quote or {}).get("accessories") or ""
    pk_lines = pricing.phukien_line_items(acc, items, ctype)
    pk_total = pricing.calc_phukien(acc, items, ctype)
    door_total = sum(i["thanh_tien"] for i in items)
    tong_cong = (quote or {}).get("value_vnd") or (door_total + pk_total)
    vat = round(tong_cong * 0.1)
    tong_tien = tong_cong + vat
    dep = (quote or {}).get("deposit_vnd") or 0
    install = (quote or {}).get("install_date") or order.get("install_date") or ""

    L = []
    if order.get("urgent"):
        L.append("🔥 GẤP — ưu tiên làm trước")
    L.append("🔨 ĐƠN SẢN XUẤT — Hưng Thành Phát")
    L.append(f"Khách: {order.get('customer_name', '')}")
    if order.get("phone"):
        L.append(f"SĐT: {order['phone']}")
    if order.get("address"):
        L.append(f"Địa chỉ: {order['address']}")
    if install:
        L.append(f"Ngày lắp: {fmt_date(install)}")
    L.append("— Bộ cửa —")
    for idx, i in enumerate(items, 1):
        extra = f" · {i['mau_sac']}" if i.get("mau_sac") else ""
        L.append(f"{idx}. {_door_desc(i)} — {i['ngang_mm']}×{i['cao_mm']}mm{extra} — {fmt_vnd(i['thanh_tien'])}")
    if pk_lines:
        L.append("— Phụ kiện —")
        for p in pk_lines:
            L.append(f"• {p['name']} ×{p['qty']} — {fmt_vnd(p['total'])}")
    L.append(f"Tổng cộng: {fmt_vnd(tong_cong)} | VAT: {fmt_vnd(vat)} | Tổng tiền: {fmt_vnd(tong_tien)}")
    if dep:
        L.append(f"Đã cọc: {fmt_vnd(dep)} — Còn lại: {fmt_vnd(tong_tien - dep)}")
    if (quote or {}).get("note"):
        L.append(f"Ghi chú: {quote['note']}")
    return "\n".join(L)


def production_message_no_price(order: dict, items: list) -> str:
    """Same bộ-cửa handoff as production_message() but strips every VND figure
    — used for the Zalo group auto-ping on chốt (money never goes to the group,
    see ZALO-BOT-PLAN.md)."""
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
    L.append("— Bộ cửa —")
    for idx, i in enumerate(items, 1):
        extra = f" · {i['mau_sac']}" if i.get("mau_sac") else ""
        L.append(f"{idx}. {_door_desc(i)} — {i['ngang_mm']}×{i['cao_mm']}mm{extra}")
    return "\n".join(L)


def copy_zalo_button(msg: str, label: str = "📋 Sao chép để dán vào nhóm Zalo") -> str:
    return (f'<button type="button" class="btn copy" style="width:100%" data-msg="{esc(msg)}" '
            f'onclick="copyMsg(this)">{esc(label)}</button>')


def stage_badge(stage: str) -> str:
    return f'<span class="chip stage-{esc(stage)}">{esc(STAGE_LABELS.get(stage, stage))}</span>'


# ---------------------------------------------------------------- touches (chăm sóc)

_TOUCH_KIND_LABELS = {
    "goi": "📞 Gọi điện",
    "zalo": "💬 Nhắn Zalo",
    "zalo_api": "📨 Gửi qua API",
    "trang_thai": "🔄 Đổi trạng thái",
    "ghi_chu": "📝 Ghi chú",
    "nhac_xong": "⏰ Nhắc xong",
    "bao_tri": "🔧 Bảo trì",
    "danh_gia": "⭐ Xin đánh giá",
}


def _touch_timeline_html(touches: list) -> str:
    """Newest-first care log, icon per kind, relative day count. Rendered as a
    connected vertical rail (a dot per entry) rather than separate cards."""
    if not touches:
        return '<div class="empty">Chưa có lượt chăm sóc nào.</div>'
    rows = []
    for t in touches:
        label = _TOUCH_KIND_LABELS.get(t["kind"], t["kind"])
        days = t.get("days_ago")
        when = "Hôm nay" if days == 0 else (f"{days} ngày trước" if days and days > 0 else fmt_date(t["created_at"]))
        detail = f' — {esc(t["detail"])}' if t.get("detail") else ""
        rows.append(f'<div class="tl-item"><span class="tl-dot"></span><div class="sub">{label}{detail} — {when}</div></div>')
    return f'<div class="timeline">{"".join(rows)}</div>'


# ---------------------------------------------------------------- Hôm nay

def today_page(chase: list, checkins: list, expiring: list, debts: list,
               reminders: list, reviews: list, summary: dict, kh_debts: list = None) -> str:
    kh_debts = kh_debts or []
    # Stat-card row (GHL-style dashboard convention) — visible on all
    # viewports. No new store.py queries: the pipeline total reuses the
    # already-existing open_quotes_summary(); the task count and debt total
    # are derived from the SAME lists rendered below, so the numbers can
    # never disagree with what's listed on the page.
    task_count = (len(chase) + len(checkins) + len(expiring) + len(debts)
                  + len(reminders) + len(reviews) + len(kh_debts))
    debt_total = sum(d["balance"] for d in debts)
    stat_row = f"""
<div class="stat-row">
  <div class="stat-card"><div class="n">{task_count}</div><div class="lbl">Việc cần làm hôm nay</div></div>
  <div class="stat-card"><div class="n">{fmt_vnd_short(summary["total"])}</div><div class="lbl">Báo giá đang theo dõi</div></div>
  <div class="stat-card"><div class="n">{fmt_vnd_short(debt_total)}</div><div class="lbl">Công nợ quá hạn</div></div>
</div>"""
    parts = [stat_row]

    if chase:
        parts.append(f"<h2>📨 Cần nhắc báo giá ({len(chase)})</h2>")
        for q in chase:
            label = PRODUCT_LABELS.get(q["product"], "sản phẩm")
            msg = render("quote_followup_2" if q["nudge_level"] == 2 else "quote_followup_1",
                         ten=q["customer_name"], san_pham=label)
            badge = '<span class="badge">Lần 2</span>' if q["nudge_level"] == 2 else ""
            parts.append(f"""
<div class="card">
  <div class="name">{esc(q["customer_name"])} {badge}</div>
  <div class="sub">{esc(label.capitalize())} — {fmt_vnd(q["value_vnd"])} — gửi {q["days_sent"]} ngày trước</div>
  {contact_buttons(q["phone"], q["zalo_phone"], msg, customer_id=q["customer_id"])}
  <div class="row">{_post_btn(f'/bao-gia/{q["id"]}/da-nhan', "✔ Đã nhắn", next_url="/", data_ajax="remove")}
  <a class="btn done" href="/bao-gia">Xem</a></div>
</div>""")

    if reminders:
        parts.append(f"<h2>⏰ Nhắc hôm nay ({len(reminders)})</h2>")
        for r in reminders:
            parts.append(f"""
<div class="card">
  <div class="name">{esc(r["customer_name"])}</div>
  <div class="sub">{esc(r["note"])} — hẹn {fmt_date(r["due_date"])}</div>
  {contact_buttons(r["phone"], r["zalo_phone"], customer_id=r["customer_id"])}
  <div class="row">{_post_btn(f'/nhac/{r["id"]}/xong', "✔ Xong", next_url="/", data_ajax="remove")}</div>
</div>""")

    if reviews:
        parts.append(f"<h2>⭐ Xin đánh giá ({len(reviews)})</h2>")
        for o in reviews:
            label = PRODUCT_LABELS.get(o["product"], "sản phẩm")
            msg = render("review_request", ten=o["customer_name"], san_pham=label)
            parts.append(f"""
<div class="card">
  <div class="name">{esc(o["customer_name"])}</div>
  <div class="sub">{esc(label.capitalize())} — lắp {fmt_date(o["install_date"])}</div>
  {contact_buttons(o["phone"], o["zalo_phone"], msg, customer_id=o["customer_id"])}
  <div class="row">{_post_btn(f'/don-hang/{o["id"]}/da-xin-danh-gia', "✔ Đã xin", next_url="/", data_ajax="remove")}</div>
</div>""")

    if checkins:
        parts.append(f"<h2>🔧 Bảo trì 6 tháng ({len(checkins)})</h2>")
        for o in checkins:
            label = PRODUCT_LABELS.get(o["product"], "sản phẩm")
            msg = render("checkin_6m", ten=o["customer_name"], san_pham=label)
            parts.append(f"""
<div class="card">
  <div class="name">{esc(o["customer_name"])}</div>
  <div class="sub">{esc(label.capitalize())} — lắp {fmt_date(o["install_date"])}</div>
  {contact_buttons(o["phone"], o["zalo_phone"], msg, customer_id=o["customer_id"])}
  <div class="row">{_post_btn(f'/don-hang/{o["id"]}/da-bao-tri', "✔ Đã bảo trì", next_url="/", data_ajax="remove")}</div>
</div>""")

    if expiring:
        parts.append(f"<h2>🛡️ Bảo hành sắp hết ({len(expiring)})</h2>")
        for o in expiring:
            label = PRODUCT_LABELS.get(o["product"], "sản phẩm")
            msg = render("warranty_expiring", ten=o["customer_name"], san_pham=label,
                         ngay=fmt_date(o["expiry_date"]))
            parts.append(f"""
<div class="card">
  <div class="name">{esc(o["customer_name"])}</div>
  <div class="sub">{esc(label.capitalize())} — hết BH {fmt_date(o["expiry_date"])}</div>
  {contact_buttons(o["phone"], o["zalo_phone"], msg, customer_id=o["customer_id"])}
  <div class="row">{_post_btn(f'/don-hang/{o["id"]}/da-nhan-bh', "✔ Đã nhắn", next_url="/", data_ajax="remove")}</div>
</div>""")

    if debts:
        parts.append(f"<h2>💰 Công nợ cần thu ({len(debts)})</h2>")
        for d in debts:
            msg = render("debt_reminder", ten=d["name"], so_tien=fmt_vnd(d["balance"]))
            last = f"trả lần cuối {fmt_date(d['last_payment'])}" if d["last_payment"] else "chưa trả lần nào"
            parts.append(f"""
<div class="card">
  <div class="name">{esc(d["name"])} <span class="chip warn">{fmt_vnd(d["balance"])}</span></div>
  <div class="sub">{last}</div>
  {contact_buttons(d["phone"], d["zalo_phone"], msg, customer_id=d["id"])}
  <div class="row"><a class="btn done" href="/cong-no/{d["id"]}">Xem sổ nợ</a></div>
</div>""")

    if kh_debts:
        parts.append(f"<h2>💵 Khách lẻ còn nợ ({len(kh_debts)})</h2>")
        for o in kh_debts:
            bal = o["balance_vnd"]
            label = PRODUCT_LABELS.get(o["product"], "sản phẩm")
            msg = render("debt_reminder", ten=o["customer_name"], so_tien=fmt_vnd(bal))
            parts.append(f"""
<div class="card">
  <div class="name">{esc(o["customer_name"])} <span class="chip warn">{fmt_vnd(bal)}</span></div>
  <div class="sub">{esc(label.capitalize())} — lắp {fmt_date(o["install_date"])} · còn nợ</div>
  {contact_buttons(o["phone"], o["zalo_phone"], msg, customer_id=o["customer_id"])}
  <div class="row">
    <form method="post" action="/don-hang/{o["id"]}/thanh-toan" style="flex:1;display:flex" data-ajax="remove">
      <input type="hidden" name="amount_vnd" value="{bal}">
      <input type="hidden" name="kind" value="thanh_toan">
      <input type="hidden" name="next" value="/">
      <button class="btn done" style="width:100%">✔ Đã thu đủ</button></form>
    <a class="btn done" href="/don-hang/{o["id"]}">Xem đơn</a>
  </div>
</div>""")

    if task_count == 0:
        parts.append('<div class="empty" style="padding-top:70px;font-size:18px">'
                     "Hôm nay không có việc cần làm ✅</div>")
    return "".join(parts)


# ---------------------------------------------------------------- customers

def customers_page(rows: list, q: str = "", loai: str = "", stage: str = "customer") -> str:
    items = "".join(f"""
<div class="card">
  <div class="row" style="margin-top:0;justify-content:space-between;align-items:flex-start">
    <a href="/khach/{c["id"]}" style="flex:1;min-width:0">
      <div class="name">{esc(c["name"])} {type_chip(c["type"])}</div>
      <div class="sub">{esc(c["phone"] or "—")} · {SOURCE_LABELS.get(c["source"], "")}</div>
    </a>
    <button type="button" class="kebab-btn" onclick="toggleCardMenu(event, this)">⋮</button>
  </div>
  <div class="card-menu" hidden>
    <a href="/khach/{c["id"]}/sua">✏️ Sửa thông tin</a>
    {f'<a href="tel:{esc(c["phone"])}">📞 Gọi điện</a>' if c["phone"] else ""}
    <form method="post" action="/khach/{c["id"]}/xoa"
      onsubmit="return confirm('Xóa khách {esc(c["name"])}? Không thể hoàn tác.')">
      <button type="submit" class="danger">🗑️ Xóa khách hàng</button>
    </form>
  </div>
</div>""" for c in rows) or (
        '<div class="empty">Chưa có lead nào — thêm khi khách xin báo giá.</div>' if stage == "lead"
        else '<div class="empty">Chưa có khách chính nào. Chốt một báo giá để chuyển khách vào đây.</div>'
    )
    return f"""
<form class="search" method="get" action="/khach">
  <input name="q" value="{esc(q)}" placeholder="Tìm tên, SĐT hoặc địa chỉ…">
  <input type="hidden" name="loai" value="{esc(loai)}">
  <input type="hidden" name="giai_doan" value="{esc(stage)}"><button>🔍</button>
</form>
<div class="cards-grid">{items}</div>
<a class="btn done" style="display:flex;margin-top:8px" href="/zalo">💬 Kết nối / gắn Zalo</a>
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
  <button class="btn big" style="margin-top:18px">{"Lưu thay đổi" if is_edit else "Thêm khách"}</button>
</form>"""


# ---------------------------------------------------------------- intake wizard
# Colour options per door type, flattened from the reference intake prototype
# (hughmai.github.io/Th-ng-tin-kh-ch-h-ng). Not priced — captured into
# quote_items.mau_sac. Kept here (presentation data), not in pricing.py.
_WIZARD_COLORS = {
    "cua_cuon": ["ghi sần", "kem", "xanh"],
    "cua_keo": ["xanh", "kem", "ghi sần", "xám xingfa"],
    "nhom_kinh": ["xám xingfa", "trắng", "giả gỗ"],
    "xingfa": ["xám xingfa", "trắng", "giả gỗ"],
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
  window.scrollTo(0,0);
}
function wizNext(){
  var s=curStep();
  if(s===1){
    if(!document.getElementById('c_name').value.trim() || !document.getElementById('c_phone').value.trim()){
      showStep(1); alert('Cần nhập Tên và Số điện thoại'); return;
    }
    showStep(2);
  } else if(s===2){ showStep(3); }
}
function wizBack(){ var s=curStep(); if(s>1) showStep(s-1); }
function customerType(){ return document.querySelector('input[name=type]:checked').value; }
function optionHtml(list){
  return list.map(function(o){ return '<option value="'+o+'">'+(o||'— Chọn —')+'</option>'; }).join('');
}
function reconcileUnits(){
  var cont=document.getElementById('units-container');
  document.querySelectorAll('.dt-chk').forEach(function(chk){
    var type=chk.dataset.type;
    var qtyEl=document.querySelector('.dt-qty[data-type="'+type+'"]');
    qtyEl.disabled=!chk.checked;
    var desired=chk.checked ? Math.max(1, parseInt(qtyEl.value)||1) : 0;
    var existing=cont.querySelectorAll('.unit-block[data-type="'+type+'"]');
    for(var i=existing.length; i<desired; i++){ cont.appendChild(buildUnit(type)); }
    existing=cont.querySelectorAll('.unit-block[data-type="'+type+'"]');
    for(var j=existing.length-1; j>=desired; j--){ existing[j].remove(); }
  });
  renumber(); updateTotal();
}
function renumber(){
  var counts={};
  document.querySelectorAll('.unit-block').forEach(function(b){
    var t=b.dataset.type; counts[t]=(counts[t]||0)+1;
    b.querySelector('.uhead .lbl').textContent = DOOR_CONFIG[t].label + ' #' + counts[t];
  });
}
function buildUnit(type){
  var cfg=DOOR_CONFIG[type];
  var div=document.createElement('div');
  div.className='unit-block'; div.dataset.type=type;
  var fields='';
  cfg.fields.forEach(function(f){
    var opts = (f.type==='select-dynamic') ? '<option value="">— Chọn —</option>' : optionHtml(f.options);
    fields += '<div class="fld"><label>'+f.label+'</label><select data-key="'+f.key+'">'+opts+'</select></div>';
  });
  var colors=COLORS[type]||[];
  fields += '<div class="fld"><label>Màu</label><select data-key="mau_sac"><option value="">— Chọn màu —</option>'+optionHtml(colors)+'</select></div>';
  div.innerHTML =
    '<div class="uhead"><span class="lbl">'+cfg.label+'</span><button type="button" class="ux">&times;</button></div>'
    + '<div class="field-grid">'+fields+'</div>'
    + '<div class="field-grid">'
    +   '<div class="fld"><label>Ngang (mm)</label><input class="u-ngang" inputmode="numeric" maxlength="4" placeholder="VD: 3000"></div>'
    +   '<div class="fld"><label>Cao (mm)</label><input class="u-cao" inputmode="numeric" maxlength="4" placeholder="VD: 2200"></div>'
    + '</div>'
    + '<div class="unit-price">Chọn đầy đủ để xem giá</div>'
    + '<div class="u-manual-wrap" style="display:none"><label>Giá nhập tay (VND)</label><input class="u-manual" inputmode="numeric" placeholder="VD: 12.000.000"></div>';
  return div;
}
function unitField(b,key){ return b.querySelector('[data-key="'+key+'"]'); }
function onUnitField(b,key){
  var cfg=DOOR_CONFIG[b.dataset.type];
  cfg.fields.forEach(function(f){
    if(f.type==='select-dynamic' && f.depends_on===key){
      var val=unitField(b,key).value;
      var opts=(f.option_map && f.option_map[val]) || [''];
      unitField(b,f.key).innerHTML=optionHtml(opts);
    }
  });
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
  if(price && ngang && cao){
    var total=Math.floor(price*ngang*cao/1000);
    priceEl.textContent='Thành tiền: '+total.toLocaleString('vi-VN')+'đ (theo bảng giá)';
    priceEl.classList.remove('manual'); manualWrap.style.display='none';
    b.dataset.match='1'; b.dataset.total=total;
  } else {
    b.dataset.match='0'; b.dataset.total=0;
    if(cn||m){
      priceEl.textContent='Không có bảng giá — nhập giá tay bên dưới';
      priceEl.classList.add('manual'); manualWrap.style.display='block';
    } else {
      priceEl.textContent='Chọn đầy đủ để xem giá';
      priceEl.classList.remove('manual'); manualWrap.style.display='none';
    }
  }
  updateTotal();
}
function unitTotal(b){
  if(b.dataset.match==='1') return parseInt(b.dataset.total)||0;
  return parseInt(b.querySelector('.u-manual').value.replace(/[^0-9]/g,''))||0;
}
function updateTotal(){
  var sum=0; var blocks=document.querySelectorAll('.unit-block');
  blocks.forEach(function(b){ sum+=unitTotal(b); });
  var gt=document.getElementById('grandtotal');
  if(blocks.length){ gt.style.display='block'; gt.textContent='Tổng tạm tính: '+sum.toLocaleString('vi-VN')+'đ ('+blocks.length+' cửa)'; }
  else { gt.style.display='none'; }
}
function removeUnit(b){
  var type=b.dataset.type; b.remove();
  var remaining=document.querySelectorAll('.unit-block[data-type="'+type+'"]').length;
  var qtyEl=document.querySelector('.dt-qty[data-type="'+type+'"]');
  if(remaining===0){ document.querySelector('.dt-chk[data-type="'+type+'"]').checked=false; qtyEl.disabled=true; qtyEl.value=1; }
  else { qtyEl.value=remaining; }
  renumber(); updateTotal();
}
(function(){
  var cont=document.getElementById('units-container');
  cont.addEventListener('change', function(e){
    var b=e.target.closest('.unit-block'); if(!b) return;
    if(e.target.dataset.key){ onUnitField(b, e.target.dataset.key); }
  });
  cont.addEventListener('input', function(e){
    var b=e.target.closest('.unit-block'); if(!b) return;
    if(e.target.classList.contains('u-ngang') || e.target.classList.contains('u-cao')){
      e.target.value=e.target.value.replace(/[^0-9]/g,'').slice(0,4); priceUnit(b);
    } else if(e.target.classList.contains('u-manual')){ fmtMoney(e.target); updateTotal(); }
  });
  cont.addEventListener('click', function(e){
    if(e.target.classList.contains('ux')){ removeUnit(e.target.closest('.unit-block')); }
  });
})();
function serializeWizard(){
  var units=[];
  document.querySelectorAll('.unit-block').forEach(function(b){
    var cnEl=unitField(b,'cong_nghe'); var mEl=unitField(b,'mau'); var scEl=unitField(b,'mau_sac');
    units.push({
      product:b.dataset.type,
      cong_nghe:cnEl?cnEl.value:'',
      mau:mEl?mEl.value:'',
      mau_sac:scEl?scEl.value:'',
      ngang:parseInt(b.querySelector('.u-ngang').value)||0,
      cao:parseInt(b.querySelector('.u-cao').value)||0,
      gia_manual:b.querySelector('.u-manual').value.replace(/[^0-9]/g,'')
    });
  });
  document.getElementById('units').value=JSON.stringify(units);
  var accs=[];
  document.querySelectorAll('.acc-chk:checked').forEach(function(chk){
    var qty=parseInt(chk.closest('.acc-row').querySelector('.acc-qty').value)||1;
    accs.push(chk.dataset.label+' x'+qty);
  });
  document.getElementById('accessories').value=accs.join(', ');
}
document.getElementById('wizform').addEventListener('submit', function(e){
  if(!document.getElementById('c_name').value.trim() || !document.getElementById('c_phone').value.trim()){
    e.preventDefault(); showStep(1); alert('Cần nhập Tên và Số điện thoại'); return;
  }
  var bad=null;
  document.querySelectorAll('.unit-block').forEach(function(b){
    if(bad) return;
    var ngang=parseInt(b.querySelector('.u-ngang').value)||0;
    var cao=parseInt(b.querySelector('.u-cao').value)||0;
    if(!ngang || !cao || !unitTotal(b)) bad=b;
  });
  if(bad){ e.preventDefault(); showStep(2); bad.scrollIntoView({behavior:'smooth',block:'center'}); alert('Mỗi cửa cần đủ kích thước và giá (theo bảng giá hoặc nhập tay)'); return; }
  serializeWizard();
});
"""


def intake_wizard_page(today: str) -> str:
    """3-step new-customer intake (customer → doors → accessories/deposit),
    reconstructed from the reference prototype. One POST to /khach/tiep-nhan
    creates the customer and, if any door units were added, a multi-item quote
    (server re-prices authoritatively via pricing.get_price)."""
    src_opts = "".join(f'<option value="{v}">{label}</option>' for v, label in SOURCE_LABELS.items())
    door_rows = "".join(f'''
    <div class="acc-row">
      <label><input type="checkbox" class="dt-chk" data-type="{key}" onchange="reconcileUnits()"> {esc(cfg["label"])}</label>
      <input type="number" class="dt-qty" data-type="{key}" min="1" value="1" disabled inputmode="numeric" oninput="reconcileUnits()">
    </div>''' for key, cfg in DOOR_CONFIG.items())
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
          <input name="phone" id="c_phone" inputmode="tel"></div>
        <div class="fld"><label>Email</label><input name="email" type="email" inputmode="email"></div>
        <div class="fld"><label>Địa chỉ</label><input name="address"></div>
        <div class="fld"><label>Khách biết mình qua đâu?</label><select name="source">{src_opts}</select></div>
        <div class="fld"><label>Số Zalo (nếu khác SĐT)</label><input name="zalo_phone" inputmode="tel"></div>
      </div>
      <label>Ghi chú</label>
      <textarea name="note"></textarea>
    </div>
    <div class="wiz-nav">
      <button type="button" class="btn copy" onclick="wizNext()">Tiếp theo →</button>
    </div>
  </div>

  <div class="wiz-step" data-step="2">
    <div class="card">
      <div class="card-title">Chọn loại cửa & số lượng</div>
      {door_rows}
    </div>
    <div id="units-container"></div>
    <div class="grand-total" id="grandtotal" style="display:none">Tổng tạm tính: 0đ</div>
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
      <div class="field-grid">
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
      <button type="submit" class="btn call">✔ Lưu khách &amp; báo giá</button>
    </div>
  </div>
</form>
{script}'''


def _customer_quote_card(c: dict, q: dict) -> str:
    """One báo giá on the customer page: bộ cửa + status actions + edit link."""
    items = q.get("items") or []
    bo_cua = bo_cua_html(items)
    edit = f'<a class="btn done" href="/bao-gia/{q["id"]}">✏️ Sửa báo giá</a>'
    if q["status"] in ("sent", "chasing"):
        lost_opts = "".join(f'<option value="{v}">{label}</option>'
                            for v, label in LOST_REASON_LABELS.items())
        actions = f"""
  <div class="row">
    {_post_btn(f'/bao-gia/{q["id"]}/da-nhan', "✔ Đã gửi", next_url=f'/khach/{c["id"]}')}
    <form method="post" action="/bao-gia/{q["id"]}/trang-thai" style="flex:1;display:flex">
      <input type="hidden" name="trang_thai" value="won">
      <button class="btn call" style="width:100%">✔ Chốt</button></form>
  </div>
  <details style="margin-top:6px"><summary class="btn danger" style="display:flex">✖ Mất</summary>
    <form method="post" action="/bao-gia/{q["id"]}/trang-thai" style="margin-top:8px">
      <input type="hidden" name="trang_thai" value="lost">
      <select name="ly_do">{lost_opts}</select>
      <button class="btn danger" style="width:100%;margin-top:8px">Xác nhận mất</button>
    </form></details>
  <div class="row" style="margin-top:6px">{edit}</div>"""
    elif q["status"] == "won":
        # Đã chốt: the đơn hàng is the editable surface; the báo giá is read-only
        # (see quote_build_page `locked`), so link it as "Xem", not "Sửa".
        prog = (f'<a class="btn copy" href="/don-hang/{q["order_id"]}">🔨 Xem tiến độ</a>'
                if q.get("order_id") else "")
        view = f'<a class="btn done" href="/bao-gia/{q["id"]}">👁 Xem báo giá</a>'
        actions = f'<div class="row" style="margin-top:6px">{prog}{view}</div>'
    else:  # lost
        reason = LOST_REASON_LABELS.get(q.get("lost_reason"), "") if q.get("lost_reason") else ""
        actions = (f'<div class="sub">Lý do: {esc(reason)}</div>' if reason else "") + \
                  f'<div class="row" style="margin-top:6px">{edit}</div>'
    return f"""
<div class="card">
  <div class="sub">{status_chip(q["status"])} {bao_gia_so(q["id"], q.get("sent_date"))}</div>
  <div class="sub">{esc(PRODUCT_LABELS.get(q["product"], "").capitalize())}
   — <b>{fmt_vnd(q["value_vnd"])}</b> — gửi {fmt_date(q["sent_date"])}</div>
  {bo_cua}
  {actions}
</div>"""


def customer_detail_page(c: dict, quotes: list, orders: list, reminders: list,
                         balance: int, today: str, touches: list = None,
                         phone_dups: list = None) -> str:
    qs = "".join(_customer_quote_card(c, q) for q in quotes)
    os_ = "".join(f"""
<a href="/don-hang/{o["id"]}"><div class="card">
  <div class="sub">🚪 {esc(PRODUCT_LABELS.get(o["product"], "").capitalize())} — {fmt_vnd(o["value_vnd"])}
   — {"lắp " + fmt_date(o["install_date"]) if o["install_date"] else "Chưa lắp"}
   {f'<span class="chip warn">còn nợ {fmt_vnd(o["balance_vnd"])}</span>' if o["customer_type"] == "KH" and o.get("balance_vnd", 0) > 0 else ""}</div>
</div></a>""" for o in orders)
    rs = "".join(f"""
<div class="card"><div class="sub">⏰ {esc(r["note"])} — {fmt_date(r["due_date"])}</div>
<div class="row">{_post_btn(f'/nhac/{r["id"]}/xong', "✔ Xong", next_url=f'/khach/{c["id"]}')}</div></div>"""
        for r in reminders)

    dup_banner = ""
    if phone_dups:
        dup_rows = "".join(
            f'<div class="row" style="align-items:center;gap:8px;margin-top:6px">'
            f'<a href="/khach/{d["id"]}" style="flex:1">{esc(d["name"])} (#{d["id"]})</a>'
            f'<form method="post" action="/khach/{c["id"]}/gop" '
            f'onsubmit="return confirm(\'Gộp khách trùng này vào «{esc(c["name"])}»? '
            f'Mọi báo giá, đơn hàng, chăm sóc của bản trùng sẽ dồn về đây và bản trùng bị xoá — '
            f'không thể hoàn tác.\')">'
            f'<input type="hidden" name="dup_id" value="{d["id"]}">'
            f'<button type="submit" class="btn done">Gộp vào đây</button></form></div>'
            for d in phone_dups)
        dup_banner = (f'<div class="card" style="border-left:4px solid #b91c1c">'
                      f'<div class="sub" style="font-weight:700">⚠️ Trùng số điện thoại</div>'
                      f'<div class="sub">Cùng SĐT với các khách sau — gộp lại nếu là cùng một người:</div>'
                      f'{dup_rows}</div>')

    debt = ""
    if c["type"] == "DL":
        debt = (f'<h2>Công nợ</h2><a class="btn done" style="display:flex" href="/cong-no/{c["id"]}">'
                f'Sổ nợ — hiện nợ {fmt_vnd(balance)}</a>')

    if c.get("zalo_user_id"):
        zalo_block = f"""
<div class="card">
  <div class="sub">💬 Đã gắn Zalo (lần nhắn gần nhất: {fmt_date(c.get("zalo_last_inbound_at"))})</div>
  <form method="post" action="/khach/{c["id"]}/gui-zalo" style="margin-top:8px">
    <textarea name="text" placeholder="Nhắn qua Zalo API…" required></textarea>
    <button class="btn zalo" style="width:100%;margin-top:8px">📨 Gửi qua API</button>
  </form>
  <form method="post" action="/khach/{c["id"]}/huy-lien-ket-zalo" style="margin-top:6px">
    <button class="btn done" style="width:100%">Hủy gắn Zalo</button>
  </form>
</div>"""
    else:
        zalo_block = f"""
<details class="card"><summary style="font-weight:700;min-height:32px;display:flex;align-items:center">💬 Gắn Zalo (dán từ /zalo)</summary>
  <form method="post" action="/khach/{c["id"]}/lien-ket-zalo">
    <label>Zalo user id</label><input name="zalo_user_id" required placeholder="Dán id từ /zalo">
    <button class="btn big" style="margin-top:12px">Gắn</button>
  </form>
</details>"""

    return f"""
<div class="detail-cols">
<div class="detail-main">
{dup_banner}
<div class="card">
  <div class="name">{esc(c["name"])} {type_chip(c["type"])}</div>
  <div class="sub">{esc(c["phone"] or "—")} · {SOURCE_LABELS.get(c["source"], "")}</div>
  {f'<div class="sub">✉️ {esc(c["email"])}</div>' if c.get("email") else ""}
  {f'<div class="sub">📍 {esc(c["address"])}</div>' if c.get("address") else ""}
  {f'<div class="sub">📝 {esc(c["note"])}</div>' if c.get("note") else ""}
  {contact_buttons(c["phone"], c.get("zalo_phone") or "", customer_id=c["id"])}
  <div class="row"><a class="btn done" href="/khach/{c["id"]}/sua">✏️ Sửa</a></div>
</div>
{zalo_block}
<details class="card"><summary style="font-weight:700;min-height:32px;display:flex;align-items:center">⏰ + Nhắc lại (hẹn gọi sau)</summary>
  <form method="post" action="/khach/{c["id"]}/nhac">
    <label>Ngày nhắc</label><input type="date" name="due_date" required min="{today}" value="{today}">
    <label>Nội dung</label><input name="note" required placeholder="VD: gọi lại sau Tết">
    <button class="btn big" style="margin-top:12px">Lưu nhắc</button>
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
    <label style="margin-top:0">Ghi chú nhanh</label>
    <div class="row">
      <input name="note" required placeholder="VD: gọi rồi, hẹn tuần sau" style="flex:2">
      <button class="btn done" style="flex:1">Lưu</button>
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
  <button class="btn big" style="margin-top:12px">Xem trước</button>
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
  <button class="btn big" style="margin-top:12px">✔ Thêm {n_ok} khách</button>
</form>""" if n_ok else '<div class="empty">Không có dòng nào hợp lệ để thêm.</div>'
    return f"""{intro}
<div class="card" style="overflow-x:auto">
<table class="ledger"><tr><th>Tên</th><th>SĐT</th><th>Loại</th><th></th></tr>{rows}</table>
</div>
{confirm}
<a class="btn done" style="display:flex;margin-top:8px" href="/nhap">↩ Sửa lại danh sách</a>"""


# ---------------------------------------------------------------- quotes

def _quote_pipeline_controls(q: dict, next_url: str = "/bao-gia") -> str:
    """Đã gửi / Chốt / Mất controls shared by the báo giá list card and the
    quote detail page, so the two never drift. Chốt posts won → the server
    creates the đơn hàng and promotes the customer to khách chính. next_url
    only steers the 'Đã gửi' redirect; chốt/mất redirects are server-owned."""
    if q["status"] in ("sent", "chasing"):
        lost_opts = "".join(f'<option value="{v}">{label}</option>'
                            for v, label in LOST_REASON_LABELS.items())
        return f"""
  <div class="row">
    {_post_btn(f'/bao-gia/{q["id"]}/da-nhan', "✔ Đã gửi", next_url=next_url)}
    <form method="post" action="/bao-gia/{q["id"]}/trang-thai" style="flex:1;display:flex">
      <input type="hidden" name="trang_thai" value="won">
      <button class="btn call" style="width:100%">✔ Chốt</button></form>
  </div>
  <details style="margin-top:8px"><summary class="btn danger" style="display:flex">✖ Mất</summary>
    <form method="post" action="/bao-gia/{q["id"]}/trang-thai" style="margin-top:8px">
      <input type="hidden" name="trang_thai" value="lost">
      <select name="ly_do">{lost_opts}</select>
      <button class="btn danger" style="width:100%;margin-top:8px">Xác nhận mất</button>
    </form></details>"""
    if q["status"] == "won" and q["order_id"]:
        return (f'<div class="row"><a class="btn copy" '
                f'href="/don-hang/{q["order_id"]}">🔨 Xem tiến độ</a></div>')
    return ""


def quotes_page(rows: list, archived: list, summary: dict) -> str:
    header = (f'<div class="total">Đang theo dõi: {summary["n"]} báo giá — '
              f'{fmt_vnd_short(summary["total"])}</div>')
    cards = []
    for q in rows:
        actions = _quote_pipeline_controls(q, next_url="/bao-gia")
        lost = f' — {LOST_REASON_LABELS.get(q["lost_reason"], "")}' if q["status"] == "lost" and q["lost_reason"] else ""
        cards.append(f"""
<div class="card">
  <div class="name">{esc(q["customer_name"])} {status_chip(q["status"])}</div>
  <div class="sub">{bao_gia_so(q["id"], q.get("sent_date"))} · {esc(PRODUCT_LABELS.get(q["product"], "").capitalize())} — {fmt_vnd(q["value_vnd"])}
   — gửi {fmt_date(q["sent_date"])} ({q["days_sent"]} ngày){lost}</div>
  {f'<div class="sub">📝 {esc(q["description"])}</div>' if q.get("description") else ""}
  <div class="row" style="flex-wrap:wrap">
    <a class="btn done" href="/bao-gia/{q["id"]}">{"👁 Xem báo giá" if q.get("order_id") else "✎ Sửa thông tin"}</a>
    {'' if q.get("order_id") else f'<a class="btn done" href="/bao-gia/{q["id"]}/hang-muc/moi">＋ Hạng mục</a>'}
    {f'<a class="btn done" href="/bao-gia/{q["id"]}/xuat">⬇ Xuất báo giá</a>' if q.get("item_count") else ''}
  </div>
  {actions}
</div>""")
    body = "".join(cards) or '<div class="empty">Chưa có báo giá nào.</div>'
    kanban = _kanban_html(rows)
    archive = ""
    if archived:
        arch_cards = "".join(f"""
<a href="/don-hang/{q["order_id"]}"><div class="card" style="opacity:.6">
  <div class="name">{esc(q["customer_name"])} {status_chip(q["status"])}</div>
  <div class="sub">{bao_gia_so(q["id"], q.get("sent_date"))} · {esc(PRODUCT_LABELS.get(q["product"], "").capitalize())} — {fmt_vnd(q["value_vnd"])}</div>
</div></a>""" for q in archived)
        archive = f"""
<details class="card" style="margin-top:12px">
  <summary style="font-weight:700;min-height:32px;display:flex;align-items:center">🗄️ Đã hoàn thành ({len(archived)})</summary>
  <div class="cards-grid" style="margin-top:8px">{arch_cards}</div>
</details>"""
    return f"""
{header}
<a class="btn add" href="/khach/tiep-nhan">+ Khách hàng mới (tạo báo giá)</a>
<div class="quotes-list-mobile">{body}</div>
{kanban}
{archive}"""


# (statuses in this column, status posted on drop, label). "sent" and
# "chasing" share one visual column — customer's ask: 3 columns matching the
# actual shop workflow (báo giá gửi ra & đang theo dõi → chốt → mất), not the
# internal contacted/not-contacted distinction.
_KANBAN_COLS = ((("sent", "chasing"), "sent", "Đã gửi"),
                (("won",), "won", "Chốt"),
                (("lost",), "lost", "Mất"))


def _kanban_html(rows: list) -> str:
    """Desktop-only pipeline board (hidden <900px via CSS). Buckets the SAME
    rows already fetched for the mobile list — switching the segment-filter
    chip to "Tất cả" populates every column; the default "Đang theo dõi"
    segment only has sent/chasing cards, which is the existing filter
    behavior, not a kanban-specific limitation."""
    cols = []
    for statuses, drop_status, label in _KANBAN_COLS:
        col_rows = [q for q in rows if q["status"] in statuses]
        total = sum(q["value_vnd"] or 0 for q in col_rows)
        cards = "".join(f"""
<div class="kanban-card" draggable="true" ondragstart="kbDragStart(event,{q['id']})">
  <div class="n"><a href="/bao-gia/{q['id']}" draggable="false">{esc(q["customer_name"])}</a></div>
  <div>{esc(PRODUCT_LABELS.get(q["product"], "").capitalize())} — {fmt_vnd(q["value_vnd"])}</div>
  <div class="acts">
    <a href="/bao-gia/{q['id']}" draggable="false">{"👁 Xem" if q.get("order_id") else "✎ Sửa thông tin"}</a>
    {'' if q.get("order_id") else f'<a href="/bao-gia/{q["id"]}/hang-muc/moi" draggable="false">＋ Hạng mục</a>'}
    {f'<a href="/bao-gia/{q["id"]}/xuat" draggable="false">⬇ Xuất</a>' if q.get("item_count") else ''}
  </div>
  <div draggable="false">{_quote_pipeline_controls(q, next_url="/bao-gia")}</div>
</div>""" for q in col_rows)
        cols.append(f"""
<div class="kanban-col" data-status="{drop_status}" ondragover="kbAllowDrop(event)" ondrop="kbDrop(event,'{drop_status}')">
  <h3>{label} ({len(col_rows)}) · {fmt_vnd_short(total)}</h3>
  {cards}
</div>""")
    return f"""
<div class="kanban">{"".join(cols)}</div>
<script>
function kbAllowDrop(ev) {{ ev.preventDefault(); }}
function kbDragStart(ev, id) {{ ev.dataTransfer.setData('text/plain', id); }}
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
  <input type="hidden" name="mode" value="{esc(mode)}"><button>🔍</button>
</form>
{items}
<a class="btn done" style="display:flex;margin-top:8px" href="/khach/moi?next={target.strip("/").replace("/", "-")}">+ Thêm khách mới</a>"""


def quote_form_page(customer: dict, today: str) -> str:
    # quotes.product CHECK doesn't allow 'xingfa' (only multi-item quote_items
    # do) — exclude it here alongside the existing 'khac' exclusion.
    prods = "".join(
        f'<label><input type="radio" name="product" value="{v}" {"checked" if v == "cua_cuon" else ""}>'
        f'<span>{label.capitalize()}</span></label>'
        for v, label in PRODUCT_LABELS.items() if v not in ("khac", "xingfa")
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
  <button class="btn big" style="margin-top:18px">Lưu báo giá</button>
</form>"""


# ---------------------------------------------------------------- multi-item quotes (calculator)

_ACCESSORIES = PHUKIEN_CATALOG


def _phukien_rows(checked: dict = None) -> str:
    """Phụ kiện checkbox+qty rows, shared by the header-form (add) and the
    quote detail page's editable phụ kiện card. checked = {key: qty} to
    pre-tick/pre-fill; empty/None renders everything unchecked."""
    checked = checked or {}
    return "".join(f"""
<div class="row" style="align-items:center">
  <label style="flex:2;margin:0"><input type="checkbox" name="{key}_chk" value="1"
    onchange="toggleQty(this,'{key}_qty')" {"checked" if key in checked else ""}> {label}</label>
  <input type="number" name="{key}_qty" id="{key}_qty" min="1" value="{checked.get(key, 1)}"
    {"" if key in checked else "disabled"} style="flex:1" inputmode="numeric">
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


def quote_header_form_page(customer: dict, today: str) -> str:
    rows = _phukien_rows()
    return f"""
<div class="card"><div class="name">{esc(customer["name"])} {type_chip(customer["type"])}</div>
<div class="sub">{esc(customer["phone"] or "")}</div></div>
<form method="post" action="/bao-gia/nhieu-hang-muc/moi">
  <input type="hidden" name="customer_id" value="{customer["id"]}">
  <label>Phụ kiện</label>
  {rows}
  <label>Đã đặt cọc (VND)</label>
  <input name="deposit" inputmode="numeric" oninput="fmtMoney(this)" placeholder="VD: 2.000.000">
  <label>Ngày lắp đặt dự kiến</label>
  <input type="date" name="install_date" min="{today}">
  <label>Ghi chú</label>
  <textarea name="note"></textarea>
  <button class="btn big" style="margin-top:18px">Tiếp tục — thêm hạng mục</button>
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
    <a class="btn done" style="flex:1" href="/bao-gia/{qid}/hang-muc/{iid}/sua">✎ Sửa</a>
    <form method="post" action="/bao-gia/{qid}/hang-muc/{iid}/xoa" style="flex:1;display:flex">
      <button class="btn danger" style="width:100%">✖ Xóa</button>
    </form>
  </div>"""
    item_cards = "".join(f"""
<div class="card">
  <div class="name">{esc(PRODUCT_LABELS.get(i["product"], "").capitalize())}</div>
  <div class="sub">{esc(i["cong_nghe"] or "")} {esc(i["mau"] or "")} — {i["ngang_mm"]}×{i["cao_mm"]}mm
   {"(nhập tay)" if i["is_manual_price"] else ""}</div>
  <div class="sub" style="font-weight:700">{fmt_vnd(i["thanh_tien"])}</div>
  {item_actions.format(qid=quote["id"], iid=i["id"])}
</div>""" for i in items)

    locked_banner = (
        f'<div class="card" style="border-left:4px solid #0f4c81">'
        f'<div class="sub" style="font-weight:700">🔒 Đã chốt — báo giá đã khóa</div>'
        f'<div class="sub">Mọi thay đổi (hạng mục, cọc, ghi chú) làm trên đơn hàng.</div>'
        f'<a class="btn copy" style="display:flex;margin-top:6px" '
        f'href="/don-hang/{quote["order_id"]}">🔨 Mở đơn hàng #{quote["order_id"]}</a></div>'
    ) if locked else ""

    dep = quote.get("deposit_vnd") or 0
    dep_str = f"{dep:,}".replace(",", ".") if dep else ""
    deposit_card = f"""
<form method="post" action="/bao-gia/{quote["id"]}/dat-coc" class="card">
  <label style="margin-top:0">💵 Đã đặt cọc (VND) — sửa lúc nào cũng được</label>
  <div class="row" style="margin-top:6px">
    <input name="deposit" inputmode="numeric" oninput="fmtMoney(this)" value="{dep_str}"
      placeholder="VD: 2.000.000" style="flex:2">
    <button class="btn done" style="flex:1">Lưu cọc</button>
  </div>
  <div class="sub" style="margin-top:8px">Còn lại: <b>{fmt_vnd((quote.get("value_vnd") or 0) - dep)}</b></div>
</form>""" if items and not locked else ""

    notes_card = f"""
<form method="post" action="/bao-gia/{quote["id"]}/ghi-chu" class="card">
  <label style="margin-top:0">📅 Ngày lắp đặt dự kiến</label>
  <input type="date" name="install_date" value="{quote.get("install_date") or ""}">
  <label>📝 Ghi chú</label>
  <textarea name="note">{esc(quote.get("note") or "")}</textarea>
  <button class="btn done" style="margin-top:8px">Lưu ghi chú</button>
</form>""" if not locked else ""

    phukien_card = f"""
<form method="post" action="/bao-gia/{quote["id"]}/phu-kien" class="card">
  <label style="margin-top:0">🔧 Phụ kiện — sửa lúc nào cũng được</label>
  {_phukien_rows(_parse_accessories_to_keys(quote.get("accessories") or ""))}
  <button class="btn done" style="margin-top:8px">Lưu phụ kiện</button>
</form>
<script>
function toggleQty(cb, qtyId) {{
  var qty = document.getElementById(qtyId);
  qty.disabled = !cb.checked;
  if (cb.checked) qty.value = qty.value || 1;
}}
</script>""" if not locked else ""

    if items:
        ctype = quote["customer_type"]
        accessories = quote.get("accessories") or ""
        pk_items = pricing.phukien_line_items(accessories, items, ctype)
        pk_total = pricing.calc_phukien(accessories, items, ctype)
        door_total = sum(i["thanh_tien"] for i in items)
        tong_cong = quote.get("value_vnd") or (door_total + pk_total)
        vat = round(tong_cong * 0.1)
        tong_tien = tong_cong + vat
        _line = ('<div class="sub" style="display:flex;justify-content:space-between">'
                 '<span>{name}</span><b>{val}</b></div>')
        pk_rows = "".join(_line.format(name=f'{esc(p["name"])} ×{p["qty"]}', val=fmt_vnd(p["total"]))
                          for p in pk_items)
        pk_block = (f'<div style="margin-top:6px;padding-top:6px;border-top:1px solid #eee">'
                    f'<div class="sub" style="font-weight:700">Phụ kiện</div>{pk_rows}</div>'
                    if pk_items else "")
        summary = f"""
<div class="card">
  {_line.format(name=f"Cửa ({len(items)} hạng mục)", val=fmt_vnd(door_total))}
  {pk_block}
  <div style="margin-top:6px;padding-top:6px;border-top:1px solid #eee">
    {_line.format(name="Tổng cộng", val=fmt_vnd(tong_cong))}
    {_line.format(name="VAT (10%)", val=fmt_vnd(vat))}
  </div>
  <div class="total" style="display:flex;justify-content:space-between;margin-top:8px">
    <span>Tổng tiền</span><span>{fmt_vnd(tong_tien)}</span></div>
</div>"""
        export_btn = (f'<a class="btn copy" style="display:flex;margin-top:8px" '
                      f'href="/bao-gia/{quote["id"]}/xuat">📄 Xuất Báo Giá (Excel)</a>')
        finish = "" if locked else f"""
<form method="post" action="/bao-gia/{quote["id"]}/hoan-tat">
  <button class="btn big" style="margin-top:12px">✔ Xong, lưu báo giá</button>
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
  <div class="sub" style="font-weight:700">Tiến độ: {status_chip(quote["status"])}{lost_note}</div>
  {_quote_pipeline_controls(quote, next_url=f'/bao-gia/{quote["id"]}')}
</div>"""

    add_item_details = "" if locked else f"""
<details class="card">
  <summary class="btn add" style="display:flex">➕ Thêm cửa</summary>
  <div style="margin-top:12px">{_quote_item_form_body(quote)}</div>
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
<a class="btn done" style="display:flex;margin-top:8px" href="/bao-gia">↩ Quay lại danh sách</a>"""


def _quote_item_form_body(quote: dict, item: dict = None) -> str:
    """Loại cửa radios + dynamic fields + kích thước/giá form — the add/edit
    hạng mục form, without the customer-name card header. Shared by the
    standalone add/edit page (quote_item_form_page) and the "➕ Thêm cửa"
    collapsible on quote_build_page, so adding an item doesn't require
    navigating away from the build page first."""
    customer_type = quote["customer_type"]
    products = list(DOOR_CONFIG.items())
    radios, blocks = [], []
    for i, (key, cfg) in enumerate(products):
        active = (item["product"] == key) if item else (i == 0)
        radios.append(
            f'<label><input type="radio" name="loai_cua" value="{key}" '
            f'{"checked" if active else ""} onchange="selectProduct(\'{key}\')">'
            f'<span>{esc(cfg["label"])}</span></label>'
        )
        field_html = []
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
    div.querySelectorAll('select').forEach(function(sel) {{ sel.disabled = !active; }});
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
  if (price && ngang && cao) {{
    var total = Math.floor(price * ngang * cao / 1000);
    preview.textContent = 'Ước tính: ' + total.toLocaleString('vi-VN') + 'đ (theo bảng giá)';
    manualWrap.style.display = 'none';
    manualInput.required = false;
  }} else {{
    preview.textContent = 'Không có bảng giá cho lựa chọn này — nhập giá tay bên dưới';
    manualWrap.style.display = 'block';
    manualInput.required = true;
  }}
}}
function prefillEdit(product, values) {{
  var radio = document.querySelector('input[name=loai_cua][value="' + product + '"]');
  if (radio) radio.checked = true;
  selectProduct(product);
  var cfg = DOOR_CONFIG[product];
  cfg.fields.filter(function(f) {{ return f.type !== 'select-dynamic'; }}).forEach(function(f) {{
    var el = document.getElementById('f_' + product + '_' + f.key);
    if (el && values[f.key] !== undefined) el.value = values[f.key];
  }});
  cfg.fields.filter(function(f) {{ return f.type === 'select-dynamic'; }}).forEach(function(f) {{
    onFieldChanged(product, f.depends_on);
    var el = document.getElementById('f_' + product + '_' + f.key);
    if (el && values[f.key] !== undefined) el.value = values[f.key];
  }});
  document.getElementById('ngang').value = values.ngang || '';
  document.getElementById('cao').value = values.cao || '';
  if (values.gia_thu_cong) document.getElementById('gia_thu_cong').value = values.gia_thu_cong;
  updatePreview();
}}
document.addEventListener('DOMContentLoaded', updatePreview);
</script>"""

    if item:
        prefill = f"""
<script>document.addEventListener('DOMContentLoaded', function() {{
  prefillEdit({json.dumps(item["product"], ensure_ascii=False)}, {{
    cong_nghe: {json.dumps(item["cong_nghe"] or "", ensure_ascii=False)},
    mau: {json.dumps(item["mau"] or "", ensure_ascii=False)},
    ngang: {item["ngang_mm"]}, cao: {item["cao_mm"]},
    gia_thu_cong: {item["thanh_tien"] if item["is_manual_price"] else 0}
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
      <input id="ngang" name="ngang" inputmode="numeric" maxlength="4"
        oninput="this.value=this.value.replace(/\\D/g,'').slice(0,4);updatePreview()" placeholder="VD: 3000">
    </div>
    <div class="unit-field">
      <label>Cao (mm)</label>
      <input id="cao" name="cao" inputmode="numeric" maxlength="4"
        oninput="this.value=this.value.replace(/\\D/g,'').slice(0,4);updatePreview()" placeholder="VD: 2200">
    </div>
  </div>
  <div class="total" id="price_preview">Chọn đầy đủ để xem giá</div>
  <div id="manual_price_wrap" style="display:none">
    <label>Giá nhập tay (VND)</label>
    <input id="gia_thu_cong" name="gia_thu_cong" inputmode="numeric" oninput="fmtMoney(this)" placeholder="VD: 12.000.000">
  </div>
  <button class="btn big" style="margin-top:18px">{submit_label}</button>
</form>
{script}{prefill}"""


def quote_item_form_page(quote: dict, item: dict = None) -> str:
    return (
        f'<div class="card"><div class="name">{esc(quote["customer_name"])} '
        f'{type_chip(quote["customer_type"])}</div></div>'
        + _quote_item_form_body(quote, item)
    )


# ---------------------------------------------------------------- orders

def orders_page(active: list, completed: list, today: str) -> str:
    """Tiến độ sản xuất — split into đang làm (default view) and đã hoàn thành
    (archived, collapsed — only shows when opened). Đơn hàng only ever come
    from a báo giá đã chốt (store.create_order_from_quote); there's no manual
    "+ Đơn hàng" entry point."""
    def _card(o: dict, dim: bool = False) -> str:
        badges = ""
        if o.get("urgent"):
            badges += '<span class="chip urgent">🔥 Gấp</span> '
        badges += stage_badge(o.get("stage") or "cho_san_xuat")
        if o["expiry_date"]:
            if o["expiry_date"] < today:
                badges += ' <span class="chip sent">Hết BH</span>'
            elif o["expiry_date"] <= _add_days(today, 30):
                badges += ' <span class="chip warn">Sắp hết BH</span>'
        bal = o.get("balance_vnd", 0) if o.get("customer_type") == "KH" else 0
        if bal > 0:
            badges += f' <span class="chip warn">còn nợ {fmt_vnd(bal)}</span>'
        inst = f"lắp {fmt_date(o['install_date'])}" if o["install_date"] else "Chưa lắp"
        style = ' style="opacity:.6"' if dim else ""
        actions = ""
        if bal > 0:
            actions = f"""
<div class="row" style="margin-top:6px">
  <details style="flex:1"><summary class="btn done" style="text-align:center">✏️ Điều chỉnh</summary>
    <form method="post" action="/don-hang/{o["id"]}/thanh-toan" style="margin-top:6px">
      <input type="hidden" name="next" value="/don-hang">
      <input name="amount_vnd" required inputmode="numeric" oninput="fmtMoney(this)" placeholder="Số tiền đã thu thêm">
      <button class="btn big" style="margin-top:6px;width:100%">Ghi thanh toán</button>
    </form>
  </details>
  <form method="post" action="/don-hang/{o["id"]}/thu-du" style="flex:1"
    onsubmit="return confirm('Đánh dấu đã thu đủ đơn #{o["id"]}?')">
    <input type="hidden" name="next" value="/don-hang">
    <button class="btn call" style="width:100%">✅ Đã thanh toán</button>
  </form>
</div>"""
        return f"""
<div class="card"{style}>
  <a href="/don-hang/{o["id"]}" style="display:block">
    <div class="name">{esc(o["customer_name"])}</div>
    <div class="sub">{badges}</div>
    <div class="sub">{esc(PRODUCT_LABELS.get(o["product"], "").capitalize())} — {fmt_vnd(o["value_vnd"])} — {inst}</div>
  </a>
  {actions}
</div>"""

    active_body = (f'<div class="cards-grid">{"".join(_card(o) for o in active)}</div>' if active
                   else '<div class="empty">Chưa có đơn hàng nào đang làm.</div>')
    archive = ""
    if completed:
        archive = f"""
<details class="card" style="margin-top:12px">
  <summary style="font-weight:700;min-height:32px;display:flex;align-items:center">🗄️ Đã hoàn thành ({len(completed)})</summary>
  <div class="cards-grid" style="margin-top:8px">{"".join(_card(o, dim=True) for o in completed)}</div>
</details>"""
    return f"{active_body}{archive}"


def _add_days(d: str, n: int) -> str:
    from datetime import datetime, timedelta
    return (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


def order_detail_page(o: dict, calls: list, today: str,
                      quote: dict = None, items: list = None, just_won: bool = False,
                      payments: list = None) -> str:
    label = PRODUCT_LABELS.get(o["product"], "sản phẩm")
    items = items or []
    payments = payments or []
    stage_now = o.get("stage") or "cho_san_xuat"

    # ── Production handoff: copy bộ cửa → dán vào nhóm Zalo ────────────────
    # Doors only — phụ kiện/generic invoice lines have no kích thước and the
    # message derives phụ kiện from the quote's accessories itself.
    door_items = [i for i in items if i.get("ngang_mm") and i.get("cao_mm")]
    handoff = ""
    if door_items:
        zalo_msg = production_message(o, quote, door_items)
        banner = ('<div class="card" style="background:#ecfdf5;border-left:4px solid #059669">'
                  '<b>✓ Đã chốt!</b> Sao chép bộ cửa rồi dán vào nhóm Zalo để xưởng bắt đầu làm.</div>'
                  if just_won else "")
        handoff = f'{banner}<div class="card">{copy_zalo_button(zalo_msg)}</div>'

    # ── Hạng mục hóa đơn: display + inline edit + printable invoice ────────
    # Quote items only show as a read-only fallback (orders predating the
    # order_items backfill); edit/invoice work on real order items.
    is_order_items = bool(items) and "order_id" in items[0]
    edit_rows = ""
    if is_order_items:
        edit_rows = "".join(f"""
<form method="post" action="/don-hang/{o["id"]}/hang-muc/{i["id"]}/sua" class="row" style="margin-top:6px">
  <input name="description" value="{esc(i.get("description") or _door_desc(i))}" style="flex:3">
  <input name="so_luong" type="number" min="1" value="{i.get("so_luong", 1)}" style="flex:0 0 56px">
  <input name="thanh_tien" inputmode="numeric" value="{i["thanh_tien"]}" style="flex:2">
  <button class="btn done" style="flex:0 0 60px">Lưu</button>
</form>
<form method="post" action="/don-hang/{o["id"]}/hang-muc/{i["id"]}/xoa" class="row" style="margin-top:2px">
  <button class="btn danger" style="width:100%;font-size:12px">Xóa dòng trên</button>
</form>""" for i in items)
    item_editor = f"""
<details class="card"><summary style="font-weight:700;min-height:32px;display:flex;align-items:center">✏️ Sửa hạng mục / thêm dòng</summary>
{edit_rows}
<form method="post" action="/don-hang/{o["id"]}/hang-muc/moi" style="margin-top:12px;border-top:1px solid #e5e7eb;padding-top:8px">
  <label>Nội dung dòng mới</label><input name="description" required placeholder="VD: Phí vận chuyển, Motor YH 300kg">
  <div class="row">
    <div style="flex:1"><label>SL</label><input name="so_luong" type="number" min="1" value="1"></div>
    <div style="flex:2"><label>Thành tiền (VND)</label><input name="thanh_tien" required inputmode="numeric" placeholder="VD: 500.000"></div>
  </div>
  <button class="btn big" style="margin-top:12px">+ Thêm dòng</button>
</form></details>"""
    hoa_don_btn = (f'<div class="row" style="margin-top:8px">'
                   f'<a class="btn copy" href="/don-hang/{o["id"]}/hoa-don">🧾 Xem hóa đơn</a></div>'
                   if is_order_items else "")
    bo_cua_block = ((f'<div class="card"><div class="sub" style="font-weight:700">Hạng mục ({len(items)})</div>'
                     f'{bo_cua_html(items)}{hoa_don_btn}</div>' if items else "")
                    + item_editor)

    # ── Tiến độ: stage stepper + Gấp toggle ───────────────────────────────
    stages_row = "".join(
        f'<form method="post" action="/don-hang/{o["id"]}/giai-doan" style="flex:1;display:flex">'
        f'<input type="hidden" name="stage" value="{k}">'
        f'<button class="btn {"call" if k == stage_now else "done"}" style="width:100%;font-size:13px"'
        f'{" disabled" if k == stage_now else ""}>{esc(lbl)}</button></form>'
        for k, lbl in STAGE_LABELS.items()
    )
    if o.get("urgent"):
        gap_btn = (f'<form method="post" action="/don-hang/{o["id"]}/gap" style="flex:1;display:flex">'
                   f'<input type="hidden" name="urgent" value="0">'
                   f'<button class="btn done" style="width:100%">✅ Bỏ đánh dấu Gấp</button></form>')
    else:
        gap_btn = (f'<form method="post" action="/don-hang/{o["id"]}/gap" style="flex:1;display:flex">'
                   f'<input type="hidden" name="urgent" value="1">'
                   f'<button class="btn danger" style="width:100%">🔥 Đánh dấu Gấp (làm trước)</button></form>')
    urgent_banner = ('<div class="card" style="background:#fef2f2;border-left:4px solid #b91c1c">'
                     '<b>🔥 ĐƠN GẤP</b> — ưu tiên làm trước</div>' if o.get("urgent") else "")
    tien_do = f"""
<h2>Tiến độ sản xuất</h2>
<div class="card">
  <div class="sub" style="margin-bottom:8px">Giai đoạn hiện tại: {stage_badge(stage_now)}</div>
  <div class="row" style="flex-wrap:wrap;gap:6px">{stages_row}</div>
  <div class="row" style="margin-top:8px">{gap_btn}</div>
</div>"""

    svc = "".join(
        f'<div class="card"><div class="sub">🔧 {fmt_date(s["call_date"])} — {esc(s["issue"])}'
        f'{" → " + esc(s["resolution"]) if s["resolution"] else ""}</div></div>'
        for s in calls
    )
    flags = []
    if o["install_date"]:
        if not o["checkin_done_at"]:
            flags.append(_post_btn(f'/don-hang/{o["id"]}/da-bao-tri', "✔ Đã bảo trì 6T",
                                   next_url=f'/don-hang/{o["id"]}'))
        if not o["expiry_notified_at"]:
            flags.append(_post_btn(f'/don-hang/{o["id"]}/da-nhan-bh', "✔ Đã nhắn BH",
                                   next_url=f'/don-hang/{o["id"]}'))
        if not o["review_requested_at"] and o["customer_type"] == "KH":
            flags.append(_post_btn(f'/don-hang/{o["id"]}/da-xin-danh-gia', "✔ Đã xin đánh giá",
                                   next_url=f'/don-hang/{o["id"]}'))
    else:
        flags.append(f"""
<details class="card" style="flex:1"><summary style="font-weight:700">📅 Ghi ngày lắp đặt</summary>
<form method="post" action="/don-hang/{o["id"]}/ngay-lap">
  <input type="date" name="install_date" required value="{today}">
  <button class="btn big" style="margin-top:8px">Lưu ngày lắp</button>
</form></details>""")

    debt_link = ""
    if o["customer_type"] == "DL":
        debt_link = (f'<a class="btn done" href="/cong-no/{o["customer_id"]}/them?loai=charge'
                     f'&don={o["id"]}&tien={o["value_vnd"] or ""}">💰 Ghi nợ đơn này</a>')

    # ── Thanh toán / còn nợ (KH only) ─────────────────────────────────────
    # Retail jobs track cọc + thanh toán here; dealers (ĐL) use công nợ (above).
    payment_block = ""
    if o["customer_type"] == "KH":
        paid, bal = o.get("paid_vnd", 0), o.get("balance_vnd", 0)
        kind_lbl = {"coc": "Cọc", "thanh_toan": "Thanh toán"}
        pay_rows = "".join(
            '<div class="sub" style="display:flex;justify-content:space-between;align-items:center;gap:8px">'
            f'<span>{fmt_date(p["pay_date"])} · {kind_lbl.get(p["kind"], p["kind"])}'
            f'{" · " + esc(p["method"]) if p.get("method") else ""}</span>'
            '<span style="display:flex;align-items:center;gap:8px">'
            f'<b>{fmt_vnd(p["amount_vnd"])}</b>'
            f'<form method="post" action="/don-hang/{o["id"]}/thanh-toan/{p["id"]}/xoa" '
            "onsubmit=\"return confirm('Xóa khoản này? Đơn sẽ trở lại còn nợ.')\">"
            f'<input type="hidden" name="next" value="/don-hang/{o["id"]}">'
            '<button title="Xóa khoản thanh toán" '
            'style="background:none;border:none;color:#b91c1c;cursor:pointer;font-size:14px;padding:0">✕</button>'
            "</form></span></div>"
            for p in payments
        ) or '<div class="sub">Chưa có thanh toán nào.</div>'
        bal_color = "#059669" if bal <= 0 else "#b91c1c"
        payment_block = f"""
<h2>Thanh toán</h2>
<div class="card">
  <div class="sub" style="display:flex;justify-content:space-between"><span>Giá trị đơn (gồm VAT)</span><b>{fmt_vnd(o["value_vnd"])}</b></div>
  <div class="sub" style="display:flex;justify-content:space-between"><span>Đã thu</span><b>{fmt_vnd(paid)}</b></div>
  <div class="total" style="display:flex;justify-content:space-between;margin-top:6px">
    <span>Còn lại</span><span style="color:{bal_color}">{fmt_vnd(bal)}</span></div>
  <div style="margin-top:8px;padding-top:8px;border-top:1px solid #eee">{pay_rows}</div>
</div>
<details class="card"><summary style="font-weight:700;min-height:32px;display:flex;align-items:center">💵 + Ghi thanh toán</summary>
<form method="post" action="/don-hang/{o["id"]}/thanh-toan" style="margin-top:8px">
  <div class="row">
    <div style="flex:1"><label style="margin-top:0">Loại</label>
      <select name="kind"><option value="thanh_toan">Thanh toán</option><option value="coc">Cọc</option></select></div>
    <div style="flex:1"><label style="margin-top:0">Hình thức</label>
      <select name="method"><option value="Tiền mặt">Tiền mặt</option><option value="Chuyển khoản">Chuyển khoản</option><option value="ZaloPay">ZaloPay</option></select></div>
  </div>
  <label>Số tiền (VND)</label>
  <input name="amount_vnd" required inputmode="numeric" oninput="fmtMoney(this)" placeholder="VD: 5.000.000">
  <label>Ngày</label><input type="date" name="pay_date" value="{today}">
  <button class="btn big" style="margin-top:12px">+ Ghi thanh toán</button>
</form></details>"""

    return f"""
{urgent_banner}
<div class="card">
  <div class="name">{esc(o["customer_name"])} {type_chip(o["customer_type"])} {stage_badge(stage_now)}</div>
  <div class="sub">{esc(label.capitalize())} — {fmt_vnd(o["value_vnd"])}</div>
  <div class="sub">{"Lắp " + fmt_date(o["install_date"]) if o["install_date"] else "Chưa lắp"}
   · BH {o["warranty_months"]} tháng{f" · hết BH {fmt_date(o['expiry_date'])}" if o["expiry_date"] else ""}</div>
  {f'<div class="sub">📍 {esc(o["address"])}</div>' if o.get("address") else ""}
  {f'<div class="sub">📝 {esc(o["description"])}</div>' if o.get("description") else ""}
  {contact_buttons(o["phone"], o["zalo_phone"])}
  <div class="row"><a class="btn done" href="/khach/{o["customer_id"]}">👤 Xem khách</a>{debt_link}</div>
</div>
<details class="card"><summary style="font-weight:700;min-height:32px;display:flex;align-items:center;color:#b91c1c">🗑️ Xóa đơn hàng</summary>
  <div class="sub" style="margin-top:6px">Xóa hạng mục, thanh toán, sửa chữa của đơn này. Báo giá gốc quay lại "Đã gửi" để chốt lại nếu cần. Lịch sử chăm sóc của khách không bị mất.</div>
  <form method="post" action="/don-hang/{o["id"]}/xoa" style="margin-top:8px"
    onsubmit="return confirm('Xóa đơn hàng #{o["id"]}? Không thể hoàn tác.')">
    <button class="btn danger" style="width:100%">Xóa đơn hàng #{o["id"]}</button>
  </form>
</details>
{handoff}
{bo_cua_block}
{payment_block}
{tien_do}
<h2>Lắp đặt & bảo hành</h2>
<div class="row">{"".join(flags)}</div>
<h2>Sửa chữa / bảo hành ({len(calls)})</h2>
{svc or '<div class="empty">Chưa có lần sửa nào.</div>'}
<details class="card"><summary style="font-weight:700;min-height:32px;display:flex;align-items:center">🔧 + Ghi sửa chữa</summary>
<form method="post" action="/don-hang/{o["id"]}/sua-chua">
  <label>Vấn đề</label><input name="issue" required placeholder="VD: thay pin remote, cửa kêu">
  <label>Xử lý</label><input name="resolution" placeholder="VD: đã thay pin, tra dầu">
  <button class="btn big" style="margin-top:12px">Lưu</button>
</form></details>"""


def invoice_page(o: dict, items: list, company: dict, today: str) -> str:
    """Printable hóa đơn for an order: company header, customer block, line-item
    table, Tổng cộng / VAT 10% / Tổng tiền (same math as production_message and
    the báo giá xlsx). Internal document — real VAT e-invoices (hoá đơn đỏ) come
    from the government e-invoice provider."""
    rows = []
    for idx, i in enumerate(items, 1):
        kich = (f' — {i["ngang_mm"]}×{i["cao_mm"]}mm'
                if i.get("ngang_mm") and i.get("cao_mm") else "")
        mau_sac = f' · {esc(i["mau_sac"])}' if i.get("mau_sac") else ""
        sl = i.get("so_luong", 1) or 1
        don_gia = i.get("don_gia") if i.get("don_gia") is not None else i["thanh_tien"] // sl
        rows.append(
            f'<tr><td>{idx}</td><td>{esc(_door_desc(i))}{kich}{mau_sac}</td>'
            f'<td class="num">{sl}</td><td class="num">{fmt_vnd(don_gia)}</td>'
            f'<td class="num">{fmt_vnd(i["thanh_tien"])}</td></tr>'
        )
    tong_cong = sum(i["thanh_tien"] for i in items)
    vat = round(tong_cong * 0.1)
    tong_tien = tong_cong + vat
    contact = " · ".join(x for x in (company.get("phone"), company.get("address")) if x)
    kh_lines = "".join(
        f'<div class="sub">{label} {esc(val)}</div>'
        for label, val in (("👤", o.get("customer_name")), ("📞", o.get("phone")),
                           ("✉️", o.get("email")), ("📍", o.get("address")))
        if val
    )
    return f"""
<style>
.inv table {{ width:100%; border-collapse:collapse; margin-top:10px }}
.inv th, .inv td {{ border:1px solid #d1d5db; padding:6px 8px; text-align:left; font-size:14px }}
.inv td.num, .inv th.num {{ text-align:right; white-space:nowrap }}
.inv .totals td {{ border:none; padding:3px 8px }}
@media print {{ .top, .nav, .no-print, .toast {{ display:none !important }} .wrap {{ max-width:none }} }}
</style>
<div class="inv">
<div class="card">
  <div style="font-size:20px;font-weight:800;color:#0f4c81">{esc(company.get("name", ""))}</div>
  <div class="sub">{esc(company.get("tagline", ""))}</div>
  {f'<div class="sub">{esc(contact)}</div>' if contact else ""}
  <div class="sub" style="margin-top:8px;font-weight:700">HÓA ĐƠN #{o["id"]} — ngày {fmt_date(o.get("install_date") or today)}</div>
  {kh_lines}
  <table>
    <tr><th>STT</th><th>Nội dung</th><th class="num">SL</th><th class="num">Đơn giá</th><th class="num">Thành tiền</th></tr>
    {"".join(rows) or '<tr><td colspan="5">Chưa có hạng mục nào.</td></tr>'}
    <tr class="totals"><td colspan="3"></td><td class="num">Tổng cộng</td><td class="num">{fmt_vnd(tong_cong)}</td></tr>
    <tr class="totals"><td colspan="3"></td><td class="num">VAT 10%</td><td class="num">{fmt_vnd(vat)}</td></tr>
    <tr class="totals"><td colspan="3"></td><td class="num"><b>Tổng tiền</b></td><td class="num"><b>{fmt_vnd(tong_tien)}</b></td></tr>
  </table>
  <div class="row no-print" style="margin-top:12px">
    <button type="button" class="btn copy" onclick="window.print()">🖨️ In hóa đơn</button>
    <a class="btn done" href="/don-hang/{o["id"]}">← Về đơn hàng</a>
  </div>
</div>
</div>"""


# ---------------------------------------------------------------- công nợ

def debts_page(dealer_rows: list, customer_rows: list, loc: str, today: str) -> str:
    """Công nợ tab. ``loc`` filters the view: 'tat-ca' (both), 'kh' (retail
    khách lẻ — orders still owing, from order_payments) or 'dl' (đại lý ledger).
    Dealers link to their sổ nợ; each unpaid KH order gets a Đã thanh toán
    button that settles the full remaining balance."""
    seg = "".join(
        f'<a class="btn {"call" if loc == key else "done"}" href="/cong-no?loc={key}" '
        f'style="flex:1;text-align:center">{lbl}</a>'
        for key, lbl in (("tat-ca", "Tất cả"), ("kh", "Khách hàng"), ("dl", "Đại lý"))
    )
    out = [f'<div class="row" style="margin-bottom:10px">{seg}</div>']

    if loc in ("tat-ca", "dl"):
        if loc == "tat-ca":
            out.append("<h2>Đại lý (sổ nợ)</h2>")
        cards = []
        for d in dealer_rows:
            days = _days_since(d["last_payment"] or d["first_charge"], today)
            style = ' style="border-left:4px solid #b91c1c"' if days >= 30 else ""
            last = f"trả lần cuối {fmt_date(d['last_payment'])}" if d["last_payment"] else "chưa trả lần nào"
            cards.append(f"""
<div class="card"{style}>
  <a href="/cong-no/{d["id"]}">
    <div class="name">{esc(d["name"])} <span class="chip warn">{fmt_vnd(d["balance"])}</span> {type_chip("DL")}</div>
    <div class="sub">{last} ({days} ngày)</div>
  </a>
  <form method="post" action="/cong-no/{d["id"]}/thanh-toan-du" style="margin-top:8px"
    onsubmit="return confirm('Đại lý {esc(d["name"])} đã trả đủ {fmt_vnd(d["balance"])}?')">
    <input type="hidden" name="next" value="/cong-no?loc={esc(loc)}">
    <button class="btn done" style="width:100%">✅ Đã thanh toán</button>
  </form>
</div>""")
        out.append("".join(cards) or '<div class="empty">Không có đại lý nào đang nợ 🎉</div>')

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
  <form method="post" action="/don-hang/{o["id"]}/thu-du" style="margin-top:8px"
    onsubmit="return confirm('Đánh dấu đã thu đủ đơn #{o["id"]}? Đơn sẽ rời khỏi danh sách nợ.')">
    <input type="hidden" name="next" value="/cong-no?loc={esc(loc)}">
    <button class="btn done" style="width:100%">✅ Đã thanh toán</button>
  </form>
</div>""")
        out.append("".join(cards) or '<div class="empty">Không có khách lẻ nào còn nợ 🎉</div>')

    return "".join(out)


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
  <button class="btn danger" style="padding:2px 8px">✖</button></form></td></tr>""" for e in entries)
    msg = render("debt_reminder", ten=c["name"], so_tien=fmt_vnd(balance)) if balance > 0 else ""
    settle = (f"""
<form method="post" action="/cong-no/{c["id"]}/thanh-toan-du" style="margin-bottom:10px"
  onsubmit="return confirm('{esc(c["name"])} đã trả đủ {fmt_vnd(balance)}? Sổ nợ sẽ về 0.')">
  <button class="btn done" style="width:100%">✅ Đã thanh toán đủ</button>
</form>""" if balance > 0 else "")
    return f"""
<div class="total">Hiện nợ: {fmt_vnd(balance)}</div>
<div class="card">
  <div class="name">{esc(c["name"])}</div>
  <div class="sub">{esc(c["phone"] or "")}</div>
  {contact_buttons(c["phone"], c.get("zalo_phone") or "", msg)}
</div>
<div class="row" style="margin-bottom:10px">
  <a class="btn danger" href="/cong-no/{c["id"]}/them?loai=charge">+ Ghi nợ</a>
  <a class="btn call" href="/cong-no/{c["id"]}/them?loai=payment">+ Thanh toán</a>
</div>
{settle}
<div class="card" style="overflow-x:auto">
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
  <button class="btn big" style="margin-top:18px">Lưu</button>
</form>"""


# ---------------------------------------------------------------- báo cáo

def reports_page(r: dict) -> str:
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
        f'<h2>Lý do mất</h2><div class="card" style="overflow-x:auto">'
        f'<table class="ledger"><tr><th>Lý do</th><th>Số lượng</th></tr>{ly_do_rows}</table></div>'
        if r["ly_do_mat"] else
        '<h2>Lý do mất</h2><div class="empty">Không có báo giá mất trong tháng.</div>'
    )

    sp_rows = "".join(
        f'<tr><td>{esc(PRODUCT_LABELS.get(x["product"], x["product"]))}</td><td>{fmt_vnd(x["total"])}</td></tr>'
        for x in r["doanh_thu_theo_sp"]
    )
    sp_block = (
        f'<h2>Doanh thu theo sản phẩm</h2><div class="card" style="overflow-x:auto">'
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
        f'<h2>Chăm sóc khách hàng</h2><div class="card" style="overflow-x:auto">'
        f'<table class="ledger"><tr><th>Loại</th><th>Số lượt</th></tr>{cs_rows}</table></div>'
        if r["cham_soc"] else
        '<h2>Chăm sóc khách hàng</h2><div class="empty">Chưa có lượt chăm sóc nào trong tháng.</div>'
    )

    return f"""
<form method="get" action="/bao-cao" class="row" style="margin-bottom:12px">
  <input type="month" name="thang" value="{esc(month)}" style="flex:2">
  <button class="btn done" style="flex:1">Xem</button>
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
        status = ('<div class="card"><div class="sub">✅ Đã kết nối Zalo OA</div>'
                  '<a class="btn done" style="display:flex;margin-top:8px" href="/zalo/oauth/start">'
                  'Kết nối lại</a></div>')
    else:
        status = ('<div class="card"><div class="sub">Chưa kết nối — bấm để đăng nhập Zalo và cấp quyền</div>'
                  '<a class="btn zalo" style="display:flex;margin-top:8px" href="/zalo/oauth/start">'
                  'Kết nối Zalo OA</a></div>')

    if not bot_configured:
        bot_status = '<div class="card"><div class="sub">Chưa cấu hình BOT_URL/BOT_TOKEN trong .env</div></div>'
    elif bot_health is None:
        bot_status = ('<div class="card"><div class="sub">⚠️ Không kết nối được bot — '
                      'kiểm tra container htp-crm-bot</div></div>')
    elif bot_health.get("awaitingQR"):
        bot_status = ('<div class="card"><div class="sub">📷 Đang chờ quét mã QR — dùng tài khoản Zalo phụ để quét</div>'
                      '<img src="/zalo/bot/qr" style="max-width:240px;display:block;margin-top:8px" /></div>')
    elif bot_health.get("loggedIn"):
        bot_status = ('<div class="card"><div class="sub">✅ Bot đã kết nối</div>'
                      '<form method="post" action="/zalo/bot/gui" style="margin-top:8px">'
                      '<button class="btn done" style="width:100%" type="submit">'
                      '📤 Gửi bảng công việc vào nhóm</button></form></div>')
    else:
        bot_status = '<div class="card"><div class="sub">⚠️ Bot mất kết nối</div></div>'
    bot_html = f"<h2>🤖 Bot nhóm</h2>{bot_status}"

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
