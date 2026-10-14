"""Performance report and day report for a book, in the same format as the paper-trader's.

    python src/analyze.py [--book paper|dry_run|replay] [--since DATE] [--config-version N] [--no-baseline]
    python src/analyze.py --day DATE [--book ...] [--no-market]

Writes <book>/reports/report_<date>.md over all closed trades, and <book>/reports/day_<date>.md for
one day: equity and P&L, closed trades, holdings at the close, scores, skipped entries and warnings.
"""
import argparse
import csv
import math
import sys
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

import paths
from broker import ET
from logger import read_trades_frame

MIN_SAMPLE = 30
DEFAULT_EQUITY = 100_000.0
MAX_LOG_LINES = 40
T_95 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36, 8: 2.31, 9: 2.26, 10: 2.23,
        12: 2.18, 15: 2.13, 20: 2.09, 30: 2.04, 60: 2.00, 120: 1.98}


def read_summary(path):
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=["date", "trades", "gross_pnl", "equity_start", "equity_end", "halted"])
    s = pd.read_csv(path)
    s["date"] = pd.to_datetime(s["date"]).dt.date
    return s.sort_values("date").reset_index(drop=True)


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


def filter_trades(df, since=None, config_version=None):
    """Priced trades (with an exit price), optionally since a date or for one config version."""
    df = df[pd.to_numeric(df["pnl_usd"], errors="coerce").notna()]
    if since is not None:
        df = df[df["date"] >= since]
    if config_version is not None:
        df = df[df["config_version"] == config_version]
    return df.sort_values(["exit_time", "symbol"], na_position="first").reset_index(drop=True)


def summary_stats(df):
    pnl = df["pnl_usd"].astype(float)
    r = pd.to_numeric(df["pnl_r"], errors="coerce")
    wins, losses = df[pnl > 0], df[pnl < 0]
    gross_win, gross_loss = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    n = len(df)
    return {
        "n": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / n if n else float("nan"),
        "avg_win_usd": pnl[pnl > 0].mean() if len(wins) else float("nan"),
        "avg_loss_usd": pnl[pnl < 0].mean() if len(losses) else float("nan"),
        "avg_win_r": r[pnl > 0].mean() if len(wins) else float("nan"),
        "avg_loss_r": r[pnl < 0].mean() if len(losses) else float("nan"),
        "expectancy_r": r.mean() if r.notna().any() else float("nan"),
        "expectancy_usd": pnl.mean() if n else float("nan"),
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else float("nan")),
        "total_pnl": pnl.sum(),
        "trading_days": df["date"].nunique(),
    }


def equity_stats(df, start_equity):
    """Equity curve from closed-trade P&L in exit order."""
    pnl = df["pnl_usd"].astype(float).to_numpy()
    equity = start_equity + np.concatenate([[0.0], np.cumsum(pnl)])
    peak = np.maximum.accumulate(equity)
    drawdown = equity - peak
    streak = longest = 0
    for p in pnl:
        streak = streak + 1 if p < 0 else 0
        longest = max(longest, streak)
    return {
        "start_equity": start_equity,
        "end_equity": equity[-1],
        "total_return_pct": (equity[-1] - start_equity) / start_equity * 100,
        "max_drawdown_usd": -drawdown.min(),
        "max_drawdown_pct": -(drawdown / peak).min() * 100,
        "longest_losing_streak": longest,
    }


def t_95(dof):
    """Two-sided 95% Student t critical value, rounded toward the more conservative table entry."""
    return T_95[max(k for k in T_95 if k <= dof)] if dof < 120 else 1.96


def expectancy_ci(df):
    """Mean R per trade with a 95% range that treats each exit day as one cluster.

    Trades closed on the same day share market conditions, so the effective sample size is closer to
    the number of days than the number of trades.
    """
    r = pd.to_numeric(df["pnl_r"], errors="coerce")
    keep = r.notna()
    r, days_col = r[keep], df["date"][keep]
    n, days = len(r), days_col.nunique()
    if n == 0:
        return {"n": 0, "days": 0, "mean": float("nan"), "lo": float("nan"), "hi": float("nan")}
    mean = float(r.mean())
    if days < 2:
        return {"n": n, "days": days, "mean": mean, "lo": float("nan"), "hi": float("nan")}
    resid = (r - mean).groupby(days_col).sum()
    half = t_95(days - 1) * math.sqrt(days / (days - 1) * float((resid ** 2).sum()) / n ** 2)
    return {"n": n, "days": days, "mean": mean, "lo": mean - half, "hi": mean + half}


def fmt_range(ci):
    if math.isnan(ci["lo"]):
        return "n/a (needs 2+ days)"
    return f"[{ci['lo']:+.2f}R, {ci['hi']:+.2f}R]"


def excludes_zero(ci):
    return not math.isnan(ci["lo"]) and (ci["lo"] > 0 or ci["hi"] < 0)


def range_note(ci):
    if math.isnan(ci["lo"]):
        return ""
    if excludes_zero(ci):
        return " (trades grouped by day; clearly away from zero)"
    return " (trades grouped by day; includes zero, so the sign is not established)"


def confidence_band(c):
    if pd.isna(c):
        return "unknown"
    return "<0.5" if c < 0.5 else "0.5-0.7" if c < 0.7 else "0.7-0.9" if c < 0.9 else ">=0.9"


def add_keys(df):
    df = df.copy()
    df["strategy_key"] = df["strategy"].astype(str) + " v" + df["strategy_version"].astype(str)
    df["score_bucket"] = df["score"].map(lambda s: "unknown" if pd.isna(s) else f"{int(s):+d}")
    df["confidence_band"] = df["confidence"].map(confidence_band)
    mem = df["memory_sha256"] if "memory_sha256" in df else pd.Series("", index=df.index)
    df["memory_version"] = mem.fillna("").astype(str).str[:12].replace("", "none")
    df["hold_days"] = pd.to_numeric(df["hold_days"], errors="coerce")
    return df


def breakdown(df, key):
    """Per-group trade count, days, win rate, total $, expectancy in R and its 95% range."""
    columns = ["n", "days", "win_rate", "pnl_usd", "expectancy_r", "range_95", "total_r"]
    if df.empty:
        return pd.DataFrame(columns=columns)
    groups = df.assign(_r=pd.to_numeric(df["pnl_r"], errors="coerce"),
                       _pnl=df["pnl_usd"].astype(float)).groupby(key, sort=True)
    return pd.DataFrame({
        "n": groups.size(),
        "days": groups["date"].nunique(),
        "win_rate": groups["_pnl"].apply(lambda s: (s > 0).mean()),
        "pnl_usd": groups["_pnl"].sum(),
        "expectancy_r": groups["_r"].mean(),
        "range_95": groups.apply(lambda g: fmt_range(expectancy_ci(g)), include_groups=False),
        "total_r": groups["_r"].sum(),
    })[columns]


def r_histogram(r_values, width=0.5, bar_width=40):
    """Text histogram of R multiples in `width`-R bins; each bin is [lo, lo + width)."""
    r = pd.to_numeric(pd.Series(r_values), errors="coerce").dropna()
    if r.empty:
        return "(no R values)"
    lo = math.floor(r.min() / width) * width
    idx = np.floor((r - lo) / width + 1e-9).astype(int)
    counts = np.bincount(idx)
    scale = max(1.0, counts.max() / bar_width)
    return "\n".join(f"{lo + i * width:+5.1f}R to {lo + (i + 1) * width:+5.1f}R | "
                     f"{'#' * math.ceil(c / scale)} {c}" for i, c in enumerate(counts))


def _fmt(v, kind="num"):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    if isinstance(v, float) and math.isinf(v):
        return "inf"
    if kind == "usd":
        return f"${v:,.2f}"
    if kind == "pct":
        return f"{v:.1%}"
    if kind == "r":
        return f"{v:+.2f}R"
    return f"{v:,.2f}" if isinstance(v, float) else str(v)


def _table(frame):
    if frame.empty:
        return "(none)"
    out = frame.copy()
    out["win_rate"] = out["win_rate"].map(lambda v: _fmt(v, "pct"))
    out["pnl_usd"] = out["pnl_usd"].map(lambda v: _fmt(v, "usd"))
    out["expectancy_r"] = out["expectancy_r"].map(lambda v: _fmt(v, "r"))
    out["total_r"] = out["total_r"].map(lambda v: _fmt(v, "r"))
    header = "| " + " | ".join([out.index.name or "group", *out.columns]) + " |"
    sep = "|" + "---|" * (len(out.columns) + 1)
    rows = ["| " + " | ".join([str(i), *map(str, row)]) + " |" for i, row in out.iterrows()]
    return "\n".join([header, sep, *rows])


BREAKDOWNS = [("By strategy", "strategy_key"), ("By config version", "config_version"), ("By ticker", "symbol"),
              ("By side", "side"), ("By exit reason", "exit_reason"), ("By score", "score_bucket"),
              ("By confidence", "confidence_band"), ("By hold days", "hold_days"),
              ("By memory version", "memory_version")]


def build_report(df, start_equity, baseline=None, title="Performance report", filters="", unpriced=0):
    """Markdown report. `baseline` is SPY buy-and-hold return in percent, or an explanation string."""
    lines = [f"# {title}", ""]
    if filters:
        lines += [f"Filters: {filters}", ""]
    n = len(df)
    if n < MIN_SAMPLE:
        lines += [f"**Sample size warning:** only {n} trades (< {MIN_SAMPLE}). "
                  "Conclusions from this report are not statistically meaningful.", ""]
    if unpriced:
        lines += [f"{unpriced} closed trades have no exit price and are left out.", ""]
    if n == 0:
        lines.append("No closed trades in this selection.")
        return "\n".join(lines) + "\n"

    df = add_keys(df)
    s = summary_stats(df)
    e = equity_stats(df, start_equity)
    ci = expectancy_ci(df)
    first, last = df["date"].min(), df["date"].max()
    lines += [
        f"Period: {first} to {last} ({s['trading_days']} trading days with trades)", "",
        "## Summary", "",
        f"- Trades: {s['n']} ({s['wins']} wins, {s['losses']} losses)",
        f"- Win rate: {_fmt(s['win_rate'], 'pct')}",
        f"- Average win: {_fmt(s['avg_win_usd'], 'usd')} ({_fmt(s['avg_win_r'], 'r')})",
        f"- Average loss: {_fmt(s['avg_loss_usd'], 'usd')} ({_fmt(s['avg_loss_r'], 'r')})",
        f"- Expectancy: {_fmt(s['expectancy_r'], 'r')} / {_fmt(s['expectancy_usd'], 'usd')} per trade",
        f"- Expectancy 95% range: {fmt_range(ci)}{range_note(ci)}",
        f"- Profit factor: {_fmt(s['profit_factor'])}",
        f"- Net P&L: {_fmt(s['total_pnl'], 'usd')}",
        "", "## Equity curve", "",
        f"- Start equity: {_fmt(e['start_equity'], 'usd')}",
        f"- Total return: {e['total_return_pct']:+.2f}%",
        f"- Max drawdown: {_fmt(e['max_drawdown_usd'], 'usd')} ({e['max_drawdown_pct']:.2f}%)",
        f"- Longest losing streak: {e['longest_losing_streak']} trades",
        "", "## SPY buy-and-hold baseline", "",
    ]
    if isinstance(baseline, (int, float)):
        lines.append(f"- SPY {first} open to {last} close: {baseline:+.2f}% vs strategy {e['total_return_pct']:+.2f}%")
    else:
        lines.append(f"- Unavailable: {baseline or 'not requested'}")
    for title_, key in BREAKDOWNS:
        lines += ["", f"## {title_}", "", _table(breakdown(df, key))]
    lines += ["", "## R-multiple distribution", "", "```", r_histogram(df["pnl_r"]), "```"]
    return "\n".join(lines) + "\n"


def spy_baseline(first, last):
    """SPY buy-and-hold return in percent from `first` open to `last` close, or an error string."""
    try:
        import config
        from broker import make_broker

        broker = make_broker(config.load_config(), verify_account=False)
        bars = broker.daily_bars(["SPY"], datetime.combine(first, datetime.min.time(), ET),
                                 datetime.combine(last + timedelta(days=1), datetime.min.time(), ET)).get("SPY")
        if bars is None or bars.empty:
            return "no SPY bars returned"
        return (float(bars["close"].iloc[-1]) / float(bars["open"].iloc[0]) - 1) * 100
    except Exception as exc:  # the report is still useful without the baseline
        return f"{type(exc).__name__}: {exc}"


def start_equity_for(summary, first_day, default=DEFAULT_EQUITY):
    s = summary[summary["date"] >= first_day] if not summary.empty else summary
    if not s.empty and pd.notna(s["equity_start"].iloc[0]):
        return float(s["equity_start"].iloc[0])
    return default


# ---- holdings and the day report ----

def holdings_table(rows, today, prices=None):
    """Open positions as a markdown table, marked at `prices` when given, else at the last close mark."""
    rows = [r for r in rows if r.get("status") != "pending"]
    if not rows:
        return "No open positions."
    lines = ["| symbol | side | qty | entry | last | stop | unrealized $ | unrealized % | R | days | score |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    total = 0.0
    for r in sorted(rows, key=lambda r: r["symbol"]):
        entry, qty = float(r["entry_price"]), float(r["qty"])
        last = (prices or {}).get(r["symbol"]) or (float(r["last_price"]) if r.get("last_price") else None)
        direction = 1 if r["side"] == "long" else -1
        stop = float(r["stop_price"]) if r.get("stop_price") else None
        pnl = (last - entry) * direction * qty if last is not None else None
        risk = abs(entry - stop) if stop is not None else 0
        days = sum(1 for _ in pd.bdate_range(r["entry_date"], today)) - 1
        total += pnl or 0
        lines.append(
            f"| {r['symbol']} | {r['side']} | {qty:g} | {r['entry_date']} @ {entry:.2f} | "
            f"{_fmt(last) if last is not None else 'n/a'} | {_fmt(stop) if stop is not None else 'none'} | "
            f"{_fmt(pnl, 'usd') if pnl is not None else 'n/a'} | "
            f"{f'{100 * (last / entry - 1) * direction:+.2f}%' if last is not None else 'n/a'} | "
            f"{_fmt((last - entry) * direction / risk, 'r') if last is not None and risk else 'n/a'} | "
            f"{days} | {r.get('score') or '-'} |")
    lines += ["", f"{len(rows)} positions, unrealized {_fmt(total, 'usd')}."]
    return "\n".join(lines)


def _rows(path, day):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("date") == day.isoformat()]


def _time(ts):
    return ts[11:16] if ts else "?"


def log_lines(path, day, max_lines=MAX_LOG_LINES):
    """WARNING and ERROR lines from sentiment.log for `day`."""
    if not path.exists():
        return []
    prefix = day.isoformat()
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = [ln.rstrip() for ln in f if ln.startswith(prefix) and (" WARNING " in ln or " ERROR " in ln)]
    if len(lines) > max_lines:
        return lines[:max_lines] + [f"... {len(lines) - max_lines} more"]
    return lines


def market_move(day):
    """SPY open-to-close move and intraday range in percent, or an explanation string."""
    try:
        import config
        from broker import make_broker

        start = datetime.combine(day, datetime.min.time(), ET)
        bars = make_broker(config.load_config(), verify_account=False).daily_bars(
            ["SPY"], start, start + timedelta(days=1)).get("SPY")
        if bars is None or bars.empty:
            return "no SPY bar returned"
        b = bars.iloc[0]
        return (f"SPY {(b['close'] / b['open'] - 1) * 100:+.2f}% open to close, "
                f"intraday range {(b['high'] - b['low']) / b['open'] * 100:.2f}%")
    except Exception as exc:  # the report is still useful without it
        return f"unavailable ({type(exc).__name__})"


def build_day_report(book_dir, day, market=None, scores=None):
    trades = _rows(book_dir / "trades.csv", day)
    orders = _rows(book_dir / "orders.csv", day)
    summary = _rows(book_dir / "daily_summary.csv", day)
    lines = [f"# Day report {day}", ""]
    if summary:
        s = summary[-1]
        lines += [f"- Equity {s['equity_start']} -> {s['equity_end']}, halted: {s['halted']}",
                  f"- Day P&L {s.get('day_pnl') or 'n/a'} ({s.get('day_return_pct') or 'n/a'}%): "
                  f"closed trades {s['gross_pnl']}, open positions' unrealized {s.get('unrealized_pnl') or 'n/a'}",
                  f"- Exposure: {s.get('positions')} positions, gross {s.get('gross_exposure')}%, "
                  f"net {s.get('net_exposure')}%",
                  f"- Notes: {s['notes'] or 'none'}"]
    else:
        lines.append("- No daily summary row (no session ran, or it has not finished).")
    lines.append(f"- Market: {market or 'not requested'}")

    lines += ["", "## Trades", ""]
    if trades:
        pnl = [float(t["pnl_usd"]) for t in trades if t["pnl_usd"]]
        r = [float(t["pnl_r"]) for t in trades if t["pnl_r"] not in ("", None)]
        lines += [f"{len(trades)} trades, {sum(p > 0 for p in pnl)} wins, {sum(p < 0 for p in pnl)} losses, "
                  f"net ${sum(pnl):,.2f}, net {sum(r):+.2f}R", "",
                  "| symbol | side | qty | entry | exit | reason | pnl $ | R | days | score |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for t in sorted(trades, key=lambda t: t["exit_time"]):
            lines.append(f"| {t['symbol']} | {t['side']} | {t['qty']} | {t['entry_date']} {_time(t['entry_time'])} "
                         f"@ {t['entry_price']} | {_time(t['exit_time'])} @ {t['exit_price'] or '?'} | "
                         f"{t['exit_reason']} | {t['pnl_usd'] or '-'} | {t['pnl_r'] or '-'} | {t['hold_days']} | "
                         f"{t['score'] or '-'} |")
    else:
        lines.append("No closed trades.")

    lines += ["", "## Holdings at the close", "", holdings_table(_read_positions(book_dir), day)]

    lines += ["", "## Entries", ""]
    entries = [o for o in orders if o["event"] == "submit" and o["client_order_id"].endswith("-entry")]
    lines += [f"- {_time(o['timestamp'])} {o['symbol']} {o['side']} {o['qty']} at ~{o['price']}, stop "
              f"{o['stop_price'] or '?'} ({o['reason']})" for o in entries] or ["None."]

    if scores is not None:
        nonzero = {s: v for s, v in scores.get("scores", {}).items() if v["score"]}
        lines += ["", "## Scores", "",
                  f"{len(scores.get('scores', {}))} symbols scored from {scores.get('input_articles', 0)} articles, "
                  f"{len(nonzero)} non-zero."]
        lines += [f"- {s} {v['score']:+d} ({v['confidence']:.2f}): {v['reason']}"
                  for s, v in sorted(nonzero.items(), key=lambda x: (-abs(x[1]['score']), -x[1]['confidence']))]

    skips = [o for o in orders if o["event"] in ("skip", "reject") or o["event"].startswith("entry_")]
    lines += ["", "## Skipped or rejected signals", ""]
    lines += [f"- {_time(o['timestamp'])} {o['symbol']} {o['side']}: {o['event']} ({o['reason']})"
              for o in skips] or ["None."]

    warnings = log_lines(paths.log_file(), day)
    lines += ["", "## Log warnings and errors", ""]
    lines += [f"    {ln}" for ln in warnings] or ["None."]
    return "\n".join(lines) + "\n"


def _read_positions(book_dir):
    path = book_dir / "positions.csv"
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main(argv=None):
    import config
    import score

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--book", choices=["paper", "dry_run", "replay"], default="paper")
    ap.add_argument("--since", type=date.fromisoformat)
    ap.add_argument("--config-version", type=int)
    ap.add_argument("--day", type=date.fromisoformat, help="write only the day report for this date")
    ap.add_argument("--no-baseline", action="store_true")
    ap.add_argument("--no-market", action="store_true")
    args = ap.parse_args(argv)
    book = args.book
    d = paths.book_dir(book)
    out = paths.reports_dir(book)
    out.mkdir(parents=True, exist_ok=True)
    today = args.day or datetime.now(ET).date()

    market = None if args.no_market else market_move(today)
    day_text = build_day_report(d, today, market, score.load_scores(today))
    (out / f"day_{today.isoformat()}.md").write_text(day_text, encoding="utf-8")
    print(day_text)
    if args.day:
        return 0

    all_trades = read_trades_frame(d / "trades.csv")
    df = filter_trades(all_trades, args.since, args.config_version)
    unpriced = len(all_trades) - len(filter_trades(all_trades))
    filters = ", ".join(f"{k}={v}" for k, v in [("book", book), ("since", args.since),
                                                 ("config_version", args.config_version)] if v)
    summary = read_summary(d / "daily_summary.csv")
    baseline = "skipped (--no-baseline)"
    start_equity = DEFAULT_EQUITY
    if not df.empty:
        start_equity = start_equity_for(summary, df["date"].min())
        if not args.no_baseline:
            baseline = spy_baseline(df["date"].min(), df["date"].max())
    report = build_report(df, start_equity, baseline, title=f"Performance report {today}", filters=filters,
                          unpriced=unpriced)
    path = out / f"report_{today.isoformat()}.md"
    path.write_text(report, encoding="utf-8")
    print(report)
    print(f"written to {path} and {out / f'day_{today.isoformat()}.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
