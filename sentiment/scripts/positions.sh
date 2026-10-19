#!/usr/bin/env bash
# Current holdings at live prices with P&L since entry. Extra arguments go to `trader.py positions`.
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python src/trader.py positions "$@"
