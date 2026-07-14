# HTP CRM — Simplify + Tracking Plan

**Date:** 2026-07-13 · **Planned by:** Fable · **Executed by:** Sonnet agents (Phase 1, then Phase 2)
**Goal:** kill the clunk (full-page reload on every tap, 2 page-loads per quote line item, heavy uncached HTML) and add the tracking layer the CRM lacks (touch history, status history, a reports page).

## Audit findings (current state)

- 4,403 lines total: `app.py` 945 (60 routes), `store.py` 999, `views.py` 1,792 (43 render functions).
- Server-rendered, no JS framework (house pattern — keep it). Every button is a `<form>` POST → redirect → full re-render. On a phone this is the #1 source of "clunky".
- All CSS is inlined into every response via `page()` — nothing cacheable, every page carries the whole stylesheet including the desktop-only kanban/sidebar CSS.
- Multi-item quote builder: each line item costs 2 page loads (`/bao-gia/{id}/hang-muc/moi` form page → POST → back to build page). `quote_item_form_page` is the largest function (148 lines).
- Two UIs stacked in one file: phone-first tabs + GHL-style desktop shell (sidebar/stat-cards/kanban) behind `@media (min-width:900px)`. Functional, but nav is defined twice.
- **Tracking gaps** (the "what am I lacking" answer):
  - `quotes.last_contact_date` is a single overwritten date — nudge a customer 3 times and the history of the first 2 is gone.
  - Status changes (`sent→chasing→won/lost`, order stages) keep no timestamps → can't compute days-to-close, time-in-stage, or follow-up compliance.
  - No reports at all beyond 3 stat cards on Hôm nay: no win rate, no monthly revenue, no lost-reason breakdown, no công nợ aging.
  - No manual note-with-timestamp on a customer ("gọi rồi, hẹn tuần sau" has nowhere to live except overwriting the note field).

Hormozi lens (*$100M Leads — Lead Nurture pillars; playbook-lead-nurture.md Pillar IV "Volume negates luck"*): half of salespeople quit after attempt #1 — the CRM already schedules the chase, but without touch history it can't prove the volume happened or show which follow-ups convert. Track first, then optimize.

## Hard constraints (both phases)

- Live production DB on the VPS → **all schema changes additive** (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ADD COLUMN` via the existing `_migrate_*` pattern in `store.py`).
- All UI text Vietnamese, match the existing tone (informal family register, no jargon).
- No JS framework, no build step. Any JS is progressive enhancement — every action must still work with JS off (keep the `<form>` fallback).
- Don't touch `pricing.py` / `baogia.py` logic.
- Match existing code style exactly (f-string HTML in `views.py`, queries in `store.py`, routes thin in `app.py`).
- Do not deploy. Do not commit. Local changes only; Hughie reviews and deploys manually.

## Phase 1 — Tracking layer (Sonnet agent 1)

1. **`touches` table** in `store.py` (`init_db`, additive):
   `id, customer_id NOT NULL REFERENCES customers(id), quote_id REFERENCES quotes(id), order_id REFERENCES orders(id), kind TEXT NOT NULL CHECK (kind IN ('goi','zalo','zalo_api','trang_thai','ghi_chu','nhac_xong','bao_tri','danh_gia')), detail TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now'))` + index on `customer_id`.
   Add `store.add_touch(...)` and `store.list_touches(customer_id, limit=20)`.
2. **Instrument existing mutations** — one `add_touch` call at each: `mark_quote_contacted` (kind `zalo`, detail "Đã nhắn báo giá #N"), `set_quote_status` (kind `trang_thai`, detail old→new + lost_reason), the `/khach/{id}/gui-zalo` route (kind `zalo_api`), reminder done (`nhac_xong`), order stage change + `da-bao-tri` + `da-xin-danh-gia` (kinds `trang_thai`/`bao_tri`/`danh_gia`). Resolve `customer_id` from the quote/order row.
3. **Timeline on customer detail** (`customer_detail_page`): new "Lịch sử chăm sóc" section, newest first, icon per kind, `fmt_date` + relative day count. Plus a one-line quick-note form → `POST /khach/{customer_id}/cham-soc` (kind `ghi_chu`).
4. **`/bao-cao` reports page** (new route + nav entry in both mobile tabs and desktop sidebar): month picker (`?thang=YYYY-MM`, default current month, VN timezone via `today_vn()`), computed read-time in `store.py`:
   - Báo giá: gửi / chốt / mất trong tháng, **tỷ lệ chốt** (won ÷ (won+lost)), tổng giá trị chốt (VND).
   - Trung bình số ngày từ gửi → chốt (use `sent_date` → `updated_at` of won quotes; good enough until touch data accumulates).
   - Lý do mất (group by `lost_reason`).
   - Doanh thu theo sản phẩm (won quote value by `product`).
   - Công nợ: tổng dư nợ hiện tại + số đại lý quá 30 ngày chưa trả.
   - Chăm sóc: số lượt touch trong tháng theo kind (proves the follow-up volume — the Hormozi metric).
5. **Verify:** `.venv/Scripts/python.exe -c "import app"` compiles; `pip install httpx` into the venv if needed and run a TestClient smoke script (login with a test `FAMILY_PASSWORD` + temp `DB_PATH`/data dir, seed via `seed_demo.py` semantics or direct `store` calls, then GET `/`, `/khach/{id}`, `/bao-cao` → 200 and key Vietnamese strings present; POST a quick note → appears in timeline). Save the smoke script as `tests_smoke.py` in the app dir.

## Phase 2 — UX de-clunk (Sonnet agent 2, after Phase 1 lands)

1. **Extract CSS** from `views.py` `page()` into `static/app.css`; serve via `app.mount("/static", StaticFiles(...))` with `Cache-Control: max-age=86400` (small middleware or custom StaticFiles). `page()` emits one `<link rel="stylesheet">`. Keep a tiny critical inline block only if needed for flash-of-unstyled.
2. **One-tap actions without full reload**: ~30-line vanilla JS helper (in `static/app.js`): any `<form data-ajax>` posts via `fetch`, and on success either removes its card (`data-ajax="remove"` — Hôm nay "Đã nhắn"/"Xong" buttons) or reloads (`data-ajax="reload"` — fallback for anything stateful). Buttons keep working with JS off (server still redirects). Apply `data-ajax="remove"` to the Hôm nay card actions only — that's the daily-driver screen.
3. **Quote builder single-screen loop**: fold the add-item form into `quote_build_page` (collapsible `<details>` "➕ Thêm cửa"), so adding an item is one POST that re-renders the same page. Keep `/hang-muc/{item_id}/sua` as a separate page. Delete the now-unused standalone add form route only if nothing links to it.
4. **Nav single source of truth**: one `NAV` list constant → renders both mobile tabs and `_sidebar_html`, with Báo cáo included. No visual redesign — same look, one definition.
5. **Gzip**: `app.add_middleware(GZipMiddleware, minimum_size=500)`.
6. **Verify:** re-run `tests_smoke.py` (must still pass untouched — proves no-JS fallback intact); confirm quote item add now takes 1 page load; confirm `/static/app.css` returns cache header.

## Explicitly out of scope

Kanban rework, customer-facing pages, Zalo OA changes, auth changes, moving off SQLite, any framework adoption, deleting the desktop shell.
