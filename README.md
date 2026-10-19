# claude-trading

Two paper-trading bots built with Claude, and the dashboard that shows how they're doing. All three
run on one Raspberry Pi.

Live dashboard: https://krystianrusin.github.io/desk/

| Project | What it is |
|---|---|
| [`daytrader/`](daytrader) | An intraday rules-based trader on an Alpaca paper account. Claude reviews each session after the close and tunes the strategy, and every change has to pass checks in code. |
| [`sentiment/`](sentiment) | A news-driven swing trader on a second paper account. Claude scores the morning's news; fixed rules size, stop and exit every trade. |
| [`desk/`](desk) | A read-only dashboard over both bots' databases, published with Tailscale Funnel. |

Each project has its own README, `requirements.txt`, virtual environment, `.env` and `data/` folder.
Everything the bots write at runtime stays in their gitignored `data/` folders.

## Tests

Each project's tests run from its own folder:

```bash
cd daytrader && .venv/bin/python -m pytest
```

## Deployment

The Pi pulls new commits; nothing pushes to it.

- **CI** (`.github/workflows/ci.yml`) tests only the projects a push changed. A change to `shared/`,
  `scripts/` or the workflow tests everything. When `desk/` changes, it also builds the dashboard's
  frontend and publishes it as a release asset named after the `desk/` tree.
- **Deploy** (`scripts/deploy.sh`, every 5 minutes) waits for CI to pass on a new commit on `main`
  and works out which projects it changed. Only those are touched:
  - it takes those projects' locks, so it never changes code under a running job of theirs;
  - it saves their configs and databases, fast-forwards, and installs requirements that changed;
  - it runs their checks and migrations, reinstalls cron, and switches and restarts the dashboard.

  If anything fails, the checkout goes back to the previous commit and every changed project gets
  its state back.
- **Cron** (`scripts/cron.sh`) installs the deploy check and each set-up project's own schedule
  (`<project>/scripts/cron.sh`).

Deploy settings (`NTFY_TOPIC`, `DEPLOY_GITHUB_TOKEN`, `DEPLOY_CI_CHECK`) are read from the
environment, `.env` at the root, or a bot's `.env`. The deploy log is `data/deploy.log`.

## Setup on the Pi

```bash
git clone https://github.com/KrystianRusin/claude-trading ~/claude-trading
~/claude-trading/daytrader/scripts/setup.sh
~/claude-trading/sentiment/scripts/setup.sh
~/claude-trading/desk/scripts/setup.sh
```

Each script is safe to re-run.

## History

The three projects were separate repos until October 2026 (claude-quant, claude-sentiment-trading
and claude-desk). Their full history was merged in. The day trader was renamed from claude-quant at
the same time.
