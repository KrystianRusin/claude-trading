"""CSV books (trades, orders, positions, daily summary), the error log and ntfy alerts."""
import csv
import json
import logging
import os
import urllib.request
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv

import paths

STRATEGY = "sentiment_swing"
STRATEGY_VERSION = 1
# The first 16 trade and 9 summary columns match the paper-trader's files, so both books read the same way.
TRADE_FIELDS = ["date", "symbol", "side", "qty", "entry_time", "entry_price", "exit_time", "exit_price",
                "exit_reason", "pnl_usd", "pnl_r", "range_pct", "entry_minute_after_open", "config_version",
                "strategy", "strategy_version",
                "entry_date", "exit_date", "pnl_pct", "hold_days", "stop_price", "score", "confidence", "score_date",
                "prompt_sha256", "memory_sha256", "held_over_earnings"]
EXIT_REASONS = ("sl", "time", "flip", "halt", "loss_limit", "unknown")
ORDER_FIELDS = ["timestamp", "date", "mode", "event", "symbol", "side", "qty", "order_type", "price",
                "stop_price", "order_id", "client_order_id", "status", "reason"]
POSITION_FIELDS = ["symbol", "side", "qty", "status", "entry_date", "entry_time", "entry_price", "entry_order_id",
                   "stop_order_id", "stop_price", "score", "confidence", "score_date", "config_version",
                   "prompt_sha256", "memory_sha256", "exit_reason", "exit_order_id", "last_price", "unrealized_pnl"]
SUMMARY_FIELDS = ["date", "trades", "wins", "losses", "gross_pnl", "equity_start", "equity_end", "halted", "notes",
                  "day_pnl", "day_return_pct", "unrealized_pnl", "positions", "gross_exposure", "net_exposure",
                  "entries", "scored"]


def _fmt_time(ts):
    return ts.isoformat(timespec="seconds") if ts else ""


def trade_row(position, exit_time, exit_price, exit_reason, hold_days):
    """One trades.csv row from a positions.csv row and how it closed.

    `date` is the exit date. pnl_r is P&L per share over the planned risk per share (entry to stop).
    """
    qty = float(position["qty"])
    entry = float(position["entry_price"])
    direction = 1 if position["side"] == "long" else -1
    priced = exit_price is not None
    per_share = (exit_price - entry) * direction if priced else None
    stop = float(position["stop_price"]) if position.get("stop_price") not in ("", None) else None
    risk = abs(entry - stop) if stop is not None else 0
    entry_time = datetime.fromisoformat(position["entry_time"]) if position.get("entry_time") else None
    minute = int((entry_time - entry_time.replace(hour=9, minute=30, second=0, microsecond=0)).total_seconds()
                 // 60) if entry_time else ""
    return {
        "date": exit_time.date().isoformat(), "symbol": position["symbol"], "side": position["side"],
        "qty": int(qty) if qty.is_integer() else qty, "entry_time": _fmt_time(entry_time),
        "entry_price": round(entry, 4), "exit_time": _fmt_time(exit_time),
        "exit_price": round(exit_price, 4) if priced else "", "exit_reason": exit_reason,
        "pnl_usd": round(per_share * qty, 2) if priced else "",
        "pnl_r": round(per_share / risk, 4) if priced and risk > 0 else "",
        "range_pct": "", "entry_minute_after_open": minute,
        "config_version": position.get("config_version", ""), "strategy": STRATEGY,
        "strategy_version": STRATEGY_VERSION,
        "entry_date": position["entry_date"], "exit_date": exit_time.date().isoformat(),
        "pnl_pct": round(100 * per_share / entry, 4) if priced else "", "hold_days": hold_days,
        "stop_price": stop if stop is not None else "", "score": position.get("score", ""),
        "confidence": position.get("confidence", ""), "score_date": position.get("score_date", ""),
        "prompt_sha256": position.get("prompt_sha256", ""), "memory_sha256": position.get("memory_sha256", ""),
        "held_over_earnings": "",
    }


def _read(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write(path, fields, rows):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def _append(path, fields, row):
    new = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


class Book:
    """One book's files: trades.csv, orders.csv, positions.csv, daily_summary.csv and book_state.json."""

    def __init__(self, directory, mode):
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.trades_path = self.dir / "trades.csv"
        self.orders_path = self.dir / "orders.csv"
        self.positions_path = self.dir / "positions.csv"
        self.summary_path = self.dir / "daily_summary.csv"
        self.state_path = self.dir / "book_state.json"

    def positions(self):
        return _read(self.positions_path)

    def save_positions(self, rows):
        _write(self.positions_path, POSITION_FIELDS, sorted(rows, key=lambda r: r["symbol"]))

    def trade(self, row):
        """Append a closed trade unless the same (symbol, side, entry_date) is already logged."""
        key = (row["symbol"], row["side"], row["entry_date"])
        if any((r["symbol"], r["side"], r["entry_date"]) == key for r in _read(self.trades_path)):
            return False
        _append(self.trades_path, TRADE_FIELDS, row)
        return True

    def trades(self):
        return _read(self.trades_path)

    def order_event(self, now, event, symbol="", **fields):
        row = {"timestamp": now.isoformat(timespec="seconds"), "date": now.date().isoformat(), "mode": self.mode,
               "event": event, "symbol": symbol, **fields}
        _append(self.orders_path, ORDER_FIELDS, row)

    def order_events(self, day=None):
        return [r for r in _read(self.orders_path) if day is None or r["date"] == day.isoformat()]

    def daily_summary(self, row):
        """Write the summary row for row['date'], replacing any earlier row for that date."""
        rows = [r for r in _read(self.summary_path) if r["date"] != row["date"]] + [row]
        _write(self.summary_path, SUMMARY_FIELDS, sorted(rows, key=lambda r: r["date"]))

    def summaries(self):
        return _read(self.summary_path)

    def state(self):
        return json.loads(self.state_path.read_text()) if self.state_path.exists() else {}

    def set_state(self, **values):
        self.state_path.write_text(json.dumps(self.state() | values, indent=2))

    def clear(self):
        for p in (self.trades_path, self.orders_path, self.positions_path, self.summary_path, self.state_path):
            p.unlink(missing_ok=True)


def read_trades_frame(path):
    """trades.csv as a DataFrame with parsed times and dates; an empty frame with the right columns if missing."""
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=TRADE_FIELDS)
    df = pd.read_csv(path)
    for col in ("entry_time", "exit_time"):
        df[col] = pd.to_datetime(df[col], errors="coerce", utc=True).dt.tz_convert("America/New_York")
    for col in ("date", "entry_date", "exit_date"):
        df[col] = pd.to_datetime(df[col]).dt.date
    return df


def setup_logging(name):
    """Console logging at INFO plus warnings and errors with tracebacks in state/sentiment.log. Loads .env."""
    load_dotenv(paths.REPO / ".env")
    log = logging.getLogger(name)
    log.setLevel(logging.INFO)
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
    fmt = logging.Formatter(f"%(asctime)s %(levelname)s {name}: %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    log.addHandler(console)
    paths.state_dir().mkdir(parents=True, exist_ok=True)
    file = logging.FileHandler(paths.log_file(), encoding="utf-8")
    file.setFormatter(fmt)
    file.setLevel(logging.WARNING)
    log.addHandler(file)
    log.propagate = False
    return log


def notify(message):
    """Send an alert to ntfy.sh when NTFY_TOPIC is set. Never raises."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    try:
        req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"),
                                     headers={"Title": "sentiment-trader"})
        urllib.request.urlopen(req, timeout=10).close()
    except Exception:
        logging.getLogger("notify").warning("ntfy alert failed", exc_info=True)
