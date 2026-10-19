"""Read-only access to the bots' data: their trader.db files and the sentiment bot's score files.

Every database is opened with mode=ro and query_only, one connection per request, and every query is
stopped after QUERY_SECONDS. Nothing here can write: the bots own their data.

Where each bot keeps a book's database, relative to its data directory:
- claude-quant: data/trader.db is the paper book, data/shadow/trader.db the shadow dry run
- claude-sentiment-trading: data/trader.db holds every book, told apart by the book column
"""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_NAME = "trader.db"
LAYOUT = {
    "quant": {"paper": DB_NAME, "dry_run": f"shadow/{DB_NAME}", "replay": f"replay/{DB_NAME}"},
    "sentiment": {"paper": DB_NAME, "dry_run": DB_NAME, "replay": DB_NAME},
}
QUERY_SECONDS = 2.0
# Tables and columns the API reads: the shared core migrations 001-003 of both bots.
REQUIRED = {
    "trades": {"book", "date", "symbol", "side", "qty", "entry_date", "entry_time", "entry_price", "exit_time",
               "exit_price", "exit_reason", "pnl_usd", "pnl_r", "pnl_pct", "hold_days", "strategy",
               "strategy_version", "config_version", "extra"},
    "order_events": {"book", "ts", "date", "event", "symbol", "side", "order_type", "reason"},
    "daily_summary": {"book", "date", "trades", "gross_pnl", "equity_start", "equity_end", "halted"},
    "equity_snapshots": {"book", "ts", "equity", "long_value", "short_value", "positions"},
    "position_snapshots": {"book", "ts", "symbol", "qty", "avg_entry_price", "price", "market_value",
                           "unrealized_pnl"},
    "runs": {"job", "started_at", "ended_at", "status", "exit_code", "error", "git_sha"},
    "versions": {"kind", "name", "version", "sha256", "effective_from", "reason", "content"},
    "bars": {"symbol", "timeframe", "ts", "close"},
}


class QueryTimeout(RuntimeError):
    pass


def db_path(settings, bot, book):
    return settings.bots[bot]["data_dir"] / LAYOUT[bot][book]


def _open(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


@contextmanager
def connect(path):
    """A read-only connection whose queries stop after QUERY_SECONDS; None when the file is missing."""
    if not Path(path).exists():
        yield None
        return
    conn = _open(path)
    deadline = time.monotonic() + QUERY_SECONDS
    conn.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
    try:
        yield conn
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e):
            raise QueryTimeout(f"query on {path} took longer than {QUERY_SECONDS}s") from e
        raise
    finally:
        conn.close()


def check(path):
    """Problems that stop the API reading this database, as strings; empty when it is usable."""
    if not Path(path).exists():
        return [f"{path}: missing (the bot has not written anything yet?)"]
    try:
        with connect(path) as conn:
            problems = []
            for table, columns in REQUIRED.items():
                have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
                if not have:
                    problems.append(f"{path}: no {table} table (run the bot's db.py migrate)")
                elif columns - have:
                    problems.append(f"{path}: {table} lacks {sorted(columns - have)}")
            return problems
    except sqlite3.Error as e:
        return [f"{path}: cannot be read ({e})"]


def query(settings, bot, book, sql, params=()):
    """Rows of `sql` on one bot's book database as dicts; [] when the database does not exist yet.
    `sql` must filter by book itself (WHERE book = ?), since the sentiment database holds them all."""
    with connect(db_path(settings, bot, book)) as conn:
        if conn is None:
            return []
        return [dict(r) for r in conn.execute(sql, params)]


def query_bot_db(settings, bot, sql, params=()):
    """Rows of `sql` on a bot's paper database, for tables that are not per book (runs, versions, bars)."""
    return query(settings, bot, "paper", sql, params)


def score_files(settings, since):
    """The sentiment bot's score files dated `since` or later, oldest first, as dicts."""
    folder = settings.bots["sentiment"]["data_dir"] / "scores"
    out = []
    for f in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        if f.stem >= since.isoformat():
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue  # a file being written, or a broken one: skip it
    return out
