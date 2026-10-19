#!/usr/bin/env bash
# Pull-based deploy of the whole repo, run by cron every 5 minutes (see scripts/cron.sh). Ships a new commit
# on origin/main to this machine once CI has passed for it, touching only the projects the commit changed:
#
#   fetch -> CI passed? -> which projects changed -> the dashboard's web build (if desk/ changed)
#   -> lock those projects -> save their state -> fast-forward -> pip install where requirements changed
#   -> each changed project's checks (incl. database migrations) -> reinstall cron, switch the web build and
#   restart the dashboard, health check. If anything fails, the checkout goes back to the previous commit and
#   every changed project gets its state back.
#
# Each project has its own lock (<project>/data/deploy.lock): its jobs hold it shared (scripts/job.sh) and a
# deploy takes it exclusively, so code never changes under a running job. A deploy takes only the locks of
# the projects it changes, so a dashboard change never waits on a trading session, and the reverse.
# Each bot's deploy is recorded in the runs table of its database. It refuses to deploy over local
# commits or local changes. Its own state is in data/ at the root of the repo.
#
# Settings, from the environment or the first of .env, daytrader/.env, sentiment/.env that has them:
#   NTFY_TOPIC            send deploy notifications to ntfy.sh/<topic>
#   DEPLOY_GITHUB_TOKEN   read token for private repos (fine-grained: Contents and Checks read-only)
#   DEPLOY_CI_CHECK       "require" (default) or "skip" to deploy without checking CI
set -uo pipefail

NAME="claude-trading"
PROJECTS=(daytrader sentiment desk)
SERVICE="claude-desk"
BUILDS_KEPT=3

# Which files of a project a commit must touch for it to deploy (an extended regex on repo paths).
# The dashboard's tests read the bots' core migrations.
paths() {
  case "$1" in
    desk) echo '^(shared|desk)/|^(daytrader|sentiment)/migrations/' ;;
    *) echo "^(shared|$1)/" ;;
  esac
}

# Files a bot's checks may change: its config (key merges) and its database (migrations). Restored on rollback.
config_files() {
  case "$1" in
    daytrader) echo "data/config.json data/config.shadow.json" ;;
    sentiment) echo "data/config.json" ;;
  esac
}
db_tool() { case "$1" in daytrader) echo db.py ;; sentiment) echo src/db.py ;; esac; }

checks() {  # run in the project's folder
  case "$1" in
    daytrader)
      "$PY" -m pytest -q && "$PY" config.py && "$PY" db.py migrate &&
        QUANT_STATE_DIR="$PWD/data" "$PY" -m pytest -q -p quant_testing tests/test_safety_strategies.py ;;
    sentiment) "$PY" -m pytest -q && "$PY" src/config.py && "$PY" src/db.py migrate ;;
    desk) "$PY" -m pytest -q && "$PY" -m desk.check ;;
  esac
}

# Consistent copy of a SQLite database through its backup API (works with the WAL file present).
sqlite_copy() { "$PY" -c 'import sqlite3, sys; s, d = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2]); s.backup(d)' "$1" "$2"; }

# The deploy as a runs row (job = deploy) in a bot's database, and what it changed in the state as versions
# rows. Uses whichever db.py is checked out; never fails the deploy.
record_deploy() {  # project, exit code, file with the output
  local tool id
  tool=$(db_tool "$1")
  [ -n "$tool" ] || return 0
  (
    cd "$1" || exit 0
    PY="$PWD/.venv/bin/python"
    id=$("$PY" "$tool" run-start deploy --started "$STARTED" --sha "$(short "$TARGET")" 2>/dev/null) &&
      "$PY" "$tool" run-end "$id" "$2" --output "$3" >/dev/null 2>&1
    [ "$2" = 0 ] && "$PY" "$tool" versions --reason deploy >/dev/null 2>&1
  )
  return 0
}

log() { echo "$(date '+%F %T') $*"; }
env_value() {
  local f
  for f in .env daytrader/.env sentiment/.env; do
    grep -qE "^$1=" "$f" 2>/dev/null && { grep -E "^$1=" "$f" | tail -1 | cut -d= -f2-; return; }
  done
}
setting() { local v="${!1:-}"; [ -n "$v" ] && echo "$v" || env_value "$1"; }
short() { git rev-parse --short=12 "$1"; }
slug() { git remote get-url origin | sed -E 's#^.*github\.com[:/]##; s#\.git$##'; }
auth() { local token; token=$(setting DEPLOY_GITHUB_TOKEN); [ -n "$token" ] && echo "Authorization: Bearer $token" || echo "X-No-Auth: 1"; }

notify() {
  local topic
  topic=$(setting NTFY_TOPIC)
  [ -n "$topic" ] && curl -fsS -m 10 -H "Title: $NAME deploy" -d "$1" "https://ntfy.sh/$topic" >/dev/null 2>&1
  return 0
}

# Log (and optionally notify) a waiting reason once per commit, not every 5 minutes.
once() {
  local key="$TARGET $1"
  [ "$(cat data/deploy_status 2>/dev/null)" = "$key" ] && return 0
  echo "$key" > data/deploy_status
  log "$(short "$TARGET"): $1"
  [ "${2:-}" = notify ] && notify "$(short "$TARGET"): $1"
  return 0
}

ci_status() {  # success | pending | failure | unknown
  [ "$(setting DEPLOY_CI_CHECK)" = "skip" ] && { echo success; return; }
  local json
  json=$(curl -fsS -m 20 -H "Accept: application/vnd.github+json" -H "$(auth)" \
    "https://api.github.com/repos/$(slug)/commits/$1/check-runs") || { echo unknown; return; }
  python3 -c '
import json, sys
runs = json.load(sys.stdin).get("check_runs", [])
if not runs or any(r["status"] != "completed" for r in runs):
    print("pending")
elif all(r["conclusion"] in ("success", "neutral", "skipped") for r in runs):
    print("success")
else:
    print("failure")' <<<"$json"
}

# The dashboard's web build: the release asset CI made for a version of desk/, unpacked into
# desk/data/web/<tree12>/. The name is the first 12 characters of the desk/ tree hash.
build_id() { git rev-parse "$1:desk" | cut -c1-12; }
fetch_build() {
  local id="$1" dest="desk/data/web/$1" tmp
  [ -f "$dest/index.html" ] && return 0
  tmp=$(mktemp -d)
  curl -fsSL -m 120 -H "$(auth)" -o "$tmp/web.tar.gz" \
    "https://github.com/$(slug)/releases/download/build-$id/web.tar.gz" || { rm -rf "$tmp"; return 1; }
  mkdir -p "$tmp/x" && tar -xzf "$tmp/web.tar.gz" -C "$tmp/x" && [ -f "$tmp/x/index.html" ] || { rm -rf "$tmp"; return 1; }
  rm -rf "$dest" && mkdir -p desk/data/web && mv "$tmp/x" "$dest"
  rm -rf "$tmp"
}
use_build() { ln -sfn "$1" desk/data/web/current.new && mv -T desk/data/web/current.new desk/data/web/current; }
restart() { XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" systemctl --user restart "$SERVICE"; }
healthy() {
  local port i
  port=$(cd desk && .venv/bin/python -c 'from desk import config; print(config.load().port)') || return 1
  for i in $(seq 1 20); do
    curl -fsS -m 2 "http://127.0.0.1:$port/api/meta" >/dev/null 2>&1 &&
      curl -fsS -m 2 "http://127.0.0.1:$port/" | grep -q '<div id="root">' && return 0
    sleep 1
  done
  return 1
}

save_state() {  # project, folder to save into
  local p="$1" f
  for f in $(config_files "$p"); do
    [ -f "$p/$f" ] && mkdir -p "$2/$p/$(dirname "$f")" && cp -p "$p/$f" "$2/$p/$f"
  done
  if [ -n "$(db_tool "$p")" ] && [ -f "$p/data/trader.db" ]; then
    mkdir -p "$2/$p" && sqlite_copy "$p/data/trader.db" "$2/$p/trader.db" || return 1
  fi
  return 0
}

restore_state() {  # project, folder it was saved into
  local p="$1" f db="$1/data/trader.db"
  for f in $(config_files "$p"); do [ -f "$2/$p/$f" ] && cp -p "$2/$p/$f" "$p/$f"; done
  [ -n "$(db_tool "$p")" ] || return 0
  if [ -f "$2/$p/trader.db" ]; then
    sqlite_copy "$2/$p/trader.db" "$db" || log "WARNING: could not restore $db from the pre-deploy copy"
  else
    rm -f "$db" "$db-wal" "$db-shm"  # created by the failed deploy; the next one re-imports
  fi
}

main() {
  cd "$(dirname "$0")/.." || exit 1
  ROOT="$PWD"
  PY=python3  # for sqlite_copy; each project's checks use its own venv
  mkdir -p data

  git fetch -q origin main || { log "git fetch failed"; exit 1; }
  local head
  head=$(git rev-parse HEAD)
  TARGET=$(git rev-parse origin/main)
  [ "$head" = "$TARGET" ] && exit 0
  [ "$(cat data/deploy_failed 2>/dev/null)" = "$TARGET" ] && exit 0  # failed before; wait for a new commit

  if ! git merge-base --is-ancestor HEAD origin/main; then
    once "not deploying: this checkout has commits that are not on origin/main" notify; exit 1
  fi
  if [ -n "$(git status --porcelain)" ]; then
    once "not deploying: the checkout has local changes ($(git status --porcelain | wc -l) files)" notify; exit 1
  fi
  case "$(ci_status "$TARGET")" in
    success) ;;
    pending) once "waiting for CI"; exit 0 ;;
    failure) echo "$TARGET" > data/deploy_failed; once "not deploying: CI failed" notify; exit 1 ;;
    *) once "not deploying: cannot read the CI result (private repo? set DEPLOY_GITHUB_TOKEN)" notify; exit 1 ;;
  esac

  local files p changed=()
  files=$(git diff --name-only "$head" "$TARGET")
  for p in "${PROJECTS[@]}"; do grep -qE "$(paths "$p")" <<<"$files" && changed+=("$p"); done
  local desk_changed=0
  [[ " ${changed[*]} " == *" desk "* ]] && desk_changed=1
  if [ "$desk_changed" = 1 ]; then
    fetch_build "$(build_id "$TARGET")" || { once "waiting for the web build release of this commit"; exit 0; }
  fi

  exec 8>data/deploy.lock
  flock -n -x 8 || exit 0  # another deploy is running
  local fd
  for p in "${changed[@]}"; do
    mkdir -p "$p/data"
    exec {fd}>>"$p/data/deploy.lock"
    flock -n -x "$fd" || { once "waiting for a running $p job to finish"; exit 0; }
  done

  STARTED=$(TZ=America/New_York date '+%FT%T%:z')
  log "deploying $(short "$head") -> $(short "$TARGET") (${changed[*]:-no project changed}): $(git log -1 --format=%s "$TARGET")"
  local saved reqs=() previous=""
  saved=$(mktemp -d)
  for p in "${changed[@]}"; do
    save_state "$p" "$saved" || { log "could not back up $p's database; not deploying"; rm -rf "$saved"; exit 1; }
    git diff --quiet "$head" "$TARGET" -- "$p/requirements.txt" || reqs+=("$p")
  done
  [ "$desk_changed" = 1 ] && previous=$(readlink desk/data/web/current 2>/dev/null || true)
  git merge -q --ff-only "$TARGET"
  for p in "${reqs[@]}"; do (cd "$p" && .venv/bin/python -m pip install -q -r requirements.txt); done

  local failed=""
  : > data/deploy_checks.log
  for p in "${changed[@]}"; do
    echo "==> $p" >> data/deploy_checks.log
    if ! (cd "$p" && PY="$PWD/.venv/bin/python" && checks "$p") >> data/deploy_checks.log 2>&1; then
      failed="the checks of $p"; break
    fi
  done
  if [ -z "$failed" ] && [ "$desk_changed" = 1 ] && ! { use_build "$(build_id "$TARGET")" && restart && healthy; }; then
    failed="the dashboard's health check (it did not answer after the restart)"
  fi

  if [ -z "$failed" ]; then
    scripts/cron.sh install
    echo "$TARGET" > data/deployed
    rm -f data/deploy_status data/deploy_failed
    for p in "${changed[@]}"; do record_deploy "$p" 0 "$ROOT/data/deploy_checks.log"; done
    ls -1dt desk/data/web/*/ 2>/dev/null | grep -v '/current/$' | tail -n +$((BUILDS_KEPT + 1)) | xargs -r rm -rf --
    log "deployed $(short "$TARGET")"
    notify "deployed $(short "$TARGET"): $(git log -1 --format=%s "$TARGET")"
  else
    git reset -q --hard "$head"
    for p in "${changed[@]}"; do restore_state "$p" "$saved"; done
    for p in "${reqs[@]}"; do (cd "$p" && .venv/bin/python -m pip install -q -r requirements.txt); done
    if [ "$desk_changed" = 1 ]; then
      [ -n "$previous" ] && use_build "$previous"
      restart && healthy || log "WARNING: the previous version of the dashboard did not come back either"
    fi
    scripts/cron.sh install
    echo "$TARGET" > data/deploy_failed
    log "deploy of $(short "$TARGET") failed $failed; rolled back to $(short "$head")."
    if [[ "$failed" == "the checks"* ]]; then
      echo "Last lines of data/deploy_checks.log:"
      tail -n 15 data/deploy_checks.log
    else
      echo "See: journalctl --user -u $SERVICE -n 50"
    fi
    for p in "${changed[@]}"; do record_deploy "$p" 1 "$ROOT/data/deploy_checks.log"; done
    notify "deploy of $(short "$TARGET") failed $failed on the Pi; rolled back to $(short "$head")"
  fi
  rm -rf "$saved"
}

main "$@"
exit
