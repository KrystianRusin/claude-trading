"""Side-by-side report of this book and the paper-trader's over their overlapping days: state/comparison.md.

    python compare.py [--book paper|dry_run]

Reads the paper-trader's state folder (PAPER_TRADER_STATE in .env) read-only. Both books keep a
daily_summary.csv with date, equity_start and equity_end; daily returns come from those.
"""
import argparse
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import paths
from analyze import daily_returns, max_drawdown, read_summary, spy_baseline

TRADING_DAYS = 252
Z95 = 1.96


def book_stats(returns, trades):
    n = len(returns)
    sd = float(returns.std(ddof=1)) if n > 1 else float("nan")
    return {"total_return": float((1 + returns).prod() - 1), "ann_vol": sd * math.sqrt(TRADING_DAYS),
            "sharpe": float(returns.mean()) / sd * math.sqrt(TRADING_DAYS) if n > 1 and sd > 0 else float("nan"),
            "max_drawdown": max_drawdown(returns), "worst_day": float(returns.min()) if n else float("nan"),
            "trades": trades, "days": n}


def difference(a, b):
    """(mean daily return difference a - b, low, high) with a normal 95% range; range None under 2 days."""
    d = (a - b).dropna()
    if len(d) < 2:
        return (float(d.mean()) if len(d) else None), None, None
    se = float(d.std(ddof=1)) / math.sqrt(len(d))
    m = float(d.mean())
    return m, m - Z95 * se, m + Z95 * se


def _trades_in(trades, col, dates):
    if trades is None or trades.empty or col not in trades:
        return 0
    days = pd.to_datetime(trades[col], errors="coerce").dt.date
    return int(days.isin(set(dates)).sum())


def _fmt(v, pct=True):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    return f"{100 * v:+.2f}%" if pct else f"{v:.2f}"


def build_comparison(ours, theirs, our_trades=None, their_trades=None, spy=None,
                     names=("sentiment-trader", "paper-trader")):
    ra, rb = daily_returns(ours), daily_returns(theirs)
    overlap = sorted(set(ra.index) & set(rb.index))
    lines = ["# Sentiment trader vs paper-trader", ""]
    if not overlap:
        return "\n".join(lines + ["No overlapping days yet."]) + "\n"
    ra, rb = ra.loc[overlap], rb.loc[overlap]
    sa = book_stats(ra, _trades_in(our_trades, "date", overlap))
    sb = book_stats(rb, _trades_in(their_trades, "date", overlap))
    lines += [f"Overlapping period: {overlap[0]} to {overlap[-1]} ({len(overlap)} trading days).", "",
              f"| | {names[0]} | {names[1]} |", "|---|---:|---:|"]
    for key, label, pct in (("total_return", "Total return", True), ("ann_vol", "Annualised volatility", True),
                            ("sharpe", "Sharpe ratio", False), ("max_drawdown", "Max drawdown", True),
                            ("worst_day", "Worst day", True)):
        lines.append(f"| {label} | {_fmt(sa[key], pct)} | {_fmt(sb[key], pct)} |")
    lines += [f"| Trades | {sa['trades']} | {sb['trades']} |", f"| Days | {sa['days']} | {sb['days']} |", ""]
    spy_text = f"{spy:+.2f}%" if isinstance(spy, (int, float)) else (spy or "not requested")
    lines.append(f"- SPY buy-and-hold, same period: {spy_text}")
    corr = float(np.corrcoef(ra, rb)[0, 1]) if len(overlap) > 2 and ra.std() > 0 and rb.std() > 0 else None
    lines.append(f"- Correlation of daily returns: {_fmt(corr, pct=False)}")
    m, lo, hi = difference(ra, rb)
    if lo is None:
        lines.append(f"- Daily return difference ({names[0]} minus {names[1]}): {_fmt(m)}; too few days for a range")
        verdict = "Too few days to tell the books apart."
    else:
        lines.append(f"- Daily return difference ({names[0]} minus {names[1]}): {_fmt(m)} "
                     f"(95% range {_fmt(lo)} to {_fmt(hi)})")
        if lo > 0:
            verdict = f"{names[0]} did better: the 95% range of the daily difference is above zero."
        elif hi < 0:
            verdict = f"{names[1]} did better: the 95% range of the daily difference is below zero."
        else:
            verdict = "Neither book is better: the 95% range of the daily difference includes zero."
    lines += ["", verdict, "", "Paper results over a short window are not evidence of skill. Not financial advice."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    import config
    from logger import read_trades_frame, setup_logging

    ap = argparse.ArgumentParser(description="compare with the paper-trader")
    ap.add_argument("--book", choices=["paper", "dry_run"])
    args = ap.parse_args(argv)
    setup_logging("compare")
    book = args.book or config.load_config()["mode"]
    ours_dir = paths.book_dir(book)
    ours = read_summary(ours_dir / "daily_summary.csv")
    other = os.environ.get("PAPER_TRADER_STATE")
    if not other or not (Path(other) / "daily_summary.csv").exists():
        text = "# Sentiment trader vs paper-trader\n\nPAPER_TRADER_STATE is not set or has no daily_summary.csv.\n"
    else:
        theirs = read_summary(Path(other) / "daily_summary.csv")
        their_trades = pd.read_csv(Path(other) / "trades.csv") if (Path(other) / "trades.csv").exists() else None
        overlap = sorted(set(ours["date"]) & set(theirs["date"]))
        spy = spy_baseline(overlap[0], overlap[-1]) if overlap else None
        text = build_comparison(ours, theirs, read_trades_frame(ours_dir / "trades.csv"), their_trades, spy,
                                names=(f"sentiment-trader ({book})", "paper-trader"))
    paths.comparison_file().write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
