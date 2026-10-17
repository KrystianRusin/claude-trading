#!/usr/bin/env bash
# The cron schedule, in one place. setup.sh installs it once and deploy.sh reinstalls it after every
# deploy, so schedule changes ship like code. Lines are tagged and replace only this repo's entries.
# Every job runs through scripts/job.sh, under the shared deploy lock.
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
RUN="cd $REPO && scripts/job.sh"

lines="*/30 7-20 * * 1-5 $RUN $PY src/news.py fetch >> $LOG 2>&1 $CRON_TAG
0 10,18 * * 0,6 $RUN $PY src/news.py fetch >> $LOG 2>&1 $CRON_TAG
30 6 * * 1-5 . $SECRETS; $RUN $PY src/score.py run >> $LOG 2>&1 $CRON_TAG
45 9 * * 1-5 $RUN $PY src/trader.py run >> $LOG 2>&1 $CRON_TAG
15 16 * * 1-5 $RUN $PY src/trader.py close >> $LOG 2>&1 $CRON_TAG
30 18 * * 1-5 $RUN sh -c \"$PY src/analyze.py; $PY src/lifetime.py; $PY src/compare.py\" >> $LOG 2>&1 $CRON_TAG
15 19 * * 1-5 $RUN $PY src/universe.py build >> $LOG 2>&1 $CRON_TAG
45 19 * * 5 . $SECRETS; $RUN $PY src/review.py run >> $LOG 2>&1 $CRON_TAG
0 3 * * * $RUN $REPO/scripts/backup.sh >> $LOG 2>&1 $CRON_TAG
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
