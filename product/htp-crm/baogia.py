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
import math
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

import qrcode
from qrcode.image.pil import PilImage
from openpyxl import Workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.properties import PageSetupProperties

import pricing
from store import bao_gia_so

_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

# Palette echoes the branded template: navy headers, red total.
_INK = "FF212121"
_NAVY = "FF1A365D"
_RED = "FFD9534F"
_GREY = "FF666666"
_WHITE = "FFFFFFFF"
_LINE = Side(style="thin", color="FFBDBDBD")
_BORDER = Border(_LINE, _LINE, _LINE, _LINE)
_FONT = "Arial"

# On A4 with fit-to-1-page-wide, printed text size is set by total column
# UNITS across the page, not the point size — so the grid is kept near the 7.87"
# printable width (≈104 units) and the body font prints close to full size. The
# wide money columns (F/G) hold the largest realistic đơn giá / tổng cộng
# ("143.400.000 ₫" ≈ 13 chars) without clipping to ####. H is wide enough to sit
# under the top-right QR and to give Ghi chú room to wrap.
_COL_WIDTHS = {"A": 4, "B": 26, "C": 11, "D": 5, "E": 10, "F": 16, "G": 19, "H": 13}
_VND = '#,##0" ₫"'
_HEADERS = ["STT", "Nội dung", "Kích thước (m)", "SL", "Diện tích (m²)",
            "Đơn giá", "Thành tiền", "Ghi chú"]
_HIEU_LUC = "15 ngày"  # quote validity shown in the customer block
_SZ = 14  # base body size; table cells and customer fields
_DEPOSIT_PCT = 0.30  # keep in step with the "Tạm ứng 30%" payment term below

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
    if item["product"] == "khac":  # free-form item — its name rides in cong_nghe
        return item.get("cong_nghe") or "Sản phẩm khác"
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


def _tlv(tag: str, value: str) -> str:
    """EMVCo tag-length-value chunk: 2-char tag + 2-digit length + value."""
    return f"{tag}{len(value):02d}{value}"


def _crc16(s: str) -> str:
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection/final xor) —
    the checksum NAPAS requires as the closing field of a VietQR payload. A wrong
    CRC makes every banking app reject the code, so this is covered by a known
    test vector: crc16('123456789') == '29B1'."""
    crc = 0xFFFF
    for byte in s.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def vietqr_payload(bank_bin: str, account: str, amount: int, info: str) -> str:
    """A dynamic VietQR (NAPAS 247) string — scannable by any Vietnamese banking
    app, with the amount and transfer memo pre-filled.

    ``bank_bin`` is the NAPAS bank code (Vietcombank = 970436), ``account`` the
    real account number (bank nicknames/aliases are not part of the standard and
    will not resolve), ``amount`` is đồng, ``info`` the memo — ASCII only, since
    banks mangle Vietnamese diacritics in the transfer description.
    """
    beneficiary = _tlv("00", bank_bin) + _tlv("01", account)
    merchant = (_tlv("00", "A000000727")      # NAPAS GUID
                + _tlv("01", beneficiary)
                + _tlv("02", "QRIBFTTA"))     # transfer to account number
    body = (_tlv("00", "01")                  # payload format indicator
            + _tlv("01", "12")                # 12 = dynamic (carries an amount)
            + _tlv("38", merchant)
            + _tlv("53", "704")               # VND
            + _tlv("54", str(int(amount)))
            + _tlv("58", "VN")
            + _tlv("62", _tlv("08", info)))   # 62-08 = purpose of transaction
    return body + "6304" + _crc16(body + "6304")


def _qr_png(payload: str) -> io.BytesIO:
    """VietQR payload -> PNG bytes, ready to drop into the sheet. The Pillow
    factory is pinned explicitly: qrcode silently falls back to a pure-python
    backend when it can't import Pillow, and that one takes no ``format``."""
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=1)
    qr.add_data(payload)
    qr.make(fit=True)
    buf = io.BytesIO()
    img = qr.make_image(image_factory=PilImage, fill_color="black", back_color="white")
    # qrcode hands back a 1-bit ("mode 1") image. Some mobile PNG decoders skip
    # bit-depth-1 files without erroring, leaving a blank gap where the QR should
    # be. RGB costs ~600 bytes and decodes everywhere.
    img.get_image().convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf


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

    def band(row, text, *, size=_SZ, bold=False, italic=False, color=_INK,
             align="left", first=1, last=8, height=None):
        ws.merge_cells(start_row=row, start_column=first, end_row=row, end_column=last)
        c = ws.cell(row=row, column=first, value=text)
        c.font = Font(name=_FONT, size=size, bold=bold, italic=italic, color=color)
        c.alignment = Alignment(horizontal=align, vertical="center", wrap_text=True)
        if height:
            ws.row_dimensions[row].height = height
        return c

    def spacer(row, height=6):
        """A blank separator row. Explicitly short — at the default height every
        gap costs ~20px of vertical scale-to-fit budget, which shrinks the type."""
        ws.row_dimensions[row].height = height

    # ── Company header ───────────────────────────────────────────────────
    # Company text is capped at column F so columns G–H stay clear for the
    # VietQR block, which takes the top-right corner the "HTP" logo used to hold.
    band(1, (company.get("name") or "").upper(), size=20, bold=True, color=_NAVY,
         first=1, last=6, height=30)

    row = 2
    if company.get("tagline"):
        band(row, f'Chuyên: {company["tagline"]}', size=11, color=_GREY, height=15,
             last=6); row += 1
    if company.get("address"):
        band(row, f'Địa chỉ: {company["address"]}', size=11, color=_GREY, height=15,
             last=6); row += 1
    if company.get("phone"):
        band(row, f'Hotline: {company["phone"]}', size=11, color=_GREY, height=15,
             last=6); row += 1
    contact = " | ".join(p for p in (
        (f'Email: {company["email"]}' if company.get("email") else ""),
        (f'Website: {company["website"]}' if company.get("website") else ""),
    ) if p)
    if contact:
        band(row, contact, size=11, color=_GREY, height=15, last=6); row += 1
    # The QR is drawn once the total is known (further down) but anchors here —
    # its caption sits on the last company-info line, level with the code above.
    qr_caption_row = max(2, row - 1)
    # thin rule under the header block
    for col in range(1, 9):
        ws.cell(row=row, column=col).border = Border(bottom=Side(style="thin", color=_NAVY))
    ws.row_dimensions[row].height = 5
    row += 1

    band(row, "BẢNG BÁO GIÁ", size=18, bold=True, color=_NAVY, align="center",
         height=26); row += 1
    spacer(row); row += 1

    # ── Customer block (two label/value columns) ─────────────────────────
    def field(r, label, value, *, first, last, size=_SZ):
        """Label and value in one merged cell — 'Kính gửi: ANH CỬA'. They used to
        sit in separate cells a whole column apart, which stranded the value ~3cm
        to the right of its label; inline also frees the full width for long
        addresses. Label is bolded via rich text, value stays regular. ``size``
        drops below _SZ for secondary blocks that sit next to smaller body text."""
        ws.merge_cells(start_row=r, start_column=first, end_row=r, end_column=last)
        c = ws.cell(row=r, column=first)
        c.value = CellRichText(
            TextBlock(InlineFont(rFont=_FONT, sz=size, b=True, color=_INK[2:]), f"{label} "),
            TextBlock(InlineFont(rFont=_FONT, sz=size, color=_INK[2:]), str(value)),
        )
        c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        # Merged cells never auto-fit, so a long address would be clipped at one
        # line. Width in char-units ≈ chars × (13pt Arial / 11pt Calibri) = 1.18,
        # scaled by size/_SZ so a smaller block gets the wrap budget it earns.
        avail = sum(_COL_WIDTHS[get_column_letter(i)] for i in range(first, last + 1))
        lines = max(1, math.ceil(len(f"{label} {value}") * 1.18 * size / _SZ / avail))
        ws.row_dimensions[r].height = max(ws.row_dimensions[r].height or 0,
                                          21 * size / _SZ * lines)

    cust_name = quote.get("customer_name") or customer.get("name") or "—"
    phone = quote.get("phone") or customer.get("phone") or "—"
    field(row, "Kính gửi:", cust_name.upper(), first=1, last=4)
    field(row, "Số báo giá:", so, first=5, last=8); row += 1
    field(row, "Địa chỉ:", customer.get("address") or "—", first=1, last=4)
    field(row, "Ngày lập:", today.strftime("%d/%m/%Y"), first=5, last=8); row += 1
    field(row, "Điện thoại:", phone, first=1, last=4)
    field(row, "Hiệu lực:", _HIEU_LUC, first=5, last=8); row += 1
    spacer(row); row += 1

    def section(r, text):
        band(r, text, size=15, bold=True, color=_NAVY, height=22)

    def header_cell(r, col, text):
        c = ws.cell(row=r, column=col, value=text)
        c.font = Font(name=_FONT, size=_SZ, bold=True, color=_WHITE)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.fill = PatternFill("solid", fgColor=_NAVY)
        c.border = _BORDER
        ws.row_dimensions[r].height = 32
        return c

    def money(r, col, value):
        c = ws.cell(row=r, column=col, value=value)
        c.number_format = _VND
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.font = Font(name=_FONT, size=_SZ, color=_INK)
        c.border = _BORDER
        return c

    def cell(r, col, value, *, align="center"):
        c = ws.cell(row=r, column=col, value=value)
        c.alignment = Alignment(horizontal=align, vertical="center", wrap_text=(col in (2, 8)))
        c.font = Font(name=_FONT, size=_SZ, color=_INK)
        c.border = _BORDER
        return c

    # ── I. HẠNG MỤC CỬA ──────────────────────────────────────────────────
    # Skipped entirely on a phụ-kiện-only báo giá (khóa, bình tích điện, chi phí
    # khác — no cửa): printing the header with no rows under it looks broken on
    # a document the customer actually receives.
    door_total = 0
    if items:
        section(row, "I. HẠNG MỤC CỬA"); row += 1
        for col, title in enumerate(_HEADERS, start=1):
            header_cell(row, col, title)
        row += 1

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
            ws.row_dimensions[row].height = 24
            row += 1
        spacer(row); row += 1

    # ── II. PHỤ KIỆN ─────────────────────────────────────────────────────
    pk_items = pricing.phukien_line_items(quote.get("accessories") or "", items, customer_type)
    pk_total = pricing.calc_phukien(quote.get("accessories") or "", items, customer_type)
    if pk_items:
        # Numbering follows whether the cửa table was drawn — a phụ-kiện-only
        # quote shouldn't open at "II." with no "I." above it.
        section(row, "II. PHỤ KIỆN" if items else "I. PHỤ KIỆN"); row += 1
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
            ws.row_dimensions[row].height = 24
            row += 1
        spacer(row); row += 1

    # ── Summary ──────────────────────────────────────────────────────────
    tong_cong = door_total + pk_total
    vat = round(tong_cong * 0.1)
    tong_tien = tong_cong + vat
    summary = [
        ("Cộng tiền hàng:", tong_cong, _INK, _SZ),
        ("Thuế VAT (10%):", vat, _INK, _SZ),
        ("TỔNG CỘNG:", tong_tien, _RED, 15),
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
        ws.row_dimensions[row].height = 24
        row += 1
    spacer(row); row += 1

    # 11pt, not _SZ: a large total spells out to ~90 chars and this row is a
    # merged band, which never auto-fits — at 13pt it would clip.
    band(row, f"(Bằng chữ: {doc_so_tien(tong_tien)})", size=11, italic=True,
         color=_INK, align="right", height=19); row += 1
    spacer(row); row += 1

    # ── IV. CAM KẾT & ĐIỀU KHOẢN ─────────────────────────────────────────
    section(row, "IV. CAM KẾT & ĐIỀU KHOẢN"); row += 1
    for line in (
        "- Chất lượng: Đúng vật liệu cam kết — bồi thường 200% nếu sai vật liệu. Có CO/CQ rõ ràng.",
        "- Thanh toán: Tạm ứng 30% khi chốt đơn hàng, 70% còn lại thanh toán sau khi lắp đặt và nghiệm thu.",
        "- Bảo hành: Sản phẩm được bảo hành chính hãng [XX] tháng kể từ ngày bàn giao.",
        "- Lắp đặt: Có mặt khảo sát trong ngày. Lắp xong, chạy thử, nghiệm thu hài lòng mới thanh toán.",
    ):
        # Same one-line constraint: the longest term runs 98 chars across the
        # 102-unit band, so it stays at 10.5pt rather than following _SZ.
        band(row, line, size=10.5, color=_INK, height=17); row += 1

    # ── Signature block ──────────────────────────────────────────────────
    # Sits bottom-right like the wet-signed paper báo giá: title, "ký ghi rõ
    # họ tên", blank height for the signature and stamp, then the signer name.
    spacer(row); row += 1
    # Bank details fill columns A–E across these same rows (written further down,
    # with the QR, since both need the final total).
    pay_row = row
    band(row, "NGƯỜI BÁO GIÁ", size=_SZ, bold=True, color=_INK, align="center",
         first=6, last=8, height=21); row += 1
    band(row, "(Ký, ghi rõ họ tên)", size=10, italic=True, color=_GREY,
         align="center", first=6, last=8, height=16); row += 1
    ws.merge_cells(start_row=row, start_column=6, end_row=row + 2, end_column=8)
    for r in range(row, row + 3):
        ws.row_dimensions[r].height = 24  # room to sign and stamp
    row += 3
    band(row, company.get("signer") or "", size=_SZ, bold=True, color=_INK,
         align="center", first=6, last=8, height=21); row += 1

    # ── VietQR: scan-to-pay the 30% deposit ──────────────────────────────
    # Anchored top-right (cols G–H, row 1) where the "HTP" logo used to sit. It
    # is written last because the amount needs the final total, but an image
    # anchor is positional, not sequential — it still lands in the header.
    # Skipped when the bank isn't configured or the quote totals nothing.
    deposit = round(tong_tien * _DEPOSIT_PCT)
    if company.get("bank_account") and deposit > 0:
        digits = "".join(c for c in str(phone) if c.isdigit())
        memo = f"{digits} chuyen tien".strip()

        # The typed-out twin of the QR, in A–E beside the signature block. These
        # details used to be left off on purpose — "the QR already carries them"
        # — but the QR is a *floating image*, and Google Sheets mobile and Zalo's
        # in-app file preview drop floating images entirely. A customer reading
        # the báo giá on a phone was left with no account number at all. Plain
        # cells render in every viewer, so payment never depends on the QR.
        pay_lines = [(lab, val) for lab, val in (
            ("Ngân hàng:", company.get("bank_name") or ""),
            ("Số tài khoản:", company["bank_account"]),
            ("Chủ tài khoản:", (company.get("bank_holder") or "").upper()),
            (f"Tạm ứng {int(_DEPOSIT_PCT * 100)}%:",
             f"{deposit:,.0f} ₫".replace(",", ".")),
            ("Nội dung CK:", memo),
        ) if val]
        # 11pt heading over 10.5pt lines — the same weights as the CAM KẾT terms
        # directly above, so the block reads as part of the document rather than
        # a bolt-on. height stays 21: these rows are shared with the signature
        # block on the right, and band() would otherwise shrink its title row.
        band(pay_row, "THÔNG TIN CHUYỂN KHOẢN", size=11, bold=True, color=_NAVY,
             first=1, last=5, height=21)
        for i, (label, value) in enumerate(pay_lines, start=1):
            field(pay_row + i, label, value, first=1, last=5, size=10.5)

        qr_buf = _qr_png(vietqr_payload(company.get("bank_bin") or "",
                                        company["bank_account"], deposit, memo))
        img = XLImage(qr_buf)
        # 92px ≈ 2.4cm printed (41 modules ≈ 0.59mm each — comfortably scannable)
        # and sits inside column H's 96px, so it never spills past the print area.
        img.width = img.height = 92
        ws.add_image(img, "H1")
        ws.merge_cells(start_row=qr_caption_row, start_column=7,
                       end_row=qr_caption_row, end_column=8)
        cap = ws.cell(row=qr_caption_row, column=7,
                      value=f"Quét mã thanh toán — Tạm ứng {int(_DEPOSIT_PCT * 100)}%")
        cap.font = Font(name=_FONT, size=8, bold=True, color=_NAVY)
        cap.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

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
    # Narrow side margins widen the printable band to ~7.87", which the trimmed
    # 102-unit grid fills at ~100% — no fit-to-width shrink, so the body font
    # prints at its full 14pt instead of being scaled down.
    ws.page_margins = PageMargins(left=0.2, right=0.2, top=0.35, bottom=0.35,
                                  header=0.15, footer=0.15)

    buf = io.BytesIO()
    wb.save(buf)
    filename = f"{so} - {_ascii_upper(quote.get('customer_name') or customer.get('name'))} - {today.strftime('%d.%m.%y')}.xlsx"
    return buf.getvalue(), filename
