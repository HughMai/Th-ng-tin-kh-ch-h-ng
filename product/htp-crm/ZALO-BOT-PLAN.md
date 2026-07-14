# ZALO-BOT-PLAN — Zalo group work bot (zca-js sidecar), chat-first

Execution plan for a Sonnet session. Planned 2026-07-14 on Fable after codebase
research; every file/function named below was verified to exist. Revised same
day: the `/viec` web board + `tho` role were CUT from v1 (Hughie's call —
chat-first, board only if chat proves insufficient). Read `CLAUDE.md` (Coding
Guidelines section) before starting. Work phase by phase, commit per phase, run
ALL smoke tests before and after each phase:

```
cd product/htp-crm
./.venv/Scripts/python.exe tests_smoke.py
./.venv/Scripts/python.exe tests_smoke_phase2.py
./.venv/Scripts/python.exe tests_smoke_phase3.py
./.venv/Scripts/python.exe tests_smoke_phase4.py
```

## Goal

The family's existing Zalo group becomes the technician interface, both
directions:

- **CRM → group:** 7:00 morning digest of today's jobs, pings when the family
  changes a stage or install date, on-demand re-send from the CRM.
- **Group → CRM:** technicians type `xong <N>` to mark a job installed, or
  `viec` to get a fresh numbered work list. Nothing else.

The CRM stays the source of truth and the family's monitoring/scheduling tool —
unchanged. The bot is a disposable convenience layer: if it dies, the family
falls back to phone calls (today's status quo) and the CRM keeps working
untouched.

## Locked decisions (do not relitigate)

- **zca-js over Zalo OA/GMF.** GMF needs a paid OA package and a new OA-created
  group; the family wants their existing group. Accepted risk: unofficial API,
  account can be banned. Mitigations: CRM canonical, bot optional/disposable,
  failure is loud (status card on `/zalo`).
- **No web board / no thợ login in v1.** Deferred, not rejected — build it only
  if the bot proves too fragile or chat proves insufficient (that, or upgrade
  to OA/GMF). Don't scaffold for it now.
- **Bot account is a burner** (separate SIM), warmed up by human use before bot
  use. Never open Zalo Web/PC with it elsewhere — zca-js allows ONE web
  listener per account; opening Zalo Web kills the listener (README warning).
- **Command grammar is exactly two commands:** `xong <N>` (N from the last sent
  list) and `viec` (reply with a fresh numbered list). No free-text parsing.
  Malformed `xong ...` gets a help reply; all other chatter is silently
  ignored.
- **No VND amounts in group messages, ever.** Digest/pings carry: customer
  name, product description, address, install date, stage.
- **No cron in the CRM app** (existing design principle). The bot sidecar owns
  the morning-digest schedule (it's already a long-running process); the CRM
  only ever computes-on-request.

## Architecture

```
Zalo group (family + thợ)
   ▲ digest 7:00 / pings / replies            │ "xong 2" / "viec"
   │                                          ▼
htp-crm-bot (Node + zca-js, port 8100 internal)
   ▲ POST /send {text}          │ POST /bot/inbound {uid,name,text}
   │ GET  /bot/digest (pull)    ▼   → responds {reply?} (bot posts reply)
htp-crm-app (FastAPI :8000) ── SQLite ./data/htp.db
   ▲
   │ normal family login (unchanged)
family phones/PC
```

Both containers share the existing external `caddy_shared` network; the bot is
never exposed publicly. Shared secret `BOT_TOKEN` authenticates both directions
(header `X-Bot-Token`).

## Existing code to reuse (verified)

| What | Where |
| --- | --- |
| Order stages `cho_san_xuat→dang_san_xuat→dang_lap→hoan_thanh`, `urgent` flag | `store.py` `_migrate_orders_columns`, `set_order_stage` (writes a touch), `set_order_urgent` |
| `_ORDER_SELECT` / `list_orders()` → dicts incl. `customer_name, phone, address, stage, install_date, description, urgent` | `store.py:1024-1049` |
| `set_order_install(order_id, date)` | `store.py:1060` |
| `views.production_message(order, quote, items)` — Vietnamese work-order text the family already copies into Zalo manually (`copy_zalo_button`) | `views.py:233,274` — reuse phrasing/tone for digest lines |
| Zalo admin page + status cards | `app.py:986` `zalo_admin`, `views.zalo_admin_page` |
| Existing OA webhook auth pattern (shared secret, no cookie, 403 when unset) | `app.py:1024` `zalo_webhook` — model for `/bot/inbound` |
| `_guard` family-cookie guard for new admin routes | `app.py:107` |
| `requests` already a dependency (used by `zalo_client.py`) | use it for CRM→bot calls |

---

## Phase 0 — Human prerequisites (Hughie, not the agent)

- [ ] Burner Zalo account on a spare SIM. **Warm it up 1–2 weeks** (real chats,
      join the group, normal phone use) before pointing the bot at it. Fresh
      accounts that instantly automate get flagged fastest.
- [ ] Group admin adds the burner account to the family work group.
- [ ] Never log the burner into Zalo Web/PC anywhere else once the bot runs.

Code (Phase 1) can be built and smoke-tested before the account is ready; only
go-live (Phase 2 steps 3+) is gated on it.

---

## Phase 1 — Bot sidecar + CRM endpoints

### 1.1 New directory `product/htp-crm/zalo-bot/`
- `package.json` — deps: `zca-js` only (v2; text-only bot ⇒ no
  `imageMetadataGetter`/sharp needed). `"type": "module"`.
- `Dockerfile` — `node:20-slim`, `npm ci --omit=dev`, `CMD ["node", "bot.js"]`,
  `ENV TZ=Asia/Ho_Chi_Minh`.
- `.dockerignore` — node_modules, data.
- `bot.js` (~150–200 lines, plain `node:http`, no express). Check current API
  signatures at https://zca-js.tdung.com if anything below errors:
  - **Login:** if `/app/data/creds.json` exists → `zalo.login({cookie, imei,
    userAgent})`; on failure or absence → `zalo.loginQR(...)` writing the QR
    image to `/app/data/qr.png`; after any successful login persist context
    (cookie/imei/userAgent from the api context) back to creds.json. Retry
    loop: on listener `error`/`closed` or login failure, mark state and retry
    every 60s.
  - **State:** `{loggedIn, awaitingQR, lastEventAt}`.
  - **Listener** (`api.listener.on("message")`, then `.start()`):
    - Only `message.type === ThreadType.Group`, plain-text
      (`typeof message.data.content === "string"`), not `message.isSelf`.
    - If `GROUP_THREAD_ID` env is unset: log `threadId` + sender for every
      group message (discovery mode) and do nothing else.
    - If `threadId === GROUP_THREAD_ID`: POST to
      `${CRM_URL}/bot/inbound` with header `X-Bot-Token: ${BOT_TOKEN}`, JSON
      `{uid: message.data.uidFrom, name: message.data.dName || "", text:
      message.data.content}`. If the response JSON contains a non-empty
      `reply`, `api.sendMessage({msg: reply}, GROUP_THREAD_ID,
      ThreadType.Group)`.
  - **Digest scheduler:** every 60s, if local time is `DIGEST_HOUR`:00 (env,
    default 7) and not yet sent today and loggedIn: GET
    `${CRM_URL}/bot/digest` (same auth header); non-empty `text` → send to
    group.
  - **HTTP server** on `PORT` (default 8100):
    - `POST /send` (X-Bot-Token) `{text}` → sendMessage to group; 503 if not
      logged in.
    - `GET /qr` → the qr.png bytes while `awaitingQR`, else 404.
    - `GET /health` → `{loggedIn, awaitingQR, lastEventAt}` (no auth; network
      is internal).

### 1.2 Compose (`docker-compose.yml`)
- Add service `bot`: `build: ./zalo-bot`, `container_name: htp-crm-bot`,
  `restart: unless-stopped`, `env_file: .env`, volume `./data-bot:/app/data`,
  `expose: ["8100"]`, network `caddy_shared`. Keep the explanatory header
  comment style of the file.

### 1.3 Config
- `.env.example` additions (Vietnamese comments, mark the whole block "Bot Zalo
  nhóm — tuỳ chọn"): `BOT_TOKEN=` (long random), `BOT_URL=http://htp-crm-bot:8100`,
  `CRM_URL=http://htp-crm-app:8000`, `GROUP_THREAD_ID=`, `DIGEST_HOUR=7`.
- CRM reads `BOT_URL`/`BOT_TOKEN`; **every bot feature no-ops silently when
  BOT_URL or BOT_TOKEN is empty** — the app must run unchanged without the bot.

### 1.4 Store (`store.py`)
- New table in `configure()` (follow existing CREATE TABLE IF NOT EXISTS
  style + comment):
  `bot_digest(digest_date TEXT NOT NULL, item_no INTEGER NOT NULL, order_id
  INTEGER NOT NULL REFERENCES orders(id), PRIMARY KEY(digest_date, item_no))`.
- `save_digest(date, order_ids: list[int])` — delete that date's rows, insert
  1-based numbering. (Every send — morning, `viec`, manual button — re-saves;
  `xong N` always resolves against the LAST sent list. Numbers can shift after
  a re-send; acceptable at family scale, and the freshest message is the one
  people reply to.)
- `get_digest_order(date, item_no) -> Optional[int]`.
- `orders_for_digest(today) -> list` — unfinished orders (`stage !=
  'hoan_thanh'`) where `urgent=1` OR `install_date <= today+1` (overdue +
  today + tomorrow), ordered: overdue, today, tomorrow, urgent-undated.
  Reuse `_ORDER_SELECT`.

### 1.5 CRM routes (`app.py`)
- Helper `_bot_send(text) -> None` — best-effort `requests.post(f"{BOT_URL}/send",
  ..., timeout=3)`, header X-Bot-Token, swallow all exceptions (a dead bot must
  never break a CRM request). No-op when unconfigured.
- Helper `_digest_text(today) -> str` — build from `orders_for_digest` +
  `save_digest`. Format (match `production_message` tone; no VND):
  ```
  🔨 CÔNG VIỆC 14/07
  1️⃣ [QUÁ HẸN 12/07] Cô Lan — cửa cuốn khe thoáng — 25 Trần Phú — 0905...
  2️⃣ [Hôm nay] Anh Minh — cửa kéo 6zem — 14h — 12 Lê Lợi
  ...
  Lắp xong nhắn: xong <số> · Xem việc: viec
  ```
  Empty string when no jobs.
- `POST /bot/inbound` — auth: header `X-Bot-Token` matches `BOT_TOKEN`
  (`secrets.compare_digest`; 403 otherwise; also 403 when BOT_TOKEN unset —
  same pattern as `zalo_webhook`). Body `{uid, name, text}`:
  - `re.match(r"(?i)^\s*xong\s+(\d+)\s*$", text)` → look up
    `get_digest_order(today, n)`:
    - hit → if already `hoan_thanh` reply "… đã xong rồi"; else
      `set_order_stage(id, 'hoan_thanh')`, and if the order has no
      `install_date` also `set_order_install(id, today)` (the check-in /
      review / KH-debt nudges are all keyed on install_date), then reply
      `✅ {customer_name} — {description}: LẮP XONG ({name} báo)`.
    - miss → reply `Không thấy số {n} trong bảng hôm nay. Gõ: viec để xem bảng
      mới.`
  - text is `viec`/`việc` (case-insensitive, trimmed) → reply
    `_digest_text(today)` (re-numbers), or "Hôm nay không có việc 🎉" when
    empty.
  - text starts with "xong" but doesn't match the pattern → help reply
    `Gõ: xong <số> (số trong bảng công việc)`.
  - anything else → `{}` (ignore chatter). Always 200 with JSON
    `{"reply": ...}` or `{}`.
- `GET /bot/digest` — same auth; returns `{"text": _digest_text(today)}`.
- Stage-change pings (best-effort, after success): in the `order_set_stage`
  route (`app.py:804`) and `order_set_install` route (`app.py:817`) →
  `_bot_send("🔔 {customer_name} — {description}: {stage label / 'lắp ' + date}")`.
  Reuse the stage labels from `views.stage_badge`. `/bot/inbound` calls store
  directly and only sends its ✅ reply — no double message.
- `/zalo` admin page (`zalo_admin` route + `views.zalo_admin_page`): add a
  "🤖 Bot nhóm" card — server-side GET `BOT_URL/health` (timeout 1.5s):
  connected dot / "mất kết nối" warning / "chưa cấu hình". When `awaitingQR`,
  show `<img src="/zalo/bot/qr">` + instruction to scan with the burner
  account. Add family-guarded proxy route `GET /zalo/bot/qr` streaming
  `BOT_URL/qr`. Add button "📤 Gửi bảng công việc vào nhóm" → family-guarded
  POST route calling `_bot_send(_digest_text(today))` — the manual re-send for
  mid-day schedule changes.

### 1.6 Tests
- New `tests_smoke_phase5.py` — copy the harness pattern from
  `tests_smoke_phase4.py` (read it first; temp DB + FastAPI TestClient). CRM
  side only; the Node bot is not under test:
  1. `/bot/inbound` wrong/missing token → 403.
  2. Seed orders; `viec` → numbered reply, `bot_digest` rows written.
  3. `xong 1` → order `hoan_thanh`; missing install_date backfilled to today;
     reply contains customer name and "LẮP XONG".
  4. `xong 99` → help reply; `xin chào` → `{}` (no reply key).
  5. `xong 1` twice → second reply says already done, stage unchanged.
  6. `/bot/digest` returns the same numbered text; replies contain no "₫" for
     an order seeded with value_vnd.
  7. With BOT_TOKEN unset → `/bot/inbound` 403; app boots and all family pages
     still work (bot fully optional).

**Phase 1 verify:** all 5 smoke files pass; `docker compose config` parses;
commit (`feat(htp-crm): zalo group bot sidecar (zca-js) — digest + xong/viec`).

---

## Phase 2 — Deploy + go-live + runbooks

Deploy target: Hostinger VPS `187.77.133.39`, app at
`https://crm.187-77-133-39.sslip.io`. **Read `references/vps-access.md` first**
(access + paths; password also in `agents/hermes/.env`). A paramiko
deploy/verify script pair from the previous session may still exist at
`C:\Users\mth97\AppData\Local\Temp\claude\c--Users-mth97-Enki\1a355240-0979-4fd4-b0f6-81c46ee1da38\scratchpad\`
(`deploy_htp.py`, `verify_htp.py`) — reuse/adapt if present, otherwise recreate
(same approach: paramiko, since Git Bash lacks sshpass).

1. Push code to VPS app dir, add new .env keys (`BOT_TOKEN` random ≥32 chars,
   `BOT_URL`, `CRM_URL`, `DIGEST_HOUR=7`; leave `GROUP_THREAD_ID` empty for
   discovery), `docker compose up -d --build`.
2. Verify: CRM `/health` ok; `docker exec htp-crm-app curl -s
   http://htp-crm-bot:8100/health` returns JSON; smoke scripts still pass
   locally.
3. **First bot login (with Hughie; gated on Phase 0):** open `/zalo` → scan QR
   card with the burner account → health flips to loggedIn; creds.json
   persisted in `./data-bot`.
4. **Group discovery:** someone sends any message in the family group →
   `docker logs htp-crm-bot` shows the `threadId` → set `GROUP_THREAD_ID` in
   .env → `docker compose up -d bot`.
5. End-to-end: press "📤 Gửi bảng công việc" on `/zalo` → digest appears in
   group → reply `viec` → fresh list; reply `xong 1` from a normal member →
   order flips in the CRM and ✅ reply appears in group.
6. Tell the family to **pin the latest digest message** in the group (Hughie —
   human habit, replaces a web board for "what's current").

### Runbooks (append to `README.md`, Vietnamese, short)
- **Bot mất kết nối / bị khóa:** `/zalo` shows the warning; if session died →
  rescan QR from the card. If account banned → new burner account, admin adds
  it to the group, delete `data-bot/creds.json`, restart bot, rescan QR, done —
  no data lost (CRM is canonical; worst case = phone calls, the old way).
- **Đổi lịch giữa ngày:** bấm "📤 Gửi bảng công việc" lại (hoặc thợ gõ `viec`)
  — bảng số mới thay bảng cũ; ghim tin mới nhất.

**Phase 2 verify:** step 5 observed working live. Final commit; open PR to
`main` (branch: create `htp-crm/zalo-work-bot` off
`htp-crm/quote-numbers-payments` — that branch's PR is still pending).

---

## Out of scope (do not build)

- **Web work board (`/viec`) + `tho` login role** — deliberately cut from v1;
  revisit only if the bot proves fragile or chat proves insufficient.
- Zalo OA/GMF group messaging (the official upgrade path, same trigger).
- Free-text/NLP parsing; any command beyond `xong <N>` and `viec`.
- Per-technician accounts/permissions; assigning jobs to specific thợ.
- Money data in group messages. Ever.
- Scheduling/editing install dates from chat (family does that in the CRM).

## Acceptance checklist

- [ ] All smoke tests (5 files) pass; app runs unchanged with bot env unset.
- [ ] 7:00 digest posts to the group; `viec` returns a fresh numbered list.
- [ ] `xong N` flips the order to hoan_thanh (+ install_date backfill) and ✅
      confirms in the group; wrong N gets help text; chatter is ignored.
- [ ] CRM stage/install changes ping the group.
- [ ] Bot death breaks nothing in the CRM and is visible on `/zalo` within one
      page load; QR rescan recovers it.
- [ ] Runbooks in README; PR open.
