# claude-desk

A read-only dashboard for two paper-trading bots,
[claude-quant](https://github.com/KrystianRusin/claude-quant) and
[claude-sentiment-trading](https://github.com/KrystianRusin/claude-sentiment-trading). It runs on
the same Raspberry Pi, reads each bot's SQLite database, and can be published with Tailscale Funnel.
The owner chooses which parts are public.

Live: https://krystianrusin.github.io/desk/

- The API is FastAPI with GET routes only. Every database is opened read-only, and queries are stopped
  after 2 seconds. There is no generic query endpoint: each view picks its fields in code, and every
  route has a pydantic response model.
- The frontend uses React, Vite, TanStack Query and TradingView lightweight-charts. The TypeScript types are
  generated from the API's OpenAPI schema. Pages poll every 30 seconds.
- Answers are cached in memory for 30 seconds and each visitor is rate-limited, so there is no
  message bus or cache server.

## Pages

| Page | Shows |
|---|---|
| Overview | Equity, today's P&L and drawdown for each bot, return curves, hot symbols, best and worst symbols, what each bot holds |
| Holdings | Open positions with unrealized P&L, today's move, days held and distance to the stop |
| Compare | Return, volatility, Sharpe and drawdown side by side, the daily-return difference with its 95% range, and days the bots disagreed |
| Trades | Every closed trade with filters and an R histogram. Each trade opens a price chart with its entry, exit, stop and target |
| Symbols | One symbol across both bots: trades, news scores, signals, price chart and headlines |
| Replay | One session played back minute by minute: equity, fills and positions |
| What changed | Every config, strategy, prompt and memory change marked on the return curve, with results before and after and the diff |
| Research | Sentiment score calibration, news volume, how the desk's ideas turned out, and attention: the daily discovery list and returns by how many attention sources were elevated |
| Notebook | The bots' journals and how each memory entry moved from hypothesis to confirmed or retired |
| Agent activity | Each night's review: what changed and whether the guard accepted it |
| Operations | Job runs and deploys |
| Downloads | Nightly CSV and Parquet exports of trades, daily results and versions |

Filters, the selected bot, day and time are kept in the URL, so any view can be shared as a link.

## What is public

Each part of the API belongs to a section that can be switched on or off. `defaults/desk.json` holds
the defaults, and the gitignored `data/desk.json` overrides them. A section that is off answers 404
and its page disappears. Unknown setting names are refused, so a typo can't publish something by
accident. `.venv/bin/python -m desk.config` prints what is public.

On by default: results, trades, holdings, symbols, scores, comparisons, versions, replay, research,
operations and downloads. Off by default: journals, memory, agent activity, version contents, job
error text, headlines and raw price bars. Headlines and price bars stay off until the data
provider's terms allow showing them.

Some things are never served: order ids, the bots' reasoning text on trades and scores, article
summaries, file paths, anything from `.env`, and the simulated books. The tests plant secrets in the
fixture data and check that none of them appear in any response.

## Running on the Pi

```bash
git clone https://github.com/KrystianRusin/claude-desk ~/claude-desk
~/claude-desk/scripts/setup.sh
```

`setup.sh` is safe to re-run. It creates the virtual environment and asks where each bot's data
folder is. It then runs the tests, downloads the web build for the current commit and installs a
systemd user service on `127.0.0.1:8787`. Finally it offers to install Tailscale and adds two cron
jobs: the deploy check and the nightly export.

The service runs as the bots' user, because SQLite needs to create a `-shm` file next to a database
to read it while a bot writes. systemd sandboxes it: the system and home folder are read-only
except the two bot data folders, the bots' `.env` files and `~/.ssh` are hidden, and CPU and memory
are capped so the traders come first.

To publish, run `sudo tailscale up` once, turn on HTTPS and the Funnel attribute in the Tailscale
admin console, then `sudo tailscale funnel --bg 8787`. The Pi accepts no inbound connections; Funnel
proxies to the local port.

## Deploys

Every push runs the API tests, a check that the generated types are current, and the web build on
GitHub Actions. On `main`, CI publishes the build as a release asset so the Pi never has to build
the frontend. `scripts/deploy.sh` runs on the Pi every 5 minutes. When a new commit has passed CI,
it downloads the build, fast-forwards, runs the checks, restarts the service and checks that it
answers. If anything fails it rolls back to the previous commit and build.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest
.venv/bin/python scripts/demo_data.py data/demo                  # made-up data for both bots
DESK_CONFIG=data/demo/desk.json .venv/bin/python -m desk.export  # optional: the download files
DESK_CONFIG=data/demo/desk.json .venv/bin/python -m desk.app     # http://127.0.0.1:8787
cd web && npm install && npm run dev                             # http://localhost:5173, proxies the API
```

- After changing a response model or route, run `scripts/gen_types.sh`. It rewrites
  `web/openapi.json` and `web/src/schema.d.ts`, and a test and CI fail until both are current.
  FastAPI and pydantic are pinned so the schema is the same everywhere.
- `tests/schema/` is a copy of the bots' shared database migrations, pinned by hash. When a bot adds
  a shared migration, copy it in and update `CORE_SHA256` in `tests/test_views.py`.
- A new view needs a function in `desk/views.py` (or `research.py`), a section in `desk/config.py`
  and `defaults/desk.json`, a route and model in `desk/app.py` and `desk/models.py`, and a test in
  `tests/test_exposure.py` that it is hidden when its section is off.
