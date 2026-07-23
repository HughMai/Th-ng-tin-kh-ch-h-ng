# Sổ Thu Chi — Hưng Thành Phát

Sổ thu chi hằng ngày: what the family spends and takes in, by day and by month. Tiền khách trả
(cọc, thanh toán, công nợ đại lý) flows in automatically from the CRM — nobody re-types it.

Two people use it: a parent and the kế toán. Each has their own password, and every line records
who wrote it.

- **Live:** https://thuchi.187-77-133-39.sslip.io — deployed 2026-07-23.
  `https://thuchi.hungthanhphat.vn` is wired in Caddy but **needs an A record
  `thuchi → 187.77.133.39` at tenten.vn** before it resolves.
- **Sibling app:** [`../htp-crm`](../htp-crm/README.md) — the source of the CRM rows.
- **Context for Claude Code:** [`CLAUDE.md`](CLAUDE.md) · **Design:** [`DESIGN.md`](DESIGN.md)

---

## Local development

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt httpx   # httpx is for the tests only
cp .env.example .env        # then edit USERS + SESSION_SECRET
uvicorn app:app --reload --port 8011
```

Open http://localhost:8011 and log in with any password from `USERS`.

Set `COOKIE_INSECURE=1` in `.env` for local http, otherwise the session cookie is TLS-only and
you'll bounce back to `/login` forever.

To see CRM rows locally, run the CRM on port 8010 with a matching `THUCHI_TOKEN` in its `.env`,
and point this app's `.env` at it:

```
CRM_URL=http://localhost:8010
THUCHI_TOKEN=<same value as in ../htp-crm/.env>
```

Leave `CRM_URL` blank and the app runs standalone — manual entry only, no CRM section.

### Tests

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe tests_smoke.py
```

Runs against a temp database and stubs the CRM, so it's safe and needs nothing running. The CRM
side of the link is tested from the other app:

```bash
cd ../htp-crm && PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe tests_smoke_api_thu.py
```

---

## Configuration (`.env`)

| Key | What |
|---|---|
| `SESSION_SECRET` | Long random string; signs the session cookie. Must differ from the CRM's. |
| `USERS` | `Tên:mật-khẩu;Tên:mật-khẩu`. The password *is* the identity — the name it maps to gets stamped on every entry that person writes. |
| `DB_PATH` | Defaults to `data/thuchi.db`. |
| `START_DATE` | `YYYY-MM-DD`. CRM money-in before this date is hidden (those months have Thu but no Chi). |
| `CRM_URL` | Internal URL of the CRM container, `http://htp-crm-app:8000`. Blank = no CRM section. |
| `THUCHI_TOKEN` | Shared secret. **Must match `THUCHI_TOKEN` in the CRM's `.env`.** Blank on either side = the endpoint is off (403). |
| `COOKIE_INSECURE` | `1` for local http only. Leave blank in production. |

---

## Cài lên điện thoại (install as an app)

It's a PWA, so it installs to the home screen and opens fullscreen with no browser
chrome — no app store, no APK.

- **Android (Chrome/Edge):** open the site → menu (⋮) → **Install app** / *Thêm vào màn hình chính*.
- **iPhone (Safari — must be Safari):** open the site → Share → **Add to Home Screen**.

The icon is a teal **TC** tile, deliberately different from the CRM's navy **H**.

Verified live 2026-07-23: HTTPS, service worker active at scope `/`, manifest served
as `application/manifest+json`, `display: standalone`, all three icons 200.

Pages are **never** served from cache — only CSS/JS/fonts/icons are. Money must never
be read from a stale cache, so with no network you get a "mất mạng" card, not old numbers.

## First deploy (once) — already done 2026-07-23, kept for rebuilds

The app runs on the Hermes VPS (`187.77.133.39`) alongside htp-crm and speed-to-lead. The
speed-to-lead Caddy owns ports 80/443 for the whole box, so this app publishes no ports — it
joins the external `caddy_shared` network and Caddy reverse-proxies to it. That same network is
how it reaches the CRM at `http://htp-crm-app:8000`.

Non-interactive SSH from Windows: [`references/vps-access.md`](../../references/vps-access.md).

**1. Ship the code**

```bash
ssh root@187.77.133.39 'mkdir -p /opt/htp-thuchi'
scp -r app.py store.py views.py static requirements.txt Dockerfile docker-compose.yml \
    root@187.77.133.39:/opt/htp-thuchi/
```

**2. Write `/opt/htp-thuchi/.env`** from `.env.example`. Generate real secrets:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"   # SESSION_SECRET
python -c "import secrets; print(secrets.token_urlsafe(32))"   # THUCHI_TOKEN
```

Set `START_DATE` to the day the family starts using it, and give each person their own password.

**3. Turn the feed on in the CRM** — add the *same* `THUCHI_TOKEN` to `/opt/htp-crm/.env`, then
ship the two changed CRM files and rebuild:

```bash
cd ../htp-crm
scp app.py store.py root@187.77.133.39:/opt/htp-crm/
ssh root@187.77.133.39 'cd /opt/htp-crm && docker compose up -d --build'
```

**4. Start this app**

```bash
ssh root@187.77.133.39 'cd /opt/htp-thuchi && docker compose up -d --build'
```

**5. Add the Caddy site block** to `/opt/speed-to-lead/Caddyfile`:

```
thuchi.hungthanhphat.vn, thuchi.187-77-133-39.sslip.io {
    reverse_proxy thuchi-app:8000
}
```

Then reload Caddy explicitly — `docker compose up -d` does **not** pick up a bind-mounted
Caddyfile change:

```bash
ssh root@187.77.133.39 'docker exec speed-to-lead-caddy-1 caddy reload --config /etc/caddy/Caddyfile'
```

**6. DNS** — add an A record `thuchi → 187.77.133.39` at tenten.vn. The sslip.io URL works
immediately without this; the pretty domain needs it.

**7. Verify**

```bash
curl -s https://thuchi.187-77-133-39.sslip.io/health     # {"status":"ok"}
```

Then log in on a phone, add one real Chi, and check a CRM payment from today shows up under Thu.

**8. Backups.** As of 2026-07-23 the box runs `/opt/backup-local.sh`
([`scripts/backup-local.sh`](scripts/backup-local.sh)) daily at 02:00 via
`/etc/cron.d/htp-backup`, snapshotting **both** `htp.db` and `thuchi.db` into
`/opt/backups` with 30-day retention (`VACUUM INTO`, WAL-safe, no downtime).

> ⚠️ **These snapshots are on the same box.** They cover app bugs, bad writes and
> accidental deletes — **not** losing the VPS. Finishing the job means configuring an
> rclone remote and running [`../htp-crm/scripts/backup.sh`](../htp-crm/scripts/backup.sh),
> which pushes off-box to Google Drive. That needs Hughie's Google OAuth, so it is
> **still outstanding**. Before this date **nothing on the box was backed up at all.**

Check it: `ls -lh /opt/backups && tail /var/log/htp-backup.log`

---

## Routine deploy

```bash
scp app.py store.py views.py root@187.77.133.39:/opt/htp-thuchi/
ssh root@187.77.133.39 'cd /opt/htp-thuchi && docker compose up -d --build'
```

Add `static` to the scp list when CSS/JS changed. Rebuild (not restart) when `requirements.txt`
changed.

---

## Troubleshooting

**"Chưa lấy được tiền đã thu bên CRM"** on every page — the CRM feed is failing. In order of
likelihood: `THUCHI_TOKEN` differs between the two `.env` files (a 403); the CRM container is
down (`docker ps`); `CRM_URL` is wrong or the app isn't on `caddy_shared`. Check with:

```bash
ssh root@187.77.133.39 'docker exec thuchi-app python -c "
import os, requests
r = requests.get(os.environ[\"CRM_URL\"] + \"/api/thu\", params={\"tu\":\"2026-07-01\",\"den\":\"2026-07-31\"},
                 headers={\"X-Thuchi-Token\": os.environ[\"THUCHI_TOKEN\"]}, timeout=5)
print(r.status_code, r.text[:200])"'
```

Manual entry is unaffected while this is broken — the sổ stays usable.

**Login bounces straight back to `/login`** — either `SESSION_SECRET` is blank, or the cookie is
Secure while you're on plain http (set `COOKIE_INSECURE=1` locally; never in production).

**A CRM row shows a wrong amount** — fix it in the CRM (đơn hàng or công nợ). This app re-reads
live, so the correction appears on the next refresh. There is deliberately no edit path here.

**Nobody can log in** — `USERS` is blank or malformed. The format is
`Tên:mật-khẩu;Tên:mật-khẩu`; a chunk without a `:` is skipped silently.
