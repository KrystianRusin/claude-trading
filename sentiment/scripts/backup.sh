#!/usr/bin/env bash
# Nightly archive of data/ into data/backups/, keeping the newest 30. Copies the newest to BACKUP_DIR when set.
set -euo pipefail
cd "$(dirname "$0")/.."
KEEP=30
[ -d data ] || { echo "no data/ folder"; exit 1; }
BACKUP_DIR="${BACKUP_DIR:-$(grep -E '^BACKUP_DIR=' .env 2>/dev/null | tail -1 | cut -d= -f2- || true)}"

mkdir -p data/backups
archive="data/backups/data-$(date +%Y%m%d-%H%M%S).tar.gz"
tar -czf "$archive" --exclude='data/trader.lock' --exclude='data/bars' --exclude='data/backups' data
echo "wrote $archive"

ls -1t data/backups/*.tar.gz | tail -n +$((KEEP + 1)) | xargs -r rm --

if [ -n "$BACKUP_DIR" ]; then
  mkdir -p "$BACKUP_DIR"
  cp "$archive" "$BACKUP_DIR/"
  echo "copied to $BACKUP_DIR"
fi
