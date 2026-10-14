"""All-time statistics since the first trading day, in the same format as the paper-trader's lifetime.md.

    python src/lifetime.py [--book paper|dry_run|replay] [--no-baseline]

Writes <book>/lifetime.md to read and <book>/lifetime.json for the weekly review. After the
paper-trader's sections it adds the sentiment-specific ones: by score, confidence and memory
version, and score calibration: for every score in every score file (traded or not), the stock's
return from that day's open to the open `max_hold_days` trading days later.
"""
import argparse
import csv
import json
import math
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

import paths
from analyze import (DEFAULT_EQUITY, _fmt, _table, add_keys, breakdown, equity_stats, expectancy_ci,
                     filter_trades, fmt_range, range_note, read_summary, spy_baseline, summary_stats)
from logger import read_trades_frame


def streaks(values):
    """Longest run of positive and of negative values."""
    best = worst = up = down = 0
    for v in values:
        up, down = (up + 1, 0) if v > 0 else (0, down + 1) if v < 0 else (0, 0)
        best, worst = max(best, up), max(worst, down)
    return best, worst


def period_table(trades, summary, period):
    """Per month ('M') or year ('Y'): days, trades, win rate, net $, return % and expectancy."""
    if trades.empty:
        return "(none)"
    t = trades.assign(_p=pd.to_datetime(trades["date"]).dt.to_period(period),
                      _r=pd.to_numeric(trades["pnl_r"], errors="coerce"), _pnl=trades["pnl_usd"].astype(float))
    g = t.groupby("_p")
    table = pd.DataFrame({"days": g["date"].nunique(), "trades": g.size(),
                          "win_rate": g["_pnl"].apply(lambda s: (s > 0).mean()),
                          "net_usd": g["_pnl"].sum(), "expectancy_r": g["_r"].mean()})
    returns = {}
    if not summary.empty:
        s = summary.assign(_p=pd.to_datetime(summary["date"]).dt.to_period(period))
        for p, rows in s.groupby("_p"):
            start, end = rows["equity_start"].iloc[0], rows["equity_end"].iloc[-1]
            if pd.notna(start) and pd.notna(end) and start:
                returns[p] = (end / start - 1) * 100
    table["return"] = [f"{returns[p]:+.2f}%" if p in returns else "n/a" for p in table.index]
    lines = ["| period | days | trades | win rate | net $ | return | expectancy |", "|---|---|---|---|---|---|---|"]
    for p, r in table.iterrows():
        lines.append(f"| {p} | {r['days']} | {r['trades']} | {_fmt(r['win_rate'], 'pct')} | "
                     f"{_fmt(r['net_usd'], 'usd')} | {r['return']} | {_fmt(r['expectancy_r'], 'r')} |")
    return "\n".join(lines)


def version_table(trades, key, label):
    frame = breakdown(trades, key)
    if frame.empty:
        return "(none)"
    spans = trades.groupby(key)["date"].agg(["min", "max"])
    frame.insert(0, "dates", [f"{spans.loc[i, 'min']} to {spans.loc[i, 'max']}" for i in frame.index])
    frame.index.name = label
    return _table(frame)


# ---- score calibration ----

def read_score_files():
    """All score files, oldest first."""
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(paths.scores_dir().glob("*.json"))]


def score_outcomes(records, bars, horizon):
    """One row per score whose `horizon`-day window has completed: date, symbol, score, confidence, fwd_return."""
    rows = []
    for rec in records:
        day = pd.Timestamp(rec["date"]).date()
        for sym, entry in rec.get("scores", {}).items():
            df = bars.get(sym)
            if df is None or day not in df.index:
                continue
            i = df.index.get_loc(day)
            if i + horizon >= len(df):
                continue
            fwd = float(df["open"].iloc[i + horizon]) / float(df["open"].iloc[i]) - 1
            rows.append({"date": day, "symbol": sym, "score": int(entry["score"]),
                         "confidence": float(entry["confidence"]), "memory_sha256": rec.get("memory_sha256", ""),
                         "fwd_return": fwd})
    return pd.DataFrame(rows, columns=["date", "symbol", "score", "confidence", "memory_sha256", "fwd_return"])


def calibration(outcomes):
    """Per score: count, mean forward return, and hit rate (the move went the score's way)."""
    out = {}
    for s, g in outcomes.groupby("score"):
        hits = (np.sign(g["fwd_return"]) == np.sign(s)).mean() if s else None
        out[f"{int(s):+d}"] = {"n": int(len(g)), "mean_fwd_return_pct": round(100 * float(g["fwd_return"].mean()), 3),
                                "hit_rate": None if hits is None else round(float(hits), 3)}
    return out


def calibration_section(outcomes, horizon):
    if outcomes is None or outcomes.empty:
        return ["No scores with a completed window yet."]
    lines = [f"{len(outcomes)} scores whose {horizon}-day window has completed. Hit rate: the move went the "
             "score's way.", "", "| score | n | mean forward return | hit rate |", "|---|---|---|---|"]
    by = calibration(outcomes)
    for s in sorted(by, key=int):
        c = by[s]
        hit = "n/a" if c["hit_rate"] is None else f"{c['hit_rate']:.1%}"
        lines.append(f"| {s} | {c['n']} | {c['mean_fwd_return_pct']:+.2f}% | {hit} |")
    mem = outcomes["memory_sha256"].fillna("").astype(str).str[:12].replace("", "none")
    if mem.nunique() > 1:
        lines += ["", "| memory version | non-zero scores | hit rate |", "|---|---|---|"]
        for v, g in outcomes[outcomes["score"] != 0].groupby(mem):
            hits = (np.sign(g["fwd_return"]) == np.sign(g["score"])).mean()
            lines.append(f"| {v} | {len(g)} | {hits:.1%} |")
    return lines


# ---- lifetime.md ----

def _open_positions(book):
    path = paths.book_dir(book) / "positions.csv"
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r["status"] != "pending"]


def build_lifetime(trades, summary, baseline=None, today=None, outcomes=None, horizon=5, holdings=()):
    today = today or date.today()
    lines = ["# Lifetime statistics", "", f"Generated {today}. Paper trading; not evidence of skill on "
             "its own. Every expectancy comes with a 95% range grouped by trading day.", ""]
    if trades.empty:
        lines.append("No closed trades yet.")
        if holdings:
            unreal = sum(float(r.get("unrealized_pnl") or 0) for r in holdings)
            lines += ["", f"- Open positions: {len(holdings)}, unrealized {_fmt(unreal, 'usd')}"]
        lines += ["", "## Score calibration", "", *calibration_section(outcomes, horizon)]
        return "\n".join(lines) + "\n"

    trades = add_keys(trades)
    first = min(trades["date"].min(), summary["date"].min() if not summary.empty else trades["date"].max())
    start_equity = float(summary["equity_start"].iloc[0]) if not summary.empty else DEFAULT_EQUITY
    current = float(summary["equity_end"].iloc[-1]) if not summary.empty else None
    s, ci = summary_stats(trades), expectancy_ci(trades)
    e = equity_stats(trades, start_equity)
    equity_curve = start_equity + trades["pnl_usd"].astype(float).cumsum()
    high_water = max(start_equity, float(equity_curve.max()))
    now_equity = current if current is not None else e["end_equity"]
    day_pnl = trades.groupby("date")["pnl_usd"].apply(lambda x: x.astype(float).sum())
    win_days, lose_days = streaks(day_pnl.tolist())
    pnl = trades["pnl_usd"].astype(float)
    best_t, worst_t = trades.loc[pnl.idxmax()], trades.loc[pnl.idxmin()]
    sessions = len(summary) if not summary.empty else trades["date"].nunique()
    halted = int(summary["halted"].astype(str).str.lower().eq("true").sum()) if not summary.empty else 0
    unreal = sum(float(r.get("unrealized_pnl") or 0) for r in holdings)

    lines += [
        "## All time", "",
        f"- Running since {first} ({sessions} sessions, {s['trading_days']} with trades, {halted} halted)",
        f"- Trades: {s['n']} ({s['wins']} wins, {s['losses']} losses, win rate {_fmt(s['win_rate'], 'pct')})",
        f"- Expectancy: {_fmt(s['expectancy_r'], 'r')} / {_fmt(s['expectancy_usd'], 'usd')} per trade, "
        f"95% range {fmt_range(ci)}{range_note(ci)}",
        f"- Profit factor: {_fmt(s['profit_factor'])}",
        f"- Net P&L: {_fmt(s['total_pnl'], 'usd')}",
        "", "## Equity", "",
        f"- Start {_fmt(start_equity, 'usd')}, now {_fmt(now_equity, 'usd')} "
        f"({(now_equity / start_equity - 1) * 100:+.2f}%)",
        f"- High-water mark {_fmt(high_water, 'usd')}; current drawdown "
        f"{_fmt(high_water - now_equity, 'usd')} ({(1 - now_equity / high_water) * 100:.2f}%)",
        f"- Max drawdown {_fmt(e['max_drawdown_usd'], 'usd')} ({e['max_drawdown_pct']:.2f}%)",
        f"- Open positions: {len(holdings)}, unrealized {_fmt(unreal, 'usd')}",
    ]
    if isinstance(baseline, (int, float)):
        lines.append(f"- SPY buy-and-hold over the same period: {baseline:+.2f}%")
    elif baseline:
        lines.append(f"- SPY baseline unavailable: {baseline}")
    lines += [
        "", "## Records", "",
        f"- Best day: {day_pnl.idxmax()} {_fmt(float(day_pnl.max()), 'usd')}; "
        f"worst day: {day_pnl.idxmin()} {_fmt(float(day_pnl.min()), 'usd')}",
        f"- Best trade: {best_t['date']} {best_t['side']} {best_t['symbol']} {_fmt(float(best_t['pnl_usd']), 'usd')}; "
        f"worst trade: {worst_t['date']} {worst_t['side']} {worst_t['symbol']} {_fmt(float(worst_t['pnl_usd']), 'usd')}",
        f"- Longest winning streak: {win_days} days; longest losing streak: {lose_days} days, "
        f"{e['longest_losing_streak']} trades",
        "", "## By month", "", period_table(trades, summary, "M"),
        "", "## By year", "", period_table(trades, summary, "Y"),
        "", "## By strategy version", "", version_table(trades, "strategy_key", "strategy"),
        "", "## By config version", "", version_table(trades, "config_version", "config"),
        "", "## By score", "", _table(breakdown(trades, "score_bucket")),
        "", "## By confidence", "", _table(breakdown(trades, "confidence_band")),
        "", "## By memory version", "", version_table(trades, "memory_version", "memory"),
        "", "## Score calibration", "", *calibration_section(outcomes, horizon),
    ]
    return "\n".join(lines) + "\n"


# ---- lifetime.json for the review ----

def _num(v, digits=4):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    return round(float(v), digits)


def _groups(trades, key):
    frame = breakdown(trades, key)
    return {str(k): {"n": int(r["n"]), "win_rate": _num(r["win_rate"], 3), "pnl_usd": _num(r["pnl_usd"], 2),
                     "expectancy_r": _num(r["expectancy_r"], 3), "range_95": r["range_95"]}
            for k, r in frame.iterrows()}


def compute(trades, summary, outcomes=None):
    """The lifetime numbers as a plain dict, for the review."""
    stats = {"days": int(len(summary)), "trades": int(len(trades))}
    if not trades.empty:
        t = add_keys(trades).assign(month=pd.to_datetime(trades["date"]).dt.strftime("%Y-%m"))
        s, ci = summary_stats(t), expectancy_ci(t)
        start = float(summary["equity_start"].iloc[0]) if not summary.empty else DEFAULT_EQUITY
        e = equity_stats(t, start)
        stats |= {"wins": s["wins"], "losses": s["losses"], "win_rate": _num(s["win_rate"], 3),
                  "expectancy_r": _num(s["expectancy_r"], 3), "expectancy_usd": _num(s["expectancy_usd"], 2),
                  "expectancy_95": [_num(ci["lo"], 3), _num(ci["hi"], 3)],
                  "profit_factor": _num(s["profit_factor"], 3), "net_pnl_usd": _num(s["total_pnl"], 2),
                  "max_drawdown_pct": _num(e["max_drawdown_pct"], 2),
                  "by": {name: _groups(t, key) for name, key in (
                      ("score", "score_bucket"), ("confidence", "confidence_band"), ("side", "side"),
                      ("exit_reason", "exit_reason"), ("month", "month"), ("memory_version", "memory_version"))}}
    if not summary.empty:
        stats["total_return_pct"] = _num((summary["equity_end"].iloc[-1] / summary["equity_start"].iloc[0] - 1) * 100, 3)
    if outcomes is not None and len(outcomes):
        stats["calibration"] = {"scores": int(len(outcomes)), "by_score": calibration(outcomes)}
    return stats


def load_outcomes(records, horizon):
    """Forward returns for every scored symbol, using cached bars and Alpaca when reachable."""
    import config
    from broker import load_bars, make_broker

    symbols = sorted({s for r in records for s in r.get("scores", {})})
    if not symbols:
        return None
    start = pd.Timestamp(records[0]["date"]).date() - timedelta(days=7)
    end = pd.Timestamp.now(tz="America/New_York").date()
    try:
        broker = make_broker(config.load_config(), verify_account=False)
    except Exception:
        broker = None
    return score_outcomes(records, load_bars(symbols, start, end, broker), horizon)


def build(book, horizon, log=None, outcomes=None, fetch=True):
    """Write <book>/lifetime.md and lifetime.json. Returns the JSON stats.

    With fetch=False nothing is downloaded: calibration uses `outcomes` and the SPY baseline is skipped.
    """
    d = paths.book_dir(book)
    trades = filter_trades(read_trades_frame(d / "trades.csv"))
    summary = read_summary(d / "daily_summary.csv")
    if outcomes is None and fetch:
        records = read_score_files()
        try:
            outcomes = load_outcomes(records, horizon) if records else None
        except Exception:
            if log:
                log.warning("score calibration skipped: no bars", exc_info=True)
    baseline = None
    if fetch and not trades.empty:
        baseline = spy_baseline(trades["date"].min(), trades["date"].max())
    text = build_lifetime(trades, summary, baseline, outcomes=outcomes, horizon=horizon,
                          holdings=_open_positions(book))
    stats = compute(trades, summary, outcomes) | {"book": book}
    d.mkdir(parents=True, exist_ok=True)
    paths.lifetime_file(book).write_text(text, encoding="utf-8")
    paths.lifetime_json(book).write_text(json.dumps(stats, indent=1, default=str), encoding="utf-8")
    return stats


def main(argv=None):
    import config
    from logger import setup_logging

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--book", choices=["paper", "dry_run", "replay"])
    ap.add_argument("--no-baseline", action="store_true", help="download nothing: no SPY baseline or calibration")
    args = ap.parse_args(argv)
    log = setup_logging("lifetime")
    cfg = config.load_config()
    book = args.book or cfg["mode"]
    build(book, cfg["exit"]["max_hold_days"], log, fetch=not args.no_baseline)
    print(paths.lifetime_file(book).read_text(encoding="utf-8"))
    print(f"written to {paths.lifetime_file(book)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
