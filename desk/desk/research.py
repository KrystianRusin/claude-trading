"""The symbol page, a trade's price chart, the sentiment bot's research and the bots' after-session activity.

Like views.py, every field is chosen here. Article summaries, the bots' reasons and their raw logs are never
served: a review log is read only for its guard result line.
"""
import re
from datetime import timedelta
from urllib.parse import urlparse

from . import notebook, sources, views

SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
SYMBOL_DAYS = 180  # score history and signals shown on a symbol page
MAX_BARS = {"1Day": 800, "1Min": 1200}
HEADLINE_DAYS = 30
MAX_HEADLINES = 100
NEWS_VOLUME_DAYS = 60
IDEA_DAYS = 90
AGENT_DAYS = 30
MAX_CHANGELOG = 4000


def valid_symbol(sym):
    return bool(sym) and bool(SYMBOL.match(sym))


# ---- symbol page

def known_symbols(settings, today=None):
    """Every symbol a bot traded, or the sentiment bot scored in the last SYMBOL_DAYS, for the symbol search."""
    today = today or views.current_day()
    out = {t["symbol"] for t in views.all_trades(settings)}
    if settings.public("scores") and "sentiment" in settings.bots:
        for rec in sources.score_files(settings, today - timedelta(days=SYMBOL_DAYS)):
            out |= set((rec.get("scores") or {}).keys())
    return {"symbols": sorted(s for s in out if valid_symbol(s))}


def symbol(settings, sym, today=None):
    """One symbol across both bots: results per bot, its trades, the quant bot's signals and (with scores) the
    sentiment bot's daily scores and desk ideas."""
    today = today or views.current_day()
    since = today - timedelta(days=SYMBOL_DAYS)
    trades = views.all_trades(settings, symbol=sym)
    stats = {}
    for bot in settings.bots:
        mine = [t for t in trades if t["bot"] == bot]
        if mine:
            s = views.trade_stats(mine)
            stats[bot] = {k: s[k] for k in ("trades", "wins", "losses", "win_rate", "total_pnl", "avg_r")} | {
                "first": min(t["date"] for t in mine), "last": max(t["date"] for t in mine)}
    signals = []
    if "quant" in settings.bots:
        book = views._paper_book(settings)
        rows = sources.query(settings, "quant", book,
                             "SELECT date, side, event FROM order_events WHERE book = ? AND symbol = ? AND date >= ? "
                             "AND (event = 'skip' OR (event = 'submit' AND order_type = 'bracket')) ORDER BY ts",
                             (book, sym, since.isoformat()))
        signals = [{"date": r["date"], "side": r["side"], "entered": r["event"] == "submit"} for r in rows]
    scores, ideas = [], []
    if settings.public("scores") and "sentiment" in settings.bots:
        for rec in sources.score_files(settings, since):
            e = (rec.get("scores") or {}).get(sym)
            try:
                if e:
                    scores.append({"date": str(rec.get("date")), "score": float(e["score"]), "confidence": float(e["confidence"]),
                                   "articles": len(e.get("article_ids") or [])})
            except (KeyError, TypeError, ValueError):
                pass
            for idea in (rec.get("desk") or {}).get("ideas", []):
                legs = [leg for leg in idea.get("legs", []) if leg.get("symbol") == sym]
                if legs:
                    ideas.append({"date": str(rec.get("date")), "kind": str(idea.get("kind") or ""),
                                  "side": str(legs[0].get("side") or ""), "conviction": views._float(idea.get("conviction")),
                                  "legs": len(idea.get("legs", []))})
    attention = [{"date": r["date"], **{k: views._num(r[k]) for k in ("volume_z", "news_z", "wiki_z", "reddit_z")},
                  "mentions": r["mentions"], "breadth": r["breadth"]}
                 for r in sources.attention_rows(settings, sym)] if "sentiment" in settings.bots else []
    return {"symbol": sym, "bots": stats, "trades": trades[:200], "signals": signals, "scores": scores, "ideas": ideas,
            "attention": attention}


def bars(settings, sym, timeframe, start, end):
    """Saved price bars of `sym` from both bots' bars tables (the sentiment bot saves daily bars, the quant bot
    minute bars of the sessions it traded), oldest first."""
    rows = {}
    for bot in settings.bots:
        for r in sources.query_bot_db(settings, bot,
                                      "SELECT ts, open, high, low, close, volume FROM bars WHERE symbol = ? AND timeframe = ? "
                                      "AND substr(ts, 1, 10) BETWEEN ? AND ? ORDER BY ts LIMIT ?",
                                      (sym, timeframe, start.isoformat(), end.isoformat(), MAX_BARS[timeframe])):
            rows.setdefault(r["ts"], {k: (views._num(v) if k != "ts" else v) for k, v in r.items()})
    out = [rows[k] for k in sorted(rows)]
    return {"symbol": sym, "timeframe": timeframe, "bars": out[-MAX_BARS[timeframe]:]}


def trade_detail(settings, bot, trade_id):
    """One trade with its stop and target, and the bars window that shows it: the minute bars of the day for an
    intraday trade, daily bars around it for a swing trade."""
    rows = sources.query_bot_db(settings, bot, f"SELECT id, book, {', '.join(views.TRADE_FIELDS)}, extra FROM trades "
                                               "WHERE id = ?", (trade_id,))
    if not rows or rows[0]["book"] not in settings.books:
        return None
    t = views._trade(bot, rows[0]["book"], rows[0])
    target = None
    if t["entry_time"]:
        legs = sources.query(settings, bot, t["book"],
                             "SELECT l.order_type, l.limit_price, l.stop_price FROM orders p JOIN orders l "
                             "ON l.book = p.book AND l.parent_order_id = p.order_id WHERE p.book = ? AND p.symbol = ? "
                             "AND p.filled_at = ?", (t["book"], t["symbol"], t["entry_time"]))
        for leg in legs:
            if leg["order_type"] == "limit":
                target = views._num(leg["limit_price"])
            elif leg["order_type"] == "stop" and t["stop_price"] is None:
                t["stop_price"] = views._num(leg["stop_price"])
    entry_day = (t["entry_date"] or t["date"])[:10]
    if entry_day == t["date"]:
        window = {"timeframe": "1Min", "start": entry_day, "end": t["date"]}
    else:
        from datetime import date
        a, b = date.fromisoformat(entry_day), date.fromisoformat(t["date"])
        window = {"timeframe": "1Day", "start": (a - timedelta(days=30)).isoformat(),
                  "end": (b + timedelta(days=10)).isoformat()}
    return {"trade": t, "target_price": target, "window": window}


def headlines(settings, sym, days=HEADLINE_DAYS, today=None):
    """The newest headlines that tag `sym`: when, the headline, its source and link. Never the summary."""
    today = today or views.current_day()
    out = []
    for _, path in reversed(sources.news_days(settings, today - timedelta(days=days))):
        for a in sources.news_articles(path):
            if sym not in (a.get("symbols") or []):
                continue
            url = str(a.get("url") or "")
            out.append({"published_at": str(a.get("published_at") or ""), "headline": str(a.get("headline") or "")[:300],
                        "source": str(a.get("source") or "")[:60],
                        "url": url if urlparse(url).scheme in ("http", "https") else None})
        if len(out) >= MAX_HEADLINES:
            break
    out.sort(key=lambda h: h["published_at"], reverse=True)
    return {"symbol": sym, "headlines": out[:MAX_HEADLINES]}


# ---- sentiment research

def _groups(by):
    out = {}
    for key, g in (by or {}).items():
        if isinstance(g, dict):
            out[str(key)] = {k: views._float(g.get(k)) for k in ("n", "win_rate", "pnl_usd", "expectancy_r")}
    return out


def research(settings, today=None):
    """The sentiment bot's research: score calibration and trade results by score and confidence (from its own
    lifetime.json), news volume per day, its daily scores split bullish and bearish, and its desk ideas with
    what came of them."""
    today = today or views.current_day()
    life, written = sources.sentiment_lifetime(settings)
    cal = (life or {}).get("calibration") or {}
    calibration = []
    for score, c in sorted((cal.get("by_score") or {}).items(), key=lambda kv: int(kv[0]) if str(kv[0]).lstrip("+-").isdigit() else 0):
        if isinstance(c, dict):
            calibration.append({"score": int(score), "n": int(views._float(c.get("n")) or 0),
                                "mean_fwd_return_pct": views._float(c.get("mean_fwd_return_pct")),
                                "hit_rate": views._float(c.get("hit_rate"))})
    by = (life or {}).get("by") or {}
    news = [{"date": d, "articles": sources.news_count(p)}
            for d, p in sources.news_days(settings, today - timedelta(days=NEWS_VOLUME_DAYS))]
    records = sources.score_files(settings, today - timedelta(days=IDEA_DAYS))
    leaning = []
    for rec in records:
        scores = [e.get("score") for e in (rec.get("scores") or {}).values() if isinstance(e, dict)]
        scores = [s for s in scores if isinstance(s, (int, float))]
        leaning.append({"date": str(rec.get("date")), "bullish": sum(s > 0 for s in scores), "bearish": sum(s < 0 for s in scores),
                        "neutral": sum(s == 0 for s in scores)})
    book = views._paper_book(settings)
    trades = [t for t in views.all_trades(settings, bot="sentiment", start=today - timedelta(days=IDEA_DAYS + 30))
              if t["book"] == book]
    held = {(p.get("symbol"), p.get("entry_date"), p.get("strategy")) for p in sources.sentiment_positions(settings, book)}
    ideas, by_kind = [], {}
    for rec in records:
        d = rec.get("date")
        for idea in (rec.get("desk") or {}).get("ideas", []):
            legs = [{"symbol": str(leg.get("symbol") or ""), "side": str(leg.get("side") or "")}
                    for leg in idea.get("legs", []) if isinstance(leg, dict)]
            kind = str(idea.get("kind") or "")
            syms = {leg["symbol"] for leg in legs}
            closed = [t for t in trades if t["symbol"] in syms and t["entry_date"] == d and t["strategy"] == kind]
            open_ = any((s, d, kind) in held for s in syms)
            rs = [t["pnl_r"] for t in closed if t["pnl_r"] is not None]
            outcome = "closed" if closed else "open" if open_ else "not traded"
            ideas.append({"date": str(d), "kind": kind, "legs": legs, "conviction": views._float(idea.get("conviction")),
                          "confidence": views._float(idea.get("confidence")), "outcome": outcome,
                          "pnl_r": round(sum(rs), 4) if rs else None})
            k = by_kind.setdefault(kind, {"kind": kind, "ideas": 0, "traded": 0, "closed": 0, "_r": []})
            k["ideas"] += 1
            k["traded"] += outcome != "not traded"
            k["closed"] += bool(closed)
            k["_r"] += rs
    kinds = [{k: v for k, v in x.items() if k != "_r"} | {"avg_r": round(sum(x["_r"]) / len(x["_r"]), 4) if x["_r"] else None}
             for x in sorted(by_kind.values(), key=lambda x: -x["ideas"])]
    from datetime import datetime, timezone
    return {"attention": attention_summary(settings, life), "as_of": datetime.fromtimestamp(written, timezone.utc).isoformat(timespec="seconds") if written else None,
            "calibration": calibration, "scored_outcomes": int(views._float(cal.get("scores")) or 0),
            "by_score": _groups(by.get("score")), "by_confidence": _groups(by.get("confidence")),
            "news": news, "leaning": leaning, "ideas": sorted(ideas, key=lambda i: i["date"] or "", reverse=True)[:200],
            "idea_kinds": kinds}


# ---- attention and discovery (claude-sentiment-trading ticket 009)

def _mean(summary):
    return views._float((summary or {}).get("mean_pct"))


def attention_summary(settings, life):
    """How forward returns differ with attention breadth (from the bot's lifetime.json), the newest discovery
    list, and how past lists did. Counts and returns only: the bot's sources are not served."""
    att = (life or {}).get("attention") or {}
    breadth = []
    for bucket, splits in sorted(((att.get("by") or {}).get("breadth") or {}).items()):
        all_ = (splits or {}).get("all") or {}
        breadth.append({"breadth": str(bucket), "n": int(views._float((all_.get("1d") or {}).get("n")) or 0),
                        "d1": _mean(all_.get("1d")), "d3": _mean(all_.get("3d")), "d5": _mean(all_.get("5d")),
                        "up_rate_1d": views._float((all_.get("1d") or {}).get("up_rate"))})
    history = [{"date": str(x.get("date")), "names": int(views._float(x.get("names")) or 0), "d1": _mean(x.get("1d")),
                "d3": _mean(x.get("3d")), "d5": _mean(x.get("5d"))} for x in (att.get("discovery") or [])
               if isinstance(x, dict)]
    rec = sources.sentiment_discovery(settings) or {}
    names = [{"symbol": str(n.get("symbol")), "reasons": [str(r) for r in (n.get("reasons") or [])][:6],
              "close": views._float(n.get("close")), "ret_1d": views._float(n.get("ret_1d")),
              "rvol": views._float(n.get("rvol")), "breadth": int(views._float(n.get("breadth")) or 0)}
             for n in rec.get("names", []) if isinstance(n, dict) and valid_symbol(str(n.get("symbol")))]
    return {"breadth": breadth, "discovery_date": rec.get("date"), "discovery": names,
            "collect_only": bool(rec.get("collect_only", True)), "discovery_history": history[::-1]}


# ---- agent activity

GUARD = re.compile(r"^=== guard (passed|failed)", re.MULTILINE)
GUARD_PROBLEMS = re.compile(r"^GUARD: (\d+) problem", re.MULTILINE)
CHANGELOG_DAY = re.compile(r"^## (\d{4}-\d{2}-\d{2})[^\n]*$", re.MULTILINE)


def changelog_entries(text):
    """{date: text} of claude-quant's changelog, one entry per '## <date>' heading (several are joined)."""
    out = {}
    parts = CHANGELOG_DAY.split(text or "")
    for day, body in zip(parts[1::2], parts[2::2]):
        out[day] = (out.get(day, "") + "\n" + body.strip()).strip()[:MAX_CHANGELOG]
    return out


def agent_activity(settings, bot, today=None):
    """Per day, newest first: the review run, the guard result (claude-quant), what the review changed, memory
    entries added or moved, whether a journal entry was written, and the changelog entry (claude-quant)."""
    today = today or views.current_day()
    since = (today - timedelta(days=AGENT_DAYS)).isoformat()
    days = {}

    def day(d):
        return days.setdefault(d, {"date": d, "reviews": [], "guard": None, "changes": [], "memory": [],
                                   "journal": False, "changelog": None})

    for r in sources.query_bot_db(settings, bot, "SELECT job, started_at, ended_at, status FROM runs WHERE job LIKE "
                                                 "'%review%' AND started_at >= ? ORDER BY started_at", (since,)):
        day(r["started_at"][:10])["reviews"].append({"job": r["job"], "status": r["status"], "started_at": r["started_at"],
                                                     "seconds": views._seconds(r["started_at"], r["ended_at"])})
    for r in sources.query_bot_db(settings, bot, "SELECT kind, name, version, effective_from, reason, sha256 FROM versions "
                                                 "WHERE effective_from >= ? AND reason != 'session' ORDER BY effective_from",
                                  (since,)):
        day(r["effective_from"][:10])["changes"].append({"kind": r["kind"], "name": r["name"], "version": r["version"],
                                                         "reason": r["reason"], "removed": r["sha256"] == ""})
    mem = notebook.memory(settings, bot)
    for e in mem["entries"]:
        for i, h in enumerate(e["history"]):
            if h["date"] >= since:
                day(h["date"])["memory"].append({"id": e["id"], "title": e["title"], "status": h["status"],
                                                 "previous": e["history"][i - 1]["status"] if i else None})
    for d in sources.journal_dates(settings, bot):
        if d >= since:
            day(d)["journal"] = True
    if bot == "quant":
        for d, text in changelog_entries(sources.quant_changelog(settings)).items():
            if d >= since:
                day(d)["changelog"] = text
        for d, entry in days.items():
            log = sources.quant_review_log(settings, d)
            if log:
                result = GUARD.findall(log)
                problems = GUARD_PROBLEMS.findall(log)
                if result:
                    entry["guard"] = {"passed": result[-1] == "passed", "problems": int(problems[-1]) if problems else 0}
    return {"bot": bot, "days": sorted(days.values(), key=lambda x: x["date"], reverse=True)}
