"""Made-up data for both bots, for working on the dashboard without the Pi.

    .venv/bin/python scripts/demo_data.py /tmp/desk-demo
    DESK_CONFIG=/tmp/desk-demo/desk.json .venv/bin/python -m desk.app

It writes <dir>/quant/ and <dir>/sentiment/ in the bots' layouts (with the schema copied in
tests/schema/) and a desk.json pointing at them. Everything is random: none of it is a real result.
"""
import json
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SCHEMA = Path(__file__).resolve().parent.parent / "tests" / "schema"
ET = ZoneInfo("America/New_York")
QUANT_SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "AMZN", "IWM"]
SENTIMENT_SYMBOLS = ["NKE", "SBUX", "TSLA", "DIS", "NFLX", "WMT", "COST", "HD", "KO", "UBER", "AMD", "LULU"]


def trading_days(end, n):
    days, d = [], end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return days[::-1]


def create(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    for f in sorted(SCHEMA.glob("*.sql")):
        conn.executescript(f.read_text())
    conn.execute("INSERT INTO meta VALUES ('schema_version', '3')")
    return conn


def put(conn, table, **row):
    conn.execute(f"INSERT OR REPLACE INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                 list(row.values()))


def iso(day, hh, mm):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=ET).isoformat()


def quant(folder, days, rng):
    conn = create(folder / "trader.db")
    equity = 100_000.0
    for i, day in enumerate(days):
        start, pnl_day, n = equity, 0.0, 0
        for sym in rng.sample(QUANT_SYMBOLS, rng.randint(0, 3)):
            r = rng.choice([2.0, 2.0, -1.0, -1.0, -1.0, rng.uniform(-0.8, 1.5)])
            risk = equity * 0.0025
            pnl, price = round(r * risk, 2), rng.uniform(50, 500)
            minute = rng.randint(15, 90)
            put(conn, "trades", book="paper", date=day.isoformat(), symbol=sym, side=rng.choice(["long", "short"]),
                qty=int(risk / (price * 0.006)), entry_date=day.isoformat(), entry_time=iso(day, 9 + (30 + minute) // 60, (30 + minute) % 60),
                entry_price=round(price, 2), exit_time=iso(day, 11 + rng.randint(0, 3), rng.randint(0, 59)),
                exit_price=round(price * (1 + r * 0.006), 2), exit_reason="tp" if r >= 2 else "sl" if r <= -1 else "time",
                pnl_usd=pnl, pnl_r=round(r, 4), strategy="opening_range_breakout",
                strategy_version="1" if i < len(days) * 0.6 else "2", config_version=str(3 + i // 20),
                extra=json.dumps({"entry_minute_after_open": minute}))
            put(conn, "order_events", book="paper", ts=iso(day, 9, 45), date=day.isoformat(), event="submit", symbol=sym,
                side="long", order_type="bracket")
            pnl_day += pnl
            n += 1
        for sym in rng.sample(QUANT_SYMBOLS, 2):
            put(conn, "order_events", book="paper", ts=iso(day, 9, 50), date=day.isoformat(), event="skip", symbol=sym,
                side="short", reason="not easy to borrow")
        equity = round(equity + pnl_day, 2)
        put(conn, "daily_summary", book="paper", date=day.isoformat(), trades=n, gross_pnl=round(pnl_day, 2),
            equity_start=round(start, 2), equity_end=equity, halted=0)
    today = days[-1]
    e = equity - 400
    for m in range(0, 375):
        e += rng.gauss(1.5, 25)
        put(conn, "equity_snapshots", book="paper", ts=iso(today, 9 + (30 + m) // 60, (30 + m) % 60), equity=round(e, 2),
            long_value=0, short_value=0, positions=0)
    jobs = [("trader", 9, 0, 6.8), ("review", 17, 0, 0.4), ("backup", 3, 0, 0.01), ("purge", 2, 50, 0.01)]
    for day in days[-10:]:
        for job, hh, mm, hours in jobs:
            status = "failed" if rng.random() < 0.05 else "ok"
            put(conn, "runs", job=job, started_at=iso(day, hh, mm),
                ended_at=(datetime.fromisoformat(iso(day, hh, mm)) + timedelta(hours=hours)).isoformat(),
                status=status, exit_code=0 if status == "ok" else 1, git_sha="3f9c2ab")
    put(conn, "runs", job="deploy", started_at=iso(days[-3], 18, 5), ended_at=iso(days[-3], 18, 6), status="ok", exit_code=0,
        git_sha="3f9c2ab")
    watch = {"symbols": [{"symbol": s, "added": (today - timedelta(days=2 if s == "AMD" else 60)).isoformat()} for s in QUANT_SYMBOLS]}
    for i, day in enumerate(days[::20]):
        put(conn, "versions", kind="config", name="", version=str(3 + i), sha256=f"c{i}", effective_from=iso(day, 9, 0),
            reason="review" if i else "deploy", content="{}")
    put(conn, "versions", kind="strategy", name="opening_range_breakout", version="2", sha256="s2",
        effective_from=iso(days[int(len(days) * 0.6)], 17, 40), reason="review", content="VERSION = 2")
    put(conn, "versions", kind="watchlist", name="", version="w1", sha256="w1", effective_from=iso(today, 17, 30),
        reason="review", content=json.dumps(watch))
    conn.commit()
    conn.close()


def sentiment(folder, days, rng):
    conn = create(folder / "trader.db")
    equity, open_trades = 100_000.0, []
    for i, day in enumerate(days):
        start = equity
        drift = sum(rng.gauss(0.0004, 0.006) * 5000 for _ in open_trades)
        closed = [t for t in open_trades if (day - t["entry"]).days >= rng.randint(3, 7)]
        for t in closed:
            r = rng.gauss(0.25, 0.9)
            pnl = round(r * 250, 2)
            put(conn, "trades", book="paper", date=day.isoformat(), symbol=t["symbol"], side=t["side"], qty=50,
                entry_date=t["entry"].isoformat(), entry_time=iso(t["entry"], 9, 45), entry_price=100,
                exit_time=iso(day, 9, 45), exit_price=round(100 + r * 5, 2), exit_reason=rng.choice(["time", "flip", "sl"]),
                pnl_usd=pnl, pnl_r=round(r, 4), pnl_pct=round(r * 5, 4), hold_days=(day - t["entry"]).days,
                strategy="sentiment_swing", strategy_version="1", config_version="2",
                extra=json.dumps({"score": 2, "confidence": 0.8}))
            open_trades.remove(t)
        if rng.random() < 0.6:
            open_trades.append({"symbol": rng.choice(SENTIMENT_SYMBOLS), "side": rng.choice(["long", "long", "short"]), "entry": day})
        equity = round(equity + drift, 2)
        put(conn, "daily_summary", book="paper", date=day.isoformat(), trades=len(closed), gross_pnl=round(drift, 2),
            equity_start=round(start, 2), equity_end=equity, halted=0)
    spy = 560.0
    for day in days:
        spy *= 1 + rng.gauss(0.0004, 0.009)
        put(conn, "bars", symbol="SPY", timeframe="1Day", ts=day.isoformat(), close=round(spy, 2))
    today = days[-1]
    e = equity
    for m in range(2, 375, 5):
        e += rng.gauss(3, 60)
        put(conn, "equity_snapshots", book="paper", ts=iso(today, 9 + (30 + m) // 60, (30 + m) % 60), equity=round(e, 2),
            long_value=10000, short_value=-2500, positions=len(open_trades))
    for day in days[-10:]:
        for job, hh, mm in (("news", 7, 0), ("score", 6, 30), ("trader-run", 9, 45), ("trader-close", 16, 15),
                            ("snapshot", 10, 2), ("review", 19, 45)):
            status = "lost" if job == "score" and day == days[-6] else "ok"
            put(conn, "runs", job=job, started_at=iso(day, hh, mm), ended_at=None if status == "lost" else iso(day, hh, mm + 1),
                status=status, exit_code=None if status == "lost" else 0, git_sha="91be04d")
    put(conn, "versions", kind="prompt", name="scoring", version="a" * 64, sha256="p1", effective_from=iso(days[0], 6, 30),
        reason="score", content="...")
    put(conn, "versions", kind="memory", name="", version="b" * 64, sha256="m1", effective_from=iso(days[-12], 19, 50),
        reason="memory save", content="[]")
    conn.commit()
    conn.close()
    scores = folder / "scores"
    scores.mkdir(exist_ok=True)
    for day in days[-5:]:
        rec = {"date": day.isoformat(), "scores": {
            s: {"score": rng.choice([-2, -1, 0, 1, 2]), "confidence": round(rng.uniform(0.4, 0.95), 2),
                "article_ids": [str(k) for k in range(rng.randint(0, 6))]} for s in rng.sample(SENTIMENT_SYMBOLS + ["SPY", "NVDA"], 8)},
            "desk": {"ideas": [{"kind": "theme", "legs": [{"symbol": "AMD", "side": "long"}]}]}}
        (scores / f"{day}.json").write_text(json.dumps(rec))


def main(argv):
    if len(argv) != 1:
        print(__doc__)
        return 2
    out = Path(argv[0]).resolve()
    rng = random.Random(7)
    days = trading_days(date.today() - timedelta(days=1 if date.today().weekday() < 5 else 0), 70)
    quant(out / "quant", days, rng)
    sentiment(out / "sentiment", days, rng)
    (out / "desk.json").write_text(json.dumps({"bots": {"quant": {"data_dir": str(out / "quant")},
                                                        "sentiment": {"data_dir": str(out / "sentiment")}}}, indent=2))
    print(f"demo data in {out}; run: DESK_CONFIG={out / 'desk.json'} .venv/bin/python -m desk.app")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
