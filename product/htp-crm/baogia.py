"""Báo Giá export — renders a quote as an .xlsx laid out like the HTP branded
quote template (``Quotation_Template_HTP.xlsx``).

Layout (cols A–H):
  A STT · B Nội dung · C Kích thước (m) · D SL · E Diện tích (m²)
  F Đơn giá · G Thành tiền · H Ghi chú

Sections: company header → customer block → I. HẠNG MỤC CỬA (door lines) →
II. PHỤ KIỆN → summary (Cộng tiền hàng / Thuế VAT 10% / TỔNG CỘNG) → bằng chữ →
IV. CAM KẾT & ĐIỀU KHOẢN. Door lines with a table price are recomputed here
(đơn giá = table đ/m² + small-door surcharge; thành tiền = đơn giá×area + flat)
so the sheet is internally consistent; manual-priced lines use the stored value.
"""
import io
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.properties import PageSetupProperties

import pricing
from store import bao_gia_so

_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

# Palette echoes the branded template: navy headers, orange logo, red total.
_INK = "FF212121"
_NAVY = "FF1A365D"
_ORANGE = "FFE8722A"
_RED = "FFD9534F"
_GREY = "FF666666"
_WHITE = "FFFFFFFF"
_LINE = Side(style="thin", color="FFBDBDBD")
_BORDER = Border(_LINE, _LINE, _LINE, _LINE)
_FONT = "Arial"

_COL_WIDTHS = {"A": 5, "B": 35, "C": 15, "D": 8, "E": 12, "F": 15, "G": 18, "H": 15}
_VND = '#,##0" ₫"'
_HEADERS = ["STT", "Nội dung", "Kích thước (m)", "SL", "Diện tích (m²)",
            "Đơn giá", "Thành tiền", "Ghi chú"]
_HIEU_LUC = "15 ngày"  # quote validity shown in the customer block

_DIGITS = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]
_SCALE = ["", "nghìn", "triệu"]  # within one 'tỷ' block (10^0 / 10^3 / 10^6)


def _scale_word(i: int) -> str:
    """Scale name for the i-th group of thousands. Repeats 'tỷ' every 10^9 so a
    fat-fingered total (an extra zero pushing a quote past 999 tỷ) still spells
    out instead of crashing the export. i=3 -> 'tỷ', 4 -> 'nghìn tỷ', 6 -> 'tỷ tỷ'."""
    return (_SCALE[i % 3] + " tỷ" * (i // 3)).strip()


def _ascii_upper(s: str) -> str:
    """'Anh Hùng — Trần Phú' -> 'ANH HUNG TRAN PHU' (safe for a filename)."""
    s = (s or "").replace("Đ", "D").replace("đ", "d")
    nfd = unicodedata.normalize("NFD", s)
    stripped = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    ascii_only = "".join(c if (c.isalnum() or c in " -_") else " " for c in stripped)
    return " ".join(ascii_only.split()).upper()


def _read_group(num: int, full: bool) -> list[str]:
    """Vietnamese words for a 0–999 group. ``full`` forces 'không trăm' /
    'lẻ' connectors when the group follows a higher, already-spoken group."""
    h, t, u = num // 100, (num // 10) % 10, num % 10
    w: list[str] = []
    if h > 0:
        w += [_DIGITS[h], "trăm"]
    elif full and (t > 0 or u > 0):
        w += ["không", "trăm"]
    if t > 1:
        w += [_DIGITS[t], "mươi"]
        if u == 1:
            w.append("mốt")
        elif u == 5:
            w.append("lăm")
        elif u > 0:
            w.append(_DIGITS[u])
    elif t == 1:
        w.append("mười")
        if u == 5:
            w.append("lăm")
        elif u > 0:
            w.append(_DIGITS[u])
    elif u > 0:
        if h > 0 or full:
            w.append("lẻ")
        w.append(_DIGITS[u])
    return w


def doc_so_tien(n: int) -> str:
    """VND amount as Vietnamese words, e.g. 13_442_000 -> 'Mười ba triệu, bốn
    trăm bốn mươi hai nghìn đồng chẵn'. Used for the 'Bằng chữ' line."""
    n = int(round(n or 0))
    if n <= 0:
        return "Không đồng"
    groups: list[int] = []
    x = n
    while x > 0:
        groups.append(x % 1000)
        x //= 1000
    chunks: list[str] = []
    emitted = False
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if g == 0:
            continue
        words = _read_group(g, emitted)
        if i > 0:
            words.append(_scale_word(i))
        chunks.append(" ".join(words))
        emitted = True
    s = ", ".join(chunks)
    return s[0].upper() + s[1:] + " đồng chẵn"


def _describe(item: dict) -> str:
    """'Cửa Cuốn Đức KV 380' — door label + technology (short) + model."""
    label = pricing.DOOR_CONFIG.get(item["product"], {}).get("label", item["product"])
    parts = [label]
    cong_nghe = item.get("cong_nghe") or ""
    if cong_nghe:
        parts.append(cong_nghe.replace("Cửa cuốn công nghệ ", ""))
    if item.get("mau"):
        parts.append(item["mau"])
    return " ".join(p for p in parts if p)


def _door_line(item: dict, customer_type: str) -> dict:
    """{desc, ngang_m, cao_m, area, don_gia, thanh_tien, ghi_chu} for one door."""
    ngang_m = (item["ngang_mm"] or 0) / 1000
    cao_m = (item["cao_mm"] or 0) / 1000
    area = ngang_m * cao_m
    table_price = None
    if not item["is_manual_price"]:
        table_price = pricing.get_price(item["product"], item.get("cong_nghe") or "",
                                        item.get("mau") or "", customer_type)
    if table_price is not None:
        per_sqm, flat = pricing.get_surcharges(item["product"], item.get("cong_nghe") or "", area)
        don_gia = table_price * 1000 + per_sqm  # full đ/m² incl. small-door surcharge
        thanh_tien = pricing.line_total(table_price, item["ngang_mm"], item["cao_mm"], per_sqm, flat)
    else:
        thanh_tien = item["thanh_tien"]
        don_gia = round(thanh_tien / area) if area else thanh_tien
    ghi_chu = " · ".join(x for x in (item.get("mau_sac"), item.get("ghi_chu")) if x)
    return {"desc": _describe(item), "ngang_m": ngang_m, "cao_m": cao_m, "area": area,
            "don_gia": don_gia, "thanh_tien": thanh_tien, "ghi_chu": ghi_chu}


def _dim(m: float) -> str:
    """4.5 -> '4.5', 3.0 -> '3' (trailing zeros trimmed for the size cell)."""
    return f"{m:g}"


def build_baogia_xlsx(quote: dict, customer: dict, items: list, company: dict) -> tuple[bytes, str]:
    """Return (xlsx_bytes, filename) for a báo giá. ``quote`` is a store.get_quote
    row (carries customer_name / customer_type / accessories), ``items`` are its
    quote_items, ``company`` is {name, tagline, phone, address, email, website}."""
    customer_type = quote.get("customer_type") or "KH"
    today = datetime.now(_TZ)
    so = bao_gia_so(quote.get("id"), quote.get("sent_date"))

    wb = Workbook()
    ws = wb.active
    ws.title = "Báo giá"
    ws.sheet_view.showGridLines = False
    for col, width in _COL_WIDTHS.items():
        ws.column_dimensions[col].width = width

    def band(row, text, *, size=11, bold=False, italic=False, color=_INK,
             align="left", first=1, last=8):
        ws.merge_cells(start_row=row, start_column=first, end_row=row, end_column=last)
        c = ws.cell(row=row, column=first, value=text)
        c.font = Font(name=_FONT, size=size, bold=bold, italic=italic, color=color)
        c.alignment = Alignment(horizontal=align, vertical="center", wrap_text=True)
        return c

    # ── Company header ───────────────────────────────────────────────────
    band(1, (company.get("name") or "").upper(), size=18, bold=True, color=_NAVY,
         first=1, last=6)
    logo = ws.cell(row=1, column=7, value="HTP")
    logo.font = Font(name=_FONT, size=18, bold=True, color=_ORANGE)
    logo.alignment = Alignment(horizontal="right", vertical="center")
    ws.merge_cells("G1:H1")

    row = 2
    if company.get("tagline"):
        band(row, f'Chuyên: {company["tagline"]}', size=10, color=_GREY); row += 1
    if company.get("address"):
        band(row, f'Địa chỉ: {company["address"]}', size=10, color=_GREY); row += 1
    if company.get("phone"):
        band(row, f'Hotline: {company["phone"]}', size=10, color=_GREY); row += 1
    contact = " | ".join(p for p in (
        (f'Email: {company["email"]}' if company.get("email") else ""),
        (f'Website: {company["website"]}' if company.get("website") else ""),
    ) if p)
    if contact:
        band(row, contact, size=10, color=_GREY); row += 1
    # thin rule under the header block
    for col in range(1, 9):
        ws.cell(row=row, column=col).border = Border(bottom=Side(style="thin", color=_NAVY))
    row += 1

    band(row, "BẢNG BÁO GIÁ", size=16, bold=True, color=_NAVY, align="center"); row += 2

    # ── Customer block (two label/value columns) ─────────────────────────
    def field(r, label, value, *, lcol, vfirst, vlast):
        lc = ws.cell(row=r, column=lcol, value=label)
        lc.font = Font(name=_FONT, size=11, bold=True, color=_INK)
        lc.alignment = Alignment(horizontal="left", vertical="center")
        ws.merge_cells(start_row=r, start_column=vfirst, end_row=r, end_column=vlast)
        vc = ws.cell(row=r, column=vfirst, value=value)
        vc.font = Font(name=_FONT, size=11, color=_INK)
        vc.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)

    cust_name = quote.get("customer_name") or customer.get("name") or "—"
    phone = quote.get("phone") or customer.get("phone") or "—"
    field(row, "Kính gửi:", cust_name.upper(), lcol=1, vfirst=3, vlast=4)
    field(row, "Số báo giá:", so, lcol=5, vfirst=7, vlast=8); row += 1
    field(row, "Địa chỉ:", customer.get("address") or "—", lcol=1, vfirst=3, vlast=4)
    field(row, "Ngày lập:", today.strftime("%d/%m/%Y"), lcol=5, vfirst=7, vlast=8); row += 1
    field(row, "Điện thoại:", phone, lcol=1, vfirst=3, vlast=4)
    field(row, "Hiệu lực:", _HIEU_LUC, lcol=5, vfirst=7, vlast=8); row += 2

    def section(r, text):
        band(r, text, bold=True, color=_NAVY)

    def header_cell(r, col, text):
        c = ws.cell(row=r, column=col, value=text)
        c.font = Font(name=_FONT, size=11, bold=True, color=_WHITE)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.fill = PatternFill("solid", fgColor=_NAVY)
        c.border = _BORDER
        return c

    def money(r, col, value):
        c = ws.cell(row=r, column=col, value=value)
        c.number_format = _VND
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.font = Font(name=_FONT, size=11, color=_INK)
        c.border = _BORDER
        return c

    def cell(r, col, value, *, align="center"):
        c = ws.cell(row=r, column=col, value=value)
        c.alignment = Alignment(horizontal=align, vertical="center", wrap_text=(col in (2, 8)))
        c.font = Font(name=_FONT, size=11, color=_INK)
        c.border = _BORDER
        return c

    # ── I. HẠNG MỤC CỬA ──────────────────────────────────────────────────
    section(row, "I. HẠNG MỤC CỬA"); row += 1
    for col, title in enumerate(_HEADERS, start=1):
        header_cell(row, col, title)
    row += 1

    door_total = 0
    for idx, item in enumerate(items, start=1):
        d = _door_line(item, customer_type)
        door_total += d["thanh_tien"]
        cell(row, 1, idx)
        cell(row, 2, d["desc"], align="left")
        cell(row, 3, f'{_dim(d["ngang_m"])} x {_dim(d["cao_m"])}')
        cell(row, 4, 1)
        cell(row, 5, round(d["area"], 2))
        money(row, 6, d["don_gia"])
        money(row, 7, d["thanh_tien"])
        cell(row, 8, d["ghi_chu"], align="left")
        row += 1
    row += 1

    # ── II. PHỤ KIỆN ─────────────────────────────────────────────────────
    pk_items = pricing.phukien_line_items(quote.get("accessories") or "", items, customer_type)
    pk_total = pricing.calc_phukien(quote.get("accessories") or "", items, customer_type)
    if pk_items:
        section(row, "II. PHỤ KIỆN"); row += 1
        header_cell(row, 1, "STT")
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=3)
        header_cell(row, 2, "Nội dung")
        ws.merge_cells(start_row=row, start_column=4, end_row=row, end_column=6)
        header_cell(row, 4, "SL")
        header_cell(row, 7, "Thành tiền")
        header_cell(row, 8, "Ghi chú")
        for col in (3, 5, 6):  # border the cells absorbed by the merges
            ws.cell(row=row, column=col).border = _BORDER
        row += 1
        for i, pk in enumerate(pk_items, start=1):
            cell(row, 1, i)
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=3)
            cell(row, 2, pk["name"], align="left")
            ws.merge_cells(start_row=row, start_column=4, end_row=row, end_column=6)
            cell(row, 4, pk["qty"])
            money(row, 7, pk["total"])
            cell(row, 8, "")
            for col in (3, 5, 6):
                ws.cell(row=row, column=col).border = _BORDER
            row += 1
        row += 1

    # ── Summary ──────────────────────────────────────────────────────────
    tong_cong = door_total + pk_total
    vat = round(tong_cong * 0.1)
    tong_tien = tong_cong + vat
    summary = [
        ("Cộng tiền hàng:", tong_cong, _INK, 11),
        ("Thuế VAT (10%):", vat, _INK, 11),
        ("TỔNG CỘNG:", tong_tien, _RED, 12),
    ]
    for label, value, color, size in summary:
        ws.merge_cells(start_row=row, start_column=5, end_row=row, end_column=6)
        lc = ws.cell(row=row, column=5, value=label)
        lc.font = Font(name=_FONT, size=size, bold=True, color=color)
        lc.alignment = Alignment(horizontal="right", vertical="center")
        lc.border = _BORDER
        ws.cell(row=row, column=6).border = _BORDER
        vc = ws.cell(row=row, column=7, value=value)
        vc.number_format = _VND
        vc.font = Font(name=_FONT, size=size, bold=True, color=color)
        vc.alignment = Alignment(horizontal="right", vertical="center")
        vc.border = _BORDER
        row += 1
    row += 1

    band(row, f"(Bằng chữ: {doc_so_tien(tong_tien)})", italic=True, color=_INK,
         align="right"); row += 2

    # ── IV. CAM KẾT & ĐIỀU KHOẢN ─────────────────────────────────────────
    section(row, "IV. CAM KẾT & ĐIỀU KHOẢN"); row += 1
    for line in (
        "- Chất lượng: Đúng vật liệu cam kết — bồi thường 200% nếu sai vật liệu. Có CO/CQ rõ ràng.",
        "- Thanh toán: Tạm ứng 30% khi chốt đơn hàng, 70% còn lại thanh toán sau khi lắp đặt và nghiệm thu.",
        "- Bảo hành: Sản phẩm được bảo hành chính hãng [XX] tháng kể từ ngày bàn giao.",
        "- Lắp đặt: Có mặt khảo sát trong ngày. Lắp xong, chạy thử, nghiệm thu hài lòng mới thanh toán.",
    ):
        band(row, line, size=10, color=_INK); row += 1

    # ── Print setup: one A4 page ─────────────────────────────────────────
    # Without this, Excel prints with its default page setup and the 8 columns
    # spill onto a 2nd/3rd sheet. Scale-to-fit (1 page wide × 1 tall) keeps the
    # whole báo giá on a single page whatever the door count.
    ws.print_area = f"A1:H{row - 1}"
    ws.page_setup.orientation = "portrait"
    ws.page_setup.paperSize = 9  # A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_margins = PageMargins(left=0.3, right=0.3, top=0.4, bottom=0.4,
                                  header=0.2, footer=0.2)

    buf = io.BytesIO()
    wb.save(buf)
    filename = f"{so} - {_ascii_upper(quote.get('customer_name') or customer.get('name'))} - {today.strftime('%d.%m.%y')}.xlsx"
    return buf.getvalue(), filename
