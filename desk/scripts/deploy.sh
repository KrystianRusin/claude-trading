#!/usr/bin/env bash
# Pull-based deploy, run by cron every 5 minutes (see scripts/cron.sh). Ships a new commit on
# origin/main to this machine once CI has passed for it:
#
#   fetch -> CI passed? -> download that commit's web build (a release asset CI made) -> fast-forward
#   -> pip install if requirements changed -> checks -> switch the web build -> restart the service
#   -> health check; if a check or the health check fails, roll back to the previous commit and build
#
# It never touches the bots' data. It refuses to deploy over local commits or local changes.
#
# Settings, from the environment or .env:
#   NTFY_TOPIC            send deploy notifications to ntfy.sh/<topic>
#   DEPLOY_GITHUB_TOKEN   read token for private repos (fine-grained: Contents and Checks read-only)
#   DEPLOY_CI_CHECK       "require" (default) or "skip" to deploy without checking CI
set -uo pipefail

NAME="claude-desk"
SERVICE="claude-desk"
BUILDS_KEPT=3

checks() {
  "$PY" -m pytest -q && "$PY" -m desk.check
}

log() { echo "$(date '+%F %T') $*"; }
env_value() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- || true; }
setting() { local v="${!1:-}"; [ -n "$v" ] && echo "$v" || env_value "$1"; }
short() { git rev-parse --short=12 "$1"; }

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

slug() { git remote get-url origin | sed -E 's#^.*github\.com[:/]##; s#\.git$##'; }

auth() { local token; token=$(setting DEPLOY_GITHUB_TOKEN); [ -n "$token" ] && echo "Authorization: Bearer $token" || echo "X-No-Auth: 1"; }

ci_status() {  # success | pending | failure | unknown
  [ "$(setting DEPLOY_CI_CHECK)" = "skip" ] && { echo success; return; }
  local json
  json=$(curl -fsS -m 20 -H "Accept: application/vnd.github+json" -H "$(auth)" \
    "https://api.github.com/repos/$(slug)/commits/$1/check-runs") || { echo unknown; return; }
  "$PY" -c '
import json, sys
runs = json.load(sys.stdin).get("check_runs", [])
if not runs or any(r["status"] != "completed" for r in runs):
    print("pending")
elif all(r["conclusion"] in ("success", "neutral", "skipped") for r in runs):
    print("success")
else:
    print("failure")' <<<"$json"
}

# Download and unpack the web build CI released for a commit into data/web/<sha12>/.
fetch_build() {
  local sha12="$1" dest="data/web/$1" tmp
  [ -f "$dest/index.html" ] && return 0
  tmp=$(mktemp -d)
  curl -fsSL -m 120 -H "$(auth)" -o "$tmp/web.tar.gz" \
    "https://github.com/$(slug)/releases/download/build-$sha12/web.tar.gz" || { rm -rf "$tmp"; return 1; }
  mkdir -p "$tmp/x" && tar -xzf "$tmp/web.tar.gz" -C "$tmp/x" && [ -f "$tmp/x/index.html" ] || { rm -rf "$tmp"; return 1; }
  rm -rf "$dest" && mkdir -p data/web && mv "$tmp/x" "$dest"
  rm -rf "$tmp"
}

use_build() { ln -sfn "$1" data/web/current.new && mv -T data/web/current.new data/web/current; }

restart() {
  XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" systemctl --user restart "$SERVICE"
}

healthy() {
  local port i
  port=$("$PY" -c 'from desk import config; print(config.load().port)') || return 1
  for i in $(seq 1 20); do
    curl -fsS -m 2 "http://127.0.0.1:$port/api/meta" >/dev/null 2>&1 &&
      curl -fsS -m 2 "http://127.0.0.1:$port/" | grep -q '<div id="root">' && return 0
    sleep 1
  done
  return 1
}

main() {
  cd "$(dirname "$0")/.." || exit 1
  PY="$PWD/.venv/bin/python"
  mkdir -p data/web

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
  fetch_build "$(short "$TARGET")" || { once "waiting for the web build release of this commit"; exit 0; }

  exec 9>data/deploy.lock
  flock -n -x 9 || exit 0  # another deploy is running

  log "deploying $(short "$head") -> $(short "$TARGET"): $(git log -1 --format=%s "$TARGET")"
  local previous reqs_changed=0
  previous=$(readlink data/web/current 2>/dev/null || true)
  git diff --quiet "$head" "$TARGET" -- requirements.txt || reqs_changed=1
  git merge -q --ff-only "$TARGET"
  [ "$reqs_changed" = 1 ] && "$PY" -m pip install -q -r requirements.txt

  local failed=""
  if ! checks > data/deploy_checks.log 2>&1; then
    failed="checks"
  elif ! { use_build "$(short "$TARGET")" && restart && healthy; }; then
    failed="the health check (the service did not answer after the restart)"
  fi
  if [ -z "$failed" ]; then
    scripts/cron.sh install
    echo "$TARGET" > data/deployed
    rm -f data/deploy_status data/deploy_failed
    ls -1dt data/web/*/ 2>/dev/null | grep -v '/current/$' | tail -n +$((BUILDS_KEPT + 1)) | xargs -r rm -rf --
    log "deployed $(short "$TARGET")"
    notify "deployed $(short "$TARGET"): $(git log -1 --format=%s "$TARGET")"
  else
    git reset -q --hard "$head"
    [ "$reqs_changed" = 1 ] && "$PY" -m pip install -q -r requirements.txt
    [ -n "$previous" ] && use_build "$previous"
    restart && healthy || log "WARNING: the previous version did not come back either; the dashboard is down"
    scripts/cron.sh install
    echo "$TARGET" > data/deploy_failed
    log "deploy of $(short "$TARGET") failed $failed; rolled back to $(short "$head")."
    if [ "$failed" = checks ]; then
      echo "Last lines of data/deploy_checks.log:"
      tail -n 15 data/deploy_checks.log
    else
      echo "See: journalctl --user -u $SERVICE -n 50"
    fi
    notify "deploy of $(short "$TARGET") failed $failed on the Pi; rolled back to $(short "$head")"
  fi
}

main "$@"
exit
