"""trader.db: migrations, the shared core schema, the CSV mirror, import, check, backup and restore."""
import hashlib
import json
import os
import re
from datetime import datetime

import pytest

import config
import db
import paths
import memory
from broker import ET, FILLED, NEW, Order, Position
from logger import Book, snapshot_rows, trade_row

# The core migrations are byte-identical in claude-quant. Change both copies together, then update
# these hashes in both repos.
CORE_SHA256 = {
    "001_core.sql": "07ee2f1a050a5fca03d6c8bcd7ba56ced6fbb9f56addf6e799e4519142593581",
    "002_dashboard.sql": "859a3602ba3a4e4e6274d22450b791222cf56011b824198473bdcb2f59299b05",
    "003_bars.sql": "4ffdf41d3b4d2f948e9b31ba6767efa7462734a1f5903806bd2c7351177d53d3",
}
T0 = datetime(2026, 10, 14, 9, 45, tzinfo=ET)


def test_core_schema_is_the_shared_one():
    core = {f.name for n, f in db.migrations() if n < 100}
    assert core == set(CORE_SHA256)
    for name, sha in CORE_SHA256.items():
        assert hashlib.sha256((db.MIGRATIONS / name).read_bytes()).hexdigest() == sha, name


def test_migration_numbers_are_unique_and_sentiment_owns_100_to_199():
    numbers = [n for n, _ in db.migrations()]
    assert len(numbers) == len(set(numbers)) and numbers[0] == 1
    assert all(n < 200 for n in numbers)  # 1-99 core, 100-199 sentiment, 200+ quant


def test_migrations_are_additive():
    for _, f in db.migrations():
        sql = re.sub(r"--.*", "", f.read_text()).upper()
        assert not re.search(r"\bDROP\b|\bRENAME\b", sql), f.name


def test_migrate_creates_schema_once(tmp_path):
    conn = db.connect(tmp_path / "t.db", migrate_schema=False)
    assert db.migrate(conn) == [n for n, _ in db.migrations()]
    assert db.migrate(conn) == []
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"meta", "order_events", "trades", "daily_summary", "orders", "equity_snapshots", "position_snapshots",
            "runs", "versions", "bars"} <= tables


def position(**kw):
    return {"symbol": "AAPL", "side": "long", "qty": "50", "entry_date": "2026-10-10",
            "entry_time": "2026-10-10T09:45:00-04:00", "entry_price": "100.0", "stop_price": "96.0",
            "score": "2", "confidence": "0.9", "score_date": "2026-10-10", "config_version": "3",
            "prompt_sha256": "abc", "memory_sha256": "", "pair": ""} | kw


def fill_book(book):
    book.order_event(T0, "submit", "AAPL", side="buy", qty=50, order_type="market", price=100.0,
                     stop_price=96.0, order_id="o1", client_order_id="st-20261014-AAPL-entry", status="new",
                     reason="score 2")
    book.order_event(T0, "skip", "NKE", reason="not easy to borrow")
    book.trade(trade_row(position(), T0, 104.0, "time", 3))
    book.trade(trade_row(position(), T0, 104.0, "time", 3))  # duplicate, ignored in both
    book.trade(trade_row(position(symbol="NKE", side="short", entry_price="80"), T0, 82.0, "sl", 2))
    book.daily_summary({"date": "2026-10-14", "trades": 2, "gross_pnl": 1, "equity_end": 100_001,
                        "halted": False, "scored": True, "day_return_pct": 0.1})
    book.daily_summary({"date": "2026-10-14", "trades": 2, "gross_pnl": 100, "equity_end": 100_100,
                        "halted": False, "scored": True, "day_return_pct": 0.2})


def test_books_share_one_database(state):
    paper, dry = Book(paths.book_dir("paper"), "paper"), Book(paths.book_dir("dry_run"), "dry_run")
    fill_book(paper)
    fill_book(dry)
    conn = db.connect(paths.db_file())
    for name, book in (("paper", paper), ("dry_run", dry)):
        assert db.compare(conn, name, *db.csv_rows(book.dir)) == []
    trade = conn.execute("SELECT * FROM trades WHERE book = 'paper' AND symbol = 'AAPL'").fetchone()
    assert (trade["pnl_usd"], trade["pnl_r"], trade["hold_days"], trade["entry_date"]) == (200.0, 1.0, 3, "2026-10-10")
    extra = json.loads(trade["extra"])
    assert extra["score"] == 2 and extra["confidence"] == 0.9 and extra["prompt_sha256"] == "abc"
    summary = conn.execute("SELECT * FROM daily_summary WHERE book = 'paper'").fetchone()
    assert summary["equity_end"] == 100_100 and json.loads(summary["extra"]) == {"day_return_pct": 0.2,
                                                                                 "scored": True}


def test_clear_empties_only_that_book(state):
    paper, replay = Book(paths.book_dir("paper"), "paper"), Book(paths.book_dir("replay"), "replay")
    fill_book(paper)
    fill_book(replay)
    replay.clear()
    conn = db.connect(paths.db_file())
    assert conn.execute("SELECT COUNT(*) FROM trades WHERE book = 'replay'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM trades WHERE book = 'paper'").fetchone()[0] == 2


def test_book_outside_the_data_folder_has_its_own_database(state, tmp_path):
    fill_book(Book(tmp_path / "elsewhere", "paper"))
    assert (tmp_path / "elsewhere" / db.DB_NAME).exists() and not paths.db_file().exists()


def test_first_mirrored_write_imports_every_book(state):
    paper, dry = Book(paths.book_dir("paper"), "paper"), Book(paths.book_dir("dry_run"), "dry_run")
    fill_book(paper)
    fill_book(dry)
    for suffix in ("", "-wal", "-shm"):  # history from before the database existed
        paths.db_file().with_name(db.DB_NAME + suffix).unlink(missing_ok=True)
    paper.order_event(T0, "fill", "AAPL", order_id="o1")
    conn = db.connect(paths.db_file())
    for name, book in (("paper", paper), ("dry_run", dry)):
        assert db.compare(conn, name, *db.csv_rows(book.dir)) == []


def test_database_failure_never_stops_the_csv(state, caplog):
    book = Book(paths.book_dir("paper"), "paper")
    paths.db_file().mkdir(parents=True)  # a folder where the database file should be
    book.order_event(T0, "submit", "AAPL")
    book.order_event(T0, "fill", "AAPL")
    assert len(book.order_events()) == 2
    assert sum("database write" in r.message for r in caplog.records) == 1


def test_cli_import_check_migrate(state, capsys):
    fill_book(Book(paths.book_dir("paper"), "paper"))
    with db.connect(paths.db_file()) as conn:
        conn.execute("DELETE FROM trades")
    assert db.main(["check"]) == 1
    assert db.main(["import"]) == 0 and db.main(["check"]) == 0
    for suffix in ("", "-wal", "-shm"):
        paths.db_file().with_name(db.DB_NAME + suffix).unlink(missing_ok=True)
    assert db.main(["migrate"]) == 0 and db.main(["check"]) == 0
    out = capsys.readouterr().out
    assert "paper: imported 2 trades, 2 order events, 1 daily summaries" in out and "paper: OK" in out


def test_backup_and_restore(state, tmp_path):
    fill_book(Book(paths.book_dir("paper"), "paper"))
    assert db.main(["backup", str(tmp_path / "b.db")]) == 0
    with db.connect(paths.db_file()) as conn:
        conn.execute("DELETE FROM trades")
    assert db.main(["restore", str(tmp_path / "b.db")]) == 0
    assert db.connect(paths.db_file()).execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 2
    with pytest.raises(FileNotFoundError):
        db.restore(tmp_path / "missing.db", paths.db_file())


def order(status=NEW, **kw):
    stop = Order("o-stop", "st-1-stop", "AAPL", "sell", 50, "stop", "held", stop_price=96.0)
    return Order(**({"id": "o-1", "client_order_id": "st-1-entry", "symbol": "AAPL", "side": "buy", "qty": 50,
                     "order_type": "market", "status": status, "legs": (stop,)} | kw))


def test_order_states_are_upserted_only_when_they_change(state, monkeypatch):
    book = Book(paths.book_dir("paper"), "paper")
    writes = []
    real = book.db.order
    monkeypatch.setattr(book.db, "order", lambda r: (writes.append(r), real(r)))
    book.order_state(T0, order())
    book.order_state(T0, order())
    book.order_state(T0, order(FILLED, filled_qty=50, filled_avg_price=100.0, filled_at=T0))
    assert len(writes) == 3
    conn = db.connect(paths.db_file())
    assert db.open_order_ids(conn, "paper") == ["o-stop"]  # a held leg is open
    rows = {r["order_id"]: r for r in conn.execute("SELECT * FROM orders")}
    assert (rows["o-1"]["status"], rows["o-1"]["filled_at"]) == ("filled", T0.isoformat())
    assert rows["o-stop"]["parent_order_id"] == "o-1"
    book.order_state(T0, Order("o-stop", "st-1-stop", "AAPL", "sell", 50, "stop", "canceled"))  # fetched alone
    assert db.open_order_ids(conn, "paper") == [] and book.open_order_ids() == []
    assert conn.execute("SELECT parent_order_id FROM orders WHERE order_id = 'o-stop'").fetchone()[0] == "o-1"


def test_snapshot_rows():
    equity, rows = snapshot_rows(T0, 100_000, {"AAPL": Position("AAPL", 50, 100.0, 104.0),
                                               "NKE": Position("NKE", -50, 80.0, 82.0)})
    assert equity == {"ts": T0.isoformat(), "equity": 100_000, "cash": 100_000 - 5200 + 4100, "long_value": 5200,
                      "short_value": -4100, "positions": 2}
    assert [r["unrealized_pnl"] for r in rows] == [200, -100]


def test_import_keeps_database_only_rows(state):
    book = Book(paths.book_dir("paper"), "paper")
    fill_book(book)
    book.order_state(T0, order())
    book.snapshot(T0, 100_000, {"AAPL": Position("AAPL", 50, 100.0, 101.0)})
    assert db.main(["import"]) == 0
    conn = db.connect(paths.db_file())
    assert [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("orders", "equity_snapshots", "position_snapshots")] == [2, 1, 1]
    book.clear()
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


def test_runs_start_end_and_lost(state):
    import subprocess

    conn = db.connect(paths.db_file())
    gone = subprocess.Popen(["true"])
    gone.wait()
    stale = db.run_start(conn, "trader-run", "2026-10-14T09:45:00-04:00", pid=gone.pid)
    ok = db.run_start(conn, "snapshot", "2026-10-14T10:02:00-04:00", "abc1234", "3", pid=os.getpid())
    db.run_end(conn, ok, 0, "2026-10-14T10:02:04-04:00", "fine")
    runs = {r["id"]: r for r in conn.execute("SELECT * FROM runs")}
    assert runs[stale]["status"] == "lost"
    assert (runs[ok]["status"], runs[ok]["error"], runs[ok]["git_sha"], runs[ok]["config_version"]) == (
        "ok", None, "abc1234", "3")


def test_versions_follow_config_prompts_and_memory(state):
    conn = db.connect(paths.db_file())
    first = db.record_versions(conn, db.current_versions(), "deploy", "2026-10-14T08:00:00-04:00")
    assert {(k, n) for k, n, _ in first} == {("config", ""), ("watchlist", ""), ("prompt", "scoring"),
                                            ("prompt", "desk"), ("prompt", "review")}
    versions = {(k, n): v for k, n, v in first}
    assert versions[("prompt", "scoring")] == hashlib.sha256(paths.SCORING_PROMPT.read_bytes()).hexdigest()
    assert versions[("config", "")] == str(config.load_config()["version"])
    assert db.record_versions(conn, db.current_versions(), "score", "2026-10-14T09:00:00-04:00") == []

    mem = memory.empty()
    mem["entries"] = [{"id": "M-1", "title": "t", "status": "supported", "scope": "scoring", "implication": "i",
                       "since": "2026-10-01", "last_reviewed": "2026-10-14", "query": None, "evidence": "e"}]
    saved = memory.save(mem | {"updated_at": "2026-10-14T19:45:00-04:00"})  # records the version itself
    row = conn.execute("SELECT * FROM versions WHERE kind = 'memory'").fetchone()
    assert (row["version"], row["reason"]) == (saved["memory_sha256"], "memory save") and row["version"]
    memory.save(saved | {"updated_at": "2026-10-15T19:45:00-04:00"})  # same entries: no new row
    assert conn.execute("SELECT COUNT(*) FROM versions WHERE kind = 'memory'").fetchone()[0] == 1


def test_config_upgrade_records_a_version(state):
    cfg = json.loads(paths.config_file().read_text())
    del cfg["risk"]["max_daily_loss_pct"]
    paths.config_file().write_text(json.dumps(cfg))
    config.load_config()
    row = db.connect(paths.db_file()).execute("SELECT * FROM versions WHERE kind = 'config'").fetchone()
    assert row["reason"] == "config upgrade" and "max_daily_loss_pct" in row["content"]


def test_cli_runs_and_versions(state, capsys, tmp_path):
    assert db.main(["run-start", "score", "--pid", str(os.getpid())]) == 0
    run_id = int(capsys.readouterr().out)
    (tmp_path / "out.log").write_text("scoring\nERROR all batches failed\n")
    assert db.main(["run-end", str(run_id), "1", "--output", str(tmp_path / "out.log")]) == 0
    assert db.main(["versions", "--reason", "deploy"]) == 0
    assert "recorded prompt scoring" in capsys.readouterr().out
    run = db.connect(paths.db_file()).execute("SELECT * FROM runs").fetchone()
    assert (run["job"], run["status"], run["error"]) == ("score", "failed", "scoring\nERROR all batches failed")


def test_note_versions_never_raises(state, caplog):
    paths.db_file().mkdir(parents=True)
    assert db.note_versions("score") == []
    assert "recording versions" in caplog.text


class BarSource:
    def __init__(self):
        self.calls = 0

    def daily_bars(self, symbols, start, end):
        import pandas as pd

        self.calls += 1
        days = [d.date() for d in pd.bdate_range(start.date(), end.date())]
        return {s: pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0, "vwap": 1.2},
                                index=pd.Index(days, name="date")) for s in symbols}


def test_fetched_bars_reach_the_database(state):
    from datetime import date

    from broker import load_bars

    db.connect(paths.db_file()).close()  # an existing database
    source = BarSource()
    load_bars(["AAPL", "NKE"], date(2026, 10, 12), date(2026, 10, 16), source)
    load_bars(["AAPL"], date(2026, 10, 12), date(2026, 10, 16), source)  # cached: nothing fetched
    conn = db.connect(paths.db_file())
    rows = conn.execute("SELECT * FROM bars WHERE symbol = 'AAPL' ORDER BY ts").fetchall()
    assert source.calls == 1 and [r["ts"] for r in rows] == [f"2026-10-{d}" for d in range(12, 17)]
    assert (rows[0]["timeframe"], rows[0]["vwap"]) == ("1Day", 1.2)


def test_bar_cache_is_backfilled_by_migrate_and_import(state, capsys):
    from datetime import date

    from broker import load_bars

    load_bars(["AAPL", "NKE"], date(2026, 10, 12), date(2026, 10, 16), BarSource())
    for suffix in ("", "-wal", "-shm"):  # the cache existed before the database
        paths.db_file().with_name(db.DB_NAME + suffix).unlink(missing_ok=True)
    conn = db.connect(paths.db_file())
    assert conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 0
    assert db.main(["migrate"]) == 0 and "bars: imported 2 symbols" in capsys.readouterr().out
    assert conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 10
    assert db.main(["import"]) == 0 and conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 10


def test_a_new_database_imports_the_bar_cache(state):
    from datetime import date

    from broker import load_bars

    load_bars(["AAPL"], date(2026, 10, 12), date(2026, 10, 16), BarSource())
    for suffix in ("", "-wal", "-shm"):
        paths.db_file().with_name(db.DB_NAME + suffix).unlink(missing_ok=True)
    fill_book(Book(paths.book_dir("paper"), "paper"))  # the first write creates the database
    assert db.connect(paths.db_file()).execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 5


def test_purge_follows_the_retention_rules(tmp_path):
    from datetime import date

    conn = db.connect(tmp_path / "p.db")
    today = date(2026, 10, 14)
    with conn:
        for sym, day in (("OLD", "2026-06-01"), ("TRADED", "2026-06-01"), ("NEW", "2026-10-01")):
            conn.execute("INSERT INTO bars (symbol, timeframe, ts) VALUES (?, '1Min', ?)", (sym, day + "T09:30:00-04:00"))
        for sym, day in (("OLD", "2024-01-02"), ("TRADED", "2024-01-02"), ("TRADED", "2023-06-01"), ("NEW", "2026-01-02")):
            conn.execute("INSERT INTO bars (symbol, timeframe, ts) VALUES (?, '1Day', ?)", (sym, day))
        conn.execute("INSERT INTO trades (book, date, symbol, side, qty, entry_date, entry_time) "
                     "VALUES ('paper', '2026-06-01', 'TRADED', 'long', 1, '2026-06-01', 'a')")
        conn.execute("INSERT INTO trades (book, date, symbol, side, qty, entry_date, entry_time) "
                     "VALUES ('paper', '2024-01-10', 'TRADED', 'long', 1, '2024-01-05', 'b')")
        for ts in ("2026-06-01T10:00:00-04:00", "2026-06-01T15:59:00-04:00", "2026-10-13T10:00:00-04:00",
                   "2026-10-13T15:59:00-04:00"):
            conn.execute("INSERT INTO equity_snapshots (book, ts, equity) VALUES ('paper', ?, 1)", (ts,))
            conn.execute("INSERT INTO position_snapshots (book, ts, symbol, qty) VALUES ('paper', ?, 'X', 1)", (ts,))
        conn.execute("INSERT INTO equity_snapshots (book, ts, equity) VALUES ('dry_run', '2026-06-01T10:00:00-04:00', 1)")
        for status, started in (("ok", "2026-06-01T03:00:00-04:00"), ("failed", "2026-06-01T03:00:00-04:00"),
                                ("lost", "2026-06-01T03:00:00-04:00"), ("ok", "2026-10-13T03:00:00-04:00")):
            conn.execute("INSERT INTO runs (job, started_at, status) VALUES ('j', ?, ?)", (started, status))
    assert db.purge(conn, today) == {"1Min bars": 1, "1Day bars": 2, "equity snapshots": 1,
                                     "position snapshots": 1, "ok runs": 1}
    bars = sorted((r[0], r[1], r[2][:10]) for r in conn.execute("SELECT symbol, timeframe, ts FROM bars"))
    assert bars == [("NEW", "1Day", "2026-01-02"), ("NEW", "1Min", "2026-10-01"),
                    ("TRADED", "1Day", "2024-01-02"), ("TRADED", "1Min", "2026-06-01")]
    kept = [r[0] for r in conn.execute("SELECT ts FROM equity_snapshots WHERE book = 'paper' ORDER BY ts")]
    assert kept == ["2026-06-01T15:59:00-04:00", "2026-10-13T10:00:00-04:00", "2026-10-13T15:59:00-04:00"]
    assert [r[0] for r in conn.execute("SELECT ts FROM position_snapshots ORDER BY ts")] == kept
    assert conn.execute("SELECT COUNT(*) FROM equity_snapshots WHERE book = 'dry_run'").fetchone()[0] == 1
    assert sorted(r[0] for r in conn.execute("SELECT status FROM runs")) == ["failed", "lost", "ok"]
    assert db.purge(conn, today) == dict.fromkeys(["1Min bars", "1Day bars", "equity snapshots",
                                                   "position snapshots", "ok runs"], 0)


def test_strip_bars_empties_only_the_bars(tmp_path):
    conn = db.connect(tmp_path / "b.db")
    with conn:
        conn.executemany("INSERT INTO bars (symbol, timeframe, ts, close) VALUES ('SPY', '1Min', ?, 1)",
                         [(str(i),) for i in range(5000)])
        conn.execute("INSERT INTO runs (job, started_at, status) VALUES ('j', 't', 'ok')")
    conn.close()
    size = (tmp_path / "b.db").stat().st_size
    db.strip_bars(tmp_path / "b.db")
    conn = db.connect(tmp_path / "b.db")
    assert conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    assert (tmp_path / "b.db").stat().st_size < size
