#!/usr/bin/env bash
# Nightly SQLite backup with WAL checkpoint. Keeps 14 days.
# Install: sudo cp deploy/housespotter-backup.{service,timer} /etc/systemd/system/
# or add to crontab: 30 2 * * * /opt/housespotter/deploy/backup.sh
set -euo pipefail

DATA_DIR="${HS_DATA_DIR:-/opt/housespotter/data}"
BACKUP_DIR="${HS_BACKUP_DIR:-/opt/housespotter/backups}"
DB="$DATA_DIR/housespotter.db"

mkdir -p "$BACKUP_DIR"
STAMP=$(date +%Y%m%d)

# .backup takes a consistent snapshot even while the app is running (WAL mode)
sqlite3 "$DB" ".backup '$BACKUP_DIR/housespotter-$STAMP.db'"
gzip -f "$BACKUP_DIR/housespotter-$STAMP.db"

# Prune backups older than 14 days
find "$BACKUP_DIR" -name "housespotter-*.db.gz" -mtime +14 -delete

echo "Backup written: $BACKUP_DIR/housespotter-$STAMP.db.gz"
