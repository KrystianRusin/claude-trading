# claude-sentiment-trading

A paper swing trader driven by news. Each morning Claude scores the news on the most liquid US
stocks and proposes a few trade ideas. Fixed rules size, stop and exit every trade, and positions
are held for days. It runs on a Raspberry Pi next to
[claude-quant](https://github.com/KrystianRusin/claude-quant), on its own Alpaca paper account, so
the two approaches can be compared over the same period.

Paper fills are optimistic, paper shorts have no borrow costs, and short-run results say little
about skill. This is not financial advice.

## How it works

Claude never places orders, edits code or writes files. It runs headless (`claude -p`) with no
tools and returns JSON, which Python validates against a strict schema before using it.

1. Each night `universe.py` keeps the 400 most liquid stocks on NYSE, NASDAQ and NYSE American
   (priced at $5 or more). `pairs.py` finds pairs among them that move together and whose price
   gap tends to revert.
2. `news.py` archives Alpaca News articles for the universe through the day.
3. At 06:30 `score.py` has Claude (Haiku) score up to 60 stocks with fresh news from -2 to +2.
   In the same run `desk.py` sends Claude (Sonnet) the scores, recent headlines, price moves and
   diverged pairs, and gets back up to 8 trade ideas: themes, pairs, momentum, reversals, sector
   moves or underrated news. Each idea has to cite its evidence. Python drops any idea that cites
   something not in the input or trades outside the universe.
4. At 09:45 `trader.py run` exits positions, then enters the strongest candidates from the scores
   and the ideas. Each position is 5% of equity with a stop at 2x the 14-day ATR. Entries stop when
   gross exposure would pass 80% or net exposure 40%.
5. Positions exit after 5 days or when their score flips sign. Pairs exit when the gap closes,
   widens too far, after 10 days, or when one leg is stopped out.
6. At 16:15 `trader.py close` records fills and checks the daily loss limit. Reports are written
   at 18:30.
7. At 19:45 `review.py` has Claude write the day's journal and update its memory.

Scores are only judged on news published after the system started. Claude's training data already
covers what followed older news, so replays never ask it to score past news.

## Memory and hypotheses

The learning loop follows claude-quant's, but Python enforces every rule because Claude here only
returns JSON.

Each evening Claude gets the day report, its active memory entries with their latest evidence,
lifetime stats and the newest score outcomes. It replies with a journal and a list of memory
operations (`add`, `update`, `retire`). Entries are never deleted, ids are never reused, at most 40
are active, and an invalid reply changes nothing.

Claude never sets an entry's status. An entry can carry a query that `src/evidence.py` tests:

    scores: score >= 1 and reason contains "guidance"
    trades: side == short and hold_days >= 3

An entry moves from `hypothesis` to `supported` once its query passes. That needs enough rows over
enough days, a 95% range that excludes zero (grouped by day), and enough rows after the entry was
registered pointing the same way. Twice the sample makes it `confirmed`, and it falls back if later
evidence fails. Only supported and confirmed entries reach the scoring prompt, so a hypothesis
under test can't influence the scores that test it. Every score and trade records the hash of the
memory it used, so results can be split by memory version.

Score calibration is the main measure of whether the scores carry information. For every score,
traded or not, `lifetime.py` measures the stock's return over the holding window. With up to 60
scores a day, that answers the question much sooner than trades alone.

## Safety

- Paper only. The Alpaca client refuses non-paper endpoints, any live account needs `ALLOW_LIVE=1`,
  and every job checks it isn't using claude-quant's account.
- `data/config.json` is validated against `defaults/config_bounds.json` on every start.
- Hard caps in code: 30 orders a day, 10% of equity per position, no stock under $5, shorts only
  when easy to borrow.
- Hitting the daily loss limit closes everything and blocks entries until the next session.
- Client order ids are deterministic (`st-<date>-<symbol>-<entry|exit|stop>`), so re-running a job
  never submits twice.
- A `HALT` file in the repo root stops entries and closes all positions.

## Data

Everything the system writes lives in the gitignored `data/` folder, including scores, memory,
journal and trade logs. Trades, orders and daily summaries are written as CSV and mirrored into
SQLite (`data/trader.db`), which also holds equity snapshots, order states, job runs, versions and
daily bars. It feeds [claude-desk](https://github.com/KrystianRusin/claude-desk), the dashboard.
The core schema in `migrations/` is shared with claude-quant and pinned by hash in both repos.

The broker layer is the same design as claude-quant's: a neutral interface in `src/broker.py` with
data and execution roles, an Alpaca backend, an Interactive Brokers stub, and a contract test suite
every backend has to pass.

## Deployment

The Pi pulls changes; nothing pushes to it. GitHub Actions runs the tests on every push.
`scripts/deploy.sh` runs every 5 minutes, fast-forwards to new commits that passed CI, migrates the
database, runs the checks and rolls back if they fail. Jobs and deploys share a lock, so code never
changes under a running job. The schedule lives in `scripts/cron.sh` and is reinstalled on every
deploy.

## Setup

You need a second Alpaca paper account, separate from claude-quant's. On the Pi:

```bash
scripts/setup.sh
```

It creates the virtual environment, asks for the account's keys, stores a Claude token, creates
`data/`, runs the tests and installs the cron jobs. Credentials stay in `.env` and are never
logged.

## Commands

All commands run as `.venv/bin/python src/<name>.py`.

| Command | What it does |
|---|---|
| `trader.py run \| close [--dry-run] [--force]` | The morning and closing jobs. `--dry-run` uses simulated fills. |
| `trader.py positions` | Current holdings at live prices. Read-only. |
| `trader.py replay --from DATE --to DATE` | Reruns the rules over saved scores and daily bars. |
| `score.py run \| show [DATE]` | Scores today's news and runs the desk, or prints a score file. |
| `analyze.py [--day DATE]` | Writes the daily report, or `--report` for a performance report. |
| `compare.py` | Compares with claude-quant over overlapping days. |
| `review.py run \| show` | Runs the evening review, or prints the memory. |

The two reports to read are `data/daily/<date>.md` (one day) and `data/lifetime.md` (everything
since the start).

## Layout

| Path | Contents |
|---|---|
| `src/` | All Python code |
| `prompts/` | Instructions for scoring, the desk and the review |
| `defaults/` | Seed config and watchlist, and the config's hard limits |
| `migrations/` | Database schema |
| `scripts/` | Setup, deploy, cron, backup |
| `tests/` | Test suite, see [tests/README.md](tests/README.md) |

## Tests

```bash
.venv/bin/python -m pytest
```

No network or credentials needed.
