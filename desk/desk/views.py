"""What the API can show, one function per view. Every field is chosen here: a column a bot adds to its
database never becomes public until a view names it.

The bots write facts and the numbers they already use for learning; these functions only aggregate
them for display.
"""
import json
import math
import statistics
from datetime import date, datetime, timedelta

from . import sources

TRADE_FIELDS = ("date", "symbol", "side", "qty", "entry_date", "entry_time", "entry_price", "exit_time",
                "exit_price", "exit_reason", "pnl_usd", "pnl_r", "pnl_pct", "hold_days", "strategy",
                "strategy_version", "config_version")
TRADE_EXTRA = ("score", "confidence", "pair", "entry_minute_after_open", "range_pct")  # from trades.extra
MAX_TRADES = 20_000  # rows read for one trades query, before paging
R_BINS = [x / 2 for x in range(-6, 7)]  # histogram edges in R, -3 to +3; values beyond go in the end bins
HOT_DAYS = 5
WATCHLIST_NEW_DAYS = 7
Z95 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36, 8: 2.31, 9: 2.26, 10: 2.23, 12: 2.18,
       15: 2.13, 20: 2.09, 25: 2.06, 30: 2.04}  # two-sided 95% t critical values by degrees of freedom


def current_day():
    """Today on the Pi's clock (Eastern, like the bots'); tests replace it."""
    return date.today()


def _num(v):
    return None if v is None else round(float(v), 6)


def _bots(settings):
    return list(settings.bots)


def _paper_book(settings):
    return "paper" if "paper" in settings.books else settings.books[0]


# ---- meta

def meta(settings):
    """Which bots and books exist and which sections are public. Problems are reported as a status
    only: their text names file paths."""
    bots = []
    for name, bot in settings.bots.items():
        status = {}
        for book in settings.books:
            problems = sources.check(sources.db_path(settings, name, book))
            status[book] = "ok" if not problems else ("missing" if "missing" in problems[0] else "unreadable")
        bots.append({"name": name, "label": bot["label"], "books": status})
    return {"bots": bots, "books": list(settings.books),
            "sections": [s for s, on in settings.sections.items() if on]}


# ---- equity

def _today_start(summaries, snaps, today):
    """Equity at the start of `today`: its summary's equity_start, else the last close before it, else the
    first snapshot of the day."""
    for s in summaries:
        if s["date"] == today and s["equity_start"] is not None:
            return s["equity_start"]
    before = [s for s in summaries if s["date"] < today and s["equity_end"] is not None]
    if before:
        return before[-1]["equity_end"]
    return snaps[0]["equity"] if snaps else None


def max_drawdown_pct(values):
    peak, worst = None, 0.0
    for v in values:
        if v is None:
            continue
        peak = v if peak is None else max(peak, v)
        if peak:
            worst = min(worst, (v / peak - 1) * 100)
    return round(worst, 4)


def equity(settings):
    """Per bot and book: the daily curve (one point per daily summary), today's intraday curve, and the
    headline numbers."""
    out = []
    for bot in _bots(settings):
        for book in settings.books:
            summaries = sources.query(settings, bot, book,
                                      "SELECT date, equity_start, equity_end, gross_pnl, trades, halted "
                                      "FROM daily_summary WHERE book = ? ORDER BY date", (book,))
            last = sources.query(settings, bot, book, "SELECT MAX(ts) AS ts FROM equity_snapshots WHERE book = ?",
                                 (book,))
            latest_ts = last[0]["ts"] if last else None
            snaps = []
            if latest_ts:
                snaps = sources.query(settings, bot, book,
                                      "SELECT ts, equity FROM equity_snapshots WHERE book = ? AND substr(ts, 1, 10) = ? "
                                      "ORDER BY ts", (book, latest_ts[:10]))
            if not summaries and not snaps:
                continue
            daily = [{"date": s["date"], "equity": _num(s["equity_end"]), "pnl": _num(s["gross_pnl"]),
                      "trades": s["trades"], "halted": bool(s["halted"])} for s in summaries]
            today = latest_ts[:10] if latest_ts else summaries[-1]["date"]
            now_equity = snaps[-1]["equity"] if snaps else summaries[-1]["equity_end"]
            start_today = _today_start(summaries, snaps, today)
            first = next((s["equity_start"] or s["equity_end"] for s in summaries
                          if (s["equity_start"] or s["equity_end"]) is not None), None)
            first = first if first is not None else (snaps[0]["equity"] if snaps else None)
            curve = [d["equity"] for d in daily] + ([now_equity] if snaps and today > daily[-1]["date"] else []) \
                if daily else [now_equity]
            out.append({
                "bot": bot, "book": book, "daily": daily,
                "intraday": {"date": today, "points": [{"ts": s["ts"], "equity": _num(s["equity"])} for s in snaps]},
                "summary": {
                    "as_of": latest_ts or summaries[-1]["date"], "equity": _num(now_equity),
                    "start_equity": _num(first),
                    "total_return_pct": _pct(now_equity, first),
                    "today_pnl": _num(now_equity - start_today) if None not in (now_equity, start_today) else None,
                    "today_return_pct": _pct(now_equity, start_today),
                    "max_drawdown_pct": max_drawdown_pct(curve),
                    "days": len(daily),
                    "halted_today": any(d["halted"] for d in daily if d["date"] == today),
                },
            })
    return {"books": out}


def _pct(now, then):
    if now is None or not then:
        return None
    return round((now / then - 1) * 100, 4)


def benchmark(settings, start=None):
    """SPY daily closes from the sentiment bot's saved bars (section prices)."""
    rows = sources.query_bot_db(settings, "sentiment",
                                "SELECT ts AS date, close FROM bars WHERE symbol = 'SPY' AND timeframe = '1Day' "
                                "AND ts >= ? ORDER BY ts", ((start or date.min).isoformat(),))
    return {"symbol": "SPY", "daily": [{"date": r["date"], "close": _num(r["close"])} for r in rows]}


# ---- trades

def _trade(bot, book, row):
    t = {"bot": bot, "book": book}
    for f in TRADE_FIELDS:
        v = row[f]
        t[f] = _num(v) if f in ("qty", "entry_price", "exit_price", "pnl_usd", "pnl_r", "pnl_pct", "hold_days") else v
    extra = json.loads(row["extra"]) if row["extra"] else {}
    for k in TRADE_EXTRA:
        if k in extra:
            t[k] = extra[k]
    return t


def all_trades(settings, bot=None, start=None, end=None, symbol=None):
    """Closed trades of the shown books, newest exit first. Filters run in SQL; the rest in Python."""
    where, params = ["book = ?"], []
    if start:
        where.append("date >= ?")
        params.append(start.isoformat())
    if end:
        where.append("date <= ?")
        params.append(end.isoformat())
    if symbol:
        where.append("symbol = ?")
        params.append(symbol)
    out = []
    for b in [bot] if bot else _bots(settings):
        for book in settings.books:
            rows = sources.query(settings, b, book,
                                 f"SELECT {', '.join(TRADE_FIELDS)}, extra FROM trades WHERE {' AND '.join(where)} "
                                 f"ORDER BY date DESC, exit_time DESC LIMIT {MAX_TRADES}", (book, *params))
            out += [_trade(b, book, r) for r in rows]
    return sorted(out, key=lambda t: (t["date"], t["exit_time"] or ""), reverse=True)


def trade_stats(trades):
    pnl = [t["pnl_usd"] for t in trades if t["pnl_usd"] is not None]
    rs = [t["pnl_r"] for t in trades if t["pnl_r"] is not None]
    wins = sum(p > 0 for p in pnl)
    hist = [0] * (len(R_BINS) - 1)
    for r in rs:
        i = min(max(int((r - R_BINS[0]) // 0.5), 0), len(hist) - 1)
        hist[i] += 1
    return {
        "trades": len(trades), "wins": wins, "losses": sum(p < 0 for p in pnl),
        "win_rate": round(wins / len(pnl), 4) if pnl else None,
        "total_pnl": round(sum(pnl), 2), "expectancy_usd": round(sum(pnl) / len(pnl), 2) if pnl else None,
        "avg_r": round(sum(rs) / len(rs), 4) if rs else None,
        "r_histogram": [{"from": R_BINS[i], "to": R_BINS[i + 1], "count": c} for i, c in enumerate(hist)],
    }


def trades(settings, *, bot=None, symbol=None, strategy=None, exit_reason=None, side=None, config_version=None,
           start=None, end=None, limit=100, offset=0):
    rows = all_trades(settings, bot=bot, start=start, end=end, symbol=symbol)
    keep = [t for t in rows
            if (strategy is None or t["strategy"] == strategy) and (exit_reason is None or t["exit_reason"] == exit_reason)
            and (side is None or t["side"] == side) and (config_version is None or t["config_version"] == config_version)]
    return {"total": len(keep), "offset": offset, "trades": keep[offset:offset + limit], "stats": trade_stats(keep)}


def trade_options(settings):
    """The values the trades filters can take."""
    rows = all_trades(settings)
    return {k: sorted({t[k] for t in rows if t[k] not in (None, "")})
            for k in ("symbol", "strategy", "exit_reason", "side", "config_version")} | {"bots": _bots(settings)}


# ---- symbols

def symbol_results(settings, days=None, min_trades=3, today=None):
    """Per bot and symbol: P&L, trade count, win rate and average R over the last `days` (None: all)."""
    today = today or current_day()
    start = today - timedelta(days=days) if days else None
    groups = {}
    for t in all_trades(settings, start=start):
        groups.setdefault((t["bot"], t["symbol"]), []).append(t)
    rows = []
    for (bot, symbol), ts in groups.items():
        if len(ts) < min_trades:
            continue
        s = trade_stats(ts)
        rows.append({"bot": bot, "symbol": symbol, "trades": s["trades"], "pnl": s["total_pnl"],
                     "win_rate": s["win_rate"], "avg_r": s["avg_r"]})
    rows.sort(key=lambda r: r["pnl"], reverse=True)
    return {"days": days, "min_trades": min_trades, "symbols": rows}


def _recent_dates(settings, bot, book, n):
    rows = sources.query(settings, bot, book, "SELECT DISTINCT date FROM order_events WHERE book = ? "
                                              "ORDER BY date DESC LIMIT ?", (book, n))
    return [r["date"] for r in rows]


def hot_quant(settings, today=None):
    """Top symbols by signals fired (entries and skips) over the quant bot's last HOT_DAYS trading days."""
    book = _paper_book(settings)
    dates = _recent_dates(settings, "quant", book, HOT_DAYS)
    if not dates:
        return []
    rows = sources.query(settings, "quant", book,
                         "SELECT symbol, side, event, ts FROM order_events WHERE book = ? AND date >= ? AND symbol != '' "
                         "AND (event = 'skip' OR (event = 'submit' AND order_type = 'bracket')) ORDER BY ts",
                         (book, min(dates)))
    by = {}
    for r in rows:
        s = by.setdefault(r["symbol"], {"symbol": r["symbol"], "signals": 0, "entries": 0, "side": None})
        s["signals"] += 1
        s["entries"] += r["event"] == "submit"
        s["side"] = r["side"] or s["side"]
    new = _new_on_watchlist(settings, today or current_day())
    for s in by.values():
        s["new_on_watchlist"] = s["symbol"] in new
    return sorted(by.values(), key=lambda s: (-s["signals"], s["symbol"]))[:5]


def _new_on_watchlist(settings, today):
    """Symbols the quant watchlist says were added in the last WATCHLIST_NEW_DAYS days. Reads the latest
    watchlist version for the badge only; its content is not served."""
    rows = sources.query_bot_db(settings, "quant", "SELECT content FROM versions WHERE kind = 'watchlist' "
                                                   "ORDER BY id DESC LIMIT 1")
    if not rows or not rows[0]["content"]:
        return set()
    try:
        entries = json.loads(rows[0]["content"]).get("symbols", [])
    except (ValueError, AttributeError):
        return set()
    since = (today - timedelta(days=WATCHLIST_NEW_DAYS)).isoformat()
    return {e["symbol"] for e in entries if isinstance(e, dict) and str(e.get("added", "")) >= since}


def hot_sentiment(settings, today=None):
    """Top symbols by sum of |score| x confidence plus desk-idea mentions over the last HOT_DAYS score files,
    with the direction and the number of articles. Scores only: the reasons are not served."""
    today = today or current_day()
    records = sources.score_files(settings, today - timedelta(days=3 * HOT_DAYS))[-HOT_DAYS:]
    by = {}
    for rec in records:
        for sym, e in (rec.get("scores") or {}).items():
            try:
                score, conf = float(e["score"]), float(e["confidence"])
            except (KeyError, TypeError, ValueError):
                continue
            s = by.setdefault(sym, {"symbol": sym, "heat": 0.0, "lean": 0.0, "articles": 0, "desk_ideas": 0})
            s["heat"] += abs(score) * conf
            s["lean"] += score * conf
            s["articles"] += len(e.get("article_ids") or [])
        for idea in (rec.get("desk") or {}).get("ideas", []):
            for leg in idea.get("legs", []):
                s = by.setdefault(leg["symbol"], {"symbol": leg["symbol"], "heat": 0.0, "lean": 0.0, "articles": 0,
                                                  "desk_ideas": 0})
                s["desk_ideas"] += 1
                s["heat"] += 1
                s["lean"] += 1 if leg.get("side") == "long" else -1
    for s in by.values():
        s["direction"] = "bullish" if s["lean"] > 0 else "bearish" if s["lean"] < 0 else "neutral"
        s["heat"] = round(s["heat"], 3)
        del s["lean"]
    return sorted((s for s in by.values() if s["heat"] > 0), key=lambda s: (-s["heat"], s["symbol"]))[:5]


def disagreements(quant_hot, settings, today=None):
    """Symbols where the quant bot's latest signal and the sentiment bot's lean point opposite ways."""
    today = today or current_day()
    records = sources.score_files(settings, today - timedelta(days=3 * HOT_DAYS))[-HOT_DAYS:]
    lean = {}
    for rec in records:
        for sym, e in (rec.get("scores") or {}).items():
            try:
                lean[sym] = lean.get(sym, 0.0) + float(e["score"]) * float(e["confidence"])
            except (KeyError, TypeError, ValueError):
                continue
    out = []
    for q in quant_hot:
        side, l = q["side"], lean.get(q["symbol"], 0.0)
        if (side == "long" and l < 0) or (side == "short" and l > 0):
            out.append({"symbol": q["symbol"], "quant": side, "sentiment": "bearish" if l < 0 else "bullish"})
    return out


def hot(settings, today=None):
    """Hot right now for each bot whose section is public, and where they disagree."""
    out = {}
    quant_hot = hot_quant(settings, today) if "quant" in settings.bots else []
    out["quant"] = quant_hot
    if settings.public("scores") and "sentiment" in settings.bots:
        out["sentiment"] = hot_sentiment(settings, today)
        out["disagree"] = disagreements(quant_hot, settings, today)
    return out


# ---- compare

def daily_returns(settings, bot):
    book = _paper_book(settings)
    rows = sources.query(settings, bot, book, "SELECT date, equity_start, equity_end FROM daily_summary "
                                              "WHERE book = ? ORDER BY date", (book,))
    return {r["date"]: r["equity_end"] / r["equity_start"] - 1 for r in rows
            if r["equity_start"] and r["equity_end"] is not None}


def _t95(df):
    if df <= 0:
        return None
    known = [k for k in Z95 if k <= df]
    return Z95[max(known)] if df <= 30 else 1.96


def return_stats(returns):
    """Headline statistics of a list of daily returns (fractions)."""
    if not returns:
        return {"days": 0}
    growth, curve = 1.0, []
    for r in returns:
        growth *= 1 + r
        curve.append(growth)
    sd = statistics.stdev(returns) if len(returns) > 1 else None
    mean = statistics.fmean(returns)
    return {"days": len(returns), "total_return_pct": round((growth - 1) * 100, 4),
            "mean_daily_pct": round(mean * 100, 4),
            "volatility_annual_pct": round(sd * math.sqrt(252) * 100, 4) if sd else None,
            "sharpe": round(mean / sd * math.sqrt(252), 3) if sd else None,
            "max_drawdown_pct": max_drawdown_pct(curve),
            "worst_day_pct": round(min(returns) * 100, 4), "best_day_pct": round(max(returns) * 100, 4)}


def compare(settings, start=None, end=None):
    """Both bots' paper books over the same date range, and the daily-return difference (sentiment minus
    quant) on the days both traded, with its 95% range."""
    series = {bot: {d: r for d, r in daily_returns(settings, bot).items()
                    if (start is None or d >= start.isoformat()) and (end is None or d <= end.isoformat())}
              for bot in _bots(settings)}
    out = {"bots": {}, "curves": {}}
    for bot, s in series.items():
        days = sorted(s)
        out["bots"][bot] = return_stats([s[d] for d in days])
        growth, curve = 1.0, []
        for d in days:
            growth *= 1 + s[d]
            curve.append({"date": d, "return_pct": round((growth - 1) * 100, 4)})
        out["curves"][bot] = curve
    if len(series) == 2:
        a, b = series.get("sentiment", {}), series.get("quant", {})
        both = sorted(set(a) & set(b))
        diff = [a[d] - b[d] for d in both]
        result = {"days": len(both), "first": both[0] if both else None, "last": both[-1] if both else None}
        if len(diff) >= 2:
            mean, sd = statistics.fmean(diff), statistics.stdev(diff)
            half = _t95(len(diff) - 1) * sd / math.sqrt(len(diff))
            corr = statistics.correlation([a[d] for d in both], [b[d] for d in both]) \
                if statistics.pstdev([a[d] for d in both]) and statistics.pstdev([b[d] for d in both]) else None
            low, high = mean - half, mean + half
            result |= {"mean_diff_pct": round(mean * 100, 4), "low_pct": round(low * 100, 4),
                       "high_pct": round(high * 100, 4), "correlation": round(corr, 4) if corr is not None else None,
                       "verdict": "sentiment ahead" if low > 0 else "quant ahead" if high < 0 else "no clear winner yet"}
        else:
            result["verdict"] = "not enough overlapping days"
        out["difference"] = result
    return out


# ---- versions

def versions(settings, with_content=False):
    """Every version row per bot, newest first, and the trade results per (strategy, version, config)."""
    out = {}
    for bot in _bots(settings):
        cols = "id, kind, name, version, sha256, effective_from, reason" + (", content" if with_content else "")
        rows = sources.query_bot_db(settings, bot, f"SELECT {cols} FROM versions ORDER BY effective_from DESC, id DESC LIMIT 1000")
        changes = [{k: r[k] for k in ("kind", "name", "version", "effective_from", "reason")} |
                   {"removed": r["sha256"] == ""} | ({"content": r["content"]} if with_content else {}) for r in rows]
        groups = {}
        for t in all_trades(settings, bot=bot):
            groups.setdefault((t["strategy"], t["strategy_version"], t["config_version"]), []).append(t)
        results = []
        for (strategy, sv, cv), ts in groups.items():
            s = trade_stats(ts)
            results.append({"strategy": strategy, "strategy_version": sv, "config_version": cv,
                            "first": min(t["date"] for t in ts), "last": max(t["date"] for t in ts),
                            "trades": s["trades"], "pnl": s["total_pnl"], "win_rate": s["win_rate"],
                            "avg_r": s["avg_r"]})
        results.sort(key=lambda r: r["first"], reverse=True)
        out[bot] = {"changes": changes, "results": results}
    return out


# ---- positions

def positions(settings):
    """The latest snapshot of each book's open positions."""
    out = []
    for bot in _bots(settings):
        for book in settings.books:
            last = sources.query(settings, bot, book, "SELECT MAX(ts) AS ts FROM equity_snapshots WHERE book = ?",
                                 (book,))
            ts = last[0]["ts"] if last else None
            if not ts:
                continue
            rows = sources.query(settings, bot, book,
                                 "SELECT symbol, qty, avg_entry_price, price, market_value, unrealized_pnl "
                                 "FROM position_snapshots WHERE book = ? AND ts = ? ORDER BY symbol", (book, ts))
            out.append({"bot": bot, "book": book, "as_of": ts,
                        "positions": [{k: (_num(v) if k != "symbol" else v) for k, v in r.items()} for r in rows]})
    return {"books": out}


# ---- ops

def _seconds(start, end):
    try:
        return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())
    except (TypeError, ValueError):
        return None


def ops(settings, with_errors=False, limit=200, today=None):
    """Recent job runs and deploys per bot, and a summary per job."""
    today = today or current_day()
    week = (today - timedelta(days=7)).isoformat()
    out = {}
    for bot in _bots(settings):
        rows = sources.query_bot_db(settings, bot, "SELECT job, started_at, ended_at, status, exit_code, error, git_sha "
                                                   "FROM runs ORDER BY started_at DESC, id DESC LIMIT ?", (limit,))
        runs = [{"job": r["job"], "started_at": r["started_at"], "ended_at": r["ended_at"], "status": r["status"],
                 "exit_code": r["exit_code"], "seconds": _seconds(r["started_at"], r["ended_at"]),
                 "git_sha": r["git_sha"]} | ({"error": r["error"]} if with_errors else {}) for r in rows]
        jobs = {}
        for r in reversed(runs):  # oldest first, so the last one seen is the latest
            j = jobs.setdefault(r["job"], {"job": r["job"], "runs": 0, "failures_7d": 0, "last_ok": None})
            j["runs"] += 1
            j["last_status"], j["last_started"] = r["status"], r["started_at"]
            if r["status"] == "ok":
                j["last_ok"] = r["started_at"]
            if r["status"] in ("failed", "lost") and r["started_at"] >= week:
                j["failures_7d"] += 1
        out[bot] = {"runs": runs, "jobs": sorted(jobs.values(), key=lambda j: j["job"])}
    return out
