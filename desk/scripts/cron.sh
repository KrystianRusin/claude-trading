#!/usr/bin/env bash
# The cron schedule: the 5-minute deploy check and the nightly download files (the dashboard itself is a
# systemd service).
# setup.sh installs it and every deploy reinstalls it. Lines are tagged and replace only this repo's
# entries.
#
#   scripts/cron.sh install    replace this repo's cron entries
#   scripts/cron.sh print      show them
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
CRON_TAG="# claude-desk"

lines="*/5 * * * * $REPO/scripts/deploy.sh >> $REPO/data/deploy.log 2>&1 $CRON_TAG
40 3 * * * cd $REPO && nice -n 10 .venv/bin/python -m desk.export >> $REPO/data/export.log 2>&1 $CRON_TAG"

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
