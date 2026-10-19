"""Pairs: stocks that move together, found automatically, and how far apart they are today.

    python src/pairs.py build    find pairs in the universe from about a year of daily closes
    python src/pairs.py show     print the pairs and the current divergences

A pair qualifies when the daily returns of its two stocks correlate between `min_correlation` and
`max_correlation` (above that they are usually share classes or near-identical funds), and the gap
between their log prices (log A - beta x log B) reverts with a half-life of at most `max_half_life_days`.
Each stock is in at most one pair, the most correlated first. The z-score is how many standard
deviations today's gap is from its average over the formation window.

The nightly universe build runs `build`, writing data/pairs.json.
"""
import argparse
import json
import math
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import paths
from broker import ET, load_bars

MIN_COVERAGE = 0.95


def log_closes(bars, days):
    """Log closes over the last `days` sessions, one column per symbol with at least MIN_COVERAGE of them."""
    closes = pd.DataFrame({s: df["close"] for s, df in bars.items() if df is not None and len(df)})
    closes = closes.sort_index().tail(days)
    closes = closes.dropna(axis=1, thresh=int(len(closes) * MIN_COVERAGE)).ffill().dropna(axis=1)
    return np.log(closes)


def spread_stats(la, lb):
    """(beta, mean, std, half_life) of the spread la - beta * lb; half_life is inf when it does not revert."""
    beta = float(np.cov(la, lb)[0, 1] / np.var(lb, ddof=1))
    spread = la - beta * lb
    mean, std = float(spread.mean()), float(spread.std(ddof=1))
    lagged = spread[:-1] - mean
    b = float(np.dot(lagged, np.diff(spread)) / np.dot(lagged, lagged))
    half_life = -math.log(2) / math.log1p(b) if -1 < b < 0 else math.inf
    return beta, mean, std, half_life


def discover(bars, pcfg):
    """Qualifying pairs, most correlated first, as dicts with the stats needed for a z-score."""
    logp = log_closes(bars, pcfg["formation_days"])
    if logp.shape[1] < 2 or len(logp) < 30:
        return []
    syms = list(logp.columns)
    corr = np.corrcoef(logp.diff().dropna().to_numpy(), rowvar=False)
    i, j = np.triu_indices(len(syms), 1)
    c = corr[i, j]
    keep = (c >= pcfg["min_correlation"]) & (c <= pcfg["max_correlation"])
    out, used = [], set()
    for k in np.argsort(-c[keep]):
        a, b, r = syms[i[keep][k]], syms[j[keep][k]], float(c[keep][k])
        if a in used or b in used:
            continue
        beta, mean, std, half_life = spread_stats(logp[a].to_numpy(), logp[b].to_numpy())
        if std <= 0 or not 0.2 <= beta <= 5 or not 1 <= half_life <= pcfg["max_half_life_days"]:
            continue
        out.append({"pair": f"{a}/{b}", "a": a, "b": b, "beta": round(beta, 6), "mean": round(mean, 6),
                    "std": round(std, 6), "correlation": round(r, 4), "half_life": round(half_life, 2)})
        used |= {a, b}
        if len(out) >= pcfg["max_pairs"]:
            break
    return out


def zscore(beta, mean, std, price_a, price_b):
    return (math.log(price_a) - float(beta) * math.log(price_b) - float(mean)) / float(std)


def divergences(pairs, prices, pcfg):
    """Pairs whose |z| is at least entry_z and under stop_z, widest first, with which side is rich."""
    out = []
    for p in pairs:
        pa, pb = prices.get(p["a"]), prices.get(p["b"])
        if not pa or not pb:
            continue
        z = zscore(p["beta"], p["mean"], p["std"], pa, pb)
        if pcfg["entry_z"] <= abs(z) < pcfg["stop_z"]:
            rich, cheap = (p["a"], p["b"]) if z > 0 else (p["b"], p["a"])
            out.append(p | {"z": round(z, 2), "rich": rich, "cheap": cheap})
    return sorted(out, key=lambda p: -abs(p["z"]))


def load():
    f = paths.pairs_file()
    return json.loads(f.read_text(encoding="utf-8"))["pairs"] if f.exists() else []


def build(broker, cfg, symbols, now):
    """Find pairs among `symbols` and write data/pairs.json. Returns the record."""
    pcfg = cfg["pairs"]
    end = now.date()
    start = end - timedelta(days=int(pcfg["formation_days"] * 1.5) + 10)
    pairs = discover(load_bars(symbols, start, end, broker), pcfg)
    record = {"generated_at": now.isoformat(), "symbols": len(symbols), "criteria": pcfg, "pairs": pairs}
    tmp = paths.pairs_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=1), encoding="utf-8")
    tmp.replace(paths.pairs_file())
    return record


def main(argv=None):
    import config
    import universe
    from broker import make_market_data
    from logger import setup_logging

    ap = argparse.ArgumentParser(description="pairs of stocks that move together")
    ap.add_argument("command", choices=["build", "show"])
    args = ap.parse_args(argv)
    config.init_data()
    log = setup_logging("pairs")
    cfg = config.load_config()
    now = datetime.now(ET)
    if args.command == "build":
        broker = make_market_data(cfg)
        record = build(broker, cfg, universe.load(config.load_watchlist()), now)
        log.info("pairs: %d found among %d symbols", len(record["pairs"]), record["symbols"])
        return 0
    pairs = load()
    if not pairs:
        print("no pairs yet; they are built with the nightly universe")
        return 0
    broker = make_market_data(cfg)
    prices = broker.latest_prices(sorted({s for p in pairs for s in (p["a"], p["b"])}))
    print("| pair | correlation | half-life days | z now |")
    print("|---|---|---|---|")
    for p in pairs:
        pa, pb = prices.get(p["a"]), prices.get(p["b"])
        z = f"{zscore(p['beta'], p['mean'], p['std'], pa, pb):+.2f}" if pa and pb else "n/a"
        print(f"| {p['pair']} | {p['correlation']} | {p['half_life']} | {z} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
