"""The committed API schema matches the app, so the frontend's generated types do too."""
import json
from pathlib import Path

from desk import openapi

COMMITTED = Path(__file__).resolve().parent.parent / "web" / "openapi.json"


def test_committed_schema_is_current():
    assert json.loads(COMMITTED.read_text()) == openapi.schema(), "run scripts/gen_types.sh"


def test_every_api_route_declares_its_answer():
    paths = openapi.schema()["paths"]
    for path, ops in paths.items():
        if path == "/api/downloads/{name}":  # a file, not JSON
            continue
        schema = ops["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema, path
