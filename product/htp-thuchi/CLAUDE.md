# Sổ Thu Chi — Claude Code Context

**Project**: Sổ thu chi hằng ngày Hưng Thành Phát — what the family spends and takes in each day
**Live**: https://thuchi.hungthanhphat.vn (fallback: https://thuchi.187-77-133-39.sslip.io)
**Stack**: FastAPI · Uvicorn · SQLite · server-rendered HTML (`views.py`) · **no JS framework**
**Repo**: a subdirectory of the Enki repo, not its own repo.

Sibling app: [`../htp-crm`](../htp-crm/CLAUDE.md). This app **reads** money-in from it; it never
writes to the CRM. Full access map — VPS, secrets: [`htp/ACCESS.md`](../../htp/ACCESS.md).

---

## Commands

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt httpx
uvicorn app:app --reload --port 8011    # local dev → http://localhost:8011
```

Log in with any password from `USERS` in `.env`. Review pages at a **390×844** viewport.

### Tests

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe tests_smoke.py
```

`tests_smoke.py` stubs the CRM feed, so it needs no live CRM. The CRM endpoint has its own
test on the other side: `../htp-crm/tests_smoke_api_thu.py`.

### Deploy

Not a git push. Same shape as the CRM — copy files to the VPS, then rebuild:

```bash
scp app.py store.py views.py root@187.77.133.39:/opt/htp-thuchi/
ssh root@187.77.133.39 'cd /opt/htp-thuchi && docker compose up -d --build'
```

Full runbook, including the CRM-side token and the Caddy block: [`README.md`](README.md).

---

## Files

| File | Role |
|---|---|
| `app.py` | FastAPI routes, per-person auth (HMAC-SHA256 session cookie), CRM money-in fetch |
| `views.py` | All server-rendered Vietnamese HTML |
| `store.py` | SQLite data layer — one `entries` table, plus day/month aggregation |
| `static/app.css` | "Quiet Ledger" tokens, teal variant (see [`DESIGN.md`](DESIGN.md)) |

## How the CRM link works

Money customers pay is **never stored here**. On every page render the app calls
`GET /api/thu?tu=&den=` on the CRM (`X-Thuchi-Token` header) and shows the rows badged **CRM**.

- **Read-only by design.** A wrong amount is fixed in the CRM (đơn hàng / công nợ), not here.
  Because the read is live, the fix appears on the next refresh. Never add an edit path for
  these rows, and never copy them into `entries` — that's how two books start disagreeing.
- **The CRM being down must never take the sổ down.** `crm_thu()` swallows every failure and
  returns a message the page shows inline; manual entry keeps working.
- The CRM side (`store.thu_between`) gates on `c.type = 'KH'` for order payments. That gate is
  the dealer double-count guard — a ĐL's money is counted once, from `debt_entries`. There's a
  test for it; don't "simplify" it away.

## Locked decisions (do not change without asking)

1. **One password per person** (`USERS` env), and every entry is stamped `created_by` /
   `updated_by`. This is deliberately **different** from the CRM's one-shared-family-password
   rule: the kế toán is an outsider who must not hold the CRM password, and a money book needs
   to say who wrote each line. Do not "align" this with the CRM.
2. **No categories.** Date, nội dung, số tiền, hình thức — that's the whole entry. Chosen for
   speed of entry; the month view reports totals, not a breakdown. Adding categories is a
   product decision, not a refactor.
3. **No running balance / opening balance.** Views show Thu, Chi, and Còn lại for a day or a
   month. A cumulative số dư would need perfect entry discipline from day one to stay honest.
4. **`START_DATE` hides older CRM money.** Months before the sổ started have Thu but no Chi, so
   showing them would read as profit that never existed.
5. **No JS framework**, and no Zalo integration (the CRM's "no VND in the group" rule applies
   to this data too — it's all VND).
6. Public routes are exactly `/login` and `/health`. Everything else requires a session.

## Design system

Read [`DESIGN.md`](DESIGN.md) before any visual change. It inherits htp-crm's "Quiet Ledger"
wholesale and changes exactly one thing: the brand ramp is teal, so the two apps are tellable
apart on a phone home screen. Every token lives in `:root`; **no hex belongs outside it**.

North star: **"nhập 10 giây"** — the quick-entry form is the top of the day view, opens focused
on the amount, and saves in one tap. Anything that adds a step to entry needs a real reason.

## Skill guardrails

- **Point browser skills at localhost, not production.** Production is the family's real money
  book. Run `uvicorn app:app --port 8011` and test there.
- **Vietnamese output needs `PYTHONIOENCODING=utf-8`** on Windows for anything shelling to Python.
- **`/ship` does not deploy this** — it would open a PR against the Enki repo. Deploy is the
  scp + `docker compose up -d --build` sequence above.
- `/qa` will find no `package.json`; this is Python/FastAPI and the tests are `tests_smoke.py`.
