#!/usr/bin/env bash
# Moves an old state/ folder (and backups/) into the data/ layout. Run once, then re-run scripts/setup.sh
# to reinstall the cron jobs with the new paths.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -d state ] || { echo "no state/ folder; nothing to migrate"; exit 0; }
[ -e data ] && { echo "data/ already exists; move the files by hand"; exit 1; }
if [ -e state/trader.lock ] && ! flock -n state/trader.lock true; then
  echo "a trader job is running; try again when it finishes"; exit 1
fi

mkdir -p data/books/paper data/memory data/logs
mv_if() { for f in "${@:1:$#-1}"; do [ -e "$f" ] && mv "$f" "${!#}"; done; return 0; }

mv_if state/config.json state/watchlist.json state/universe.json state/comparison.md data/
mv_if state/news state/scores state/bars data/
mv_if state/memory.md state/memory.json data/memory/
[ -d state/memory_history ] && mv state/memory_history data/memory/history
mv_if state/sentiment.log state/cron.log data/logs/
mv_if state/dry_run state/replay data/books/
mv_if state/trades.csv state/orders.csv state/positions.csv state/daily_summary.csv state/book_state.json \
  state/sim_account.json state/reports state/lifetime.md state/lifetime.json data/books/paper/
rm -f state/trader.lock
[ -d backups ] && mv backups data/backups

if rmdir state 2>/dev/null; then
  echo "moved state/ into data/"
else
  echo "moved state/ into data/; these were left in state/ because their place is unknown:"
  ls -A state
fi
echo "now re-run scripts/setup.sh to reinstall the cron jobs"
