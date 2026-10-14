"""The tradable universe: the most liquid stocks, rebuilt nightly into data/universe.json.

    python src/universe.py build    screen every listed stock by price and average dollar volume
    python src/universe.py show     print the current universe

The watchlist is always included. Without a universe file, the watchlist alone is the universe.
"""
import argparse
import json
import sys
from datetime import datetime, timedelta

import paths
from broker import ET
from config import SYMBOL_RE

BAR_LOOKBACK_DAYS = 45


def screen(assets, bars, ucfg):
    """Symbols ranked by average dollar volume, keeping those priced and traded above the minimums."""
    ranked = []
    for a in assets:
        sym = a["symbol"]
        df = bars.get(sym)
        if not SYMBOL_RE.match(sym) or df is None or len(df) < ucfg["volume_days"] // 2:
            continue
        recent = df.tail(ucfg["volume_days"])
        price = float(recent["close"].iloc[-1])
        dollar_volume = float((recent["close"] * recent["volume"]).mean())
        if price >= ucfg["min_price"] and dollar_volume >= ucfg["min_dollar_volume"]:
            ranked.append((dollar_volume, sym))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    return [sym for _, sym in ranked[:ucfg["max_symbols"]]]


def build(broker, cfg, now):
    """Screen the market and write data/universe.json. Returns the record."""
    assets = broker.list_equities()
    bars = broker.daily_bars([a["symbol"] for a in assets], now - timedelta(days=BAR_LOOKBACK_DAYS), now)
    symbols = screen(assets, bars, cfg["universe"])
    record = {"generated_at": now.isoformat(), "listed": len(assets), "with_bars": len(bars),
              "criteria": cfg["universe"], "symbols": symbols}
    tmp = paths.universe_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=1))
    tmp.replace(paths.universe_file())
    return record


def load(watchlist):
    """The current universe plus the watchlist; the watchlist alone when no universe has been built."""
    f = paths.universe_file()
    symbols = json.loads(f.read_text())["symbols"] if f.exists() else []
    return list(dict.fromkeys(watchlist + symbols))


def main(argv=None):
    import config
    from broker import make_broker
    from logger import notify, setup_logging

    ap = argparse.ArgumentParser(description="tradable universe")
    ap.add_argument("command", choices=["build", "show"])
    args = ap.parse_args(argv)
    config.init_data()
    log = setup_logging("universe")
    if args.command == "show":
        f = paths.universe_file()
        if not f.exists():
            print("no universe built yet; the watchlist is used")
            return 0
        record = json.loads(f.read_text())
        print(f"built {record['generated_at']}: {len(record['symbols'])} of {record['listed']} listed stocks")
        print(" ".join(record["symbols"]))
        return 0
    try:
        cfg = config.load_config()
        record = build(make_broker(cfg, verify_account=False), cfg, datetime.now(ET))
    except Exception as e:
        log.exception("universe build failed; keeping the previous universe")
        notify(f"universe build failed: {e}")
        return 1
    log.info("universe: %d symbols from %d listed stocks", len(record["symbols"]), record["listed"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
