#!/usr/bin/env bash
# One-time setup on the Raspberry Pi that runs the bots. Safe to re-run: each step checks what is done.
#
#   scripts/setup.sh
#
# Creates the venv, writes data/desk.json (where the bots' data is; everything else keeps the
# defaults in defaults/desk.json), installs the dashboard as a sandboxed systemd user service,
# downloads the current web build, checks for Tailscale (installing it if you agree), and installs
# the deploy cron job. It never makes the dashboard public: that is `tailscale funnel`, run by you.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
PY="$REPO/.venv/bin/python"
UNIT_DIR="$HOME/.config/systemd/user"
SERVICE="claude-desk"

step() { printf '\n==> %s\n' "$*"; }
ask() { local reply; read -r -p "$1 [y/N] " reply; [[ "$reply" =~ ^[Yy]$ ]]; }
ask_path() { local reply; read -r -p "$1 [$2] " reply; echo "${reply:-$2}"; }

step "Python virtual environment and packages"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "Python 3.11+ required"; exit 1; }
[ -x "$PY" ] || python3 -m venv .venv
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -r requirements.txt
echo "OK"

step "Where the bots keep their data (data/desk.json)"
mkdir -p data
if [ -f data/desk.json ]; then
  echo "OK: data/desk.json exists; edit it to change paths or what is public"
else
  quant=$(ask_path "claude-quant data folder" "$HOME/claude-trade/data")
  sentiment=$(ask_path "claude-sentiment-trading data folder" "$HOME/claude-sentiment-trading/data")
  "$PY" - "$quant" "$sentiment" <<'EOF'
import json, sys
json.dump({"bots": {"quant": {"data_dir": sys.argv[1]}, "sentiment": {"data_dir": sys.argv[2]}}},
          open("data/desk.json", "w"), indent=2)
EOF
  echo "OK: wrote data/desk.json. Which sections are public: .venv/bin/python -m desk.config"
fi
"$PY" -m desk.config
read -r QUANT_DATA SENTIMENT_DATA PORT < <("$PY" -c '
from desk import config
s = config.load()
print(s.bots["quant"]["data_dir"], s.bots["sentiment"]["data_dir"], s.port)')

step "Tests and data check"
"$PY" -m pytest -q
"$PY" -m desk.check || { echo "Fix the problems above (is each bot deployed with its database migrated?)"; exit 1; }

step "Web build for this commit"
sha=$(git rev-parse --short=12 HEAD)
if [ -f "data/web/$sha/index.html" ]; then
  echo "OK: already downloaded"
else
  slug=$(git remote get-url origin | sed -E 's#^.*github\.com[:/]##; s#\.git$##')
  tmp=$(mktemp -d)
  if curl -fsSL -o "$tmp/web.tar.gz" "https://github.com/$slug/releases/download/build-$sha/web.tar.gz"; then
    mkdir -p "data/web/$sha" && tar -xzf "$tmp/web.tar.gz" -C "data/web/$sha"
    ln -sfn "$sha" data/web/current
    echo "OK: data/web/$sha"
  else
    echo "No build released for $sha yet (CI still running, or this commit is not on main)."
    echo "Re-run this script once CI has passed; until then the dashboard serves only its API."
  fi
  rm -rf "$tmp"
fi

step "systemd user service ($SERVICE)"
# Runs as you, but sandboxed: the system and your home are read-only except the bots' data folders
# (SQLite needs to create the -shm file next to a database to read it safely while a bot writes), and
# the bots' secrets are hidden from it. The app itself opens every database read-only.
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/$SERVICE.service" <<EOF
[Unit]
Description=claude-desk: read-only dashboard over the trading bots' data
After=network-online.target

[Service]
WorkingDirectory=$REPO
ExecStart=$PY -m desk.app
Restart=on-failure
RestartSec=5
Nice=10
CPUQuota=50%
MemoryMax=300M
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$QUANT_DATA $SENTIMENT_DATA
InaccessiblePaths=-$(dirname "$QUANT_DATA")/.env -$(dirname "$SENTIMENT_DATA")/.env -$HOME/.config/paper-trader -$HOME/.config/sentiment-trader -$HOME/.ssh

[Install]
WantedBy=default.target
EOF
if ! loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q yes; then
  echo "Letting your services run without a login session (needs sudo once)"
  sudo loginctl enable-linger "$USER"
fi
systemctl --user daemon-reload
systemctl --user enable --now "$SERVICE"
systemctl --user restart "$SERVICE"
sleep 2
if curl -fsS -m 5 "http://127.0.0.1:$PORT/api/meta" >/dev/null; then
  echo "OK: http://127.0.0.1:$PORT (only this machine; see Tailscale below to publish it)"
else
  echo "The service did not answer. Look at: journalctl --user -u $SERVICE -n 50"; exit 1
fi

step "Tailscale (to publish the dashboard)"
if command -v tailscale >/dev/null; then
  echo "OK: $(tailscale version | head -1)"
elif ask "Tailscale is not installed. Install it now (official script from tailscale.com)?"; then
  curl -fsSL https://tailscale.com/install.sh | sh
else
  echo "Skipped. Install later with: curl -fsSL https://tailscale.com/install.sh | sh"
fi
cat <<EOF

To make the dashboard public (only when you want to):
  1. sudo tailscale up                     log this Pi into your tailnet (once)
  2. In https://login.tailscale.com/admin: DNS -> enable HTTPS certificates, and in Access
     controls add the "funnel" node attribute for this machine (the admin page offers a button).
  3. sudo tailscale funnel --bg $PORT       publish http://127.0.0.1:$PORT at https://<this-pi>.<tailnet>.ts.net
To stop publishing: sudo tailscale funnel --https=443 off
Before publishing, check what is public: .venv/bin/python -m desk.config
EOF

step "Deploy cron job"
scripts/cron.sh install
scripts/cron.sh print
echo
echo "Done."
