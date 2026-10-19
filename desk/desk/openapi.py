"""Print the API's OpenAPI schema, which scripts/gen_types.sh saves as web/openapi.json and turns into the
frontend's types (web/src/schema.d.ts). The schema does not depend on the settings, so the defaults are used.

    .venv/bin/python -m desk.openapi
"""
import json

from . import app, config


def schema():
    return app.create_app(config.load(config.DEFAULTS)).openapi()


if __name__ == "__main__":
    print(json.dumps(schema(), indent=1, sort_keys=True))
