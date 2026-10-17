# Sentiment Swing Trader

A paper trading system that trades like a retail swing trader. Every morning Claude scores the
news of the most liquid US stocks, then a "trading desk" step has Claude read the whole day (news,
price moves, stocks that usually move together but have drifted apart) and propose a few trade
ideas. Fixed rules size, stop and exit every trade, holding positions for days. It runs under cron
on a Raspberry Pi next to the paper-trader, on its own Alpaca paper account, so the two books can
be compared over the same period.

The design is in [docs/SPEC.md](docs/SPEC.md).

**Not financial advice.** This is an experiment. Paper fills are optimistic, paper shorts have no
real borrow costs, and results over a short window are not evidence of skill.

## Where to look

| File | What |
|---|---|
| `data/daily/<date>.md` | Everything about one day: equity and P&L, closed trades, holdings at the close, entries, the desk's ideas with their reasoning, news scores, skipped entries, warnings |
| `data/lifetime.md` | Everything since the start: totals, by strategy, month, ticker, score and exit reason, current holdings, score calibration, and the comparison with the paper-trader |

Both are rewritten at 18:30 each weekday. For live holdings with each position's P&L since entry,
at any time:

```bash
scripts/positions.sh
```

## How it works

1. `universe.py` screens every stock listed on NYSE, NASDAQ and NYSE American each night and
   keeps the 400 with the highest average dollar volume over 20 days, priced at $5 or more, in
   `data/universe.json`. The watchlist is always included. It then runs `pairs.py build`.
2. `pairs.py` looks through the universe for pairs of stocks that move together: daily returns
   correlated between 0.7 and 0.97 over the last year, and a price gap that reverts with a
   half-life of at most 20 days. Each stock is in at most one pair. Written to `data/pairs.json`.
3. `news.py` archives Alpaca News API articles for the universe into `data/news/<date>.jsonl`.
4. `score.py` (06:30) picks the universe stocks with news since the previous run (open positions
   first, then the most covered, at most 60) and has headless Claude Code (`claude -p`, Haiku, no
   tools) score each from -2 to +2 with `prompts/scoring.md`. Python checks each reply against a
   strict schema and writes `data/scores/<date>.json`.
5. In the same run, `desk.py` sends Claude (Sonnet, no tools) the non-zero scores, the newest 150
   headlines, every stock's 1, 5 and 20-day returns, the pairs whose gap is 2 or more standard
   deviations from normal, and the current holdings, with `prompts/desk.md`. Claude replies with up
   to 8 ideas, each of one kind:
   - `theme`: a second-order effect (data centres need power, so power producers may rise).
   - `pair`: short the stock that ran ahead of its partner and buy the one that fell behind.
   - `momentum`, `reversal`, `sector`, `news`: a move likely to continue, a move with no news
     likely to retrace, an industry moving together, or news the scores underrated.

   Every idea cites its evidence (headline ids, scores, price moves or pairs). Python drops any idea
   that cites something not in the input, trades a stock outside the universe or already held, or
   gets a pair's sides wrong. The ideas are saved in the score file.
6. `trader.py run` (09:45) exits positions, then enters candidates from both the scores and the
   desk, one shared pool ranked by conviction (|score|) then confidence. Each needs |score| 2, or 1
   with confidence of at least 0.7. Each position is 5% of equity with a GTC stop at 2 x the
   14-day ATR (3 x for pair legs). A pair enters both legs or neither. There is no cap on the
   number of positions; entries stop when gross exposure would pass 80% or net 40% of equity.
7. Exits: news and desk positions after `max_hold_days` (5) or when their score flips sign. Pairs
   when the gap is back within 0.5 standard deviations (`converged`), widens past 4 (`diverged`),
   after 10 days (`time`), or when one leg is stopped out (`pair_leg`).
8. `trader.py close` (16:15) records fills and stop-outs, marks positions to market, checks the
   daily loss limit and writes the daily summary.
9. `analyze.py` and `lifetime.py` (18:30) write the daily file and the lifetime file.
10. `review.py` (weekdays 19:45) has Claude write the day's journal and update its memory of
    hypotheses. Entries that pass the evidence test reach the next scoring runs.

Claude never places orders, edits code or config, or writes files. It only prints scores and ideas.

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
the paper-trader repo, stores a Claude token (or reuses the paper-trader's), creates `data/`, runs
the tests and an Alpaca check, and installs the cron jobs. Re-running it is safe.

Credentials live only in `.env` (mode 600) and `~/.config/sentiment-trader/env`; they are never
logged or printed.

## Schedule (Eastern time)

| Time | Job |
|---|---|
| Every 30 min, 07:00-20:00 weekdays | `news.py fetch` |
| 10:00 and 18:00 weekends | `news.py fetch` |
| 06:30 weekdays | `score.py run`: news scores, then the desk's ideas (skips market holidays) |
| 09:45 weekdays | `trader.py run` |
| 16:15 weekdays | `trader.py close` |
| 18:30 weekdays | `analyze.py`, `lifetime.py`, `compare.py` |
| 19:15 weekdays | `universe.py build`, then the pairs |
| 19:45 weekdays | `review.py run`: journal and memory |
| 03:00 daily | `scripts/backup.sh` |
| Every 5 min | `scripts/deploy.sh`: ships new commits on `origin/main` once CI passes |

The schedule lives in `scripts/cron.sh`; every deploy reinstalls it.

The times avoid the paper-trader's 09:00 start and 17:00 review.

The cron jobs trade the paper account. `trader.py run` and `close` do nothing on market holidays
and weekends; `--force` runs them anyway. `--dry-run` on any `trader.py` command uses simulated
fills and a separate book in `data/books/dry_run/` instead, for trying changes without placing
orders.

## Continuous deployment

Every push and pull request runs the tests on GitHub Actions (`.github/workflows/ci.yml`, Python 3.11
like the Pi). The Pi pulls changes; GitHub never pushes to it (no inbound access, and no self-hosted runner, which is unsafe on a repo):

- `scripts/cron.sh` holds the whole schedule. `setup.sh` installs it, and every deploy reinstalls it, so schedule changes ship like code.
- `scripts/deploy.sh` runs every 5 minutes. When `origin/main` has a new commit and CI passed for it,
  it takes the deploy lock, fast-forwards, runs `pip install` if `requirements.txt` changed, runs the
  checks on the Pi, and reinstalls cron. If a check fails it rolls back to the previous commit
  (restoring the config too) and does not retry that commit. It refuses to deploy over local commits
  or local changes, because the checkout should always equal `origin/main`.
- Every scheduled job runs through `scripts/job.sh`, which holds a shared lock on `data/deploy.lock`.
  A deploy only starts when no job holds it, and a job that starts during a deploy waits for it. So
  code never changes under a running trading session or review.
- `data/` is never touched beyond what the checks do (merging new config keys), so Claude's state
  survives every deploy.

Settings in `.env`: `NTFY_TOPIC` for deploy notifications, `DEPLOY_GITHUB_TOKEN` (a fine-grained
read-only token with Checks access, needed for private repos), and `DEPLOY_CI_CHECK=skip` to deploy
without waiting for CI. Logs: `data/logs/deploy.log`, plus the output of the last checks in `data/deploy_checks.log`.

## Commands

```bash
.venv/bin/python src/score.py show 2026-10-13
```

```bash
.venv/bin/python src/pairs.py show
```

```bash
.venv/bin/python src/analyze.py --day 2026-10-13
```

```bash
.venv/bin/python src/trader.py replay --from 2026-10-01 --to 2026-10-31
```

- `universe.py build | show`: rebuild the universe and pairs, or print the universe.
- `pairs.py build | show`: rebuild the pairs, or print them with today's z-scores.
- `news.py fetch | status`: archive news, or show articles per day and the most covered symbols.
- `score.py run | show [DATE]`: score today's news and run the desk, or print a score file.
- `trader.py run | close [--dry-run] [--force]`: `--force` ignores the calendar and run time.
- `trader.py positions [--dry-run]`: current holdings at live prices, with today's entries and
  closed trades. Read-only, safe at any time.
- `trader.py replay --from DATE --to DATE`: reruns the rules over saved score files and daily bars
  into `data/books/replay/`, with simulated fills at the open and stops checked against daily bars.
- `analyze.py [--day DATE]`: write the daily file. `--report [--since DATE] [--config-version N]`
  prints a performance report over closed trades instead.
- `lifetime.py`: rewrite `data/lifetime.md` and `data/lifetime.json`.
- `compare.py`: print the comparison with the paper-trader, over overlapping days only. It calls a
  book better only when the 95% range of the daily return difference excludes zero.
- `review.py run | show`: run the self-review now, or print the memory.
- `config.py`: create the data folder from the defaults and validate the config.
- `broker.py`: check the account, calendar, bars and news.

`analyze.py`, `lifetime.py` and `compare.py` take `--book dry_run` or `--book replay` to report on
a simulated book; those reports go inside the book's folder.

## Stats

The stats use the same files, columns, sections and math as the paper-trader (`claude-trade`), so
both books read the same way and can be aggregated by one tool.

- `data/books/paper/trades.csv`: one row per closed trade. The first 16 columns match the
  paper-trader's; `strategy` is `sentiment_swing` for news scores or the desk idea's kind (`theme`,
  `pair`, `momentum`, `reversal`, `sector`, `news`), so every report splits results by strategy.
  Pair legs also record the `pair`.
- `data/books/paper/daily_summary.csv`: one row per trading day. The first 9 columns match.
- `date` on a trade is its exit date. `pnl_r` is the P&L per share over the planned risk per share
  (entry to stop), so a stop-out is about -1R.
- Exit reasons: `sl` stop, `time` held too long, `flip` score changed sign, `converged`,
  `diverged` and `pair_leg` for pairs, `halt`, `loss_limit`, `unknown`.
- Expectancy comes with a 95% range that treats each exit day as one cluster.
- `gross_pnl` in the daily summary is the P&L of trades closed that day; `day_pnl` is the change in
  equity, including open positions.

`lifetime.md` also measures **score calibration**: for every score ever given, traded or not, the
stock's return from that day's open to the open `max_hold_days` trading days later. With up to 60
scores a day, this answers "do the scores carry information?" much sooner than the trades alone can.

## Journal, memory and hypotheses

The learning loop works the same way as the paper-trader's, but Python enforces every rule, because
Claude here has no tools and only returns JSON.

Every weekday at 19:45, `review.py` sends Claude (Sonnet, no tools):
- today's day report
- every active memory entry with its latest evidence check
- lifetime stats
- the calibration of scores whose outcome is new
- the most instructive examples: the biggest moves with a score, against a score, and on a score of 0

Claude replies with a **journal** and a list of **memory operations**. Python validates both, then:

- writes `data/journal/<date>.md` (market, result, what went well, what went wrong, luck versus
  logic, execution, memory updates). It is written once; a second run that day does nothing.
- applies the operations to `data/memory/` (`memory.md` to read, `memory.json`, every version in
  `history/`):
  - `add` a hypothesis, `update` its evidence, implication or query, `retire` it with a reason
  - entries are never deleted, ids are never reused, at most 40 are active
  - an invalid reply leaves everything unchanged

**Claude never sets a status.** Each entry can carry a query, which `src/evidence.py` tests:

    scores: score >= 1 and reason contains "guidance"
    trades: side == short and hold_days >= 3

`scores` holds every non-zero score whose forward window completed, tested on the signed forward
return. `trades` holds closed trades, tested on R. The test needs enough rows over enough days, a
95% range that excludes zero (rows grouped by day), and enough rows **after the entry was
registered** pointing the same way. Only later outcomes count, so a hypothesis cannot be fitted to
the data it came from.

The status follows the evidence, and falls back if later evidence fails:
- `hypothesis` until the query passes
- `supported` once it passes
- `confirmed` with twice the sample
- an entry without a query stays a hypothesis

Only supported and confirmed `scoring` entries are appended to `prompts/scoring.md`. A hypothesis
under test therefore never changes the scores that test it. Each score file and trade records
`memory_sha256`, the hash of the entries that reached the prompt, so the reports show results per
memory version. Set `memory.use_in_scoring` to `false` in `data/config.json` to score without them.

A memory written by the old weekly review converts on first load: each lesson becomes a hypothesis
without a query, and the old file stays in `history/`.

Memory covers reading news, the desk's ideas and how trades turn out. Claude still cannot change
the trading rules, the config or any file.

## Stopping

Create a file named `HALT` in the repo root. Every trader job then places no entries and closes
all positions. Delete it to resume. Set `desk.enabled` to `false` in `data/config.json` to trade
the news scores only.

## Safety

- The Alpaca client is built with `paper=True` and refuses any endpoint without `paper-api`.
- Every trader job checks the account differs from `PAPER_TRADER_ACCOUNT`.
- `data/config.json` is validated against `defaults/config_bounds.json` at every start. Keys added
  in a newer version are filled in from the defaults, keeping your values.
- Hard caps in code: 30 orders a day, no position over 10% of equity, no stock under $5, shorts
  only when easy to borrow.
- Losing `max_daily_loss_pct` in a day closes everything and blocks entries until the next session.
- Client order ids are `st-<YYYYMMDD>-<SYMBOL>-<entry|exit|stop>`, so re-running a job never
  double-submits. Each job reconciles `positions.csv` with the account on start.

## Layout

| Path | What |
|---|---|
| `src/` | All Python code; every command runs as `.venv/bin/python src/<name>.py` |
| `prompts/` | Fixed instructions for scoring, the desk and the daily review |
| `defaults/` | Config and watchlist copied into `data/` once, and the config's hard limits |
| `scripts/` | `positions.sh`, `setup.sh`, nightly `backup.sh`, and `migrate.sh` for an old `state/` folder |
| `tests/` | Test suite, see [tests/README.md](tests/README.md) |
| `docs/SPEC.md` | The design |
| `data/` | Everything the system writes; not in git |

Inside `data/`:

| Path | What |
|---|---|
| `daily/<date>.md`, `lifetime.md` | The two reports, see Where to look |
| `config.json`, `watchlist.json` | Active config and watchlist; watchlist symbols are always in the universe |
| `universe.json`, `pairs.json` | Tonight's universe and pairs |
| `news/`, `scores/`, `bars/` | News archive, score files with the desk's ideas, cached daily bars |
| `books/paper/` | `trades.csv`, `orders.csv`, `positions.csv`, `daily_summary.csv` |
| `books/dry_run/`, `books/replay/` | Simulated books, with their own reports |
| `memory/` | Claude's memory entries (`memory.md`, `memory.json`) and every version in `history/` |
| `journal/` | The daily review's journal, one file per trading day, never rewritten |
| `logs/` | `cron.log` (all cron output) and `sentiment.log` (warnings and errors with tracebacks) |
| `backups/` | The last 30 nightly archives of `data/` |

To move an old `state/` folder into this layout, run `scripts/migrate.sh`, then `scripts/setup.sh`
to reinstall the cron jobs. `setup.sh` runs the migration itself when it finds `state/`.

`held_over_earnings` in `trades.csv` stays blank: Alpaca's free data has no earnings calendar.

Rule changes bump `version` in the config, and every trade records it.
