"""trader.db: migrations, the shared core schema, the CSV mirror, import, check, backup and restore."""
import hashlib
import json
import re
from datetime import datetime

import pytest

import db
import paths
from broker import ET
from logger import Book, trade_row

# The core schema is byte-identical in claude-quant. Change both copies together, then update this
# hash in both repos.
CORE_SHA256 = "07ee2f1a050a5fca03d6c8bcd7ba56ced6fbb9f56addf6e799e4519142593581"
T0 = datetime(2026, 10, 14, 9, 45, tzinfo=ET)


def test_core_schema_is_the_shared_one():
    assert hashlib.sha256((db.MIGRATIONS / "001_core.sql").read_bytes()).hexdigest() == CORE_SHA256


def test_migration_numbers_are_unique_and_sentiment_owns_100_to_199():
    numbers = [n for n, _ in db.migrations()]
    assert len(numbers) == len(set(numbers)) and numbers[0] == 1
    assert all(n == 1 or 100 <= n < 200 for n in numbers)  # 2-99 core, 100-199 sentiment, 200+ quant


def test_migrations_are_additive():
    for _, f in db.migrations():
        sql = re.sub(r"--.*", "", f.read_text()).upper()
        assert not re.search(r"\bDROP\b|\bRENAME\b", sql), f.name


def test_migrate_creates_schema_once(tmp_path):
    conn = db.connect(tmp_path / "t.db", migrate_schema=False)
    assert db.migrate(conn) == [n for n, _ in db.migrations()]
    assert db.migrate(conn) == []
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"meta", "order_events", "trades", "daily_summary"} <= tables


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
