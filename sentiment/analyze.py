"""Performance report for a book: state/reports/report_<date>.md.

    python analyze.py [--book paper|dry_run|replay]

The key question is whether higher scores lead to better returns, so trades are broken down by
score bucket, confidence band, side, exit reason, symbol and prompt version.
"""
import argparse
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import paths
from broker import ET
from logger import read_trades_frame

MIN_TRADES = 30
BOOTSTRAP_RUNS = 2000


def read_summary(path):
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=["date", "equity_start", "equity_end"])
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def daily_returns(summary):
    """Daily return series indexed by date, from equity_start (previous close) and equity_end."""
    if summary.empty:
        return pd.Series(dtype=float)
    s = summary.set_index("date")
    return (s["equity_end"] / s["equity_start"] - 1).astype(float)


def max_drawdown(returns):
    """Largest peak-to-trough fall of the compounded return curve, as a negative fraction."""
    if returns.empty:
        return 0.0
    curve = (1 + returns).cumprod()
    peak = np.maximum.accumulate(np.concatenate([[1.0], curve.to_numpy()]))[1:]
    return float((curve / peak - 1).min())


def expectancy_range(trades, seed=0, runs=BOOTSTRAP_RUNS):
    """(mean pnl_pct per trade, 2.5th, 97.5th percentile) from a bootstrap that resamples entry dates.

    Trades entered on the same day share the market's move that day, so whole days are resampled
    together. The range is None with fewer than two entry dates.
    """
    if trades.empty:
        return None, None, None
    mean = float(trades["pnl_pct"].mean())
    groups = [g["pnl_pct"].to_numpy() for _, g in trades.groupby("entry_date")]
    if len(groups) < 2:
        return mean, None, None
    rng = np.random.default_rng(seed)
    sums = np.array([g.sum() for g in groups])
    counts = np.array([len(g) for g in groups])
    picks = rng.integers(0, len(groups), size=(runs, len(groups)))
    means = sums[picks].sum(axis=1) / counts[picks].sum(axis=1)
    return mean, float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def confidence_band(c):
    if pd.isna(c):
        return "unknown"
    return "<0.5" if c < 0.5 else "0.5-0.7" if c < 0.7 else "0.7-0.9" if c < 0.9 else ">=0.9"


def breakdown(trades, col):
    g = trades.groupby(col, dropna=False)
    out = pd.DataFrame({"trades": g.size(), "win_rate": g["pnl_pct"].apply(lambda x: (x > 0).mean()),
                        "mean_pnl_pct": g["pnl_pct"].mean(), "total_pnl_usd": g["pnl_usd"].sum()})
    return out


def _table(df, index_name):
    lines = [f"| {index_name} | trades | win rate | mean P&L % | total P&L $ |", "|---|---:|---:|---:|---:|"]
    for k, r in df.iterrows():
        lines.append(f"| {k} | {int(r['trades'])} | {r['win_rate']:.0%} | {r['mean_pnl_pct']:+.2f} | "
                     f"{r['total_pnl_usd']:+,.2f} |")
    return "\n".join(lines)


def _pct(x):
    return "n/a" if x is None or pd.isna(x) else f"{100 * x:+.2f}%"


def spy_return(spy, start, end):
    """Buy-and-hold SPY from the close before `start` (or the first open) to the close on `end`."""
    if spy is None or spy.empty:
        return None
    before = spy[spy.index < start]
    window = spy[(spy.index >= start) & (spy.index <= end)]
    if window.empty:
        return None
    base = float(before["close"].iloc[-1]) if not before.empty else float(window["open"].iloc[0])
    return float(window["close"].iloc[-1]) / base - 1


def today_section(trades, summary, orders):
    """What happened on the latest summary day: P&L split, entries and exits."""
    day = summary.iloc[-1]
    d = day["date"]
    lines = [f"## Today ({d})", ""]
    if "day_pnl" in summary.columns and pd.notna(day.get("day_pnl")):
        lines.append(f"- P&L: {float(day['day_pnl']):+,.2f} USD ({float(day['day_return_pct']):+.2f}%), "
                     f"realized {float(day['realized_pnl']):+,.2f}, open positions' unrealized "
                     f"{float(day['unrealized_pnl']):+,.2f}")
    lines.append(f"- Equity: {float(day['equity_start']):,.2f} to {float(day['equity_end']):,.2f}")
    entries = [o for o in orders if o["date"] == d.isoformat() and o["event"] == "submit"
               and o["client_order_id"].endswith("-entry")]
    exits = trades[trades["exit_date"] == d]
    lines.append(f"- Entries: {len(entries)}")
    lines += [f"  - {o['symbol']} {o['side']} {o['qty']} at ~{o['price']} ({o['reason']})" for o in entries]
    lines.append(f"- Exits: {len(exits)}")
    for t in exits.itertuples():
        result = (f"{float(t.pnl_pct):+.2f}% ({float(t.pnl_usd):+,.2f} USD)" if pd.notna(t.pnl_pct)
                  else "no exit price")
        lines.append(f"  - {t.symbol} {t.side}: {t.exit_reason}, {result}")
    return lines + [""]


def build_report(trades, summary, spy=None, title="Sentiment trader report", orders=()):
    priced = trades[pd.to_numeric(trades["pnl_pct"], errors="coerce").notna()].copy()
    priced["pnl_pct"] = priced["pnl_pct"].astype(float)
    priced["pnl_usd"] = priced["pnl_usd"].astype(float)
    returns = daily_returns(summary)
    lines = [f"# {title}", ""]
    if not returns.empty:
        lines.append(f"Period: {returns.index[0]} to {returns.index[-1]} ({len(returns)} trading days).")
    lines.append(f"Closed trades: {len(trades)} ({len(trades) - len(priced)} without an exit price).")
    lines.append("")
    if not summary.empty:
        lines += today_section(trades, summary, list(orders))
    if len(priced) < MIN_TRADES:
        lines += [f"**Warning: only {len(priced)} priced trades. Under {MIN_TRADES}, none of these numbers "
                  "is evidence of anything.**", ""]
    wins, losses = priced[priced["pnl_pct"] > 0], priced[priced["pnl_pct"] <= 0]
    mean, lo, hi = expectancy_range(priced)
    loss_sum = -losses["pnl_usd"].sum()
    pf = wins["pnl_usd"].sum() / loss_sum if loss_sum > 0 else None
    total = float((1 + returns).prod() - 1) if not returns.empty else None
    spy_ret = spy_return(spy, returns.index[0], returns.index[-1]) if not returns.empty else None
    rng = f" (95% range {lo:+.2f} to {hi:+.2f}, by entry date)" if lo is not None else ""
    lines += [
        "## All time", "",
        f"- Win rate: {len(wins) / len(priced):.0%}" if len(priced) else "- Win rate: n/a",
        f"- Average win: {wins['pnl_pct'].mean():+.2f}% / average loss: {losses['pnl_pct'].mean():+.2f}%"
        if len(wins) and len(losses) else "- Average win / loss: n/a",
        f"- Expectancy per trade: {mean:+.2f}%{rng}" if mean is not None else "- Expectancy per trade: n/a",
        f"- Profit factor: {pf:.2f}" if pf is not None else "- Profit factor: n/a",
        f"- Total return: {_pct(total)}",
        f"- Max drawdown: {_pct(max_drawdown(returns))}",
        f"- Worst day: {_pct(returns.min() if not returns.empty else None)}",
        f"- SPY buy-and-hold, same period: {_pct(spy_ret)}", "",
    ]
    if len(priced):
        priced["score_bucket"] = priced["score"].map(lambda s: "unknown" if pd.isna(s) else f"{int(s):+d}")
        priced["confidence_band"] = priced["confidence"].map(confidence_band)
        priced["prompt"] = priced["prompt_sha256"].fillna("unknown").astype(str).str[:12]
        priced["memory"] = (priced["memory_sha256"] if "memory_sha256" in priced else pd.Series("", index=priced.index))
        priced["memory"] = priced["memory"].fillna("").astype(str).str[:12].replace("", "none")
        for col, name in (("score_bucket", "Score"), ("confidence_band", "Confidence"), ("side", "Side"),
                          ("exit_reason", "Exit reason"), ("symbol", "Symbol"), ("prompt", "Prompt sha256"), ("memory", "Memory version")):
            lines += [f"## By {name.lower()}", "", _table(breakdown(priced, col), name), ""]
    lines.append("Paper fills are optimistic and paper shorts have no borrow costs. Not financial advice.")
    return "\n".join(lines) + "\n"


def load_spy(start, end):
    """SPY daily bars from the cache or Alpaca; None when unavailable."""
    import config
    from broker import load_bars, make_broker

    try:
        broker = make_broker(config.load_config(), verify_account=False)
    except Exception:
        broker = None
    try:
        return load_bars(["SPY"], start - timedelta(days=7), end, broker).get("SPY")
    except Exception:
        return None


def main(argv=None):
    import config

    ap = argparse.ArgumentParser(description="performance report")
    ap.add_argument("--book", choices=["paper", "dry_run", "replay"])
    args = ap.parse_args(argv)
    book = args.book or config.load_config()["mode"]
    d = paths.book_dir(book)
    trades = read_trades_frame(d / "trades.csv")
    summary = read_summary(d / "daily_summary.csv")
    spy = load_spy(summary["date"].iloc[0], summary["date"].iloc[-1]) if not summary.empty else None
    from logger import Book
    orders = Book(d, book).order_events()
    report = build_report(trades, summary, spy, title=f"Sentiment trader report ({book})", orders=orders)
    paths.reports_dir().mkdir(parents=True, exist_ok=True)
    out = paths.reports_dir() / f"report_{datetime.now(ET).date().isoformat()}.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
