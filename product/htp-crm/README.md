# HTP CRM — Sổ khách hàng Hưng Thành Phát

Phone-first Vietnamese CRM for the family door business (cửa cuốn / cửa kéo /
cửa nhôm kính). Digitizes the paper notebook and kills the two named leaks from
`clients/htp/discovery.md`: quotes that never get followed up, and customer
info trapped in a sổ tay.

**Stack:** FastAPI + SQLite + server-rendered Vietnamese HTML (no JS framework).
Same house pattern as `product/speed-to-lead-demo` and `product/qr-menu`.

## What it does

Home screen is **Hôm nay** — the morning to-do list, computed at read time
(no cron):

| Section | Rule |
|---|---|
| 📨 Cần nhắc báo giá | Quote sent 2+ days ago with no contact; re-surfaces every 3 days after a nudge until Chốt/Mất. 5+ days old → "Lần 2" stronger template |
| ⏰ Nhắc hôm nay | Manual reminders ("gọi lại sau Tết") due today |
| ⭐ Xin đánh giá | KH order installed within 14 days → ask for a Google review |
| 🔧 Bảo trì 6 tháng | Installed 6+ months ago, warranty still live, no check-in yet |
| 🛡️ Bảo hành sắp hết | Warranty expires within 30 days |
| 💰 Công nợ cần thu | Dealer balance > 0 and no payment for 30+ days |

Every card has **📋 Chép tin nhắn** (tap-to-copy prewritten Vietnamese message),
**Zalo** (zalo.me deep link), and **Gọi** (tel:). Sending stays human — the app
decides *who and what*, a person taps send. Customers linked to the HTP **Zalo
OA** (see `/zalo`) also get a **📨 Gửi qua API** button that sends the same
message straight through Zalo's OA Message API instead of opening the app —
still a manual tap, just no app-switch. See `zalo_client.py`.

Tabs: Hôm nay · Khách (search folds diacritics — "hung" finds "Hùng") · Báo giá
(pipeline with value total) · Đơn hàng (warranty math) · Công nợ (append-only
dealer ledger, running balance).

Follow-up rules are constants at the top of `store.py`. Message wording lives in
`templates_vi.py` — **draft text; polish against `references/htp-review-style.md`
and set the real `GOOGLE_REVIEW_LINK` before the parents use it.**

## Báo giá calculator (multi-item quotes)

"+ Nhiều hạng mục" on the Báo giá tab starts the real price calculator, ported
from the original `customer_form/index.html` tool: pick a customer → capture
accessories/deposit/install date → add door-unit line items (Cửa Cuốn / Cửa
Kéo / Cửa Nhôm Kính) with cascading công nghệ/mẫu dropdowns and ngang×cao
dimensions. The server (`pricing.py`) authoritatively computes the price from
the same KH/ĐL dual-tier table the original tool used — the in-browser
preview is UX only, never trusted. Every mẫu in the dropdowns has a matching
table price; the manual VND fallback only kicks in for a combo the table
truly has no entry for. Line items sum into the quote's total automatically.
The old single-item "+ Thêm báo giá" (hand-typed price) still works unchanged
for quick verbal quotes to repeat customers. Full pricing logic (in
Vietnamese, for the family to reference/edit) lives in
`BAOGIA-PRICING-LOGIC.md`.

## Desktop shell (GHL-style)

On screens ≥900px wide the same pages grow a left sidebar nav (bottom nav
hides), a stat-card row appears at the top of every viewport (open-pipeline
value, overdue công nợ, today's task count), and Báo giá renders as a 4-column
kanban board (Đã gửi/Đang theo dõi/Chốt/Mất) — drag a card between columns to
change its status, same routes the buttons already use. Below 900px nothing
changes: bottom nav, segmented list, and card layout are exactly as before.

## Cài như app (PWA — iPhone / Android / máy tính)

The CRM is an installable Progressive Web App: no App Store, no download — the
live site adds itself as a full-screen app with its own icon. `manifest.webmanifest`
+ `static/sw.js` (root-scope service worker) + `static/icons/*` supply the install
criteria; the head tags live in `views.PWA_HEAD`. **Requires HTTPS** (already true
on `crm.hungthanhphat.vn`) — it will not offer to install over plain http.

Hand the family these steps once:

- **iPhone (Safari):** mở `crm.hungthanhphat.vn` → nút Chia sẻ → **Thêm vào MH chính**.
- **Android (Chrome):** mở trang → menu ⋮ → **Cài đặt ứng dụng / Thêm vào MH chính**.
- **Máy tính (Chrome/Edge):** biểu tượng cài đặt ⊕ ở thanh địa chỉ → **Cài đặt**.

After install it opens standalone (no browser bar) and every deploy updates it
automatically. The service worker caches only shell assets (CSS/JS/fonts/icons) —
never CRM pages or data — so the family always sees fresh info; offline it shows a
"mất mạng" card (`static/offline.html`). Bump `CACHE` in `sw.js` when a shell asset
changes. Test install on the real HTTPS domain, not localhost.

## Run locally

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
cp .env.example .env        # set FAMILY_PASSWORD, SESSION_SECRET, COOKIE_INSECURE=1
python seed_demo.py          # demo data (DELETES the db — dev only!)
uvicorn app:app --reload --port 8010
```

Log in with `FAMILY_PASSWORD`. Check pages at a 390×844 viewport.

## Bulk import (paper notebook → CRM)

`/nhap` — paste one customer per line: `Tên, SĐT, KH hoặc ĐL, địa chỉ`.
Preview shows green (will add) / yellow (duplicate phone, skipped) / red
(unparseable) before anything is written. Dedupe is by normalized phone.

## Deploy (Hermes VPS — shared Caddy)

The STL Caddy owns ports 80/443 on the VPS, so this compose file has **no Caddy
of its own** — the app joins the external `caddy_shared` network.

```bash
# once, on the VPS
docker network create caddy_shared
# add `caddy_shared` to the caddy service networks in /opt/speed-to-lead/docker-compose.yml

# deploy
scp app.py store.py views.py templates_vi.py pricing.py baogia.py zalo_client.py \
    requirements.txt Dockerfile docker-compose.yml root@187.77.133.39:/opt/htp-crm/
scp -r static root@187.77.133.39:/opt/htp-crm/
# then create .env on the box
ssh root@187.77.133.39 'cd /opt/htp-crm && docker compose up -d --build'
```

Live at **`https://crm.hungthanhphat.vn`** (memorable domain added 2026-07-18,
DNS managed at tenten.vn — A record `crm -> 187.77.133.39`). Old
`https://crm.187-77-133-39.sslip.io` still works as a fallback.

Append to `/opt/speed-to-lead/Caddyfile` (back it up first):

```
crm.hungthanhphat.vn {
    reverse_proxy htp-crm-app:8000
}

crm.187-77-133-39.sslip.io {
    reverse_proxy htp-crm-app:8000
}
```

Reload Caddy with `docker exec speed-to-lead-caddy-1 caddy reload --config
/etc/caddy/Caddyfile` (a plain `docker compose up -d` does NOT reload it — the
Caddyfile is bind-mounted so compose sees no config change), then
`curl https://crm.hungthanhphat.vn/health`. Update both STATE.md files.
Tap-to-copy needs HTTPS — test the copy button on the real domain.

**Backup** (SQLite, safest via .backup):

```bash
ssh root@187.77.133.39 'docker exec htp-crm-app python -c "import sqlite3; sqlite3.connect(\"data/htp.db\").execute(\"VACUUM INTO \x27data/htp-backup.db\x27\")"'
scp root@187.77.133.39:/opt/htp-crm/data/htp-backup.db backups/
```

## Files

- `app.py` — routes + shared-password auth (HMAC session cookie, per-IP login throttle)
- `store.py` — schema (customers/quotes/quote_items/orders/service_calls/reminders/debt_entries), follow-up rule constants, all queries
- `pricing.py` — ported báo giá calculator: door-model config, KH/ĐL price tables, `get_price()`/`line_total()`
- `views.py` — server-rendered Vietnamese HTML, phone-first shell + GHL-style desktop shell (sidebar/stat-cards/kanban, additive CSS only)
- `templates_vi.py` — copyable Zalo message templates + `zalo_link()`
- `seed_demo.py` — dev demo data (never run in prod)
- `static/manifest.webmanifest`, `static/sw.js`, `static/offline.html`, `static/icons/` — PWA install plumbing (served via `/manifest.webmanifest` + `/sw.js` routes in `app.py`)
