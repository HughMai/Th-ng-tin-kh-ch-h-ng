"""Báo Giá export — renders a quote as an .xlsx laid out like the family's
Google-Sheet quote (the ``generateBaoGia`` Apps Script this ports).

Column layout (A–I):
  A STT · B Nội dung · C Ngang(m) · D Cao(m) · E SL · F Diện tích(m²)
  G Đơn giá · H Thành tiền · I Ghi chú

Sections: door line items → PHỤ KIỆN → Tiền phụ kiện / Tổng cộng / VAT 10% /
Tổng tiền. Door lines with a table price are recomputed here (đơn giá = table
đ/m² + small-door surcharge; thành tiền = đơn giá×area + flat) so the sheet is
internally consistent; manual-priced lines use the stored thành tiền.
"""
import io
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

import pricing
from store import bao_gia_so

_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

# Palette echoes the Apps Script's purple accents.
_INK = "FF212121"
_ACCENT = "FF673AB7"
_FILL_HEAD = "FFEDE7F6"
_FILL_TOTAL = "FFD1C4E9"
_FILL_PK = "FFF0F0F0"
_BORDER = Border(*[Side(style="thin", color="FFBDBDBD")] * 4)

_COL_WIDTHS = {"A": 5, "B": 34, "C": 9, "D": 9, "E": 6, "F": 11, "G": 14, "H": 16, "I": 22}
_VND = "#,##0"
_HEADERS = ["STT", "NỘI DUNG", "Ngang (m)", "Cao (m)", "SL", "Diện tích (m²)",
            "Đơn giá", "Thành tiền", "Ghi chú"]


def _ascii_upper(s: str) -> str:
    """'Anh Hùng — Trần Phú' -> 'ANH HUNG TRAN PHU' (safe for a filename)."""
    s = (s or "").replace("Đ", "D").replace("đ", "d")
    nfd = unicodedata.normalize("NFD", s)
    stripped = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    ascii_only = "".join(c if (c.isalnum() or c in " -_") else " " for c in stripped)
    return " ".join(ascii_only.split()).upper()


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
    return {"desc": _describe(item), "ngang_m": ngang_m, "cao_m": cao_m, "area": area,
            "don_gia": don_gia, "thanh_tien": thanh_tien, "ghi_chu": item.get("mau_sac") or ""}


def build_baogia_xlsx(quote: dict, customer: dict, items: list, company: dict) -> tuple[bytes, str]:
    """Return (xlsx_bytes, filename) for a báo giá. ``quote`` is a store.get_quote
    row (carries customer_name / customer_type / accessories), ``items`` are its
    quote_items, ``company`` is {name, tagline, phone, address}."""
    customer_type = quote.get("customer_type") or "KH"
    today = datetime.now(_TZ)
    so = bao_gia_so(quote.get("id"), quote.get("sent_date"))

    wb = Workbook()
    ws = wb.active
    ws.title = "Báo giá"
    ws.sheet_view.showGridLines = False
    for col, width in _COL_WIDTHS.items():
        ws.column_dimensions[col].width = width

    def merged(row, text, *, size=11, bold=False, color=_INK, align="left"):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=9)
        c = ws.cell(row=row, column=1, value=text)
        c.font = Font(name="Calibri", size=size, bold=bold, color=color)
        c.alignment = Alignment(horizontal=align, vertical="center")

    # ── Company + document title ─────────────────────────────────────────
    merged(1, (company.get("name") or "").upper(), size=18, bold=True, color=_ACCENT, align="center")
    row = 2
    if company.get("tagline"):
        merged(row, company["tagline"], size=10, color="FF666666", align="center"); row += 1
    contact = " · ".join(p for p in (
        (f'ĐC: {company["address"]}' if company.get("address") else ""),
        (f'ĐT: {company["phone"]}' if company.get("phone") else ""),
    ) if p)
    if contact:
        merged(row, contact, size=10, color="FF666666", align="center"); row += 1
    row += 1
    merged(row, "BẢNG BÁO GIÁ", size=15, bold=True, color=_ACCENT, align="center"); row += 2

    # ── Customer block ───────────────────────────────────────────────────
    merged(row, f"KÍNH GỬI : {(quote.get('customer_name') or customer.get('name') or '').upper()}",
           bold=True); row += 1
    merged(row, f"Đ/C : {customer.get('address') or '—'}"); row += 1
    merged(row, f"ĐT : {quote.get('phone') or customer.get('phone') or '—'}"); row += 1
    merged(row, f"Số báo giá : {so}", align="right"); row += 1
    merged(row, "Ngày : " + today.strftime("%d/%m/%Y"), align="right"); row += 2

    # ── Table header ─────────────────────────────────────────────────────
    for col, title in enumerate(_HEADERS, start=1):
        c = ws.cell(row=row, column=col, value=title)
        c.font = Font(bold=True, color=_INK)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.fill = PatternFill("solid", fgColor=_FILL_HEAD)
        c.border = _BORDER
    row += 1

    def money(r, col, value):
        c = ws.cell(row=r, column=col, value=value)
        c.number_format = _VND
        c.alignment = Alignment(horizontal="right")
        c.font = Font(color=_INK)
        c.border = _BORDER
        return c

    def plain(r, col, value, fmt=None, align="center"):
        c = ws.cell(row=r, column=col, value=value)
        if fmt:
            c.number_format = fmt
        c.alignment = Alignment(horizontal=align, vertical="center", wrap_text=(col in (2, 9)))
        c.font = Font(color=_INK)
        c.border = _BORDER
        return c

    # ── Door rows ────────────────────────────────────────────────────────
    door_total = 0
    for idx, item in enumerate(items, start=1):
        d = _door_line(item, customer_type)
        door_total += d["thanh_tien"]
        plain(row, 1, idx)
        plain(row, 2, d["desc"], align="left")
        plain(row, 3, round(d["ngang_m"], 3), fmt="0.000")
        plain(row, 4, round(d["cao_m"], 3), fmt="0.000")
        plain(row, 5, 1)
        plain(row, 6, round(d["area"], 2), fmt="0.00")
        money(row, 7, d["don_gia"])
        money(row, 8, d["thanh_tien"])
        plain(row, 9, d["ghi_chu"], align="left")
        row += 1

    # ── Phụ kiện section ─────────────────────────────────────────────────
    pk_items = pricing.phukien_line_items(quote.get("accessories") or "", items, customer_type)
    pk_total = pricing.calc_phukien(quote.get("accessories") or "", items, customer_type)
    if pk_items:
        for col in range(1, 10):
            c = ws.cell(row=row, column=col)
            c.fill = PatternFill("solid", fgColor=_FILL_PK)
            c.border = _BORDER
        hc = ws.cell(row=row, column=2, value="PHỤ KIỆN")
        hc.font = Font(bold=True, italic=True, color="FF555555")
        row += 1
        for pk in pk_items:
            plain(row, 1, "")
            plain(row, 2, pk["name"], align="left")
            plain(row, 5, pk["qty"])
            money(row, 7, pk["unit_cost"])
            money(row, 8, pk["total"])
            plain(row, 9, "")
            # fill the gaps so the row is fully bordered
            for col in (3, 4, 6):
                plain(row, col, "")
            row += 1

    # ── Summary rows ─────────────────────────────────────────────────────
    tong_cong = door_total + pk_total
    vat = round(tong_cong * 0.1)
    tong_tien = tong_cong + vat
    summary = [
        ("Tiền phụ kiện", pk_total, False, None),
        ("Tổng cộng", tong_cong, True, _FILL_HEAD),
        ("VAT (10%)", vat, False, None),
        ("Tổng tiền", tong_tien, True, _FILL_TOTAL),
    ]
    for label, value, bold, fill in summary:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=7)
        lc = ws.cell(row=row, column=1, value=label)
        lc.font = Font(bold=bold, color=_ACCENT)
        lc.alignment = Alignment(horizontal="right", vertical="center")
        vc = ws.cell(row=row, column=8, value=value)
        vc.number_format = _VND
        vc.font = Font(bold=bold, color=_ACCENT)
        vc.alignment = Alignment(horizontal="right", vertical="center")
        if fill:
            for col in range(1, 9):
                ws.cell(row=row, column=col).fill = PatternFill("solid", fgColor=fill)
        row += 1

    buf = io.BytesIO()
    wb.save(buf)
    filename = f"{so} - {_ascii_upper(quote.get('customer_name') or customer.get('name'))} - {today.strftime('%d.%m.%y')}.xlsx"
    return buf.getvalue(), filename
