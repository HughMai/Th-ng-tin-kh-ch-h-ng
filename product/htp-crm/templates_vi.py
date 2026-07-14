"""Vietnamese message templates for the HTP CRM tap-to-copy buttons.

DRAFT WORDING — polish against references/htp-review-style.md (the family's
voice on Google reviews) before the parents start sending these.

Placeholders: {ten} customer name · {san_pham} product label · {so_tien}
formatted VND · {ngay} date (DD/MM/YYYY) · {link} Google review link.
No DB table, no edit UI: this file IS the template store (edit + redeploy).
"""

# Google Business Profile review link — replace with HTP's real short link
# (Google Maps -> business profile -> "Ask for reviews" -> copy link).
GOOGLE_REVIEW_LINK = "https://g.page/r/REPLACE_ME/review"

PRODUCT_LABELS = {
    "nhom_kinh": "cửa nhôm kính",
    "cua_cuon": "cửa cuốn",
    "cua_keo": "cửa kéo",
    "xingfa": "nhôm xingfa",
    "khac": "sản phẩm",
}

SOURCE_LABELS = {
    "gioi_thieu": "Giới thiệu",
    "facebook": "Facebook",
    "google": "Google",
    "vang_lai": "Vãng lai",
    "khac": "Khác",
}

LOST_REASON_LABELS = {
    "gia_cao": "Giá cao",
    "chon_cho_khac": "Chọn chỗ khác",
    "khong_tra_loi": "Không trả lời",
    "hoan": "Hoãn lại",
    "khac": "Khác",
}

# Production stages for a won order (Tiến độ board). Ordered — the "next stage"
# button walks this sequence.
STAGE_LABELS = {
    "cho_san_xuat": "Chờ sản xuất",
    "dang_san_xuat": "Đang sản xuất",
    "dang_lap": "Đang lắp đặt",
    "hoan_thanh": "Hoàn thành",
}

TEMPLATES = {
    # Day-2 gentle nudge after a quote went quiet.
    "quote_followup_1": (
        "Chào {ten}, em bên Hưng Thành Phát ạ. Hôm trước bên em có gửi báo giá "
        "{san_pham} cho mình. Anh/chị xem giúp em đã được chưa ạ? Nếu cần điều "
        "chỉnh kích thước hay mẫu mã gì anh/chị cứ nhắn em nhé. Em cảm ơn ạ!"
    ),
    # Day-5+ stronger nudge — invite the price conversation instead of losing silently.
    "quote_followup_2": (
        "Chào {ten}, em bên Hưng Thành Phát ạ. Không biết anh/chị đã quyết định "
        "về phần {san_pham} chưa ạ? Nếu anh/chị còn băn khoăn về giá hay chất "
        "lượng, mình cứ trao đổi thêm với em — bên em làm hơn 20 năm, bảo hành "
        "rõ ràng, anh/chị yên tâm ạ."
    ),
    # 6-month courtesy check-in after install.
    "checkin_6m": (
        "Chào {ten}, em bên Hưng Thành Phát ạ. Bộ {san_pham} nhà mình lắp được "
        "khoảng 6 tháng rồi, anh/chị dùng có êm không ạ? Nếu cửa có kêu hay cần "
        "tra dầu, anh/chị nhắn em để bên em qua kiểm tra giúp mình nhé."
    ),
    # Warranty expiring within 30 days.
    "warranty_expiring": (
        "Chào {ten}, em bên Hưng Thành Phát ạ. Bộ {san_pham} nhà mình sắp hết "
        "hạn bảo hành ngày {ngay}. Nếu cửa có vấn đề gì anh/chị báo em sớm để "
        "bên em kiểm tra trong thời gian bảo hành cho mình nhé ạ."
    ),
    # Polite dealer debt reminder.
    "debt_reminder": (
        "Chào {ten}, Hưng Thành Phát xin phép nhắc nhẹ công nợ hiện tại của "
        "mình là {so_tien} ạ. Anh/chị sắp xếp thanh toán giúp bên em nhé. "
        "Em cảm ơn nhiều ạ!"
    ),
    # Post-install review ask (KH only).
    "review_request": (
        "Chào {ten}, cảm ơn anh/chị đã tin tưởng Hưng Thành Phát ạ! Nếu anh/chị "
        "hài lòng với bộ {san_pham}, anh/chị cho bên em một đánh giá trên Google "
        "giúp em nhé: {link} — Em cảm ơn nhiều ạ!"
    ),
}


def render(key: str, **kw) -> str:
    kw.setdefault("link", GOOGLE_REVIEW_LINK)
    return TEMPLATES[key].format(**kw)


def zalo_link(phone: str) -> str:
    """0901234567 -> https://zalo.me/84901234567 (Zalo wants country code)."""
    p = "".join(c for c in (phone or "") if c.isdigit())
    if p.startswith("0"):
        p = "84" + p[1:]
    return f"https://zalo.me/{p}"
