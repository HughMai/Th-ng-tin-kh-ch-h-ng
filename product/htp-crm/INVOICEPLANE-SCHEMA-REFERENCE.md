# InvoicePlane schema → htp-crm reference

**What:** InvoicePlane's quote/invoice data model, mapped onto htp-crm's existing schema.
**Why:** Sanity-check htp-crm against a proven, 3.1k-star invoicing app — and steal the few patterns worth stealing.
**Source:** `application/modules/setup/sql/000_1.0.0.sql` (the v1.0.0 base install). Columns below are verified from that file. Later IP versions (v1.7.x) add a `products` catalog, quote/invoice discount fields, and client `vat_id` — flagged where relevant but **not** column-verified here.

> **Headline:** htp-crm already covers ~80% of InvoicePlane's model and is a *better fit* for HTP — VND integers, KH/DL tiers, m²-based door line items, Zalo, and follow-up automation are all things IP has no concept of. Don't migrate to IP or copy it wholesale. Borrow the 3 patterns in §3.

---

## 1. Table-by-table mapping (IP → htp-crm)

| InvoicePlane table | htp-crm equivalent | Verdict |
|---|---|---|
| `ip_clients` | `customers` | ✅ **Ahead** — htp-crm adds KH/DL `type`, `source`, `stage` (lead→customer), Zalo linking, diacritic-folded search |
| `ip_quotes` (header) | `quotes` (header) | ✅ Covered — htp-crm status (sent/chasing/won/lost) fits HTP better than IP's draft/sent/approved |
| `ip_quote_items` | `quote_items` | ✅ **Ahead** — htp-crm carries door specs (product, cong_nghe, mau, ngang_mm×cao_mm, mau_sac); IP items are just name/qty/price |
| `ip_quote_amounts` | `quotes.value_vnd` (computed) | ⚠️ Partial — htp-crm stores only the total; IP stores subtotal / tax_total / total as persisted rows |
| `ip_invoices` (header) | `orders` (won job) | ✅ Covered — htp-crm adds production `stage` + `urgent`, warranty tracking |
| `ip_invoice_items` | `order_items` ("hạng mục hóa đơn") | ✅ Covered — snapshot-copied from quote on chốt |
| `ip_invoice_amounts` (`invoice_paid`, `invoice_balance`) | *(nothing)* | ❌ **Gap** — no per-order paid/balance for KH jobs (see §3.1) |
| `ip_payments` | `debt_entries` (DL only) | ⚠️ Partial — dealer ledger is great; **retail (KH) payments aren't recorded** (see §3.1) |
| `ip_tax_rates` / `invoice_tax_total` | *(nothing)* | ❌ Gap — no VAT representation; `value_vnd` is pre-VAT (see §3.4) |
| `ip_invoice_groups` (number prefix/year/pad) | *(uses row `id`)* | ❌ Gap — no human-facing báo giá/đơn number (see §3.2) |
| `ip_quotes.quote_url_key` (CHAR(32)) | *(nothing)* | ❌ Gap — no shareable customer-facing quote link (see §3.3) |
| `ip_invoices_recurring` | *(nothing)* | N/A — HTP doesn't do recurring billing |
| `ip_item_lookups` (autocomplete) | pricing engine (`pricing.py`) | ✅ Covered differently — prices are computed, not looked up |
| *(none)* | `touches`, `reminders`, `service_calls`, `zalo_events` | 🟢 htp-crm-only — IP has no follow-up, care log, or warranty callbacks at all |

---

## 2. The verified IP core tables (for reference)

Compact column lists from the v1.0.0 base schema. `DECIMAL(10,2)` throughout because IP is float-money + multi-currency; **htp-crm's INTEGER VND is the right call for Vietnam — keep it.**

```
ip_clients        : client_id, client_name, client_address_1/2, client_city/state/zip/country,
                    client_phone, client_mobile, client_fax, client_email, client_web, client_active
ip_quotes         : quote_id, invoice_id (0 until converted), user_id, client_id, invoice_group_id,
                    quote_status_id, quote_date_created, quote_date_modified, quote_date_expires,
                    quote_number, quote_url_key
ip_quote_items    : item_id, quote_id, item_tax_rate_id, item_date_added, item_name,
                    item_description, item_quantity, item_price, item_order
ip_quote_amounts  : quote_amount_id, quote_id, quote_item_subtotal, quote_item_tax_total,
                    quote_tax_total, quote_total
ip_invoices       : invoice_id, user_id, client_id, invoice_group_id, invoice_status_id,
                    invoice_date_created, invoice_date_modified, invoice_date_due,
                    invoice_number, invoice_terms, invoice_url_key
ip_invoice_items  : item_id, invoice_id, item_tax_rate_id, item_date_added, item_name,
                    item_description, item_quantity, item_price, item_order
ip_invoice_amounts: invoice_amount_id, invoice_id, invoice_item_subtotal, invoice_item_tax_total,
                    invoice_tax_total, invoice_total, invoice_paid, invoice_balance
ip_payments       : payment_id, invoice_id, payment_method_id, payment_date, payment_amount, payment_note
ip_tax_rates      : tax_rate_id, tax_rate_name, tax_rate_percent
ip_invoice_groups : invoice_group_id, invoice_group_name, invoice_group_prefix,
                    invoice_group_next_id, invoice_group_left_pad, prefix_year, prefix_month
```

**Structural idea worth noting:** IP separates *documents* (`quotes`, `invoices`) from their *money* (`quote_amounts`, `invoice_amounts`) into 1:1 sibling tables, so totals are persisted, queryable, and never recomputed on the fly. htp-crm instead recomputes `value_vnd` on every write (`recompute_quote_derived_fields`). htp-crm's approach is simpler and fine at HTP's volume — but the one place persisted amounts pay off is **paid/balance** (§3.1), because "who owes what" is a query you run constantly.

---

## 3. Patterns worth borrowing (ranked by value to HTP)

### 3.1 — Payment + balance tracking for retail (KH) orders ⭐ the real gap
> ✅ **Implemented** (Phase 4): `order_payments` table, per-order `paid_vnd`/`balance_vnd`, deposit carryover on chốt, and the "💵 Khách lẻ còn nợ" Hôm nay section. See `store.py`, `app.py` `/don-hang/{id}/thanh-toan`, `tests_smoke_phase4.py`.

Today htp-crm tracks money owed **only for dealers** (`debt_entries`, filtered `type='DL'`). A retail job's deposit lives on `quotes.deposit_vnd`, but once it becomes an order there's no record of *what's actually been paid* or *what's outstanding*. That's Section 9's "receivables" leak for the KH half of the book.

IP's answer: a `payments` table (many payments per invoice) + persisted `invoice_paid` / `invoice_balance`. Adapted to htp-crm's style:

```sql
CREATE TABLE IF NOT EXISTS order_payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL REFERENCES orders(id),
    kind        TEXT NOT NULL CHECK (kind IN ('coc','thanh_toan')),  -- deposit | payment
    amount_vnd  INTEGER NOT NULL CHECK (amount_vnd > 0),
    pay_date    TEXT NOT NULL,
    method      TEXT,                    -- 'tien_mat' | 'chuyen_khoan' | 'zalopay'
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
```
Then `balance = orders.value_vnd - SUM(order_payments.amount_vnd)`, computed at read time (the same pattern `dealer_balance()` already uses). Gives you a "còn nợ" number on every order and a new Hôm-nay section: *KH jobs with an outstanding balance N days after install.*

> Note: `debt_entries` already IS a general charge/payment ledger — you *could* extend it to KH instead of adding a table. Cleaner to keep dealer running-credit and per-job payments separate, but it's a judgment call.

### 3.2 — Formal document numbers (báo giá / đơn number, separate from `id`)
> ✅ **Implemented** (Phase 4): `store.bao_gia_so()` renders `BG-2026-0042` on the báo giá list, detail, page title, and the exported xlsx + filename. Derived (not stored), year from `sent_date`.

htp-crm shows quotes as `#123` (the raw autoincrement). IP keeps a human-facing `quote_number`/`invoice_number` with a prefix + year + zero-pad (`ip_invoice_groups`), e.g. `BG-2026-0042`. Worth it once customers keep and reference báo giá across visits. Cheap version — no new table, just a formatter:

```python
def bao_gia_so(quote_id: int, created: str) -> str:   # "BG-2026-0042"
    return f"BG-{created[:4]}-{quote_id:04d}"
```
Only build the full IP-style per-year resetting counter if HTP actually wants numbers restarting each year.

### 3.3 — Shareable public quote/invoice link (`url_key`)
IP gives every quote/invoice a `CHAR(32)` random `url_key` so a customer can open it at a public URL without logging in. For HTP this is the **Zalo-native báo giá**: generate a link, paste it into the Zalo chat, customer views a clean web báo giá on their phone. Add `url_key TEXT` to `quotes` (random token), serve a read-only view. High leverage given HTP lives on Zalo and currently sends báo giá as photos.

### 3.4 — Tax/VAT columns — only if hóa đơn đỏ is needed
htp-crm's `value_vnd` is pre-VAT with no tax representation. IP models it as `tax_rate_percent` + per-item and per-doc `tax_total`. Add this **only when HTP needs to issue VAT invoices (hóa đơn GTGT / hóa đơn đỏ)** — likely for dealers or larger jobs.

> ⚠️ **Legal caveat (repeat from earlier):** a legally valid Vietnamese VAT invoice is a **hóa đơn điện tử** issued through a licensed provider (MISA, Viettel, VNPT…), registered with the tax authority. htp-crm can *compute and display* the VAT figure, but the official hóa đơn must still go through a provider. Don't let a tax column imply legal compliance.

---

## 4. What NOT to copy from InvoicePlane
- **`DECIMAL` money / multi-currency** — htp-crm's INTEGER VND is correct for a single-currency VN business. Keep it.
- **`user_id` / multi-tenant plumbing** (`ip_user_clients`, per-user scoping) — HTP is single-tenant family-run. Dead weight.
- **Recurring invoices, merchant gateways, email templates, imports** — no HTP use case.
- **IP's generic `item_name`/`item_price` line items** — htp-crm's spec-carrying `quote_items` (dimensions, model, colour) are strictly better for doors. Don't flatten them.

**Bottom line:** htp-crm's model is the right one. The only genuine gap that maps to real HTP pain (Section 9 receivables) is **§3.1 — KH payment/balance tracking**. Build that; treat §3.2–3.4 as optional polish.
