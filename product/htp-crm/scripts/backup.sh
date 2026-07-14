#!/usr/bin/env bash
#
# HTP CRM — daily off-box SQLite backup.
#
# Takes a consistent snapshot with SQLite `VACUUM INTO` (WAL-safe, no downtime),
# gzips it, and pushes it to Google Drive via rclone. Keeps a short local rolling
# copy on the VPS and prunes old snapshots on both sides.
#
# Install on the VPS (see references/vps-access.md and BACKUP-RUNBOOK.md):
#   scp this file to /opt/htp-crm/scripts/backup.sh ; chmod +x it
#   crontab -e ->  0 2 * * * /opt/htp-crm/scripts/backup.sh >> /var/log/htp-crm-backup.log 2>&1
#
# Override any of these via the environment (e.g. in the crontab line) if needed.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/htp-crm}"
CONTAINER="${CONTAINER:-htp-crm-app}"
DB_IN_CONTAINER="${DB_IN_CONTAINER:-data/htp.db}"   # path as seen inside the container
DATA_DIR="${DATA_DIR:-$APP_DIR/data}"               # host side of the ./data bind mount
REMOTE="${REMOTE:-gdrive:htp-crm-backups}"          # rclone remote:path
RETAIN_LOCAL_DAYS="${RETAIN_LOCAL_DAYS:-14}"
RETAIN_REMOTE_DAYS="${RETAIN_REMOTE_DAYS:-30}"

TS="$(date +%F)"                                    # YYYY-MM-DD
log() { echo "[$(date +'%F %T')] $*"; }

log "backup start (db=$CONTAINER:$DB_IN_CONTAINER remote=$REMOTE)"

# 1. Consistent snapshot via VACUUM INTO, written into the bind-mounted data dir
#    (so it lands on the host at $DATA_DIR). Clear any stale same-day target
#    first — VACUUM INTO refuses to overwrite an existing file.
SNAP_NAME="htp-backup-$TS.db"
rm -f "$DATA_DIR/$SNAP_NAME" "$DATA_DIR/$SNAP_NAME.gz"
docker exec "$CONTAINER" python -c "import sqlite3; sqlite3.connect('$DB_IN_CONTAINER').execute(\"VACUUM INTO '$(dirname "$DB_IN_CONTAINER")/$SNAP_NAME'\")"
log "snapshot written: $DATA_DIR/$SNAP_NAME ($(du -h "$DATA_DIR/$SNAP_NAME" | cut -f1))"

# 2. Compress (gzip replaces the .db with .db.gz)
gzip -f "$DATA_DIR/$SNAP_NAME"
SNAP_GZ="$DATA_DIR/$SNAP_NAME.gz"
log "compressed: $SNAP_GZ ($(du -h "$SNAP_GZ" | cut -f1))"

# 3. Push off-box
rclone copy "$SNAP_GZ" "$REMOTE/"
log "uploaded to $REMOTE/"

# 4. Prune — local rolling copies, then old remote snapshots
find "$DATA_DIR" -maxdepth 1 -name 'htp-backup-*.db.gz' -mtime "+$RETAIN_LOCAL_DAYS" -delete
log "pruned local snapshots older than ${RETAIN_LOCAL_DAYS}d"
rclone delete --min-age "${RETAIN_REMOTE_DAYS}d" "$REMOTE/"
log "pruned remote snapshots older than ${RETAIN_REMOTE_DAYS}d"

log "backup done"
