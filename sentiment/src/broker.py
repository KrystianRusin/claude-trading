"""Broker interface: broker-neutral types, the two backend roles, the backend registry and the bar cache.

Run `python src/broker.py` to check credentials, the account, the calendar and a sample of news.
`python src/broker.py account-number --env <path>` prints the account number for the keys in another
.env file (setup uses it to read the paper-trader's account without printing its keys).

A backend fills one or both roles:
- `MarketData`: calendar, daily bars, latest prices, assets and news (Alpaca today).
- `ExecutionBroker`: account, orders and positions.
The trader sees one `Broker` that combines the two, so orders can go to one broker while data and
news come from another. `BROKER_BACKEND` picks the execution backend and `DATA_BACKEND` the data
backend (both default to "alpaca"). Backends translate their order statuses and errors into the
ones below; nothing outside a backend module imports a broker SDK.

Live accounts: every execution backend reports `is_paper()`. A backend connected to a live account
is refused unless ALLOW_LIVE=1 is set in the environment or .env.
"""
import argparse
import importlib
import os
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

import db
import paths

ET = ZoneInfo("America/New_York")
BAR_COLUMNS = ["open", "high", "low", "close", "volume"]

# Order statuses. Backends map theirs onto these.
NEW = "new"  # working at the broker
HELD = "held"  # an attached stop waiting for its entry to fill
PARTIALLY_FILLED = "partially_filled"
PENDING_CANCEL = "pending_cancel"
FILLED = "filled"
CANCELED = "canceled"
REJECTED = "rejected"
EXPIRED = "expired"
OPEN_STATUSES = frozenset({NEW, HELD, PARTIALLY_FILLED, PENDING_CANCEL})
FINAL_STATUSES = frozenset({FILLED, CANCELED, REJECTED, EXPIRED})
STATUSES = OPEN_STATUSES | FINAL_STATUSES


class BrokerError(RuntimeError):
    """A broker refused or failed a request."""


class DuplicateOrderError(BrokerError):
    """An order with this client_order_id already exists."""


class OrderRejectedError(BrokerError):
    """The broker rejected the order (buying power, invalid prices, symbol not tradable, ...)."""


class OTOUnavailableError(BrokerError):
    """The entry with an attached stop was rejected for a reason other than a duplicate id."""


class LiveAccountError(BrokerError):
    """The backend is connected to a live account and ALLOW_LIVE=1 is not set."""


class SameAccountError(RuntimeError):
    pass


@dataclass(frozen=True)
class Capabilities:
    attached_stop: bool  # a market entry with a GTC stop that activates when the entry fills (OTO)


@dataclass(frozen=True)
class Account:
    account_number: str
    equity: float
    last_equity: float  # equity at the previous close
    shorting_enabled: bool = True


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float  # negative for shorts
    avg_entry_price: float
    current_price: float | None = None


@dataclass(frozen=True)
class Order:
    id: str
    client_order_id: str
    symbol: str
    side: str  # "buy" or "sell"
    qty: float
    order_type: str  # "market" or "stop"
    status: str  # one of STATUSES
    filled_qty: float = 0.0
    filled_avg_price: float | None = None
    filled_at: datetime | None = None
    stop_price: float | None = None
    legs: tuple = field(default_factory=tuple)

    @property
    def is_open(self):
        return self.status in OPEN_STATUSES


class MarketData(ABC):
    """Calendar, prices, assets and news. All datetimes are ET-aware."""

    @abstractmethod
    def trading_days(self, start, end):
        """Trading dates from `start` to `end`, inclusive."""

    @abstractmethod
    def daily_bars(self, symbols, start, end):
        """{symbol: daily bars indexed by date}."""

    @abstractmethod
    def latest_prices(self, symbols):
        """{symbol: last trade price}."""

    @abstractmethod
    def asset_flags(self, symbol):
        """(tradable, shortable, easy_to_borrow)."""

    @abstractmethod
    def get_news(self, symbols, start, end):
        """Article dicts (id, published_at, symbols, headline, summary, source, url) mentioning any of `symbols`."""

    def list_equities(self):
        """Active, tradable stocks as dicts (symbol, name, exchange, shortable, easy_to_borrow)."""
        raise NotImplementedError(f"{type(self).__name__} cannot list the equity universe")


class ExecutionBroker(ABC):
    """Account, orders and positions. Statuses are the ones above; errors are BrokerError subclasses."""

    capabilities = Capabilities(attached_stop=False)

    @abstractmethod
    def is_paper(self) -> bool:
        """True when connected to a paper (simulated) account."""

    @abstractmethod
    def get_account(self) -> Account: ...

    @abstractmethod
    def submit_entry(self, symbol, side, qty, stop_price, client_order_id) -> Order:
        """Market entry with an attached GTC stop as one order; side is "long" or "short".

        Only for backends with `capabilities.attached_stop`; the stop is `Order.legs[0]`. Raises
        DuplicateOrderError on a reused id and OTOUnavailableError when the combined order is refused."""

    @abstractmethod
    def submit_market_order(self, symbol, side, qty, client_order_id) -> Order:
        """Plain DAY market order; side is "buy" or "sell". After hours it queues for the next open."""

    @abstractmethod
    def submit_stop_order(self, symbol, side, qty, stop_price, client_order_id) -> Order:
        """GTC stop order; side is "buy" or "sell"."""

    @abstractmethod
    def get_order(self, order_id) -> Order:
        """The order with its legs."""

    @abstractmethod
    def get_order_by_client_id(self, client_order_id) -> Order | None:
        """The order with its legs, or None if no order has this id."""

    @abstractmethod
    def open_orders(self) -> list:
        """Every open order, legs listed as orders of their own."""

    @abstractmethod
    def cancel_order(self, order_id, wait=10.0) -> Order:
        """Cancel and wait up to `wait` seconds until the order is no longer open, so its shares are
        released; returns the order as it then stands. Quiet if the order is already closed."""

    @abstractmethod
    def list_positions(self) -> dict:
        """{symbol: Position}."""

    def wait_for_fill(self, order_id, wait=10.0):
        """Poll until the order is no longer open or `wait` seconds pass; returns the order."""
        deadline = time.monotonic() + wait
        order = self.get_order(order_id)
        while order.is_open and time.monotonic() < deadline:
            time.sleep(0.5)
            order = self.get_order(order_id)
        return order

    def on_close(self, day):
        """End-of-day hook; the simulator uses it to trigger stops. Nothing to do for a real account."""


class Broker(ExecutionBroker, MarketData):
    """Everything the trader needs: one execution role and one data role."""


class CombinedBroker(Broker):
    """A Broker made of an execution backend and a separate data backend."""

    def __init__(self, execution, data):
        self.execution = execution
        self.data = data
        self.capabilities = execution.capabilities

    # data
    def trading_days(self, start, end):
        return self.data.trading_days(start, end)

    def daily_bars(self, symbols, start, end):
        return self.data.daily_bars(symbols, start, end)

    def latest_prices(self, symbols):
        return self.data.latest_prices(symbols)

    def asset_flags(self, symbol):
        return self.data.asset_flags(symbol)

    def get_news(self, symbols, start, end):
        return self.data.get_news(symbols, start, end)

    def list_equities(self):
        return self.data.list_equities()

    # execution
    def is_paper(self):
        return self.execution.is_paper()

    def get_account(self):
        return self.execution.get_account()

    def submit_entry(self, symbol, side, qty, stop_price, client_order_id):
        return self.execution.submit_entry(symbol, side, qty, stop_price, client_order_id)

    def submit_market_order(self, symbol, side, qty, client_order_id):
        return self.execution.submit_market_order(symbol, side, qty, client_order_id)

    def submit_stop_order(self, symbol, side, qty, stop_price, client_order_id):
        return self.execution.submit_stop_order(symbol, side, qty, stop_price, client_order_id)

    def get_order(self, order_id):
        return self.execution.get_order(order_id)

    def get_order_by_client_id(self, client_order_id):
        return self.execution.get_order_by_client_id(client_order_id)

    def open_orders(self):
        return self.execution.open_orders()

    def cancel_order(self, order_id, wait=10.0):
        return self.execution.cancel_order(order_id, wait)

    def list_positions(self):
        return self.execution.list_positions()

    def wait_for_fill(self, order_id, wait=10.0):
        return self.execution.wait_for_fill(order_id, wait)

    def on_close(self, day):
        return self.execution.on_close(day)


def chunks(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


def to_et(ts):
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=ET)
    return ts.astimezone(ET)


def check_separate_account(own, other):
    """Refuse to trade when this account is the paper-trader's. `other` is "none" when there is none."""
    if not other:
        raise SameAccountError("PAPER_TRADER_ACCOUNT is not set; run scripts/setup.sh")
    if other.strip().lower() != "none" and own.strip() == other.strip():
        raise SameAccountError("these keys belong to the paper-trader's account; use a second paper account")


def check_live(execution):
    """Refuse a live account unless ALLOW_LIVE=1."""
    if not execution.is_paper() and os.environ.get("ALLOW_LIVE") != "1":
        raise LiveAccountError(f"refusing to run: {type(execution).__name__} is connected to a live account "
                               "and ALLOW_LIVE=1 is not set")
    return execution


# Backends are "module:Class" so that importing broker never imports a broker SDK.
EXECUTION_BACKENDS = {"alpaca": "alpaca_backend:AlpacaExecution", "ibkr": "ibkr_backend:IBKRExecution"}
DATA_BACKENDS = {"alpaca": "alpaca_backend:AlpacaMarketData"}


def _backend(registry, variable):
    name = os.environ.get(variable, "alpaca")
    if name not in registry:
        raise RuntimeError(f"unknown {variable} {name!r}; available: {sorted(registry)}")
    target = registry[name]
    if isinstance(target, str):
        module, _, cls = target.partition(":")
        target = getattr(importlib.import_module(module), cls)
    return target


def make_execution(verify_account=True):
    """The execution backend named by BROKER_BACKEND, after the live-account check and, unless
    `verify_account` is false, the check that it is not the paper-trader's account."""
    load_dotenv(paths.ENV_FILE)
    execution = check_live(_backend(EXECUTION_BACKENDS, "BROKER_BACKEND")())
    if verify_account:
        check_separate_account(execution.get_account().account_number, os.environ.get("PAPER_TRADER_ACCOUNT", ""))
    return execution


def make_market_data(cfg, feed=None):
    """The data backend named by DATA_BACKEND. Needs no trading access. `feed` overrides the config's, for
    full-market ("sip") history older than 15 minutes, which the free plan includes."""
    load_dotenv(paths.ENV_FILE)
    return _backend(DATA_BACKENDS, "DATA_BACKEND")(feed=feed or cfg.get("data_feed", "iex"))


def make_broker(cfg, verify_account=True):
    return CombinedBroker(make_execution(verify_account), make_market_data(cfg))


def load_bars(symbols, start, end, broker=None):
    """Daily bars from the cache in data/bars/, fetching and saving missing ranges when a data source is given.

    Newly fetched bars are also written to the bars table of data/trader.db."""
    paths.bars_dir().mkdir(parents=True, exist_ok=True)
    out = {}
    missing = []
    for s in symbols:
        f = paths.bars_dir() / f"{s}.csv"
        df = pd.read_csv(f, index_col="date", parse_dates=["date"]) if f.exists() else None
        if df is not None:
            df.index = df.index.date
            df.index.name = "date"
        if df is None or df.empty or df.index.min() > start or df.index.max() < end:
            missing.append(s)
        out[s] = df
    if broker is not None and missing:
        fetched = broker.daily_bars(missing, datetime.combine(start, datetime.min.time(), ET),
                                    datetime.combine(end, datetime.max.time(), ET))
        for s, df in fetched.items():
            old = out.get(s)
            merged = df if old is None else pd.concat([old, df])
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
            merged.to_csv(paths.bars_dir() / f"{s}.csv", index_label="date")
            out[s] = merged
        db.note_bars(fetched)  # for the dashboard's charts
    return {s: df[(df.index >= start) & (df.index <= end)] for s, df in out.items() if df is not None}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", nargs="?", default="check", choices=["check", "account-number"])
    ap.add_argument("--env", help="a .env file to read keys from (account-number only)")
    args = ap.parse_args(argv)
    if args.command == "account-number":
        from alpaca_backend import AlpacaExecution

        print(AlpacaExecution(env_file=args.env).get_account().account_number)
        return 0

    import config
    cfg = config.load_config()
    symbols = config.load_watchlist()
    broker = make_broker(cfg)
    acct = broker.get_account()
    print(f"{type(broker.execution).__name__} {'paper' if broker.is_paper() else 'LIVE'} account OK: "
          f"equity={acct.equity} last_equity={acct.last_equity} shorting_enabled={acct.shorting_enabled}")
    print(f"data from {type(broker.data).__name__}; attached stops: {broker.capabilities.attached_stop}")
    now = datetime.now(ET)
    print(f"trading days this week and next: {broker.trading_days(now.date(), now.date() + timedelta(days=10))}")
    bars = broker.daily_bars(["SPY"], now - timedelta(days=10), now).get("SPY")
    print("SPY daily bars:")
    print(bars.tail(3) if bars is not None else "  none returned")
    news = broker.get_news(symbols[:5], now - timedelta(days=2), now)
    print(f"news for {symbols[:5]} in the last 2 days: {len(news)} articles")
    for a in news[-3:]:
        print(f"  {a['published_at']} {','.join(a['symbols'][:4])}: {a['headline'][:90]}")
    print(f"open positions: {list(broker.list_positions())}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        print(e)
        sys.exit(1)
