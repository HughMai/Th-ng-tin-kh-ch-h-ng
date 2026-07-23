#!/usr/bin/env bash
#
# On-box daily SQLite snapshots for BOTH family apps (htp-crm + htp-thuchi).
#
# Why this exists separately from htp-crm/scripts/backup.sh: that script pushes
# off-box to Google Drive via rclone, which needs an authorised rclone remote.
# Until that's set up, this gives the databases *some* protection — consistent
# daily snapshots with retention, on the same box.
#
# WHAT THIS DOES NOT PROTECT AGAINST: losing the VPS. Same disk, same machine.
# It covers app bugs, bad writes and accidental deletes, not disk/host loss.
# Finish the job by configuring rclone and running htp-crm/scripts/backup.sh.
#
# Snapshots use SQLite VACUUM INTO — WAL-safe and consistent, no downtime.
#
# Install on the VPS:
#   scp this file to /opt/backup-local.sh ; chmod +x /opt/backup-local.sh
#   crontab -e ->  0 2 * * * /opt/backup-local.sh >> /var/log/htp-backup.log 2>&1
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/opt/backups}"
RETAIN_DAYS="${RETAIN_DAYS:-30}"
TS="$(date +%F)"

log() { echo "[$(date +'%F %T')] $*"; }

snapshot() {
  container="$1"; db_in_container="$2"; label="$3"
  if ! docker ps --format '{{.Names}}' | grep -qx "$container"; then
    log "SKIP $label — container '$container' is not running"
    return 0
  fi
  tmp="/tmp/${label}-${TS}.db"
  docker exec "$container" rm -f "/tmp/snap.db"
  docker exec "$container" python -c \
    "import sqlite3; sqlite3.connect('${db_in_container}').execute(\"VACUUM INTO '/tmp/snap.db'\")"
  docker cp "$container:/tmp/snap.db" "$tmp"
  docker exec "$container" rm -f "/tmp/snap.db"
  gzip -f "$tmp"
  mv -f "${tmp}.gz" "$BACKUP_DIR/${label}-${TS}.db.gz"
  log "$label -> $BACKUP_DIR/${label}-${TS}.db.gz ($(du -h "$BACKUP_DIR/${label}-${TS}.db.gz" | cut -f1))"
}

mkdir -p "$BACKUP_DIR"
log "backup start"
snapshot htp-crm-app  data/htp.db     htp-crm
snapshot thuchi-app   data/thuchi.db  htp-thuchi
find "$BACKUP_DIR" -maxdepth 1 -name '*.db.gz' -mtime "+$RETAIN_DAYS" -delete
log "pruned snapshots older than ${RETAIN_DAYS}d"
log "backup done"
