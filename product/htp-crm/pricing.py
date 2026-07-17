"""Báo giá price calculator — ported from the original standalone tool
(customer_form/index.html, recovered via `git show HEAD:customer_form/index.html`).

Single source of truth for both (1) rendering the cascading <select> options
in the multi-item quote form, and (2) authoritatively computing price on
POST. Same principle as product/qr-menu/app.py's api_order: never trust a
client-submitted price when get_price() finds a table match — the client-side
preview in the item form is UX only.

Formula note: the original JS computed `price * ngang_mm * cao_mm / 1_000_000`
and treated the raw output as "nghìn đồng" shorthand (a KV380 door at
3000x2200mm came out to "9570", read by the family as 9.570.000đ). This CRM
stores true VND, so the divisor here is 1000, not 1_000_000 — confirmed with
the business owner (line_total(1450, 3000, 2200) == 9_570_000).
"""
import re

DOOR_CONFIG = {
    "cua_cuon": {
        "label": "Cửa Cuốn",
        "fields": [
            {"key": "cong_nghe", "label": "Công nghệ", "type": "select",
             "options": ["", "Cửa cuốn công nghệ Úc", "Cửa cuốn công nghệ Đức",
                         "Cửa cuốn công nghệ Đài Loan", "Inox"]},
            {"key": "mau", "label": "Chọn loại", "type": "select-dynamic", "depends_on": "cong_nghe",
             "option_map": {
                 "Cửa cuốn công nghệ Đức": ["", "KV 412", "KV 380", "KV 422 R", "KV 432 R",
                                            "KV 468 R", "OT 70", "LQ 71", "CT 5122",
                                            "MT 500 R", "CT 5222", "CT 5222 R"],
                 "Inox": ["", "6zem", "8zem"],
                 "Cửa cuốn công nghệ Đài Loan": ["", "6zem", "8zem", "1ly"],
                 "Cửa cuốn công nghệ Úc": ["", "Tole màu 5.2 zem", "Tole màu 5.2 zem blusc"],
             }},
        ],
    },
    "cua_keo": {
        "label": "Cửa Kéo",
        "fields": [
            {"key": "cong_nghe", "label": "Loại", "type": "select", "options": ["", "Có lá", "Không lá"]},
            {"key": "mau", "label": "Chọn mẫu", "type": "select",
             "options": ["", "6zem", "8zem", "1ly", "1.2ly", "1.4ly", "1.6ly"]},
        ],
    },
    "nhom_kinh": {
        "label": "Cửa Nhôm Kính",
        "fields": [
            {"key": "cong_nghe", "label": "Phân loại", "type": "select", "options": ["", "Nhôm Việt", "Nhôm Nhập"]},
            {"key": "mau", "label": "Chọn mẫu", "type": "select-dynamic", "depends_on": "cong_nghe",
             "option_map": {
                 "Nhôm Việt": ["", "Hàng 1.4 ly ( Cửa đi)", "Hàng 2.0 ly", "Cửa số"],
                 "Nhôm Nhập": ["", "Cửa đi", "Cửa số", "Nhập 1.4 ly"],
             }},
        ],
    },
    "xingfa": {
        "label": "Nhôm Xingfa",
        "fields": [
            {"key": "mau", "label": "Chọn mẫu", "type": "select",
             "options": ["", "Series 55 - 1.2mm", "Series 65 - 1.4mm",
                         "Series 70 - 1.6mm", "Series 90 - 2.0mm"]},
        ],
    },
}

# Ported verbatim from the original JS PRICES table, including its own gaps:
# DOOR_CONFIG lists 'MT 500 R' as a selectable Đức option but only 'MT 5222 R'
# has a price entry here. That mismatch is in the SOURCE tool, not introduced
# here — 'MT 500 R' falls to the manual-price path like any unmapped combo.
# Cửa Nhôm Kính and Nhôm Xingfa have NO entries at all (same source gap) —
# both always fall to manual price until real numbers are supplied.
_PRICES_KH = {
    "Cửa Kéo|Có lá - 6zem": 640, "Cửa Kéo|Có lá - 8zem": 700, "Cửa Kéo|Có lá - 1ly": 760,
    "Cửa Kéo|Có lá - 1.2ly": 820, "Cửa Kéo|Có lá - 1.4ly": 900,
    "Cửa Kéo|Không lá - 6zem": 540, "Cửa Kéo|Không lá - 8zem": 600, "Cửa Kéo|Không lá - 1ly": 660,
    "Cửa Kéo|Không lá - 1.2ly": 720, "Cửa Kéo|Không lá - 1.4ly": 800,
    "Cửa cuốn công nghệ Đức|KV 380": 1450, "Cửa cuốn công nghệ Đức|KV 422 R": 1750,
    "Cửa cuốn công nghệ Đức|KV 468 R": 2150, "Cửa cuốn công nghệ Đức|CT 5222 R": 2200,
    "Cửa cuốn công nghệ Đức|MT 5222 R": 2300,
    "Inox|6zem": 1700, "Inox|8zem": 1900,
    "Cửa cuốn công nghệ Đài Loan|6zem": 500, "Cửa cuốn công nghệ Đài Loan|8zem": 560,
    "Cửa cuốn công nghệ Đài Loan|1ly": 780,
    "Cửa cuốn công nghệ Úc|Tole màu 5.2 zem": 700, "Cửa cuốn công nghệ Úc|Tole màu 5.2 zem blusc": 900,
}
_PRICES_DL = {
    "Cửa Kéo|Có lá - 6zem": 560, "Cửa Kéo|Có lá - 8zem": 620, "Cửa Kéo|Có lá - 1ly": 680,
    "Cửa Kéo|Có lá - 1.2ly": 740, "Cửa Kéo|Có lá - 1.4ly": 820,
    "Cửa Kéo|Không lá - 6zem": 480, "Cửa Kéo|Không lá - 8zem": 540, "Cửa Kéo|Không lá - 1ly": 600,
    "Cửa Kéo|Không lá - 1.2ly": 620, "Cửa Kéo|Không lá - 1.4ly": 660,
    "Cửa cuốn công nghệ Đức|KV 380": 1300, "Cửa cuốn công nghệ Đức|KV 422 R": 1600,
    "Cửa cuốn công nghệ Đức|KV 432 R": 1800, "Cửa cuốn công nghệ Đức|KV 468 R": 2000,
    "Cửa cuốn công nghệ Đức|CT 5222 R": 2100, "Cửa cuốn công nghệ Đức|MT 5222 R": 2200,
    "Inox|6zem": 1600, "Inox|8zem": 1800,
    "Cửa cuốn công nghệ Đài Loan|6zem": 400, "Cửa cuốn công nghệ Đài Loan|8zem": 460,
    "Cửa cuốn công nghệ Đài Loan|1ly": 700,
    "Cửa cuốn công nghệ Úc|Tole màu 5.2 zem": 550,
}


def _price_key(product: str, cong_nghe: str, mau: str) -> str:
    if product == "cua_keo":
        return f"Cửa Kéo|{cong_nghe} - {mau}"
    return f"{cong_nghe}|{mau}"


def get_price(product: str, cong_nghe: str, mau: str, customer_type: str) -> int | None:
    """Per-unit table price, or None if there's no exact match (caller must
    fall back to a required manual VND field in that case)."""
    table = _PRICES_DL if customer_type == "DL" else _PRICES_KH
    return table.get(_price_key(product, cong_nghe or "", mau or ""))


def area_m2(ngang_mm: int, cao_mm: int) -> float:
    """Door area in m² from millimetre dimensions."""
    return (ngang_mm or 0) * (cao_mm or 0) / 1_000_000


def get_surcharges(product: str, cong_nghe: str, area: float) -> tuple[int, int]:
    """Small-door + Úc-tech surcharges as (per_sqm_vnd, flat_vnd), both FULL VND.

    Ported from the Apps Script ``getSurcharges_``: every door except Cửa Nhôm
    Kính carries a per-m² small-size surcharge (+40k under 5m², +20k under 8m²),
    and a Cửa cuốn công nghệ Úc under 8m² carries a flat +600k."""
    per_sqm = 0
    if product != "nhom_kinh":
        if area < 5:
            per_sqm = 40_000
        elif area < 8:
            per_sqm = 20_000
    flat = 600_000 if (cong_nghe == "Cửa cuốn công nghệ Úc" and area < 8) else 0
    return per_sqm, flat


def line_total(price_per_unit: int, ngang_mm: int, cao_mm: int,
               per_sqm_vnd: int = 0, flat_vnd: int = 0) -> int:
    """Door line total: table price × area, plus the small-door per-m² surcharge
    and any flat fee. ``price_per_unit`` is the scaled table price (×1000 = đ/m²);
    the surcharge args are FULL VND from ``get_surcharges``. With both surcharges
    at their 0 default this is the original ``price × ngang × cao / 1000``."""
    base = price_per_unit * ngang_mm * cao_mm // 1000
    surcharge = per_sqm_vnd * ngang_mm * cao_mm // 1_000_000  # per_sqm_vnd × area_m²
    return base + surcharge + flat_vnd


# ── Phụ kiện (accessories) — priced, ported from the Apps Script calculator ──
# Catalog shared by the quote forms (views.py) and the price math below. Keys are
# form-field-safe; labels are the exact strings stored in ``quotes.accessories``
# ("<label> x<qty>", comma-joined) and parsed back out here.
PHUKIEN_CATALOG = [
    ("moto_rmoc",      "Moto + rmoc"),
    ("moto_rmoc_uc",   "Moto + rmoc Úc"),
    ("khoa_tole",      "Khóa ngang (tole)"),
    ("khoa_uc",        "Khóa ngang (Úc)"),
    ("binh_tich_dien", "Bình Tích Điện"),
]

# Motor cost by resolved weight tier (full VND). Motors + their ray/rmoc track
# are priced per Cửa Cuốn bộ; the tier is picked from that door's area.
_MOTOR_COST_KH = {"Moto + rmoc 300kg": 3_300_000, "Moto + rmoc 400kg": 3_500_000,
                  "Moto + rmoc 500kg": 4_000_000, "Moto + rmoc Úc": 4_500_000}
_MOTOR_COST_DL = {"Moto + rmoc 300kg": 3_100_000, "Moto + rmoc 400kg": 3_300_000,
                  "Moto + rmoc 500kg": 3_900_000, "Moto + rmoc Úc": 4_300_000}
# Non-motor accessory cost (full VND), charged once per báo giá.
_ACCESSORY_COST_KH = {"Khóa ngang (tole)": 350_000, "Khóa ngang (Úc)": 450_000,
                      "Bình Tích Điện": 2_200_000}
_ACCESSORY_COST_DL = {"Khóa ngang (tole)": 300_000, "Khóa ngang (Úc)": 400_000,
                      "Bình Tích Điện": 2_000_000}


def resolve_motor_label(item: str, area: float) -> str | None:
    """Map a phụ kiện name to its priced motor tier, or None if it isn't a motor.

    "Moto + rmoc" resolves by the door's area to a 300/400/500kg tier; "Moto +
    rmoc Úc" is a fixed tier. Ported from the Apps Script ``resolveMotorLabel_``."""
    if item == "Moto + rmoc Úc":
        return "Moto + rmoc Úc"
    if item == "Moto + rmoc":
        if area < 12:
            return "Moto + rmoc 300kg"
        if area < 14.5:
            return "Moto + rmoc 400kg"
        return "Moto + rmoc 500kg"
    return None


def motor_cost(label: str, customer_type: str) -> int:
    return (_MOTOR_COST_DL if customer_type == "DL" else _MOTOR_COST_KH).get(label, 0)


def accessory_cost(item: str, customer_type: str) -> int:
    return (_ACCESSORY_COST_DL if customer_type == "DL" else _ACCESSORY_COST_KH).get(item, 0)


def _parse_phukien(accessories_str: str) -> list[tuple[str, int, int | None]]:
    """'Moto + rmoc x1, Lò xo x1 =300000' ->
    [('Moto + rmoc', 1, None), ('Lò xo', 1, 300000)]. A trailing ' =<amount>'
    marks a free-form 'chi phí khác' line carrying its own full-VND price (name
    not in the catalog); catalog parts have price None and are table-looked-up."""
    out = []
    for part in (accessories_str or "").split(", "):
        part = part.strip()
        m = re.match(r"^(.+?) x(\d+)(?: =(\d+))?$", part)
        if m:
            out.append((m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None))
    return out


def extras_of(accessories_str: str) -> list[tuple[str, int]]:
    """Free-form 'chi phí khác' entries as (name, full-VND amount) — the parts
    carrying an explicit '=<amount>' price, not catalog phụ kiện. Feeds the
    editable textarea so extras survive a re-save."""
    return [(name, price) for name, qty, price in _parse_phukien(accessories_str)
            if price is not None]


def calc_phukien(accessories_str: str, items: list, customer_type: str) -> int:
    """Total phụ kiện cost (full VND). Motors are charged per Cửa Cuốn bộ (tier
    picked from each door's area); locks/battery are charged once. Mirrors the
    Apps Script ``calcPhukien_``."""
    parts = _parse_phukien(accessories_str)
    motor_total = 0
    for it in items:
        if it["product"] != "cua_cuon":
            continue
        area = area_m2(it["ngang_mm"], it["cao_mm"])
        for name, qty, price in parts:
            if price is not None:  # free-form extra — not a motor
                continue
            label = resolve_motor_label(name, area)
            if label:
                motor_total += motor_cost(label, customer_type) * qty
    accessory_total = 0
    for name, qty, price in parts:
        if price is not None:  # free-form 'chi phí khác' — explicit full-VND price
            accessory_total += price * qty
            continue
        if resolve_motor_label(name, 0):  # motor part — already priced per bộ above
            continue
        accessory_total += accessory_cost(name, customer_type) * qty
    return motor_total + accessory_total


def phukien_line_items(accessories_str: str, items: list, customer_type: str) -> list[dict]:
    """Phụ kiện rendered as báo-giá line items: motors grouped by resolved tier
    (each Cửa Cuốn bộ needs its own), accessories listed once. Each dict is
    {name, qty, unit_cost, total}. Mirrors the Apps Script ``buildPhukienLineItems_``."""
    parts = _parse_phukien(accessories_str)
    tiers: dict[str, list[int]] = {}  # label -> [qty, unit_cost]
    for it in items:
        if it["product"] != "cua_cuon":
            continue
        area = area_m2(it["ngang_mm"], it["cao_mm"])
        for name, qty, price in parts:
            if price is not None:  # free-form extra — not a motor
                continue
            label = resolve_motor_label(name, area)
            if not label:
                continue
            if label not in tiers:
                tiers[label] = [0, motor_cost(label, customer_type)]
            tiers[label][0] += qty
    out = [{"name": lbl, "qty": qty, "unit_cost": uc, "total": uc * qty}
           for lbl, (qty, uc) in tiers.items()]
    for name, qty, price in parts:
        if price is not None:  # free-form 'chi phí khác' — its own full-VND price
            out.append({"name": name, "qty": qty, "unit_cost": price, "total": price * qty})
            continue
        if resolve_motor_label(name, 0):
            continue
        uc = accessory_cost(name, customer_type)
        out.append({"name": name, "qty": qty, "unit_cost": uc, "total": uc * qty})
    return out
