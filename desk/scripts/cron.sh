#!/usr/bin/env bash
# The dashboard's cron schedule: the nightly download files (the dashboard itself is a systemd service).
# The repo's scripts/cron.sh installs it (from setup.sh and after every deploy). Lines are tagged and
# replace only this project's entries.
#
#   scripts/cron.sh install    replace this project's cron entries
#   scripts/cron.sh print      show them
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
CRON_TAG="# claude-desk"

lines="40 3 * * * cd $REPO && nice -n 10 .venv/bin/python -m desk.export >> $REPO/data/export.log 2>&1 $CRON_TAG"

case "${1:-}" in
  print) echo "$lines" ;;
  install)
    mkdir -p "$REPO/data"
    existing=$(crontab -l 2>/dev/null || true)
    new=$({ grep -vF "$CRON_TAG" <<<"$existing" || true; echo "$lines"; })
    [ "$new" = "$existing" ] || echo "$new" | crontab -
    ;;
  *) echo "usage: scripts/cron.sh install|print"; exit 2 ;;
esac
