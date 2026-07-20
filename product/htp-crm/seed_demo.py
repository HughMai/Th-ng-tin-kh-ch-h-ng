"""Dev-only demo seed for the HTP CRM. DELETES the configured DB and rebuilds
it with data that lights up every Hôm nay section on first load:

  - quote sent 1 day ago   -> must NOT appear in "Cần nhắc"
  - quote sent 3 days ago  -> nudge Lần 1
  - quote sent 6 days ago  -> nudge Lần 2
  - order installed ~6.2 months ago            -> "Bảo trì 6 tháng"
  - order whose 24-month warranty ends in ~20d -> "Bảo hành sắp hết"
  - KH order installed 3 days ago              -> "Xin đánh giá"
  - dealer: 2 charges + 1 payment 45 days ago  -> "Công nợ cần thu"
  - manual reminder due today                  -> "Nhắc hôm nay"
  - multi-item báo giá: auto-priced cửa cuốn (Đức KV380) + auto-priced
    nhôm kính (Nhôm Việt) line item -> exercises the calculator flow end-to-end

NEVER run against the production database.
"""
import calendar
import os
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

import pricing
import store

_db_path_env = os.environ.get("DB_PATH", "data/htp.db")
DB_PATH = _db_path_env if os.path.isabs(_db_path_env) else str(HERE / _db_path_env)


def shift_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def main() -> None:
    for suffix in ("", "-wal", "-shm"):
        p = Path(DB_PATH + suffix)
        if p.exists():
            p.unlink()
    store.configure(DB_PATH)
    today = date.fromisoformat(store.today_vn())

    # --- customers: 3 KH + 3 ĐL -------------------------------------------
    kh_hung = store.create_customer("Anh Hùng — Trần Phú", "0901111111", "KH", "gioi_thieu",
                                    "12 Trần Phú")
    kh_lan = store.create_customer("Chị Lan — Lê Lợi", "0902222222", "KH", "facebook",
                                   "34 Lê Lợi")
    kh_tuan = store.create_customer("Anh Tuấn — Nguyễn Huệ", "0903333333", "KH", "google",
                                    "56 Nguyễn Huệ")
    dl_minh_phat = store.create_customer("Đại lý Minh Phát", "0911111111", "DL", "khac",
                                         "KCN Hòa Khánh")
    dl_thanh_cong = store.create_customer("Đại lý Thành Công", "0912222222", "DL", "khac")
    store.create_customer("Đại lý Song Long", "0913333333", "DL", "gioi_thieu")

    # --- quotes: -1d hidden, -3d Lần 1, -6d Lần 2 ---------------------------
    store.create_quote(kh_lan, "nhom_kinh", "Cửa nhôm kính 2 cánh 1.2x2.2m", 18_000_000,
                       iso(today - timedelta(days=1)))
    store.create_quote(kh_hung, "cua_cuon", "Cửa cuốn khe thoáng 3x2.5m, mô-tơ Đài Loan",
                       12_000_000, iso(today - timedelta(days=3)))
    store.create_quote(kh_tuan, "cua_keo", "Cửa kéo Đài Loan 4x2.8m", 9_500_000,
                       iso(today - timedelta(days=6)))

    # --- orders -------------------------------------------------------------
    # These are all jobs already on the wall, so they sit at the terminal stage.
    # 'dang_lap' matters for the review ask specifically: orders_review_due()
    # gates on stage, so an order left at chờ sản xuất would never queue one.
    # Check-in due: installed ~6.2 months ago (still inside 24-month warranty).
    o_checkin = store.create_order(kh_hung, "cua_cuon", "Cửa cuốn nhà chính", 11_000_000,
                                   iso(shift_months(today, -6) - timedelta(days=7)), 24)
    # Warranty expiring in ~20 days (24-month warranty).
    o_expiry = store.create_order(kh_tuan, "nhom_kinh", "Bộ cửa nhôm kính mặt tiền", 25_000_000,
                                  iso(shift_months(today + timedelta(days=20), -24)), 24)
    # Review ask: KH order installed 3 days ago.
    o_review = store.create_order(kh_lan, "cua_cuon", "Cửa cuốn gara", 14_000_000,
                                  iso(today - timedelta(days=3)), 24)
    # Dealer order (should NOT trigger a review card — the queue is KH only).
    dl_order = store.create_order(dl_thanh_cong, "cua_keo", "5 bộ cửa kéo", 40_000_000,
                                  iso(today - timedelta(days=3)), 24)
    for _oid in (o_checkin, o_expiry, o_review, dl_order):
        store.set_order_stage(_oid, "dang_lap")

    # --- dealer ledger: overdue Minh Phát, linked charge for Thành Công ------
    store.add_debt_entry(dl_minh_phat, "charge", 10_000_000, iso(today - timedelta(days=90)),
                         "3 bộ cửa cuốn")
    store.add_debt_entry(dl_minh_phat, "charge", 5_000_000, iso(today - timedelta(days=60)),
                         "2 bộ cửa kéo")
    store.add_debt_entry(dl_minh_phat, "payment", 8_000_000, iso(today - timedelta(days=45)),
                         "chuyển khoản")
    store.add_debt_entry(dl_thanh_cong, "charge", 40_000_000, iso(today - timedelta(days=3)),
                         "5 bộ cửa kéo", order_id=dl_order)
    store.add_debt_entry(dl_thanh_cong, "payment", 40_000_000, iso(today - timedelta(days=1)),
                         "trả đủ")  # balance 0 -> must NOT appear anywhere

    # --- manual reminder due today -------------------------------------------
    store.create_reminder(kh_tuan, iso(today), "Khách hẹn gọi lại bàn thêm về màu cửa")

    # --- multi-item báo giá (calculator flow): two auto-priced door types ---
    multi_quote = store.create_quote_header(
        kh_hung, accessories="Motor x1, Remote x2", deposit_vnd=3_000_000,
        install_date=iso(today + timedelta(days=14)), note="Khách muốn màu ghi",
    )
    price = pricing.get_price("cua_cuon", "Cửa cuốn công nghệ Đức", "KV 380", "KH")
    store.add_quote_item(multi_quote, "cua_cuon", "Cửa cuốn công nghệ Đức", "KV 380",
                        3000, 2200, pricing.line_total(price, 3000, 2200), is_manual_price=False)
    nk_price = pricing.get_price("nhom_kinh", "Nhôm Việt", "Cửa sổ 1.2ly", "KH")
    store.add_quote_item(multi_quote, "nhom_kinh", "Nhôm Việt", "Cửa sổ 1.2ly",
                        1200, 2000, pricing.line_total(nk_price, 1200, 2000), is_manual_price=False)

    print(f"Seeded demo data into {DB_PATH} (today = {today})")


if __name__ == "__main__":
    main()
