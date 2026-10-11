# Sentiment Swing Trader

A paper trading system that archives company news, has Claude score the news of the most liquid
US stocks from -2 to +2 every morning, and trades those scores with fixed rules, holding positions for days.
It runs under cron on a Raspberry Pi next to the paper-trader, on its own Alpaca paper account, so
the two books can be compared over the same period.

The design is in [SPEC.md](SPEC.md).

**Not financial advice.** This is an experiment. Paper fills are optimistic, paper shorts have no
real borrow costs, and results over a short window are not evidence of skill.

## How it works

1. `universe.py` screens every stock listed on NYSE, NASDAQ and NYSE American each night and
   keeps the 400 with the highest average dollar volume over 20 days, priced at $5 or more, in
   `state/universe.json`. The watchlist is always included.
2. `news.py` archives Alpaca News API articles for the universe into `state/news/<date>.jsonl`.
3. `score.py` picks the universe stocks with news since the previous scoring run (open positions
   first, then the most covered, at most 60) and sends their news to headless Claude Code
   (`claude -p`, Haiku, no tools) in batches of 20, with the fixed instructions in
   `SCORING_PROMPT.md`. Python checks each reply against a strict schema and writes
   `state/scores/<date>.json`. Score files are never edited. A batch whose reply is invalid twice
   is left out and listed in `failed_symbols`; if every batch fails, nothing is written and the
   trader makes no entries that day.
4. `trader.py run` (09:45) exits positions held `max_hold_days` trading days or whose score flipped
   sign, then enters longs on +2 (or +1 with confidence >= 0.7) and shorts on -2/-1, sized at 5% of
   equity, each with a GTC stop at 2 x the 14-day ATR.
5. `trader.py close` (16:15) records fills and stop-outs, marks positions to market, checks the
   daily loss limit and writes the daily summary.
6. `analyze.py` and `compare.py` (18:30) write the performance report and the comparison with the
   paper-trader.

Claude never places orders, edits code or config, or writes files. It only prints scores.

Scores are judged only on news that arrived after the system started: Claude's training data
already contains what happened after older news, so replays never ask it to score past news.

## Setup

You need a **second Alpaca paper account**, separate from the paper-trader's. In the Alpaca
dashboard, open the account menu, create a new paper account, and generate API keys for it. Setup
refuses keys that reach the same account as the paper-trader.

On the Pi, from the repo root:

```bash
scripts/setup.sh
```

It installs packages and the virtual environment, asks for the new account's keys and the path to
the paper-trader repo, stores a Claude token (or reuses the paper-trader's), creates `state/`,
runs the tests and an Alpaca check, and installs the cron jobs. Re-running it is safe.

Credentials live only in `.env` (mode 600) and `~/.config/sentiment-trader/env`; they are never
logged or printed.

## Schedule (Eastern time)

| Time | Job |
|---|---|
| Every 30 min, 07:00-20:00 weekdays | `news.py fetch` |
| 10:00 and 18:00 weekends | `news.py fetch` |
| 06:30 weekdays | `score.py run` (fetches news first; skips market holidays) |
| 09:45 weekdays | `trader.py run` |
| 16:15 weekdays | `trader.py close` |
| 18:30 weekdays | `analyze.py`, `compare.py` |
| 19:15 weekdays | `universe.py build` |
| 03:00 daily | `scripts/backup.sh` |

The times avoid the paper-trader's 09:00 start and 17:00 review.

## Dry-run, then paper orders

The trader starts with `"mode": "dry_run"` in `state/config.json`: real data and scores, simulated
fills, books in `state/dry_run/`. After about 30 scored sessions, switch to paper orders by setting
`"mode": "paper"` in `state/config.json`. Paper books are written to `state/` directly.

`--dry-run` on `trader.py run` or `close` forces dry-run for one job.

## Commands

```bash
.venv/bin/python news.py status
```

```bash
.venv/bin/python score.py show 2026-10-13
```

```bash
.venv/bin/python trader.py replay --from 2026-10-01 --to 2026-10-31
```

```bash
.venv/bin/python analyze.py --book dry_run
```

- `universe.py build | show`: rebuild the universe, or print it. Its limits are the `universe`
  section of `state/config.json`.
- `news.py fetch | status`: archive news, or show articles per day and the most covered symbols.
- `score.py run | show [DATE]`: score today's news, or print a score file.
- `trader.py run | close [--dry-run] [--force]`: `--force` ignores the calendar and run time.
- `trader.py replay --from DATE --to DATE`: reruns the rules over saved score files and daily bars
  into `state/replay/`, with simulated fills at the open and stops checked against daily bars.
- `analyze.py [--book paper|dry_run|replay]`: report to `state/reports/report_<date>.md`.
- `compare.py [--book paper|dry_run]`: comparison to `state/comparison.md`, over overlapping days
  only. It calls a book better only when the 95% range of the daily return difference excludes zero.
- `config.py`: create missing state files and validate the config.
- `broker.py`: check the account, calendar, bars and news.

## Stopping

Create a file named `HALT` in the repo root. Every trader job then places no entries and closes
all positions. Delete it to resume.

## Safety

- The Alpaca client is built with `paper=True` and refuses any endpoint without `paper-api`.
- Every trader job checks the account differs from `PAPER_TRADER_ACCOUNT`.
- `state/config.json` is validated against `config_bounds.json` at every start.
- Hard caps in code: 30 orders a day, no position over 10% of equity, no stock under $5, shorts
  only when easy to borrow.
- Losing `max_daily_loss_pct` in a day closes everything and blocks entries until the next session.
- Client order ids are `st-<YYYYMMDD>-<SYMBOL>-<entry|exit|stop>`, so re-running a job never
  double-submits. Each job reconciles `state/positions.csv` with the account on start.

## Files

| Path | What |
|---|---|
| `config.default.json`, `watchlist.default.json` | Defaults copied into `state/` once; watchlist symbols are always in the universe |
| `state/universe.json` | Tonight's screened universe |
| `config_bounds.json` | Hard limits for the config |
| `state/news/`, `state/scores/` | News archive and score files |
| `state/trades.csv` | One row per closed trade, with score, config version and prompt hash |
| `state/orders.csv` | Every submit, fill, cancel, reject and skipped entry |
| `state/positions.csv` | Open positions and their stops |
| `state/daily_summary.csv` | One row per trading day |
| `state/sentiment.log` | Warnings and errors with tracebacks |
| `state/cron.log` | All cron job output |
| `state/dry_run/`, `state/replay/` | Simulated books |
| `backups/` | The last 30 nightly archives of `state/` |

`held_over_earnings` in `trades.csv` stays blank: Alpaca's free data has no earnings calendar.

Rule changes bump `version` in the config, and every trade records it. Keep the rules fixed for
the first months so results stay interpretable.
