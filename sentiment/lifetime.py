"""Lifetime aggregate of a book: state/lifetime.md to read and state/lifetime.json for the review.

    python lifetime.py [--book paper|dry_run|replay]

Besides the book's own results it measures score calibration: for every score in every score file
(traded or not), the stock's return from that day's open to the open `max_hold_days` trading days
later. That says whether the scores carry information independent of the trading rules.
"""
import argparse
import json
import math
import sys
from datetime import timedelta

import numpy as np
import pandas as pd

import paths
from analyze import (MIN_TRADES, confidence_band, daily_returns, expectancy_range, load_spy, max_drawdown,
                     read_summary, spy_return)
from logger import read_trades_frame

TRADING_DAYS = 252


def read_score_files():
    """All score files, oldest first."""
    files = sorted(paths.scores_dir().glob("*.json"))
    return [json.loads(f.read_text(encoding="utf-8")) for f in files]


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


def _group(priced, col):
    out = {}
    for k, g in priced.groupby(col, dropna=False):
        out[str(k)] = {"trades": int(len(g)), "win_rate": round(float((g["pnl_pct"] > 0).mean()), 3),
                       "mean_pnl_pct": round(float(g["pnl_pct"].mean()), 3),
                       "total_pnl_usd": round(float(g["pnl_usd"].sum()), 2)}
    return out


def _r(x, digits=4):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), digits)


def compute(trades, summary, outcomes=None, spy=None):
    """The lifetime stats as a plain dict (JSON-ready)."""
    priced = trades[pd.to_numeric(trades["pnl_pct"], errors="coerce").notna()].copy()
    priced["pnl_pct"] = priced["pnl_pct"].astype(float)
    priced["pnl_usd"] = priced["pnl_usd"].astype(float)
    returns = daily_returns(summary)
    sd = float(returns.std(ddof=1)) if len(returns) > 1 else float("nan")
    wins, losses = priced[priced["pnl_pct"] > 0], priced[priced["pnl_pct"] <= 0]
    mean, lo, hi = expectancy_range(priced)
    loss_sum = -losses["pnl_usd"].sum()
    stats = {
        "first_day": str(returns.index[0]) if len(returns) else None,
        "last_day": str(returns.index[-1]) if len(returns) else None,
        "days": int(len(returns)),
        "total_return_pct": _r(100 * ((1 + returns).prod() - 1)) if len(returns) else None,
        "ann_volatility_pct": _r(100 * sd * math.sqrt(TRADING_DAYS)),
        "sharpe": _r(returns.mean() / sd * math.sqrt(TRADING_DAYS)) if len(returns) > 1 and sd > 0 else None,
        "max_drawdown_pct": _r(100 * max_drawdown(returns)),
        "best_day_pct": _r(100 * returns.max()) if len(returns) else None,
        "worst_day_pct": _r(100 * returns.min()) if len(returns) else None,
        "spy_return_pct": None,
        "trades": int(len(trades)), "priced_trades": int(len(priced)),
        "enough_trades": bool(len(priced) >= MIN_TRADES),
        "win_rate": _r((priced["pnl_pct"] > 0).mean(), 3) if len(priced) else None,
        "avg_win_pct": _r(wins["pnl_pct"].mean(), 3) if len(wins) else None,
        "avg_loss_pct": _r(losses["pnl_pct"].mean(), 3) if len(losses) else None,
        "expectancy_pct": _r(mean, 3), "expectancy_95_low": _r(lo, 3), "expectancy_95_high": _r(hi, 3),
        "profit_factor": _r(wins["pnl_usd"].sum() / loss_sum, 3) if loss_sum > 0 else None,
        "total_pnl_usd": _r(priced["pnl_usd"].sum(), 2),
        "avg_hold_days": _r(pd.to_numeric(priced["hold_days"], errors="coerce").mean(), 2) if len(priced) else None,
    }
    if len(returns) and spy is not None:
        stats["spy_return_pct"] = _r(100 * (spy_return(spy, returns.index[0], returns.index[-1]) or float("nan")))
    if len(priced):
        priced["score_bucket"] = priced["score"].map(lambda s: "unknown" if pd.isna(s) else f"{int(s):+d}")
        priced["confidence_band"] = priced["confidence"].map(confidence_band)
        priced["month"] = pd.to_datetime(priced["exit_date"]).dt.strftime("%Y-%m")
        mem = priced["memory_sha256"] if "memory_sha256" in priced else pd.Series("", index=priced.index)
        priced["memory"] = mem.fillna("").astype(str).str[:12].replace("", "none")
        stats["by"] = {name: _group(priced, col) for name, col in (
            ("score", "score_bucket"), ("confidence", "confidence_band"), ("side", "side"),
            ("exit_reason", "exit_reason"), ("month", "month"), ("memory_version", "memory"))}
    if outcomes is not None and len(outcomes):
        mem = outcomes["memory_sha256"].fillna("").astype(str).str[:12].replace("", "none")
        stats["calibration"] = {"scores": int(len(outcomes)), "by_score": calibration(outcomes),
                                "by_memory_version": {k: calibration(g) for k, g in outcomes.groupby(mem)}}
    return stats


def _fmt(v, suffix=""):
    return "n/a" if v is None else f"{v:+.2f}{suffix}" if suffix == "%" else f"{v}{suffix}"


def render(stats):
    lines = ["# Lifetime", ""]
    if not stats["days"]:
        return "# Lifetime\n\nNo trading days yet.\n"
    lines += [f"{stats['first_day']} to {stats['last_day']}, {stats['days']} trading days.", ""]
    if not stats["enough_trades"]:
        lines += [f"**Only {stats['priced_trades']} priced trades: under {MIN_TRADES}, these numbers are not "
                  "evidence of anything yet.**", ""]
    rng = (f" (95% range {stats['expectancy_95_low']:+.2f} to {stats['expectancy_95_high']:+.2f})"
           if stats["expectancy_95_low"] is not None else "")
    lines += [
        f"- Total return: {_fmt(stats['total_return_pct'], '%')} (SPY {_fmt(stats['spy_return_pct'], '%')})",
        f"- P&L: {_fmt(stats['total_pnl_usd'])} USD from {stats['priced_trades']} priced trades "
        f"({stats['trades']} closed)",
        f"- Win rate: {_fmt(stats['win_rate'])}, average win {_fmt(stats['avg_win_pct'], '%')}, "
        f"average loss {_fmt(stats['avg_loss_pct'], '%')}",
        f"- Expectancy per trade: {_fmt(stats['expectancy_pct'], '%')}{rng}",
        f"- Profit factor: {_fmt(stats['profit_factor'])}, average hold {_fmt(stats['avg_hold_days'])} days",
        f"- Volatility (annualised): {_fmt(stats['ann_volatility_pct'], '%')}, Sharpe {_fmt(stats['sharpe'])}",
        f"- Max drawdown: {_fmt(stats['max_drawdown_pct'], '%')}, best day {_fmt(stats['best_day_pct'], '%')}, "
        f"worst day {_fmt(stats['worst_day_pct'], '%')}", ""]
    cal = stats.get("calibration")
    if cal:
        lines += ["## Score calibration (every score, traded or not)", "",
                  f"{cal['scores']} scores with a completed window. Hit rate: the move went the score's way.", "",
                  "| Score | n | mean forward return | hit rate |", "|---|---:|---:|---:|"]
        for s in sorted(cal["by_score"], key=int):
            c = cal["by_score"][s]
            hit = "n/a" if c["hit_rate"] is None else f"{c['hit_rate']:.0%}"
            lines.append(f"| {s} | {c['n']} | {c['mean_fwd_return_pct']:+.2f}% | {hit} |")
        lines.append("")
        versions = cal.get("by_memory_version", {})
        if len(versions) > 1:
            lines += ["By memory version, hit rate of non-zero scores:", ""]
            for v, by in versions.items():
                nonzero = [c for s, c in by.items() if s != "+0"]
                n = sum(c["n"] for c in nonzero)
                hits = sum(c["n"] * c["hit_rate"] for c in nonzero) / n if n else None
                lines.append(f"- {v}: {n} scores, hit rate {'n/a' if hits is None else f'{hits:.0%}'}")
            lines.append("")
    for name, groups in stats.get("by", {}).items():
        lines += [f"## Trades by {name.replace('_', ' ')}", "", "| | trades | win rate | mean P&L % | total P&L $ |",
                  "|---|---:|---:|---:|---:|"]
        for k, g in groups.items():
            lines.append(f"| {k} | {g['trades']} | {g['win_rate']:.0%} | {g['mean_pnl_pct']:+.2f} | "
                         f"{g['total_pnl_usd']:+,.2f} |")
        lines.append("")
    return "\n".join(lines) + "\n"


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
    """Compute and write state/lifetime.md and state/lifetime.json for a book. Returns the stats.

    With fetch=False nothing is downloaded: calibration uses `outcomes` and SPY is left out.
    """
    d = paths.book_dir(book)
    trades = read_trades_frame(d / "trades.csv")
    summary = read_summary(d / "daily_summary.csv")
    if outcomes is None and fetch:
        records = read_score_files()
        try:
            outcomes = load_outcomes(records, horizon) if records else None
        except Exception:
            if log:
                log.warning("score calibration skipped: no bars", exc_info=True)
    spy = load_spy(summary["date"].iloc[0], summary["date"].iloc[-1]) if fetch and not summary.empty else None
    stats = compute(trades, summary, outcomes, spy) | {"book": book}
    paths.lifetime_file().write_text(render(stats), encoding="utf-8")
    paths.lifetime_json().write_text(json.dumps(stats, indent=1), encoding="utf-8")
    return stats


def main(argv=None):
    import config
    from logger import setup_logging

    ap = argparse.ArgumentParser(description="lifetime aggregate")
    ap.add_argument("--book", choices=["paper", "dry_run", "replay"])
    args = ap.parse_args(argv)
    log = setup_logging("lifetime")
    cfg = config.load_config()
    build(args.book or cfg["mode"], cfg["exit"]["max_hold_days"], log)
    print(paths.lifetime_file().read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
