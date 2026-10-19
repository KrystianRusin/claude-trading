"""Fake data folders for both bots, built with the bots' shared migrations (copied in tests/schema/)."""
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from desk import app as app_mod
from desk import config

SCHEMA = Path(__file__).parent / "schema"
DAYS = [date(2026, 10, 5) + timedelta(days=i) for i in range(10) if (date(2026, 10, 5) + timedelta(days=i)).weekday() < 5]
TODAY = DAYS[-1]  # 2026-10-16
SECRET = "Traceback: /home/krystian/claude-trade/.env account PA3SECRET"


def create_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    for f in sorted(SCHEMA.glob("*.sql")):
        conn.executescript(f.read_text())
    conn.execute("INSERT INTO meta VALUES ('schema_version', '3')")
    return conn


def insert(conn, table, **row):
    conn.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))


def daily(conn, book, returns, start=100_000.0):
    equity = start
    for day, r in zip(DAYS, returns):
        end = round(equity * (1 + r), 2)
        insert(conn, "daily_summary", book=book, date=day.isoformat(), trades=1, gross_pnl=round(end - equity, 2),
               equity_start=equity, equity_end=end, halted=0)
        equity = end
    return equity


def build_quant(folder):
    conn = create_db(folder / "trader.db")
    last = daily(conn, "paper", [0.01, -0.005, 0.002, 0.003, -0.001, 0.004, 0.0, 0.002])
    for i, (sym, pnl, r) in enumerate([("SPY", 120, 1.2), ("SPY", -50, -0.5), ("QQQ", 80, 0.8), ("SPY", 30, 0.3),
                                       ("AAPL", -100, -1.0), ("QQQ", 60, 0.6), ("QQQ", -20, -0.2)]):
        day = DAYS[i].isoformat()
        insert(conn, "trades", book="paper", date=day, symbol=sym, side="long", qty=10, entry_date=day,
               entry_time=f"{day}T09:46:00-04:00", entry_price=100, exit_time=f"{day}T10:30:00-04:00", exit_price=101,
               exit_reason="tp" if pnl > 0 else "sl", pnl_usd=pnl, pnl_r=r, strategy="opening_range_breakout",
               strategy_version="1" if i < 4 else "2", config_version="3",
               extra=json.dumps({"entry_minute_after_open": 16, "range_pct": 0.8, "internal_note": "not public"}))
    for day in DAYS[-5:]:
        d = day.isoformat()
        insert(conn, "order_events", book="paper", ts=f"{d}T09:46:00-04:00", date=d, event="submit", symbol="SPY",
               side="long", order_type="bracket", order_id="o-secret-1", client_order_id="pt-x")
        insert(conn, "order_events", book="paper", ts=f"{d}T09:47:00-04:00", date=d, event="skip", symbol="NVDA",
               side="short", reason="not easy to borrow")
    insert(conn, "order_events", book="paper", ts=f"{TODAY}T10:00:00-04:00", date=TODAY.isoformat(), event="submit",
           symbol="SPY", side="sell", order_type="market")  # an exit, not a signal
    for minute in range(0, 30, 5):
        ts = f"{TODAY}T09:{30 + minute}:00-04:00"
        insert(conn, "equity_snapshots", book="paper", ts=ts, equity=last + minute, long_value=1000, short_value=0,
               positions=1)
        insert(conn, "position_snapshots", book="paper", ts=ts, symbol="SPY", qty=10, avg_entry_price=100, price=101,
               market_value=1010, unrealized_pnl=10)
    insert(conn, "runs", job="trader", started_at=f"{TODAY}T09:00:00-04:00", ended_at=f"{TODAY}T15:50:00-04:00",
           status="ok", exit_code=0, git_sha="abc1234")
    insert(conn, "runs", job="review", started_at=f"{TODAY}T17:00:00-04:00", ended_at=f"{TODAY}T17:20:00-04:00",
           status="failed", exit_code=1, error=SECRET, git_sha="abc1234")
    insert(conn, "versions", kind="config", name="", version="3", sha256="aa", effective_from=f"{DAYS[0]}T09:00:00-04:00",
           reason="deploy", content='{"version": 3, "secret_param": 42}')
    watchlist = {"symbols": [{"symbol": "SPY", "added": "2026-01-01"}, {"symbol": "NVDA", "added": TODAY.isoformat()}]}
    insert(conn, "versions", kind="watchlist", name="", version="bb", sha256="bb", effective_from=f"{TODAY}T09:00:00-04:00",
           reason="review", content=json.dumps(watchlist))
    insert(conn, "versions", kind="config", name="shadow", version=None, sha256="", effective_from=f"{TODAY}T17:30:00-04:00",
           reason="review", content=None)
    conn.commit()
    conn.close()
    # the shadow book has its own file
    conn = create_db(folder / "shadow" / "trader.db")
    daily(conn, "dry_run", [0.02, 0.01])
    conn.commit()
    conn.close()


def build_sentiment(folder):
    conn = create_db(folder / "trader.db")
    daily(conn, "paper", [0.004, 0.003, -0.002, 0.001, 0.0, 0.002, 0.001, 0.003])
    daily(conn, "replay", [0.5])  # not shown unless the books say so
    for i, (sym, pnl, r) in enumerate([("NKE", 200, 1.0), ("NKE", 150, 0.75), ("NKE", -50, -0.25), ("TSLA", -300, -1.0)]):
        insert(conn, "trades", book="paper", date=DAYS[i + 3].isoformat(), symbol=sym, side="long" if i < 3 else "short",
               qty=50, entry_date=DAYS[i].isoformat(), entry_time=f"{DAYS[i]}T09:45:00-04:00", entry_price=100,
               exit_time=f"{DAYS[i + 3]}T09:45:00-04:00", exit_price=104, exit_reason="time", pnl_usd=pnl, pnl_r=r,
               hold_days=3, strategy="sentiment_swing", strategy_version="1", config_version="2",
               extra=json.dumps({"score": 2, "confidence": 0.9, "prompt_sha256": "abc", "reason": "model text"}))
    insert(conn, "trades", book="replay", date=DAYS[1].isoformat(), symbol="ZZZ", side="long", qty=1, entry_date="x",
           entry_time="x", pnl_usd=1e6, strategy="sentiment_swing")
    for day in DAYS:
        insert(conn, "bars", symbol="SPY", timeframe="1Day", ts=day.isoformat(), close=500 + DAYS.index(day))
    insert(conn, "runs", job="snapshot", started_at=f"{TODAY}T10:02:00-04:00", ended_at=f"{TODAY}T10:02:04-04:00",
           status="ok", exit_code=0)
    insert(conn, "runs", job="score", started_at=f"{TODAY}T06:30:00-04:00", status="lost")
    conn.commit()
    conn.close()
    scores = folder / "scores"
    scores.mkdir()
    for day, entries in ((DAYS[-2], {"NKE": (2, 0.9, 3), "SPY": (-1, 0.8, 1)}), (TODAY, {"NKE": (1, 0.5, 2), "SPY": (-2, 0.9, 4)})):
        rec = {"date": day.isoformat(), "prompt_sha256": "abc",
               "scores": {s: {"score": sc, "confidence": c, "reason": "model text", "article_ids": [str(i) for i in range(n)]}
                          for s, (sc, c, n) in entries.items()},
               "desk": {"ideas": [{"kind": "theme", "legs": [{"symbol": "AMD", "side": "long"}], "conviction": 2,
                                   "confidence": 0.7, "reason": "model text"}]}}
        (scores / f"{day}.json").write_text(json.dumps(rec))
    (scores / "2026-10-15.json.tmp").write_text("{broken")


@pytest.fixture
def bots(tmp_path):
    build_quant(tmp_path / "quant")
    build_sentiment(tmp_path / "sentiment")
    return tmp_path


def write_settings(tmp_path, **override):
    raw = {"bots": {"quant": {"data_dir": str(tmp_path / "quant")},
                    "sentiment": {"data_dir": str(tmp_path / "sentiment")}}} | override
    path = tmp_path / "desk.json"
    path.write_text(json.dumps(raw))
    return config.load(path)


@pytest.fixture
def settings(bots):
    return write_settings(bots)


@pytest.fixture
def client(settings):
    return TestClient(app_mod.create_app(settings))


@pytest.fixture(autouse=True)
def fixed_day(monkeypatch):
    from desk import views

    monkeypatch.setattr(views, "current_day", lambda: TODAY)
