#!/usr/bin/env bash
# Regenerate the API schema (web/openapi.json) and the frontend's types from it (web/src/schema.d.ts). Run it
# after changing desk/models.py or a route; tests/test_openapi.py and CI fail until both are up to date.
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m desk.openapi > web/openapi.json
(cd web && npm run --silent gen:types)
echo "wrote web/openapi.json and web/src/schema.d.ts"
