# claude-desk

A read-only dashboard over two paper-trading bots, [claude-quant](https://github.com/KrystianRusin/claude-quant)
and [claude-sentiment-trading](https://github.com/KrystianRusin/claude-sentiment-trading). It runs on the
same Raspberry Pi and reads each bot's `data/trader.db`. It can be published on the internet with
Tailscale Funnel, showing only the sections the owner switches on.

- **API:** FastAPI (`desk/`). It has only GET routes, opens every database read-only, and stops any
  query after 2 seconds. There is no general query endpoint: each view picks its fields in code.
- **Web:** React + Vite + TanStack Query (`web/`), with TradingView lightweight-charts for time
  series. It refreshes every 30 seconds by polling; there are no long-lived connections.
- **Not used:** no message bus, no cache server, and nothing that writes. The app caches answers in
  memory for 30 s and rate-limits each visitor.

## Pages

| Page | Section(s) | Shows |
|---|---|---|
| Overview | `equity`, `symbols`, `scores`, `positions`, `prices` | Each bot's equity, today's P&L, return and drawdown; return curves since the start and for today; hot right now; best and worst symbols; where the bots disagree; open positions; the SPY line |
| Compare | `compare` | Return, volatility, Sharpe, drawdown and best and worst day per bot; the daily-return difference with its 95% range and a verdict |
| Trades | `trades` | Every closed trade, with filters kept in the URL, an R histogram and win rate and expectancy |
| What changed | `versions`, `version_content` | Every config, strategy, watchlist, prompt and memory change; results per strategy and config version |
| Operations | `ops`, `ops_errors` | Job runs and deploys: status, timing, failures; the error text only with `ops_errors` |

The symbol page, research pages and visitor features come later (tickets 007 and 008).

## What is public

`defaults/desk.json` holds the defaults. Your `data/desk.json` (gitignored) overrides them, and
`.venv/bin/python -m desk.config` prints what is public. A section that is off answers 404 and its
page or panel disappears. An unknown setting or section name is an error, so a typo can't quietly
publish something.

| Section | Default | Why |
|---|---|---|
| `equity`, `trades`, `symbols`, `scores`, `compare`, `versions`, `ops` | on | The bots' results and how they got there |
| `positions` | off | Shows what each bot holds right now |
| `version_content` | off | The text of configs, strategies, prompts and memory |
| `ops_errors` | off | The end of a failed job's output, which can hold paths or account details |
| `prices` | off | Raw price bars (the SPY line): check Alpaca's and the data provider's terms before showing them publicly |

Some things are never served, whatever the settings say:
- order and client order ids
- the bots' reasons and model text
- prompt and memory hashes on trades
- file paths
- anything from `.env`
- the replay and dry-run books, unless you add them to `books`

Tests check this against data with planted secrets.

```json
{
  "bots": {
    "quant": {"data_dir": "/home/krystian/claude-trade/data"},
    "sentiment": {"data_dir": "/home/krystian/claude-sentiment-trading/data"}
  },
  "sections": {"positions": true}
}
```

## On the Pi

```bash
git clone https://github.com/KrystianRusin/claude-desk ~/claude-desk
~/claude-desk/scripts/setup.sh
```

`setup.sh` does the following (it is safe to re-run):
- makes the venv;
- asks where each bot's data folder is;
- runs the tests and `python -m desk.check`;
- downloads the web build of the current commit;
- installs the `claude-desk` systemd user service on `127.0.0.1:8787` (it needs `sudo` once, to let
  the service run without a login session);
- checks for Tailscale and offers to install it;
- installs the 5-minute deploy cron job.

**The service is sandboxed.** It runs as your user, because SQLite needs to create a `-shm` file
next to a database to read it safely while a bot writes. Inside the sandbox:
- the system and your home are read-only, except the two bot data folders;
- the bots' `.env` files, `~/.config/paper-trader`, `~/.config/sentiment-trader` and `~/.ssh` are
  hidden;
- CPU and memory are capped (`CPUQuota=50%`, `MemoryMax=300M`, `Nice=10`), so the traders come
  first.

### Publishing it

Nothing is public until you run Funnel yourself:

1. `sudo tailscale up` logs the Pi into your tailnet (once).
2. In the Tailscale admin console, turn on HTTPS certificates and give this machine the `funnel`
   attribute.
3. `sudo tailscale funnel --bg 8787` publishes the dashboard at `https://<pi>.<tailnet>.ts.net`.

To stop publishing, run `sudo tailscale funnel --https=443 off`. Funnel only proxies to the
local port. The Pi accepts no inbound connections, and the dashboard is never on your LAN.

### Deploys

Every push to `main` runs CI (`.github/workflows/ci.yml`): the API tests, a typecheck and the web
build. On `main`, CI then releases the build as `build-<sha>/web.tar.gz` and keeps the newest 5.

On the Pi, `scripts/deploy.sh` runs every 5 minutes. When a new commit has passed CI, it:
1. downloads that commit's build and fast-forwards the checkout;
2. runs the checks and points `data/web/current` at the new build;
3. restarts the service and checks it answers.

If a check or the health check fails, the deploy puts back the previous commit and build, then
waits for the next commit.

Deploy logs are in `data/deploy.log`. The service logs with `journalctl --user -u claude-desk`.
`NTFY_TOPIC` in `.env` sends deploy notifications.

## Working on it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest
.venv/bin/python scripts/demo_data.py data/demo        # made-up data for both bots
DESK_CONFIG=data/demo/desk.json .venv/bin/python -m desk.app
cd web && npm install && npm run dev                   # http://localhost:5173, proxying the API
```

- **Schema copy:** `tests/schema/` is a copy of the bots' shared core migrations. A test pins their
  hashes, the same ones the bots pin. When a bot adds a core migration, copy it in and update
  `CORE_SHA256` in `tests/test_views.py`.
- **API changes:** a new view gets its own function in `desk/views.py`, a section in
  `desk/config.py` (and `defaults/desk.json`), a route in `desk/app.py`, and tests in
  `tests/test_exposure.py` showing it is hidden when its section is off.
