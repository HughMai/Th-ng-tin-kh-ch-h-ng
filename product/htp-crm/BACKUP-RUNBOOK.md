# HTP CRM — Infra Hardening Runbook

Copy-paste steps for the two live-VPS jobs: **rotate the family password** and
**automate the daily off-box backup**. Host details are in
`references/vps-access.md` (Hermes, `root@187.77.133.39`, Docker Compose at
`/opt/htp-crm`, app served at `https://crm.187-77-133-39.sslip.io`).

Run everything from your machine. Nothing here is destructive to data except
where noted.

---

## 1. Rotate `FAMILY_PASSWORD`

The live login gate is still `test123`. Pick a strong value and swap it in.

**Generate a candidate** (either style — the app just needs a non-empty string):

```bash
openssl rand -base64 12          # random, e.g. "9Kf2Qm7pLxRt"
# …or a memorable passphrase you'll actually type on a phone: 3-4 words + a number
```

**Apply it on the VPS:**

```bash
ssh root@187.77.133.39
cd /opt/htp-crm
# edit .env, set FAMILY_PASSWORD=<your new value> (nano .env, or sed below)
sed -i 's/^FAMILY_PASSWORD=.*/FAMILY_PASSWORD=<NEW_VALUE>/' .env
grep '^FAMILY_PASSWORD=' .env                       # confirm it took
docker compose up -d --force-recreate htp-crm-app   # env_file is injected at CREATE, not restart
```

**Verify:** open `https://crm.187-77-133-39.sslip.io` in a private window and log
in with the new password.

> **Note:** rotating the password does **not** log out existing devices — the
> 30-day session cookie is signed by `SESSION_SECRET`, not the password. To force
> everyone to re-login, also rotate `SESSION_SECRET` in `.env` (any long random
> string) and `--force-recreate` again.

Update your local dev `.env` too if you want dev to match (optional; dev is fine
staying on `test123` with `COOKIE_INSECURE=1`).

---

## 2. Daily off-box backup → Google Drive

Snapshots the SQLite DB with `VACUUM INTO` (WAL-safe, no downtime), gzips it, and
pushes it to Google Drive with rclone. Script: `scripts/backup.sh` (in the repo).

### 2a. Install + authorize rclone (one-time)

```bash
ssh root@187.77.133.39
curl https://rclone.org/install.sh | sudo bash
```

Google Drive OAuth can't open a browser on the headless VPS, so authorize on your
**Windows** machine and paste the token in:

```bash
# On the VPS:
rclone config
#   n) new remote
#   name> gdrive
#   storage> drive
#   client_id / client_secret> (blank — press enter)
#   scope> 1  (full access)  — or 2 (drive.file) if you prefer least-privilege
#   Edit advanced config> n
#   Use auto config?> n      <-- IMPORTANT: say NO (headless)
#   -> it prints a command to run on a machine WITH a browser
```

```powershell
# On Windows (needs rclone installed locally: winget install Rclone.Rclone):
rclone authorize "drive"
#   -> opens a browser, you approve, it prints a token blob
```

Paste that token back into the VPS prompt, accept `team drive> n`, confirm `y`.
Then verify:

```bash
rclone lsd gdrive:                 # should list your Drive folders, no error
```

### 2b. Deploy the script + first run

```bash
# From your machine (repo root):
scp product/htp-crm/scripts/backup.sh root@187.77.133.39:/opt/htp-crm/scripts/backup.sh
ssh root@187.77.133.39 'chmod +x /opt/htp-crm/scripts/backup.sh && /opt/htp-crm/scripts/backup.sh'
```

Expect log lines ending in `backup done`. Confirm the upload:

```bash
ssh root@187.77.133.39 'rclone ls gdrive:htp-crm-backups'   # shows htp-backup-<date>.db.gz
```

### 2c. Schedule it

```bash
ssh root@187.77.133.39
timedatectl                        # note the server timezone (cron uses it)
crontab -e
# add this line (02:00 server time daily):
0 2 * * * /opt/htp-crm/scripts/backup.sh >> /var/log/htp-crm-backup.log 2>&1
crontab -l                         # confirm
```

Retention defaults: 14 days local on the VPS, 30 days on Drive (override with
`RETAIN_LOCAL_DAYS` / `RETAIN_REMOTE_DAYS` env vars in the cron line if needed).

---

## 3. Restore from a backup (when you need it)

```bash
# Pick a snapshot and pull it down:
rclone copy gdrive:htp-crm-backups/htp-backup-2026-07-14.db.gz .
gunzip htp-backup-2026-07-14.db.gz

# Swap it in (stop app, back up the current file first, then replace):
ssh root@187.77.133.39 'cd /opt/htp-crm && docker compose stop htp-crm-app && cp data/htp.db data/htp.db.pre-restore'
scp htp-backup-2026-07-14.db root@187.77.133.39:/opt/htp-crm/data/htp.db
ssh root@187.77.133.39 'cd /opt/htp-crm && docker compose start htp-crm-app'
```

Sanity-check the app loads and recent quotes/orders are present before deleting
`data/htp.db.pre-restore`.
