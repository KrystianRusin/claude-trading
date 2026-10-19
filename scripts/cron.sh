#!/usr/bin/env bash
# The cron schedule of the whole repo: the 5-minute deploy check, plus each project's own schedule
# (<project>/scripts/cron.sh) for every project that is set up here (has its .venv). The projects' setup
# scripts and every deploy run it, so schedule changes ship like code. Lines are tagged per project and
# replace only that project's entries.
#
#   scripts/cron.sh install    replace the deploy entry and every set-up project's entries
#   scripts/cron.sh print      show them
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
CRON_TAG="# claude-trading"
PROJECTS=(daytrader sentiment desk)

lines="*/5 * * * * $REPO/scripts/deploy.sh >> $REPO/data/deploy.log 2>&1 $CRON_TAG"

case "${1:-}" in
  print)
    echo "$lines"
    for p in "${PROJECTS[@]}"; do [ -x "$p/.venv/bin/python" ] && "$p/scripts/cron.sh" print; done
    ;;
  install)
    mkdir -p "$REPO/data"
    existing=$(crontab -l 2>/dev/null || true)
    new=$({ grep -vF "$CRON_TAG" <<<"$existing" || true; echo "$lines"; })
    [ "$new" = "$existing" ] || echo "$new" | crontab -
    for p in "${PROJECTS[@]}"; do [ -x "$p/.venv/bin/python" ] && "$p/scripts/cron.sh" install; done
    true
    ;;
  *) echo "usage: scripts/cron.sh install|print"; exit 2 ;;
esac
