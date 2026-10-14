# Sentiment Swing Trader

A paper trading system that archives company news, has Claude score the news of the most liquid
US stocks from -2 to +2 every morning, and trades those scores with fixed rules, holding positions for days.
It runs under cron on a Raspberry Pi next to the paper-trader, on its own Alpaca paper account, so
the two books can be compared over the same period.

The design is in [docs/SPEC.md](docs/SPEC.md).

**Not financial advice.** This is an experiment. Paper fills are optimistic, paper shorts have no
real borrow costs, and results over a short window are not evidence of skill.

## How it works

1. `universe.py` screens every stock listed on NYSE, NASDAQ and NYSE American each night and
   keeps the 400 with the highest average dollar volume over 20 days, priced at $5 or more, in
   `data/universe.json`. The watchlist is always included.
2. `news.py` archives Alpaca News API articles for the universe into `data/news/<date>.jsonl`.
3. `score.py` picks the universe stocks with news since the previous scoring run (open positions
   first, then the most covered, at most 60) and sends their news to headless Claude Code
   (`claude -p`, Haiku, no tools) in batches of 20, with the fixed instructions in
   `prompts/scoring.md`. Python checks each reply against a strict schema and writes
   `data/scores/<date>.json`. Score files are never edited. A batch whose reply is invalid twice
   is left out and listed in `failed_symbols`; if every batch fails, nothing is written and the
   trader makes no entries that day.
4. `trader.py run` (09:45) exits positions held `max_hold_days` trading days or whose score flipped
   sign, then enters longs on +2 (or +1 with confidence >= 0.7) and shorts on -2/-1, sized at 5% of
   equity, each with a GTC stop at 2 x the 14-day ATR.
5. `trader.py close` (16:15) records fills and stop-outs, marks positions to market, checks the
   daily loss limit and writes the daily summary.
6. `analyze.py`, `lifetime.py` and `compare.py` (18:30) write the day report, the performance
   report, the lifetime statistics and the comparison with the paper-trader.
7. `review.py` (Fridays 19:45) gives Claude the week's evidence and has it update its lessons
   memory, which the next scoring runs read.

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
the paper-trader repo, stores a Claude token (or reuses the paper-trader's),
creates `data/`, runs the tests and an Alpaca check, and installs the cron jobs. Re-running it is safe.

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
| 18:30 weekdays | `analyze.py`, `lifetime.py`, `compare.py` |
| 19:15 weekdays | `universe.py build` |
| 19:45 Fridays | `review.py run` |
| 03:00 daily | `scripts/backup.sh` |

The times avoid the paper-trader's 09:00 start and 17:00 review.

## Running

The cron jobs trade the paper account; the book is `data/books/paper/`.

`trader.py run` and `close` do nothing on market holidays and weekends; `--force` runs them anyway.
`close` logs the path of the daily summary it wrote.

`--dry-run` on any `trader.py` command uses simulated fills and a separate book in
`data/books/dry_run/` instead, for trying changes without placing orders.

## Commands

```bash
.venv/bin/python src/news.py status
```

```bash
.venv/bin/python src/score.py show 2026-10-13
```

```bash
.venv/bin/python src/trader.py replay --from 2026-10-01 --to 2026-10-31
```

```bash
.venv/bin/python src/trader.py positions
```

```bash
.venv/bin/python src/analyze.py --day 2026-10-13
```

- `universe.py build | show`: rebuild the universe, or print it. Its limits are the `universe`
  section of `data/config.json`.
- `news.py fetch | status`: archive news, or show articles per day and the most covered symbols.
- `score.py run | show [DATE]`: score today's news, or print a score file.
- `trader.py run | close [--dry-run] [--force]`: `--force` ignores the calendar and run time.
- `trader.py positions [--dry-run]`: current holdings at live prices, with today's entries and
  closed trades. Read-only, safe at any time.
- `trader.py replay --from DATE --to DATE`: reruns the rules over saved score files and daily bars
  into `data/books/replay/`, with simulated fills at the open and stops checked against daily bars.
- `analyze.py [--book ...] [--since DATE] [--config-version N] [--no-baseline]`: writes the
  performance report and today's day report into `<book>/reports/`. `--day DATE` writes only that
  day's report.
- `lifetime.py [--book ...] [--no-baseline]`: rewrites `<book>/lifetime.md` and `lifetime.json`.
- `review.py run | show`: run the self-review now, or print the memory.
- `compare.py [--book paper|dry_run]`: comparison to `data/comparison.md`, over overlapping days
  only. It calls a book better only when the 95% range of the daily return difference excludes zero.
- `config.py`: create the data folder from the defaults and validate the config.
- `broker.py`: check the account, calendar, bars and news.

## Stats

The stats use the same files, columns, sections and math as the paper-trader (`claude-trade`), so
both books read the same way and can be aggregated by one tool. `<book>` is `data/books/paper/`, or
`data/books/dry_run/` with `--book dry_run`.

| What | Where | Same as the paper-trader |
|---|---|---|
| Closed trades | `<book>/trades.csv` | First 16 columns identical; sentiment columns follow |
| Each day | `<book>/daily_summary.csv` | First 9 columns identical; P&L split, exposure and scoring follow |
| Day report | `<book>/reports/day_<date>.md` | Same layout as `review/day_report.py`, plus holdings, entries and scores |
| Performance report | `<book>/reports/report_<date>.md` | Same sections as `analyze.py`, plus score, confidence, hold days and memory version |
| Lifetime | `<book>/lifetime.md` | Same sections as `lifetime.py`, plus score, confidence, memory version and calibration |
| Current holdings | `trader.py positions`, `<book>/positions.csv` | Sentiment-trader only; the paper-trader is flat each night |

Shared definitions:

- `date` on a trade is its exit date. `pnl_r` is the P&L per share over the planned risk per
  share (entry to stop), so a stop-out is about -1R.
- Exit reasons: `sl` stop, `time` held `max_hold_days`, `flip` score changed sign, `halt`,
  `loss_limit`, `unknown`.
- Expectancy comes with a 95% range that treats each exit day as one cluster.
- `gross_pnl` in the daily summary is the P&L of trades closed that day; `day_pnl` is the change in
  equity, including open positions.

`lifetime.md` also measures **score calibration**: for every score ever given, traded or not, the
stock's return from that day's open to the open `max_hold_days` trading days later. With up to 60
scores a day, this answers "do the scores carry information?" much sooner than the trades alone can.

## Memory and the weekly review

Every Friday `review.py` sends Claude (Sonnet, no tools) the lifetime stats, the calibration of
scores whose outcome is new since the last review, and the most instructive examples: the biggest
moves with a score, against a score, and on a score of 0, each with its reason and headlines.
Claude replies with JSON only: up to 12 lessons, each with its evidence and a confidence, plus
"working" and "not working" lists. Python validates the reply and writes `data/memory/memory.md`;
an invalid reply leaves the old memory in place. The review is skipped until at least
`memory.min_new_outcomes` new outcomes exist.

Scoring appends the lessons to `prompts/scoring.md` as advisory notes. Each score file and trade
records `memory_sha256`, so the report and lifetime stats show results per memory version. Set
`memory.use_in_scoring` to `false` in `data/config.json` to score without the lessons.

The lessons only cover how to read news. Claude still cannot change the trading rules, the config
or any file.

## Stopping

Create a file named `HALT` in the repo root. Every trader job then places no entries and closes
all positions. Delete it to resume.

## Safety

- The Alpaca client is built with `paper=True` and refuses any endpoint without `paper-api`.
- Every trader job checks the account differs from `PAPER_TRADER_ACCOUNT`.
- `data/config.json` is validated against `defaults/config_bounds.json` at every start.
- Hard caps in code: 30 orders a day, no position over 10% of equity, no stock under $5, shorts
  only when easy to borrow.
- Losing `max_daily_loss_pct` in a day closes everything and blocks entries until the next session.
- Client order ids are `st-<YYYYMMDD>-<SYMBOL>-<entry|exit|stop>`, so re-running a job never
  double-submits. Each job reconciles `<book>/positions.csv` with the account on start.

## Layout

| Path | What |
|---|---|
| `src/` | All Python code; every command runs as `.venv/bin/python src/<name>.py` |
| `prompts/` | Fixed instructions for scoring and the weekly review |
| `defaults/` | Config and watchlist copied into `data/` once, and the config's hard limits |
| `scripts/` | `setup.sh`, nightly `backup.sh`, and `migrate.sh` for an old `state/` folder |
| `tests/` | Test suite, see [tests/README.md](tests/README.md) |
| `docs/SPEC.md` | The design |
| `data/` | Everything the system writes; not in git |

Inside `data/`:

| Path | What |
|---|---|
| `config.json`, `watchlist.json` | Active config and watchlist; watchlist symbols are always in the universe |
| `universe.json` | Tonight's screened universe |
| `news/`, `scores/`, `bars/` | News archive, score files, cached daily bars |
| `books/paper/`, `books/dry_run/`, `books/replay/` | One folder per book, see below |
| `memory/` | Claude's current lessons (`memory.md`, `memory.json`) and every version in `history/` |
| `logs/` | `cron.log` (all cron output) and `sentiment.log` (warnings and errors with tracebacks) |
| `comparison.md` | Comparison with the paper-trader |
| `backups/` | The last 30 nightly archives of `data/` |

Inside each book:

| Path | What |
|---|---|
| `trades.csv` | One row per closed trade, see Stats |
| `orders.csv` | Every submit, fill, cancel, reject and skipped entry |
| `positions.csv` | Open positions and their stops, marked at the close |
| `daily_summary.csv` | One row per trading day, written by `trader.py close` |
| `reports/` | Day reports and performance reports |
| `lifetime.md`, `lifetime.json` | All-time statistics, rewritten nightly |

To move an old `state/` folder into this layout, run `scripts/migrate.sh`, then `scripts/setup.sh`
to reinstall the cron jobs. `setup.sh` runs the migration itself when it finds `state/`.

`held_over_earnings` in `trades.csv` stays blank: Alpaca's free data has no earnings calendar.

Rule changes bump `version` in the config, and every trade records it. Keep the rules fixed for
the first months so results stay interpretable.
