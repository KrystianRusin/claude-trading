#!/usr/bin/env bash
# One-time setup on a Raspberry Pi (64-bit Raspberry Pi OS) or other Debian/Ubuntu machine.
# Safe to re-run: each step skips what is already done.
#
#   scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
PY="$REPO/.venv/bin/python"
CRON_TAG="# sentiment-trader"
PT_SECRETS="$HOME/.config/paper-trader/env"
OWN_SECRETS_DIR="$HOME/.config/sentiment-trader"

step() { printf '\n==> %s\n' "$*"; }
ask() { local reply; read -r -p "$1 [y/N] " reply; [[ "$reply" =~ ^[Yy]$ ]]; }
env_value() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- || true; }
set_env() {
  touch .env
  chmod 600 .env
  grep -vE "^$1=" .env > .env.tmp || true
  printf '%s=%s\n' "$1" "$2" >> .env.tmp
  mv .env.tmp .env
  chmod 600 .env
}

step "Checking platform"
[ "$(uname -s)" = "Linux" ] || { echo "This script is for Linux."; exit 1; }
arch=$(uname -m)
if [ "$arch" != "aarch64" ] && [ "$arch" != "x86_64" ]; then
  echo "Need a 64-bit OS (found $arch). Flash Raspberry Pi OS Lite (64-bit)."; exit 1
fi
mem_mb=$(awk '/MemTotal/ {print int($2 / 1024)}' /proc/meminfo)
[ "$mem_mb" -lt 1800 ] && echo "Warning: ${mem_mb} MB RAM. The Claude CLI may struggle below 2 GB."
echo "OK: $arch, ${mem_mb} MB RAM"

step "System packages"
if command -v python3 >/dev/null && python3 -c 'import venv' 2>/dev/null && command -v git >/dev/null \
    && command -v curl >/dev/null; then
  echo "OK: already installed"
else
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3 python3-venv python3-pip git curl ca-certificates >/dev/null
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
  || { echo "Python 3.11+ required, found $(python3 --version)."; exit 1; }
echo "OK: $(python3 --version)"

step "Timezone"
tz=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone)
if [ "$tz" = "America/Toronto" ] || [ "$tz" = "America/New_York" ]; then
  echo "OK: $tz"
elif ask "Timezone is $tz. Cron times assume Eastern. Set it to America/Toronto?"; then
  sudo timedatectl set-timezone America/Toronto
  echo "OK: America/Toronto"
else
  echo "Left as $tz. Shift the cron hours yourself so they match Eastern time."
fi

step "Python virtual environment and packages"
[ -x "$PY" ] || python3 -m venv .venv
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -r requirements.txt
echo "OK"

step "Alpaca keys for the second paper account (.env)"
if [ -n "$(env_value ALPACA_API_KEY)" ] && [ -n "$(env_value ALPACA_SECRET_KEY)" ]; then
  echo "OK: .env already has keys"
else
  echo "Paste the keys of the SECOND paper account, not the paper-trader's (input is hidden)."
  read -r -s -p "ALPACA_API_KEY: " key; echo
  read -r -s -p "ALPACA_SECRET_KEY: " secret; echo
  [ -n "$key" ] && [ -n "$secret" ] || { echo "Both keys are required."; exit 1; }
  set_env ALPACA_API_KEY "$key"
  set_env ALPACA_SECRET_KEY "$secret"
  unset key secret
fi
own=$("$PY" broker.py account-number --env .env) || { echo "The keys do not reach a paper account."; exit 1; }
echo "OK: keys reach paper account $own"

if [ -n "$(env_value PAPER_TRADER_ACCOUNT)" ]; then
  echo "OK: PAPER_TRADER_ACCOUNT already set"
else
  read -r -p "Path to the paper-trader repo (blank if there is none): " pt
  pt="${pt/#\~/$HOME}"
  if [ -z "$pt" ]; then
    set_env PAPER_TRADER_ACCOUNT none
  else
    [ -f "$pt/.env" ] || { echo "No .env in $pt."; exit 1; }
    other=$("$PY" broker.py account-number --env "$pt/.env") || { echo "Could not read the paper-trader's account."; exit 1; }
    if [ "$other" = "$own" ]; then
      echo "These keys belong to the paper-trader's account. Create a second paper account and use its keys."
      exit 1
    fi
    set_env PAPER_TRADER_ACCOUNT "$other"
    for d in "$pt/state" "$pt/data"; do
      if [ -f "$d/daily_summary.csv" ] || [ -d "$d" ]; then set_env PAPER_TRADER_STATE "$d"; break; fi
    done
    echo "OK: paper-trader account $other differs from $own"
  fi
fi
if ! grep -q '^NTFY_TOPIC=' .env; then
  read -r -p "ntfy.sh topic for alerts (blank for none): " topic
  set_env NTFY_TOPIC "$topic"
fi
chmod 600 .env

step "Claude Code CLI"
export PATH="$HOME/.local/bin:$PATH"
if command -v claude >/dev/null; then
  echo "OK: $(claude --version 2>/dev/null | head -1)"
else
  curl -fsSL https://claude.ai/install.sh | bash
  command -v claude >/dev/null || { echo "claude not found after install; check ~/.local/bin"; exit 1; }
fi

step "Claude token for scoring"
if [ -f "$PT_SECRETS" ] && grep -q '^export CLAUDE_CODE_OAUTH_TOKEN=.\+' "$PT_SECRETS"; then
  SECRETS="$PT_SECRETS"
  echo "OK: reusing the paper-trader's token in $SECRETS"
else
  SECRETS="$OWN_SECRETS_DIR/env"
  mkdir -p "$OWN_SECRETS_DIR"
  chmod 700 "$OWN_SECRETS_DIR"
  if [ -f "$SECRETS" ] && grep -q '^export CLAUDE_CODE_OAUTH_TOKEN=.\+' "$SECRETS"; then
    echo "OK: token already stored in $SECRETS"
  else
    echo "'claude setup-token' prints a sign-in link. Sign in with your Claude subscription and copy the token."
    claude setup-token || true
    read -r -s -p "Paste the token (input is hidden): " token; echo
    [ -n "$token" ] || { echo "No token given."; exit 1; }
    umask 077
    printf 'export CLAUDE_CODE_OAUTH_TOKEN=%s\n' "$token" > "$SECRETS"
    chmod 600 "$SECRETS"
    unset token
    echo "OK: stored in $SECRETS"
  fi
fi
# shellcheck disable=SC1090
. "$SECRETS"
if claude -p "reply with ok" --model claude-haiku-5-5 --tools "" 2>&1 | grep -qi '\bok\b'; then
  echo "OK: claude -p answers"
else
  echo "Warning: 'claude -p' did not answer ok. Check the token, then re-run this script."
fi

step "State folder"
"$PY" config.py

step "Tests"
"$PY" -m pytest -q

step "Alpaca check (account, calendar, news)"
"$PY" broker.py

step "Cron jobs"
LOG="$REPO/state/cron.log"
RUN="cd $REPO &&"
cron_lines="*/30 7-20 * * 1-5 $RUN $PY news.py fetch >> $LOG 2>&1 $CRON_TAG
0 10,18 * * 0,6 $RUN $PY news.py fetch >> $LOG 2>&1 $CRON_TAG
30 6 * * 1-5 . $SECRETS; $RUN $PY score.py run >> $LOG 2>&1 $CRON_TAG
45 9 * * 1-5 $RUN $PY trader.py run >> $LOG 2>&1 $CRON_TAG
15 16 * * 1-5 $RUN $PY trader.py close >> $LOG 2>&1 $CRON_TAG
30 18 * * 1-5 $RUN $PY analyze.py >> $LOG 2>&1 && $PY compare.py >> $LOG 2>&1 $CRON_TAG
0 3 * * * $REPO/scripts/backup.sh >> $LOG 2>&1 $CRON_TAG"
existing=$(crontab -l 2>/dev/null || true)
{ grep -vF "$CRON_TAG" <<<"$existing" || true; echo "$cron_lines"; } | crontab -
echo "OK: installed"

step "Done"
crontab -l | grep -F "$CRON_TAG"
cat <<EOF

Schedule (local time, Eastern):
  news fetch   every 30 min 07:00-20:00 weekdays, 10:00 and 18:00 weekends
  scoring      06:30 weekdays
  trader run   09:45 weekdays (exits early on holidays)
  trader close 16:15 weekdays
  reports      18:30 weekdays (state/reports/, state/comparison.md)
  backup       03:00 daily (backups/)

Logs: state/cron.log (everything), state/sentiment.log (warnings and errors).

The trader starts in dry-run (simulated fills in state/dry_run/). After 30 scored sessions,
switch to paper orders by setting "mode": "paper" in state/config.json.
EOF
