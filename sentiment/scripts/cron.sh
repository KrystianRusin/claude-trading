#!/usr/bin/env bash
# The cron schedule, in one place. setup.sh installs it once and deploy.sh reinstalls it after every
# deploy, so schedule changes ship like code. Lines are tagged and replace only this repo's entries.
# Every job runs through scripts/job.sh, under the shared deploy lock, and is recorded in the runs table
# of data/trader.db under its JOB_NAME.
#
#   scripts/cron.sh install    replace this repo's cron entries
#   scripts/cron.sh print      show them
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
PY="$REPO/.venv/bin/python"
CRON_TAG="# sentiment-trader"
PT_SECRETS="$HOME/.config/paper-trader/env"
if [ -f "$PT_SECRETS" ] && grep -q '^export CLAUDE_CODE_OAUTH_TOKEN=.\+' "$PT_SECRETS"; then
  SECRETS="$PT_SECRETS"
else
  SECRETS="$HOME/.config/sentiment-trader/env"
fi
LOG="$REPO/data/logs/cron.log"
job() { echo "cd $REPO && JOB_NAME=$1 scripts/job.sh"; }  # a job under the deploy lock, named in the runs table
# Snapshots run 2 minutes off the 5-minute deploy check, so the two do not keep meeting at the lock.

lines="*/30 7-20 * * 1-5 $(job news) $PY src/news.py fetch >> $LOG 2>&1 $CRON_TAG
0 10,18 * * 0,6 $(job news) $PY src/news.py fetch >> $LOG 2>&1 $CRON_TAG
0 6 * * * $(job attention) $PY src/attention.py morning >> $LOG 2>&1 $CRON_TAG
0 18 * * * $(job reddit) $PY src/attention.py collect apewisdom >> $LOG 2>&1 $CRON_TAG
0 19 * * 1-5 $(job attention-volume) $PY src/attention.py collect volume >> $LOG 2>&1 $CRON_TAG
30 6 * * 1-5 . $SECRETS; $(job score) $PY src/score.py run >> $LOG 2>&1 $CRON_TAG
45 9 * * 1-5 $(job trader-run) $PY src/trader.py run >> $LOG 2>&1 $CRON_TAG
32-57/5 9 * * 1-5 $(job snapshot) $PY src/trader.py snapshot >> $LOG 2>&1 $CRON_TAG
2-57/5 10-15 * * 1-5 $(job snapshot) $PY src/trader.py snapshot >> $LOG 2>&1 $CRON_TAG
15 16 * * 1-5 $(job trader-close) $PY src/trader.py close >> $LOG 2>&1 $CRON_TAG
30 18 * * 1-5 $(job analytics) sh -c \"$PY src/analyze.py; $PY src/lifetime.py; $PY src/compare.py\" >> $LOG 2>&1 $CRON_TAG
15 19 * * 1-5 $(job universe) $PY src/universe.py build >> $LOG 2>&1 $CRON_TAG
45 19 * * 1-5 . $SECRETS; $(job review) $PY src/review.py run >> $LOG 2>&1 $CRON_TAG
50 2 * * * $(job purge) $PY src/db.py purge >> $LOG 2>&1 $CRON_TAG
0 3 * * * $(job backup) $REPO/scripts/backup.sh >> $LOG 2>&1 $CRON_TAG
*/5 * * * * $REPO/scripts/deploy.sh >> $REPO/data/logs/deploy.log 2>&1 $CRON_TAG"

case "${1:-}" in
  print) echo "$lines" ;;
  install)
    mkdir -p "$REPO/data/logs"
    existing=$(crontab -l 2>/dev/null || true)
    new=$({ grep -vF "$CRON_TAG" <<<"$existing" || true; echo "$lines"; })
    [ "$new" = "$existing" ] || echo "$new" | crontab -
    ;;
  *) echo "usage: scripts/cron.sh install|print"; exit 2 ;;
esac
