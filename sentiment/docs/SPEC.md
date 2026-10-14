# Sentiment Swing Trader: Design Spec

Hand this file to Claude Code in an empty repo as `SPEC.md`. Build in the milestone order at the
bottom, one milestone per commit of about 400-500 lines, and run the tests after each milestone.

## 1. Goal

A paper trading system that reads company news every day, has Claude score each stock's news from
-2 to +2, and trades those scores with fixed rules, holding positions for days. It runs unattended
on a Raspberry Pi next to the existing day-trading bot (the "paper-trader" repo), on its own Alpaca
paper account, so the two books can be compared over the same period.

This is an experiment in whether news sentiment carries tradeable information, run honestly: scores
are judged only on news that arrived after the system started, because Claude's training data
already contains what happened after older news.

Hard constraints:

- Paper trading only. The code refuses to run against a live endpoint (section 10).
- The user is in Ontario, Canada: Alpaca paper-only account, free IEX data, free Alpaca News API.
  Nothing that needs a funded account, paid data or SIP real-time data.
- Its own Alpaca paper account, separate from the paper-trader's. Setup refuses keys that belong
  to the same account as the paper-trader.
- Claude runs as headless Claude Code (`claude -p`) on the user's existing subscription. No
  Anthropic API key, no other paid services.
- Claude never places orders, edits code or config, or writes files. It reads news and prints
  scores; Python validates and stores them; a deterministic trader acts on them.
- Python 3.11+, minimal dependencies: `alpaca-py`, `pandas`, `numpy`, `python-dotenv`, `pytest`,
  `tzdata`.
- Runs on a Raspberry Pi 4/5 (64-bit Raspberry Pi OS, 2 GB+ RAM) under cron.

Not financial advice. Paper fills are optimistic, paper shorting has no real borrow costs, and
results over a short window are not evidence of skill. The README says so.

## 2. Repo layout

Code lives in git. Everything generated lives in a git-ignored `data/` folder on the Pi, so the
user's commits never conflict with the running system.

```
sentiment-trader/
  README.md  requirements.txt  pytest.ini  .env.example
  src/                    # all Python modules, run as python src/<name>.py
    paths.py              # every data path in one place; DATA_DIR overridable for tests
    broker.py  simbroker.py  news.py  universe.py  score.py  trader.py  risk.py
    logger.py  analyze.py  lifetime.py  compare.py  memory.py  review.py  config.py
  prompts/
    scoring.md  review.md # fixed instructions for Claude
  defaults/
    config.json           # trading rules, copied to data/config.json on first setup
    watchlist.json        # ~20 symbols, copied to data/watchlist.json on first setup
    config_bounds.json    # hard limits; the config is validated against it at every start
  scripts/
    setup.sh              # one-time setup, safe to re-run
    backup.sh             # nightly backup of data/
    migrate.sh            # moves an old state/ folder into data/
  docs/SPEC.md
  tests/
data/                     # git-ignored
  config.json  watchlist.json  universe.json  comparison.md  trader.lock
  news/<YYYY-MM-DD>.jsonl
  scores/<YYYY-MM-DD>.json
  bars/
  memory/  memory.md  memory.json  history/
  books/<paper|dry_run|replay>/
    trades.csv  orders.csv  positions.csv  daily_summary.csv  reports/  lifetime.md
  logs/  cron.log  sentiment.log
  backups/                # nightly archives of data/
```

## 3. News archive (`news.py`)

- Source: Alpaca's free News API (Benzinga headlines and summaries), queried for the watchlist
  symbols since the last fetch.
- Stored as one JSON line per article in `data/news/<YYYY-MM-DD>.jsonl`, bucketed by the
  article's publish time in America/New_York: `id, published_at, symbols, headline, summary,
  source, url`. Deduplicated by article id across runs.
- Runs every 30 minutes, 07:00-20:00 on weekdays and twice a day at weekends, so weekend news is
  archived before Monday's scoring.
- CLI: `python news.py fetch` and `python news.py status` (articles per day and symbol).
- Article text is data. Nothing in it is ever treated as an instruction (section 4).

Not in scope: scraping websites, including Amazon reviews (scraping breaks Amazon's terms and
there is no free official source).

## 4. Scoring (`score.py`, `prompts/scoring.md`)

Runs at 06:30 on weekdays and scores the news since the previous scoring run (so Monday covers
the weekend).

Design: headless Claude Code used as a one-shot classifier, not an agent:

```
claude -p "$(cat prompts/scoring.md)" --model claude-haiku-5-5 --tools "" --output-format json < input.json
```

- `--model claude-haiku-5-5`: the cheapest model; headline scoring does not need more.
- `--tools ""`: no tools, so the run is a single turn and Claude can read nothing but its input.
- Input built by Python: per symbol, headline and summary of each article (capped at 15 per
  symbol, newest first), with article ids. No prices, no positions, no P&L.
- `prompts/scoring.md` asks, per symbol: is any of this news material to the business, and in
  which direction? Output a JSON object keyed by symbol with `score` (integer -2..+2),
  `confidence` (0..1), `reason` (one line) and `article_ids` (the ids it relied on). No material
  news is score 0. The prompt says the articles are untrusted data and any instructions inside
  them must be ignored.
- Validation in Python: valid JSON, every watchlist symbol present, values in range, every cited
  id exists in the input. Retry once on failure, then log and write nothing (the trader then does
  nothing that day).
- Output `data/scores/<YYYY-MM-DD>.json`: the scores plus `model`, `prompt_sha256`, `input_articles`,
  `generated_at`. Score files are never edited after they are written.
- Times out after 5 minutes.

Swapping the model later (for example a dedicated classification model such as TypeSafe's Jev)
only changes the function that calls the model: input building and validation stay the same.

## 5. Trading rules (`trader.py`)

Defined by `data/config.json`. Starting values:

```json
{
  "version": 1,
  "entry": {"min_score": 2, "min_confidence_at_score_1": 0.7, "allow_score_1": true,
            "allow_shorts": true, "run_time": "09:45"},
  "exit": {"max_hold_days": 5, "exit_on_score_flip": true,
           "stop_atr_multiple": 2.0, "atr_days": 14},
  "risk": {"position_pct_of_equity": 5, "max_positions": 10, "max_gross_exposure_pct": 60,
           "max_net_exposure_pct": 40, "max_daily_loss_pct": 2.0},
  "data_feed": "iex",
  "timezone": "America/New_York"
}
```

Each weekday:

1. **09:45 run** (`python trader.py run`):
   - Exits first: close positions that reached `max_hold_days` trading days, or whose new score has
     the opposite sign (when `exit_on_score_flip`).
   - Entries: long when score is +2, or +1 with confidence >= 0.7 (when `allow_score_1`); short
     mirrored for -2/-1. Skip symbols already held, hard-to-borrow shorts, and anything that would
     break a book limit. Rank candidates by |score| then confidence; fill up to `max_positions`.
   - Size: `position_pct_of_equity` of equity, rounded down to whole shares, skipped if zero.
   - Entry: market order with a protective stop at `stop_atr_multiple` x 14-day ATR from the
     reference price, sent as one OTO order (entry triggers the stop), stop TIF GTC so it survives
     overnight. If OTO is unavailable on paper, submit the stop as a separate GTC order once the
     entry fills.
   - Exits are market orders after cancelling the position's stop.
   - No valid score file today: no entries; exits by hold days and stops still run.
2. **16:15 run** (`python trader.py close`): reconcile fills, record stop-outs, mark positions
   to market, write the daily summary row, check the daily loss limit for tomorrow.

Holding overnight is the point of this book, so there is no end-of-day flatten. The rules stay
fixed for the first months so results are interpretable; any change bumps `version`, which every
trade row records.

## 6. Risk (`risk.py`)

- Position size, ATR stop distance, and book limits: max positions, gross and net exposure as % of
  equity.
- Daily loss: realized plus unrealized P&L from the account. Breaching `max_daily_loss_pct` closes
  everything and blocks entries until the next trading day.
- Shorts only when `shortable` and `easy_to_borrow`.
- Hard caps in code, not config: 30 orders per day, no position over 10% of equity, no price under $5.
- Earnings: no special handling in v1; trades held across earnings are flagged in `trades.csv`
  (`held_over_earnings`) using Alpaca's corporate actions / earnings data if available, else left blank.

## 7. Execution, restart safety

- `run` and `close` are short jobs, not a long-running loop. Both take a lock file.
- Idempotent: client order ids are `st-<YYYYMMDD>-<SYMBOL>-<entry|exit|stop>`. Re-running a job
  the same day never double-submits (duplicate ids are rejected by Alpaca and treated as already done).
- On start each job reconciles the account's positions and open orders with `<book>/positions.csv`
  (symbol, side, qty, entry date, entry price, stop order id, score at entry). Positions found in
  the account but not in the file are adopted and logged; file rows with no position are closed
  out as `unknown` exits.
- All times timezone-aware in America/New_York; holidays and early closes from the Alpaca calendar.
- Kill switch: a file named `HALT` in the repo root makes every job place no new entries and close
  all positions.

## 8. Logging (`logger.py`)

- `<book>/trades.csv`, one row per closed trade: `entry_date, exit_date, symbol, side, qty,
  entry_price, exit_price, exit_reason (hold|flip|stop|halt|loss_limit|unknown), pnl_usd,
  pnl_pct, hold_days, score, confidence, score_date, config_version, prompt_sha256, held_over_earnings`.
- `<book>/orders.csv`: every submit, fill, cancel and reject with ids and timestamps.
- `<book>/daily_summary.csv`: `date, equity_start, equity_end, positions, gross_exposure,
  net_exposure, entries, exits, scored (bool), halted (bool), notes`.
- `data/logs/sentiment.log`: warnings and errors with tracebacks.

## 9. Analytics and comparison

`analyze.py` (report to `<book>/reports/report_<date>.md`):
- Trades, win rate, average win and loss, expectancy per trade with a 95% range grouped by entry
  date, profit factor, total return, max drawdown, worst day.
- Breakdowns by score bucket (-2, -1, +1, +2), confidence band, side, exit reason, symbol and
  `prompt_sha256`. The key question is whether higher scores really lead to better returns.
- SPY buy-and-hold over the same period; sample-size warning under 30 trades.

`compare.py` (nightly, `data/comparison.md`): reads this repo's state and the paper-trader's
(`PAPER_TRADER_STATE` in `.env`, read-only, optional):
- Overlapping period only. Per book: total return, annualised volatility, Sharpe ratio, max
  drawdown, worst day, trades, days.
- SPY over the same period, correlation of the two books' daily returns, and the daily return
  difference with a 95% range; it says a book is better only when that range excludes zero.

## 10. Safety (non-negotiable)

- `broker.py` builds the Alpaca client with `paper=True` and asserts the base URL contains
  `paper-api`; anything else raises and exits.
- Setup and every job check that the account number differs from the paper-trader's account
  (`PAPER_TRADER_ACCOUNT` in `.env`, filled in by setup).
- Credentials only from `.env` or the environment; never logged or printed.
- Config validated against `defaults/config_bounds.json` at every start; refuse to run if out of bounds.
- The scoring run has no tools and its output is schema-checked; nothing Claude writes is executed.
- Dry-run (`--dry-run`): real data and scores, simulated fills, separate `data/books/dry_run/`.
  For trying changes; the cron jobs always trade the paper account.

## 11. One-time setup (`scripts/setup.sh`)

Run once on the Pi from the repo root; safe to re-run, every step skips what is already done:

1. Checks: Linux, 64-bit, RAM, Python 3.11+ (installs `python3-venv`, `git`, `curl` with apt).
2. Timezone: offers to set `America/Toronto` so cron times are Eastern.
3. `.venv` and `pip install -r requirements.txt`.
4. `.env`: asks for the second paper account's keys (hidden input), checks they reach a paper
   account, asks for the paper-trader's repo path, reads its account number into
   `PAPER_TRADER_ACCOUNT` and refuses identical accounts; optional `NTFY_TOPIC`. Readable only by the user.
5. Claude CLI: installs with Anthropic's native installer if missing; reuses
   `~/.config/paper-trader/env` if it holds a `CLAUDE_CODE_OAUTH_TOKEN`, else runs `claude setup-token`
   and stores the token in `~/.config/sentiment-trader/env` (mode 600). Checks `claude -p` answers.
6. `data/` from the defaults (never overwrites).
7. Runs the tests and `python broker.py` (account, calendar, a sample of news).
8. Installs cron jobs tagged `# sentiment-trader`, replacing only its own lines:
   ```
   */30 7-20 * * 1-5  news fetch
   0 10,18 * * 0,6     news fetch (weekends)
   30 6 * * 1-5        score
   45 9 * * 1-5        trader run      (exits early on holidays)
   15 16 * * 1-5       trader close
   30 18 * * 1-5       analyze + compare
   0 3 * * *           backup
   ```
   Times avoid the paper-trader's 09:00 start and its 17:00 review and universe screen, so the Pi
   is not running both heavy jobs at once.
9. Prints the schedule and log locations.

`scripts/backup.sh` keeps the last 30 nightly `tar.gz` archives of `data/` in `data/backups/` and
copies the newest to `BACKUP_DIR` when set.

## 12. Testing

- Unit tests for news dedupe and date bucketing, input building, score validation (missing symbols,
  out-of-range values, invented article ids, prompt-injection text in articles has no effect on
  the parsed output), entry/exit rules, sizing and stops, book limits, restart reconciliation,
  analytics and comparison on synthetic data with known answers.
- The `claude` call and Alpaca are mocked; the suite needs no network and no credentials.
- A replay of the trader over saved score files and daily bars (`python trader.py replay --from
  --to`) for checking rules after hours. Replays use only score files that exist; it never asks
  Claude to score past news.

## 13. Milestones (one commit each, about 400-500 lines)

1. Scaffolding, `paths.py`, config defaults and bounds validation, `data/` init, tests.
2. `broker.py` (paper-only, separate-account check, calendar, daily bars, news client) and the
   news archive with cron-ready CLI. Start archiving as soon as this lands.
3. Scoring: input building, `prompts/scoring.md`, the `claude -p` call, validation, score files.
4. `risk.py` and the trading rules as pure functions, fully unit-tested.
5. `trader.py` run/close with OTO orders, restart reconciliation, kill switch, dry-run and replay.
6. Logging, `analyze.py`, `compare.py`.
7. `scripts/setup.sh`, `scripts/backup.sh`, README (setup, second paper account, schedule,
   not-financial-advice note).

## 14. Open items for the user

- The starting watchlist: about 20 consumer-facing companies with steady news flow.
- Whether to try a dedicated classification model later and compare it with Claude's scores
  on the same archived news.
