#!/usr/bin/env bash
# The day trader's cron schedule, in one place. The repo's scripts/cron.sh installs it (from setup.sh and
# after every deploy), so schedule changes ship like code. Lines are tagged and replace only this project's
# entries, including those tagged "# paper-trader" from before the monorepo.
# Jobs run through scripts/job.sh, which records them in the runs table under JOB_NAME.
#
#   scripts/cron.sh install    replace this project's cron entries
#   scripts/cron.sh print      show them
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
SECRETS="$HOME/.config/daytrader/env"
CRON_TAG="# daytrader"
OLD_TAG="# paper-trader"

lines="0 9 * * 1-5 JOB_NAME=trader $REPO/scripts/job.sh $REPO/scripts/run_trader.sh >> $REPO/data/cron.log 2>&1 $CRON_TAG
0 17 * * 1-5 . $SECRETS; JOB_NAME=review $REPO/scripts/job.sh $REPO/review/run_review.sh $CRON_TAG
50 2 * * * JOB_NAME=purge $REPO/scripts/job.sh $REPO/.venv/bin/python $REPO/db.py purge >> $REPO/data/cron.log 2>&1 $CRON_TAG
0 3 * * * JOB_NAME=backup $REPO/scripts/job.sh $REPO/.venv/bin/python $REPO/db.py backup >> $REPO/data/cron.log 2>&1 $CRON_TAG"

case "${1:-}" in
  print) echo "$lines" ;;
  install)
    existing=$(crontab -l 2>/dev/null || true)
    new=$({ grep -vF -e "$CRON_TAG" -e "$OLD_TAG" <<<"$existing" || true; echo "$lines"; })
    [ "$new" = "$existing" ] || echo "$new" | crontab -
    ;;
  *) echo "usage: scripts/cron.sh install|print"; exit 2 ;;
esac
