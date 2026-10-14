"""Alpaca paper backend: account, orders, positions, calendar, daily bars and news. Paper only.

Run `python src/broker.py` to check credentials, the account, the calendar and a sample of news.
`python src/broker.py account-number --env <path>` prints the account number for the keys in another
.env file (setup uses it to read the paper-trader's account without printing its keys).
"""
import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import dotenv_values, load_dotenv

import paths

ET = ZoneInfo("America/New_York")
BAR_COLUMNS = ["open", "high", "low", "close", "volume"]
STOCK_EXCHANGES = {"NYSE", "NASDAQ", "AMEX"}
BARS_BATCH = 1000
NEWS_BATCH = 100
OPEN_STATUSES = {"new", "accepted", "pending_new", "partially_filled", "held", "accepted_for_bidding",
                 "pending_cancel", "pending_replace", "done_for_day"}


class LiveEndpointError(RuntimeError):
    pass


class SameAccountError(RuntimeError):
    pass


class DuplicateOrderError(RuntimeError):
    """An order with this client_order_id already exists."""


class OTOUnavailableError(RuntimeError):
    """The OTO entry was rejected for a reason other than a duplicate id."""


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
    status: str
    filled_qty: float = 0.0
    filled_avg_price: float | None = None
    filled_at: datetime | None = None
    stop_price: float | None = None
    legs: tuple = field(default_factory=tuple)

    @property
    def is_open(self):
        return self.status in OPEN_STATUSES


def chunks(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


def assert_paper_url(url):
    if "paper-api" not in str(url):
        raise LiveEndpointError(f"refusing to run: trading endpoint is not a paper endpoint ({url})")


def check_separate_account(own, other):
    """Refuse to trade when this account is the paper-trader's. `other` is "none" when there is none."""
    if not other:
        raise SameAccountError("PAPER_TRADER_ACCOUNT is not set; run scripts/setup.sh")
    if other.strip().lower() != "none" and own.strip() == other.strip():
        raise SameAccountError("these keys belong to the paper-trader's account; use a second paper account")


def load_credentials(env_file=None):
    if env_file:
        values = dotenv_values(env_file)
    else:
        load_dotenv(paths.ENV_FILE)
        values = os.environ
    key, secret = values.get("ALPACA_API_KEY"), values.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in the environment or .env")
    return key, secret


def to_et(ts):
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=ET)
    return ts.astimezone(ET)


def _float(v):
    return None if v is None else float(v)


def _value(v):
    return getattr(v, "value", v)


def order_from_alpaca(o):
    return Order(
        id=str(o.id), client_order_id=o.client_order_id, symbol=o.symbol, side=_value(o.side),
        qty=float(o.qty or 0), order_type=_value(o.order_type or o.type), status=_value(o.status),
        filled_qty=float(o.filled_qty or 0), filled_avg_price=_float(o.filled_avg_price),
        filled_at=to_et(o.filled_at), stop_price=_float(o.stop_price),
        legs=tuple(order_from_alpaca(leg) for leg in (o.legs or [])),
    )


def daily_frames(df):
    """Split alpaca's (symbol, timestamp) bar frame into {symbol: frame indexed by ET date}."""
    out = {}
    if df is None or df.empty:
        return out
    for symbol, g in df.groupby(level=0):
        g = g.droplevel(0)
        g.index = pd.Index([to_et(t.to_pydatetime()).date() for t in g.index], name="date")
        out[symbol] = g[BAR_COLUMNS].sort_index()
    return out


def article_from_alpaca(n):
    return {"id": str(n.id), "published_at": to_et(n.created_at).isoformat(), "symbols": list(n.symbols or []),
            "headline": n.headline or "", "summary": n.summary or "", "source": n.source or "",
            "url": n.url or ""}


class AlpacaBroker:
    """Trading and data through Alpaca's paper API. All datetimes are ET-aware."""

    def __init__(self, feed="iex", env_file=None):
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.historical.news import NewsClient
        from alpaca.trading.client import TradingClient

        key, secret = load_credentials(env_file)
        self.trading = TradingClient(key, secret, paper=True)
        assert_paper_url(self.base_url)
        self.data = StockHistoricalDataClient(key, secret)
        self.news = NewsClient(key, secret)
        self.feed = feed

    @property
    def base_url(self):
        url = self.trading._base_url
        return str(getattr(url, "value", url))

    def verify_separate_account(self):
        check_separate_account(self.get_account().account_number, os.environ.get("PAPER_TRADER_ACCOUNT", ""))

    def get_account(self):
        assert_paper_url(self.base_url)
        a = self.trading.get_account()
        if a.trading_blocked or a.account_blocked:
            raise RuntimeError("paper account is blocked from trading")
        return Account(account_number=str(a.account_number), equity=float(a.equity),
                       last_equity=float(a.last_equity), shorting_enabled=bool(a.shorting_enabled))

    def trading_days(self, start, end):
        from alpaca.trading.requests import GetCalendarRequest

        return [d.date for d in self.trading.get_calendar(GetCalendarRequest(start=start, end=end))]

    def daily_bars(self, symbols, start, end):
        """{symbol: daily bars indexed by date}, fetched in batches. `end` is capped 16 minutes ago for free IEX data."""
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        end = min(to_et(end), datetime.now(ET) - timedelta(minutes=16))
        out = {}
        for batch in chunks(list(symbols), BARS_BATCH):
            req = StockBarsRequest(symbol_or_symbols=batch, timeframe=TimeFrame.Day,
                                   start=to_et(start), end=end, feed=DataFeed(self.feed))
            out.update(daily_frames(self.data.get_stock_bars(req).df))
        return out

    def latest_prices(self, symbols):
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockLatestTradeRequest

        trades = self.data.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=list(symbols), feed=DataFeed(self.feed)))
        return {s: float(t.price) for s, t in trades.items()}

    def asset_flags(self, symbol):
        """(tradable, shortable, easy_to_borrow)."""
        a = self.trading.get_asset(symbol)
        return bool(a.tradable), bool(a.shortable), bool(a.easy_to_borrow)

    def list_equities(self):
        """Active, tradable stocks on NYSE, NASDAQ and NYSE American as dicts."""
        from alpaca.trading.enums import AssetClass, AssetStatus
        from alpaca.trading.requests import GetAssetsRequest

        assets = self.trading.get_all_assets(GetAssetsRequest(status=AssetStatus.ACTIVE,
                                                              asset_class=AssetClass.US_EQUITY))
        return [{"symbol": a.symbol, "name": a.name or "", "exchange": _value(a.exchange),
                 "shortable": bool(a.shortable), "easy_to_borrow": bool(a.easy_to_borrow)}
                for a in assets if a.tradable and _value(a.exchange) in STOCK_EXCHANGES]

    def get_news(self, symbols, start, end):
        """Articles mentioning any of `symbols`, requested in batches; one article may come back twice."""
        from alpaca.data.requests import NewsRequest

        out = []
        for batch in chunks(list(symbols), NEWS_BATCH):
            req = NewsRequest(symbols=",".join(batch), start=to_et(start), end=to_et(end), sort="asc",
                              include_content=False)
            out += [article_from_alpaca(n) for n in self.news.get_news(req).data.get("news", [])]
        return out

    def _submit(self, req):
        from alpaca.common.exceptions import APIError

        try:
            return order_from_alpaca(self.trading.submit_order(req))
        except APIError as e:
            if "client_order_id must be unique" in str(e):
                raise DuplicateOrderError(req.client_order_id) from e
            raise

    def submit_entry(self, symbol, side, qty, stop_price, client_order_id):
        """Market entry with a GTC protective stop as one OTO order; side is "long" or "short"."""
        from alpaca.common.exceptions import APIError
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest, StopLossRequest

        req = MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide.BUY if side == "long" else OrderSide.SELL,
                                 time_in_force=TimeInForce.GTC, order_class=OrderClass.OTO,
                                 client_order_id=client_order_id, stop_loss=StopLossRequest(stop_price=stop_price))
        try:
            return self._submit(req)
        except APIError as e:
            raise OTOUnavailableError(str(e)) from e

    def submit_market_order(self, symbol, side, qty, client_order_id):
        """Plain DAY market order; side is "buy" or "sell". After hours it queues for the next open."""
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        return self._submit(MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide(side),
                                               time_in_force=TimeInForce.DAY, client_order_id=client_order_id))

    def submit_stop_order(self, symbol, side, qty, stop_price, client_order_id):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import StopOrderRequest

        return self._submit(StopOrderRequest(symbol=symbol, qty=qty, side=OrderSide(side), stop_price=stop_price,
                                             time_in_force=TimeInForce.GTC, client_order_id=client_order_id))

    def get_order(self, order_id):
        from alpaca.trading.requests import GetOrderByIdRequest

        return order_from_alpaca(self.trading.get_order_by_id(order_id, filter=GetOrderByIdRequest(nested=True)))

    def get_order_by_client_id(self, client_order_id):
        from alpaca.common.exceptions import APIError

        try:
            o = self.trading.get_order_by_client_id(client_order_id)
        except APIError:
            return None
        return self.get_order(o.id)

    def open_orders(self):
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = self.trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=False, limit=500))
        return [order_from_alpaca(o) for o in orders]

    def cancel_order(self, order_id, wait=10.0):
        """Cancel and wait until the order is no longer open, so its shares are released."""
        from alpaca.common.exceptions import APIError

        try:
            self.trading.cancel_order_by_id(order_id)
        except APIError:
            pass
        deadline = time.monotonic() + wait
        order = self.get_order(order_id)
        while order.is_open and time.monotonic() < deadline:
            time.sleep(0.5)
            order = self.get_order(order_id)
        return order

    def wait_for_fill(self, order_id, wait=10.0):
        deadline = time.monotonic() + wait
        order = self.get_order(order_id)
        while order.is_open and time.monotonic() < deadline:
            time.sleep(0.5)
            order = self.get_order(order_id)
        return order

    def list_positions(self):
        return {p.symbol: Position(p.symbol, float(p.qty), float(p.avg_entry_price), _float(p.current_price))
                for p in self.trading.get_all_positions()}

    def on_close(self, day):
        """End-of-day hook; the simulator uses it to trigger stops. Nothing to do for a real account."""


def make_broker(cfg, verify_account=True):
    load_dotenv(paths.ENV_FILE)
    broker = AlpacaBroker(feed=cfg.get("data_feed", "iex"))
    if verify_account:
        broker.verify_separate_account()
    return broker


def load_bars(symbols, start, end, broker=None):
    """Daily bars from the cache in data/bars/, fetching and saving missing ranges when a broker is given."""
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
    return {s: df[(df.index >= start) & (df.index <= end)] for s, df in out.items() if df is not None}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", nargs="?", default="check", choices=["check", "account-number"])
    ap.add_argument("--env", help="a .env file to read keys from (account-number only)")
    args = ap.parse_args(argv)
    if args.command == "account-number":
        print(AlpacaBroker(env_file=args.env).get_account().account_number)
        return 0

    import config
    cfg = config.load_config()
    symbols = config.load_watchlist()
    broker = make_broker(cfg)
    acct = broker.get_account()
    print(f"paper account OK: equity={acct.equity} last_equity={acct.last_equity} "
          f"shorting_enabled={acct.shorting_enabled}")
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
    except (LiveEndpointError, SameAccountError, RuntimeError) as e:
        print(e)
        sys.exit(1)
