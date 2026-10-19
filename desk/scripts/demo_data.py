"""Made-up data for both bots, for working on the dashboard without the Pi.

    .venv/bin/python scripts/demo_data.py /tmp/desk-demo
    DESK_CONFIG=/tmp/desk-demo/desk.json .venv/bin/python -m desk.app

It writes <dir>/quant/ and <dir>/sentiment/ in the bots' layouts (with the schema copied in
tests/schema/) and a desk.json pointing at them, with every section public except the error text, so each
page has something to show. Everything is random: none of it is a real result.
"""
import csv
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
SENTIMENT_SYMBOLS = ["NKE", "SBUX", "TSLA", "DIS", "NFLX", "WMT", "COST", "HD", "KO", "UBER", "AMD", "LULU", "NVDA", "META"]
INTRADAY_DAYS = 5  # the last days get minute (quant) or 5-minute (sentiment) snapshots, for the replay


def trading_days(end, n):
    days, d = [], end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return days[::-1]


def create(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    for f in sorted(SCHEMA.glob("*.sql")):
        conn.executescript(f.read_text())
    conn.execute("INSERT INTO meta VALUES ('schema_version', '3')")
    return conn


def put(conn, table, **row):
    conn.execute(f"INSERT OR REPLACE INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                 list(row.values()))


def at(day, minutes_after_open):
    t = datetime(day.year, day.month, day.day, 9, 30, tzinfo=ET) + timedelta(minutes=minutes_after_open)
    return t.isoformat()


def iso(day, hh, mm):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=ET).isoformat()


def quant_memory(n_days, rng):
    """memory.md as it might look after n_days of reviews."""
    ladder = [("M-1", "Breakouts after 10:30 rarely reach the target", "entry_minute_after_open >= 60", 4, 18, None),
              ("M-2", "Narrow opening ranges give the best R", "range_pct < 0.5", 8, 30, 44),
              ("M-3", "Short entries on hard-to-borrow names get skipped", None, 12, None, None),
              ("M-4", "Mondays are weaker than other days", "weekday == 0", 15, None, 35),
              ("M-5", "Gap-up days fade before the breakout", "gap_pct > 1", 40, 58, None)]
    active, retired = [], []
    for eid, title, query, born, supported, retire in ladder:
        if n_days < born:
            continue
        status = "supported" if supported and n_days >= supported else "hypothesis"
        if eid == "M-2" and n_days >= 44:
            status = "confirmed"
        lines = [f"### {eid}: {title}", f"- Status: {status}",
                 f"- Since: day {born} | Last reviewed: day {n_days}", "- Scope: opening_range_breakout"]
        if query:
            lines.append(f"- Query: `{query}`")
        lines += [f"- Evidence: {rng.randint(8, 60)} trades over {max(1, n_days - born)} days, avg R {rng.uniform(-0.4, 0.6):+.2f}",
                  "- Implication: keep watching; no config change on this alone"]
        if retire and n_days >= retire:
            lines.insert(1, f"- Retired: day {retire}, the effect went away on newer trades")
            retired.append("\n".join(lines))
        else:
            active.append("\n".join(lines))
    return ("# Trading memory\n\n## Active\n\n" + ("\n\n".join(active) or "_No entries yet._") +
            "\n\n## Retired\n\n" + ("\n\n".join(retired) or "_None._") + "\n")


def journal(bot, day, rng, pnl, trades):
    mood = "a good day" if pnl > 0 else "a losing day" if pnl < 0 else "a flat day"
    lines = [f"# {day.isoformat()}", "", "## Market",
             rng.choice(["Indexes drifted higher on light volume.", "A risk-off open that recovered by noon.",
                         "Tech led, small caps lagged.", "Choppy, no clear direction after the first hour."]),
             "", "## Result", f"{mood.capitalize()}: {trades} {'trade' if trades == 1 else 'trades'}, P&L {pnl:+,.0f}.", "",
             "## Went well", f"- {rng.choice(['Entries filled close to the plan.', 'Stops held their levels.', 'No skipped signals.'])}",
             "", "## Went wrong", f"- {rng.choice(['One stop was hit within minutes.', 'Nothing notable.', 'A late entry chased the move.'])}",
             "", "## Luck or logic",
             rng.choice(["Mostly the strategy behaving as designed.", "Too few trades to say.", "Some luck on the exit."])]
    if bot == "sentiment":
        lines += ["", "## Memory updates", "- Added evidence to M-1."]
    return "\n".join(lines) + "\n"


def minute_bars(conn, sym, day, t, rng):
    """A session of 1-minute bars that passes through the trade's entry and exit prices."""
    first = t["price"] * (1 + rng.gauss(0, 0.002))
    prev = first
    for m in range(0, 375):
        if m <= t["entry_m"]:
            base = first + (t["price"] - first) * m / max(t["entry_m"], 1)
        elif m <= t["exit_m"]:
            base = t["price"] + (t["exit_price"] - t["price"]) * (m - t["entry_m"]) / (t["exit_m"] - t["entry_m"])
        else:
            base = prev * (1 + rng.gauss(0, 0.0006))
        close = base * (1 + rng.gauss(0, 0.0007)) if m not in (t["entry_m"], t["exit_m"]) else base
        wick = abs(rng.gauss(0, 0.0006)) * base
        put(conn, "bars", symbol=sym, timeframe="1Min", ts=at(day, m), open=round(prev, 2), high=round(max(prev, close) + wick, 2),
            low=round(min(prev, close) - wick, 2), close=round(close, 2), volume=rng.randint(2, 40) * 1000)
        prev = close


def quant(folder, days, rng):
    conn = create(folder / "trader.db")
    equity, order_n = 100_000.0, 0
    intraday = set(days[-INTRADAY_DAYS:])
    for i, day in enumerate(days):
        start, pnl_day, n, day_trades = equity, 0.0, 0, []
        for sym in rng.sample(QUANT_SYMBOLS, rng.randint(0, 3)):
            side = rng.choice(["long", "short"])
            r = rng.choice([2.0, 2.0, -1.0, -1.0, -1.0, round(rng.uniform(-0.8, 1.5), 2)])
            risk = equity * 0.0025
            price = round(rng.uniform(50, 500), 2)
            qty = max(1, int(risk / (price * 0.006)))
            pnl = round(r * price * 0.006 * qty, 2)
            entry_m, exit_m = rng.randint(15, 90), rng.randint(100, 330)
            sign = 1 if side == "long" else -1
            exit_price = round(price * (1 + sign * r * 0.006), 2)
            reason = "tp" if r >= 2 else "sl" if r <= -1 else "time"
            minute = entry_m
            t = {"symbol": sym, "side": side, "qty": qty, "price": price, "exit_price": exit_price, "entry_m": entry_m,
                 "exit_m": exit_m, "pnl": pnl, "reason": reason}
            day_trades.append(t)
            minute_bars(conn, sym, day, t, rng)
            put(conn, "trades", book="paper", date=day.isoformat(), symbol=sym, side=side, qty=qty,
                entry_date=day.isoformat(), entry_time=at(day, entry_m), entry_price=price, exit_time=at(day, exit_m),
                exit_price=exit_price, exit_reason=reason, pnl_usd=pnl, pnl_r=r, pnl_pct=round(r * 0.6, 4),
                strategy="opening_range_breakout", strategy_version="1" if i < len(days) * 0.6 else "2",
                config_version=str(3 + i // 20),
                extra=json.dumps({"entry_minute_after_open": minute, "range_pct": round(rng.uniform(0.2, 1.4), 2)}))
            put(conn, "order_events", book="paper", ts=at(day, entry_m), date=day.isoformat(), event="submit", symbol=sym,
                side=side, order_type="bracket")
            order_n += 1
            parent = f"q{order_n}"
            stop = round(price * (1 - sign * 0.006), 2)
            target = round(price * (1 + sign * 0.012), 2)
            put(conn, "orders", book="paper", order_id=parent, symbol=sym, side="buy" if side == "long" else "sell", qty=qty,
                order_type="market", status="filled", filled_qty=qty, filled_avg_price=price, filled_at=at(day, entry_m),
                created_at=at(day, entry_m), updated_at=at(day, entry_m))
            for leg, kind, px, hit in (("s", "stop", stop, reason == "sl"), ("t", "limit", target, reason == "tp")):
                put(conn, "orders", book="paper", order_id=parent + leg, parent_order_id=parent, symbol=sym,
                    side="sell" if side == "long" else "buy", qty=qty, order_type=kind,
                    status="filled" if hit else "canceled", filled_qty=qty if hit else 0,
                    filled_avg_price=exit_price if hit else None, filled_at=at(day, exit_m) if hit else None,
                    stop_price=px if kind == "stop" else None, limit_price=px if kind == "limit" else None,
                    created_at=at(day, entry_m), updated_at=at(day, exit_m))
            if reason == "time":
                put(conn, "orders", book="paper", order_id=parent + "x", symbol=sym,
                    side="sell" if side == "long" else "buy", qty=qty, order_type="market", status="filled",
                    filled_qty=qty, filled_avg_price=exit_price, filled_at=at(day, exit_m), created_at=at(day, exit_m),
                    updated_at=at(day, exit_m))
            pnl_day += pnl
            n += 1
        for sym in rng.sample(QUANT_SYMBOLS, 2):
            put(conn, "order_events", book="paper", ts=at(day, 20), date=day.isoformat(), event="skip", symbol=sym,
                side=rng.choice(["long", "short"]), reason="not easy to borrow")
        if day in intraday:
            e, realized = start, 0.0
            for m in range(0, 376):
                unreal, held = 0.0, 0
                for t in day_trades:
                    if t["entry_m"] <= m < t["exit_m"]:
                        frac = (m - t["entry_m"]) / (t["exit_m"] - t["entry_m"])
                        px = round(t["price"] + (t["exit_price"] - t["price"]) * frac + rng.gauss(0, t["price"] * 0.0015), 2)
                        sign = 1 if t["side"] == "long" else -1
                        u = round((px - t["price"]) * t["qty"] * sign, 2)
                        unreal += u
                        held += 1
                        put(conn, "position_snapshots", book="paper", ts=at(day, m), symbol=t["symbol"],
                            qty=t["qty"] * sign, avg_entry_price=t["price"], price=px, market_value=round(px * t["qty"] * sign, 2),
                            unrealized_pnl=u)
                    elif m == t["exit_m"]:
                        realized += t["pnl"]
                e = start + realized + unreal
                put(conn, "equity_snapshots", book="paper", ts=at(day, m), equity=round(e, 2), long_value=0, short_value=0,
                    positions=held)
        equity = round(equity + pnl_day, 2)
        put(conn, "daily_summary", book="paper", date=day.isoformat(), trades=n, gross_pnl=round(pnl_day, 2),
            equity_start=round(start, 2), equity_end=equity, halted=0)
        if i >= len(days) - 20:
            (folder / "journal").mkdir(exist_ok=True)
            (folder / "journal" / f"{day}.md").write_text(journal("quant", day, rng, pnl_day, n))
    today = days[-1]
    jobs = [("trader", 9, 0, 6.8), ("backup", 3, 0, 0.01), ("purge", 2, 50, 0.01)]
    for day in days[-10:]:
        for job, hh, mm, hours in jobs:
            status = "failed" if rng.random() < 0.05 else "ok"
            put(conn, "runs", job=job, started_at=iso(day, hh, mm),
                ended_at=(datetime.fromisoformat(iso(day, hh, mm)) + timedelta(hours=hours)).isoformat(),
                status=status, exit_code=0 if status == "ok" else 1, git_sha="3f9c2ab")
    changelog = ["# Changelog", ""]
    (folder / "reports").mkdir(exist_ok=True)
    for i, day in enumerate(days[-30:]):
        failed = i == 22
        put(conn, "runs", job="review", started_at=iso(day, 17, 0), ended_at=iso(day, 17, 20 + i % 15),
            status="ok", exit_code=0, git_sha="3f9c2ab")
        (folder / "reports" / f"review_{day}.log").write_text(
            "(made-up review output)\n" + ("GUARD: 2 problem(s)\n=== guard failed; changes reverted\n" if failed
                                           else "GUARD: ok\n=== guard passed; changes kept\n"))
        if failed:
            changelog += [f"## {day} (guard)", "", "The automated guard rejected this review's changes and reverted them.", ""]
        elif i % 4 == 0:
            changelog += [f"## {day}", "", rng.choice([
                "- No config change: the evidence for M-1 is still under the threshold.",
                "- Swapped AMD onto the watchlist for IWM (higher opening-range volume over 20 days).",
                "- Tightened latest_entry to 10:15. Evidence: M-1, 31 trades since registration, avg R -0.42."]), ""]
    (folder / "changelog.md").write_text("\n".join(changelog) + "\n")
    put(conn, "runs", job="deploy", started_at=iso(days[-3], 18, 5), ended_at=iso(days[-3], 18, 6), status="ok", exit_code=0,
        git_sha="3f9c2ab")
    watch = {"symbols": [{"symbol": s, "added": (today - timedelta(days=2 if s == "AMD" else 60)).isoformat()} for s in QUANT_SYMBOLS]}
    for i, day in enumerate(days[::20]):
        cfg = {"version": 3 + i, "risk_per_trade_pct": round(0.25 + 0.05 * i, 2), "max_trades_per_day": 3 + (i % 2),
               "range_minutes": 15, "force_exit": "15:45", "latest_entry": "10:30" if i < 2 else "10:15"}
        put(conn, "versions", kind="config", name="", version=str(3 + i), sha256=f"c{i}", effective_from=iso(day, 17, 40),
            reason="review" if i else "deploy", content=json.dumps(cfg, indent=2))
    strategy = 'VERSION = {v}\n\ndef signal(bars, cfg):\n    high, low = opening_range(bars, cfg["range_minutes"])\n{body}'
    put(conn, "versions", kind="strategy", name="opening_range_breakout", version="1", sha256="s1",
        effective_from=iso(days[0], 9, 0), reason="deploy",
        content=strategy.format(v=1, body="    return breakout(bars, high, low)\n"))
    put(conn, "versions", kind="strategy", name="opening_range_breakout", version="2", sha256="s2",
        effective_from=iso(days[int(len(days) * 0.6)], 17, 40), reason="review",
        content=strategy.format(v=2, body="    if range_pct(high, low) > cfg['max_range_pct']:\n        return None\n"
                                           "    return breakout(bars, high, low)\n"))
    put(conn, "versions", kind="watchlist", name="", version="w1", sha256="w1", effective_from=iso(today, 17, 30),
        reason="review", content=json.dumps(watch))
    for i, day in enumerate(days):
        if i % 4 == 3:
            put(conn, "versions", kind="memory", name="", version=None, sha256=f"m{i}", effective_from=iso(day, 17, 35),
                reason="review", content=quant_memory(i, rng))
    conn.commit()
    conn.close()


def sentiment_memory(n_days):
    ladder = [("M-1", "Strong earnings beats keep drifting for 3 to 5 days", "scores: score >= 2 and source contains 'earnings'",
               "scoring", 3, 20, 45, None),
              ("M-2", "Analyst downgrades are already priced by the open", "scores: score <= -1 and reason contains 'downgrade'",
               "scoring", 10, 34, None, None),
              ("M-3", "Low-confidence scores add noise", "scores: confidence < 0.5", "scoring", 12, None, None, 30),
              ("M-4", "Desk ideas with conviction 3 beat single-name scores", "trades: conviction >= 3", "desk", 25, 50, None, None),
              ("M-5", "Entries skipped after a gap rarely recover", None, "trading", 40, None, None, None),
              ("M-6", "Fridays' scores carry over badly into Monday", "scores: weekday == 4", "scoring", 52, None, None, None)]
    out = []
    for eid, title, query, scope, born, sup, conf, retire in ladder:
        if n_days < born:
            continue
        status = "hypothesis"
        if sup and n_days >= sup:
            status = "supported"
        if conf and n_days >= conf:
            status = "confirmed"
        if retire and n_days >= retire:
            status = "retired"
        out.append({"id": eid, "title": title, "status": status, "scope": scope, "since": f"day {born}",
                    "last_reviewed": f"day {n_days}", "query": query,
                    "evidence": f"{30 + 4 * (n_days - born)} outcomes after registration", "implication": "",
                    "retired_reason": "contradicted by newer outcomes" if status == "retired" else None})
    return out


UNIVERSE = SENTIMENT_SYMBOLS + ["SPY", "QQQ", "AAPL", "MSFT", "AMZN"]
HEADLINES = ["{s} shares move after analyst note", "{s} beats estimates, raises guidance", "{s} faces regulatory questions",
             "What to watch in {s} this week", "{s} announces buyback", "{s} cut to neutral at a large bank",
             "Options traders pile into {s}", "{s} supplier warns on demand"]


def daily_bars(symbols, days, rng):
    """{symbol: {day: (open, high, low, close)}}, a random walk per symbol."""
    out = {}
    for sym in symbols:
        p, bars = rng.uniform(30, 400), {}
        for d in days:
            o = p * (1 + rng.gauss(0, 0.004))
            c = o * (1 + rng.gauss(0.0005, 0.015))
            bars[d] = (round(o, 2), round(max(o, c) * (1 + abs(rng.gauss(0, 0.006))), 2),
                       round(min(o, c) * (1 - abs(rng.gauss(0, 0.006))), 2), round(c, 2))
            p = c
        out[sym] = bars
    return out


def score_records(days, bars, rng):
    """Score files for the last 30 days. Scores lean the way the next 5 days went, a little more often than not."""
    out = {}
    for i, day in enumerate(days[-30:]):
        k = days.index(day)
        scores = {}
        for sym in rng.sample(UNIVERSE, 10):
            ahead = bars[sym][days[min(k + 5, len(days) - 1)]][0] / bars[sym][day][0] - 1
            sign = (1 if ahead > 0 else -1) * (1 if rng.random() < 0.58 else -1)
            mag = rng.choice([0, 1, 1, 2, 2, 3])
            scores[sym] = {"score": sign * mag, "confidence": round(rng.uniform(0.4, 0.95), 2),
                           "article_ids": [f"{day}-{sym}-{n}" for n in range(rng.randint(0, 6))]}
        ideas = []
        for _ in range(rng.randint(0, 2)):
            kind = rng.choice(["theme", "momentum", "pair"])
            a, b = rng.sample(SENTIMENT_SYMBOLS, 2)
            legs = [{"symbol": a, "side": "long"}] + ([{"symbol": b, "side": "short"}] if kind == "pair" else [])
            ideas.append({"kind": kind, "legs": legs, "conviction": rng.randint(1, 3), "confidence": round(rng.uniform(0.5, 0.9), 2)})
        out[day] = {"date": day.isoformat(), "scores": scores, "desk": {"ideas": ideas}}
    return out


def sentiment(folder, days, rng):
    conn = create(folder / "trader.db")
    bars = daily_bars(UNIVERSE, days, rng)
    records = score_records(days, bars, rng)
    equity, open_trades, order_n, closed_all = 100_000.0, [], 0, []
    history_days = days[:-INTRADAY_DAYS]
    for i, day in enumerate(history_days):
        start = equity
        held_value = sum(rng.gauss(0.0004, 0.006) * 5000 for _ in open_trades)
        realized, n_closed = 0.0, 0
        for t in [t for t in open_trades if (day - t["entry"]).days >= t["hold"]]:
            sym, sign = t["symbol"], 1 if t["side"] == "long" else -1
            entry, exit_ = bars[sym][t["entry"]][0], bars[sym][day][0]
            r = sign * (exit_ - entry) / (entry * 0.05)
            reason = "time"
            if r < -1:
                r, exit_, reason = -1.0, round(entry * (1 - sign * 0.05), 2), "sl"
            qty = max(1, round(250 / (entry * 0.05)))
            pnl = round(r * entry * 0.05 * qty, 2)
            realized += pnl
            n_closed += 1
            closed_all.append(t | {"r": r, "pnl": pnl})
            put(conn, "trades", book="paper", date=day.isoformat(), symbol=sym, side=t["side"], qty=qty,
                entry_date=t["entry"].isoformat(), entry_time=iso(t["entry"], 9, 45), entry_price=entry,
                exit_time=iso(day, 9, 45), exit_price=exit_, exit_reason=reason, stop_price=round(entry * (1 - sign * 0.05), 2),
                pnl_usd=pnl, pnl_r=round(r, 4), pnl_pct=round(sign * (exit_ / entry - 1) * 100, 4),
                hold_days=(day - t["entry"]).days, strategy=t["strategy"], strategy_version="1",
                config_version="2" if i < 40 else "3",
                extra=json.dumps({"score": t["score"], "confidence": t["confidence"]} if t["score"] is not None else {}))
            open_trades.remove(t)
        rec = records.get(day)
        held = {t["symbol"] for t in open_trades}
        if rec:
            strong = [(s, e) for s, e in rec["scores"].items() if abs(e["score"]) >= 2 and s in SENTIMENT_SYMBOLS and s not in held]
            for sym, e in strong[:2]:
                open_trades.append({"symbol": sym, "side": "long" if e["score"] > 0 else "short", "entry": day,
                                    "hold": rng.randint(3, 7), "strategy": "sentiment_swing", "score": e["score"],
                                    "confidence": e["confidence"]})
                held.add(sym)
            for idea in rec["desk"]["ideas"]:
                if rng.random() < 0.6 and not {leg["symbol"] for leg in idea["legs"]} & held:
                    for leg in idea["legs"]:
                        open_trades.append({"symbol": leg["symbol"], "side": leg["side"], "entry": day, "hold": rng.randint(3, 7),
                                            "strategy": idea["kind"], "score": None, "confidence": None})
                        held.add(leg["symbol"])
        elif rng.random() < 0.6:
            sym = rng.choice([s for s in SENTIMENT_SYMBOLS if s not in held])
            sc = rng.choice([2, 3, -2])
            open_trades.append({"symbol": sym, "side": "long" if sc > 0 else "short", "entry": day, "hold": rng.randint(3, 7),
                                "strategy": "sentiment_swing", "score": sc, "confidence": round(rng.uniform(0.6, 0.9), 2)})
        equity = round(equity + realized + held_value * 0.2, 2)
        put(conn, "daily_summary", book="paper", date=day.isoformat(), trades=n_closed,
            gross_pnl=round(realized, 2), equity_start=round(start, 2), equity_end=equity, halted=0)
    # what it holds now: the positions still open, with prices walking through the last days
    held = {t["symbol"] for t in open_trades}
    for sym in rng.sample([s for s in SENTIMENT_SYMBOLS if s not in held], max(0, 5 - len(open_trades))):
        open_trades.append({"symbol": sym, "side": rng.choice(["long", "long", "short"]), "hold": 5,
                            "entry": rng.choice(history_days[-6:] + days[-INTRADAY_DAYS:-1]), "strategy": "sentiment_swing",
                            "score": None, "confidence": None})
    last_close = {s: bars[s][history_days[-1]][3] for s in UNIVERSE}
    holdings = []
    for t in open_trades[:6]:
        sign = 1 if t["side"] == "long" else -1
        price = bars[t["symbol"]][t["entry"]][0] if t["entry"] in history_days else None
        holdings.append(t | {"price": price, "last": last_close[t["symbol"]], "sign": sign,
                             "score": t["score"] if t["score"] is not None else rng.choice([2, 3]) * sign,
                             "confidence": t["confidence"] or round(rng.uniform(0.6, 0.95), 2)})
    path = {}
    for day in days[-INTRADAY_DAYS:]:
        start = equity
        times = [at(day, m) for m in range(2, 390, 5)] + [iso(day, 16, 0)]
        open_value = {}
        for ts in times:
            for h in holdings:
                h["last"] = round(h["last"] * (1 + rng.gauss(0.0002 * h["sign"], 0.002)), 2)
                path.setdefault((h["symbol"], day), []).append(h["last"])
                if h["entry"] <= day and h["price"] is None:  # entered today, at the first snapshot
                    h["price"] = h["last"]
                open_value.setdefault(h["symbol"], h["last"])
            live = [h for h in holdings if h["entry"] <= day]
            for h in live:
                h.setdefault("qty", max(1, int(5000 / h["price"])))
            e = start + sum((h["last"] - open_value[h["symbol"]]) * h["qty"] * h["sign"] for h in live)
            put(conn, "equity_snapshots", book="paper", ts=ts, equity=round(e, 2),
                long_value=round(sum(h["last"] * h["qty"] for h in live if h["sign"] > 0), 2),
                short_value=round(-sum(h["last"] * h["qty"] for h in live if h["sign"] < 0), 2), positions=len(live))
            for h in live:
                put(conn, "position_snapshots", book="paper", ts=ts, symbol=h["symbol"], qty=h["qty"] * h["sign"],
                    avg_entry_price=h["price"], price=h["last"], market_value=round(h["last"] * h["qty"] * h["sign"], 2),
                    unrealized_pnl=round((h["last"] - h["price"]) * h["qty"] * h["sign"], 2))
        equity = round(e, 2)
        put(conn, "daily_summary", book="paper", date=day.isoformat(), trades=0, gross_pnl=0,
            equity_start=round(start, 2), equity_end=equity, halted=0)
    for (sym, day), ps in path.items():  # the daily bars of held symbols follow their snapshots
        bars[sym][day] = (ps[0], max(ps), min(ps), ps[-1])
    for h in holdings:
        h["stop"] = round(h["price"] * (1 - h["sign"] * 0.05), 2)
        order_n += 1
        h["order"] = f"s{order_n}"
        put(conn, "orders", book="paper", order_id=h["order"], symbol=h["symbol"], side="buy" if h["sign"] > 0 else "sell",
            qty=h["qty"], order_type="market", status="filled", filled_qty=h["qty"], filled_avg_price=h["price"],
            filled_at=iso(h["entry"], 9, 45), created_at=iso(h["entry"], 9, 45), updated_at=iso(h["entry"], 9, 45))
        put(conn, "orders", book="paper", order_id=h["order"] + "s", parent_order_id=h["order"], symbol=h["symbol"],
            side="sell" if h["sign"] > 0 else "buy", qty=h["qty"], order_type="stop", status="new", stop_price=h["stop"],
            created_at=iso(h["entry"], 9, 45), updated_at=iso(h["entry"], 9, 45))
    book = folder / "books" / "paper"
    book.mkdir(parents=True, exist_ok=True)
    with open(book / "positions.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["symbol", "side", "qty", "status", "entry_date", "entry_time", "entry_price",
                                          "stop_price", "score", "confidence", "strategy", "reason", "pair"])
        w.writeheader()
        for h in holdings:
            w.writerow({"symbol": h["symbol"], "side": h["side"], "qty": h["qty"], "status": "open",
                        "entry_date": h["entry"].isoformat(), "entry_time": iso(h["entry"], 9, 45), "entry_price": h["price"],
                        "stop_price": h["stop"], "score": h["score"], "confidence": h["confidence"],
                        "strategy": h["strategy"], "reason": "(made up)", "pair": ""})
    for sym, by_day in bars.items():
        for day, (o, hi, lo, c) in by_day.items():
            put(conn, "bars", symbol=sym, timeframe="1Day", ts=day.isoformat(), open=o, high=hi, low=lo, close=c,
                volume=rng.randint(1, 30) * 1e6)
    for day in days[-10:]:
        for job, hh, mm in (("news", 7, 0), ("score", 6, 30), ("trader-run", 9, 45), ("trader-close", 16, 15),
                            ("snapshot", 10, 2)):
            status = "lost" if job == "score" and day == days[-6] else "ok"
            put(conn, "runs", job=job, started_at=iso(day, hh, mm), ended_at=None if status == "lost" else iso(day, hh, mm + 1),
                status=status, exit_code=None if status == "lost" else 0, git_sha="91be04d")
    for day in days[-30:]:
        put(conn, "runs", job="review", started_at=iso(day, 19, 45), ended_at=iso(day, 19, 48), status="ok", exit_code=0,
            git_sha="91be04d")
    put(conn, "versions", kind="prompt", name="scoring", version="a" * 64, sha256="p1", effective_from=iso(days[0], 6, 30),
        reason="score", content="Score each symbol from -3 to 3 on today's news...")
    for i, day in enumerate(days[::40]):
        cfg = {"version": 2 + i, "entry": {"min_score": 2, "min_confidence": 0.6 + 0.1 * i},
               "exit": {"max_hold_days": 5, "stop_atr": 2.0}}
        put(conn, "versions", kind="config", name="", version=str(2 + i), sha256=f"sc{i}", effective_from=iso(day, 6, 0),
            reason="config upgrade", content=json.dumps(cfg, indent=2))
    for i, day in enumerate(days):
        if i % 3 == 2:
            put(conn, "versions", kind="memory", name="", version=f"{i:064x}", sha256=f"m{i}",
                effective_from=iso(day, 19, 50), reason="memory save",
                content=json.dumps(sentiment_memory(i), indent=1, sort_keys=True))
        if i >= len(days) - 20:
            (folder / "journal").mkdir(exist_ok=True)
            (folder / "journal" / f"{day}.md").write_text(journal("sentiment", day, rng, rng.gauss(50, 400), rng.randint(0, 3)))
    conn.commit()
    conn.close()
    scores = folder / "scores"
    scores.mkdir(exist_ok=True)
    for f in scores.glob("*.json"):
        f.unlink()
    for day, rec in records.items():
        (scores / f"{day}.json").write_text(json.dumps(rec))
    news = folder / "news"
    news.mkdir(exist_ok=True)
    for f in news.glob("*.jsonl"):
        f.unlink()
    for day in days[-30:]:
        with open(news / f"{day}.jsonl", "w") as f:
            for n in range(rng.randint(15, 50)):
                sym = rng.choice(UNIVERSE)
                f.write(json.dumps({"id": f"{day}-{n}", "published_at": f"{day}T{rng.randint(11, 21):02d}:{rng.randint(0, 59):02d}:00Z",
                                    "symbols": [sym], "headline": rng.choice(HEADLINES).format(s=sym),
                                    "summary": "(made up)", "source": rng.choice(["benzinga", "reuters", "marketwatch"]),
                                    "url": f"https://example.com/news/{day}-{n}"}) + "\n")
    life = lifetime(closed_all, records, bars, days)
    life["attention"] = attention(folder, days, rng)
    (folder / "lifetime.json").write_text(json.dumps(life, indent=1))


def attention(folder, days, rng):
    """The sentiment bot's attention rows, discovery lists and their calibration (ticket 009), made up."""
    conn = sqlite3.connect(folder / "trader.db")
    conn.execute("CREATE TABLE IF NOT EXISTS attention (date TEXT, symbol TEXT, in_universe INTEGER, volume_z REAL, rvol REAL, "
                 "news_z REAL, articles INTEGER, wiki_z REAL, page_views INTEGER, reddit_z REAL, mentions INTEGER, "
                 "breadth INTEGER, acceleration REAL, close REAL, ret_1d REAL, PRIMARY KEY (date, symbol))")
    for day in days[-20:]:
        for sym in UNIVERSE:
            zs = {k: round(rng.gauss(0.2, 1.1), 2) for k in ("volume_z", "news_z", "wiki_z", "reddit_z")}
            put(conn, "attention", date=day.isoformat(), symbol=sym, in_universe=1, **zs,
                mentions=max(0, int(rng.gauss(20, 15))), breadth=sum(z >= 2 for z in zs.values()))
    conn.commit()
    conn.close()
    (folder / "discovery").mkdir(exist_ok=True)
    picks = [("IRON", ["volume +4.8σ", "news +2.1σ"], 77.91, 37.1, 10.7), ("CLF", ["volume +2.4σ", "news +2.7σ"], 14.04, 6.2, 3.1),
             ("SOUN", ["volume +3.1σ", "reddit +2.9σ", "wikipedia +2.2σ"], 9.85, 12.4, 4.4)]
    history = []
    for i, day in enumerate(days[-12:]):
        names = [{"symbol": s, "reasons": r, "close": c, "ret_1d": m, "rvol": v, "breadth": len(r)}
                 for s, r, c, m, v in (picks if i == 11 else rng.sample(picks, rng.randint(0, 3)))]
        (folder / "discovery" / f"{day}.json").write_text(json.dumps(
            {"date": day.isoformat(), "session": days[-13 + i].isoformat(), "collect_only": True, "names": names}))
        if names and i < 9:
            history.append({"date": day.isoformat(), "names": len(names),
                            **{f"{h}d": {"n": len(names), "mean_pct": round(rng.gauss(0.3 * h, 2), 3)} for h in (1, 3, 5)}})
    breadth = {}
    for b, n, drift in (("0", 6200, 0.02), ("1", 1400, 0.05), ("2", 210, 0.25), ("3+", 40, -0.3)):
        breadth[b] = {"all": {f"{h}d": {"n": n, "mean_pct": round(drift * h + rng.gauss(0, 0.05), 3),
                                        "up_rate": round(0.5 + drift / 2, 3)} for h in (1, 3, 5)}}
    return {"by": {"breadth": breadth}, "discovery": history}


def lifetime(closed, records, bars, days, horizon=5):
    """What the sentiment bot's lifetime.json holds: calibration of the scores against the next `horizon` days, and
    trade results by score and confidence."""
    by_score = {}
    for day, rec in records.items():
        k = days.index(day)
        if k + horizon >= len(days):
            continue
        for sym, e in rec["scores"].items():
            if e["score"]:
                fwd = bars[sym][days[k + horizon]][0] / bars[sym][day][0] - 1
                by_score.setdefault(e["score"], []).append(fwd)
    calibration = {f"{s:+d}": {"n": len(f), "mean_fwd_return_pct": round(100 * sum(f) / len(f), 3),
                               "hit_rate": round(sum((x > 0) == (s > 0) for x in f) / len(f), 3)}
                   for s, f in sorted(by_score.items())}

    def groups(key):
        out = {}
        for t in closed:
            if t["score"] is not None:
                out.setdefault(key(t), []).append(t)
        return {k: {"n": len(ts), "win_rate": round(sum(t["pnl"] > 0 for t in ts) / len(ts), 3),
                    "pnl_usd": round(sum(t["pnl"] for t in ts), 2),
                    "expectancy_r": round(sum(t["r"] for t in ts) / len(ts), 3)} for k, ts in sorted(out.items())}

    return {"days": len(days), "trades": len(closed),
            "calibration": {"scores": sum(c["n"] for c in calibration.values()), "by_score": calibration},
            "by": {"score": groups(lambda t: f"{t['score']:+d}"),
                   "confidence": groups(lambda t: "0.8 and up" if t["confidence"] >= 0.8 else "below 0.8")}}


def main(argv):
    if len(argv) != 1:
        print(__doc__)
        return 2
    out = Path(argv[0]).resolve()
    rng = random.Random(7)
    days = trading_days(date.today() - timedelta(days=1 if date.today().weekday() < 5 else 0), 70)
    quant(out / "quant", days, rng)
    sentiment(out / "sentiment", days, rng)
    (out / "desk.json").write_text(json.dumps({
        "bots": {"quant": {"data_dir": str(out / "quant")}, "sentiment": {"data_dir": str(out / "sentiment")}},
        "sections": {"journal": True, "memory": True, "version_content": True, "prices": True, "headlines": True,
                     "agent": True}}, indent=2))
    print(f"demo data in {out}; run: DESK_CONFIG={out / 'desk.json'} .venv/bin/python -m desk.app")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
