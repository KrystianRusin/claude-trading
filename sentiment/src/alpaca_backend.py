"""Alpaca backends: market data and news (free IEX feed) and paper-only order execution.

The only module that imports alpaca-py. It translates Alpaca's order statuses and API errors into
the broker-neutral ones in broker.py. Alpaca has no live brokerage for Canadian residents, so the
execution backend always uses the paper endpoint and refuses any other.
"""
import logging
import os
from datetime import datetime, timedelta

import pandas as pd
from dotenv import dotenv_values, load_dotenv

import broker as b
import paths

LOG = logging.getLogger(__name__)
STOCK_EXCHANGES = {"NYSE", "NASDAQ", "AMEX"}
BARS_BATCH = 1000
NEWS_BATCH = 100

# Alpaca order status -> neutral status. tests/test_broker.py checks that every status alpaca-py
# knows is listed.
STATUS_MAP = {
    "new": b.NEW, "accepted": b.NEW, "pending_new": b.NEW, "accepted_for_bidding": b.NEW,
    "pending_replace": b.NEW, "pending_review": b.NEW, "done_for_day": b.NEW, "stopped": b.NEW,
    "held": b.HELD, "suspended": b.HELD,
    "partially_filled": b.PARTIALLY_FILLED, "pending_cancel": b.PENDING_CANCEL,
    "filled": b.FILLED, "canceled": b.CANCELED, "replaced": b.CANCELED, "expired": b.EXPIRED,
    "rejected": b.REJECTED,
    "calculated": None,  # done for the day, settlement pending: filled if fully filled, else expired
}


def assert_paper_url(url):
    if "paper-api" not in str(url):
        raise b.LiveAccountError(f"refusing to run: Alpaca endpoint is not the paper endpoint ({url})")


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


def _float(v):
    return None if v is None else float(v)


def _value(v):
    return getattr(v, "value", v)


def neutral_status(status, qty, filled_qty):
    status = _value(status)
    if status not in STATUS_MAP:
        LOG.warning("unknown Alpaca order status %r; treating the order as open", status)
        return b.NEW
    if STATUS_MAP[status] is None:
        return b.FILLED if qty and filled_qty >= qty else b.EXPIRED
    return STATUS_MAP[status]


def order_from_alpaca(o):
    qty, filled = float(o.qty or 0), float(o.filled_qty or 0)
    return b.Order(
        id=str(o.id), client_order_id=o.client_order_id, symbol=o.symbol, side=_value(o.side),
        qty=qty, order_type=_value(o.order_type or o.type), status=neutral_status(o.status, qty, filled),
        filled_qty=filled, filled_avg_price=_float(o.filled_avg_price),
        filled_at=b.to_et(o.filled_at), stop_price=_float(o.stop_price),
        legs=tuple(order_from_alpaca(leg) for leg in (o.legs or [])),
    )


def daily_frames(df):
    """Split alpaca's (symbol, timestamp) bar frame into {symbol: frame indexed by ET date}."""
    out = {}
    if df is None or df.empty:
        return out
    for symbol, g in df.groupby(level=0):
        g = g.droplevel(0)
        g.index = pd.Index([b.to_et(t.to_pydatetime()).date() for t in g.index], name="date")
        out[symbol] = g[b.BAR_COLUMNS].sort_index()
    return out


def article_from_alpaca(n):
    return {"id": str(n.id), "published_at": b.to_et(n.created_at).isoformat(), "symbols": list(n.symbols or []),
            "headline": n.headline or "", "summary": n.summary or "", "source": n.source or "",
            "url": n.url or ""}


def _translate(e, client_order_id=None):
    """An alpaca APIError as a BrokerError subclass."""
    if client_order_id and "client_order_id must be unique" in str(e):
        return b.DuplicateOrderError(client_order_id)
    if client_order_id and e.status_code in (403, 422):
        return b.OrderRejectedError(str(e))
    return b.BrokerError(f"Alpaca API error ({e.status_code}): {e}")


class AlpacaMarketData(b.MarketData):
    def __init__(self, feed="iex", env_file=None):
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.historical.news import NewsClient
        from alpaca.trading.client import TradingClient

        key, secret = load_credentials(env_file)
        self.trading = TradingClient(key, secret, paper=True)  # calendar and assets
        self.data = StockHistoricalDataClient(key, secret)
        self.news = NewsClient(key, secret)
        self.feed = feed

    def trading_days(self, start, end):
        from alpaca.trading.requests import GetCalendarRequest

        return [d.date for d in self.trading.get_calendar(GetCalendarRequest(start=start, end=end))]

    def daily_bars(self, symbols, start, end):
        """{symbol: daily bars indexed by date}, fetched in batches. `end` is capped 16 minutes ago for free IEX data."""
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        end = min(b.to_et(end), datetime.now(b.ET) - timedelta(minutes=16))
        out = {}
        for batch in b.chunks(list(symbols), BARS_BATCH):
            req = StockBarsRequest(symbol_or_symbols=batch, timeframe=TimeFrame.Day,
                                   start=b.to_et(start), end=end, feed=DataFeed(self.feed))
            out.update(daily_frames(self.data.get_stock_bars(req).df))
        return out

    def latest_prices(self, symbols):
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockLatestTradeRequest

        trades = self.data.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=list(symbols), feed=DataFeed(self.feed)))
        return {s: float(t.price) for s, t in trades.items()}

    def asset_flags(self, symbol):
        a = self.trading.get_asset(symbol)
        return bool(a.tradable), bool(a.shortable), bool(a.easy_to_borrow)

    def list_equities(self):
        """Active, tradable stocks on NYSE, NASDAQ and NYSE American."""
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
        for batch in b.chunks(list(symbols), NEWS_BATCH):
            req = NewsRequest(symbols=",".join(batch), start=b.to_et(start), end=b.to_et(end), sort="asc",
                              include_content=False)
            out += [article_from_alpaca(n) for n in self.news.get_news(req).data.get("news", [])]
        return out


class AlpacaExecution(b.ExecutionBroker):
    capabilities = b.Capabilities(attached_stop=True)

    def __init__(self, env_file=None, trading=None):
        if trading is None:
            from alpaca.trading.client import TradingClient

            key, secret = load_credentials(env_file)
            trading = TradingClient(key, secret, paper=True)
        self.trading = trading
        assert_paper_url(self.base_url)

    @property
    def base_url(self):
        url = self.trading._base_url
        return str(getattr(url, "value", url))

    def is_paper(self):
        return "paper-api" in self.base_url

    def _call(self, fn, *args, client_order_id=None, **kwargs):
        from alpaca.common.exceptions import APIError

        try:
            return fn(*args, **kwargs)
        except APIError as e:
            raise _translate(e, client_order_id) from e

    def get_account(self):
        assert_paper_url(self.base_url)
        a = self._call(self.trading.get_account)
        if a.trading_blocked or a.account_blocked:
            raise b.BrokerError("paper account is blocked from trading")
        return b.Account(account_number=str(a.account_number), equity=float(a.equity),
                         last_equity=float(a.last_equity), shorting_enabled=bool(a.shorting_enabled))

    def _submit(self, req):
        return order_from_alpaca(self._call(self.trading.submit_order, req, client_order_id=req.client_order_id))

    def submit_entry(self, symbol, side, qty, stop_price, client_order_id):
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest, StopLossRequest

        req = MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide.BUY if side == "long" else OrderSide.SELL,
                                 time_in_force=TimeInForce.GTC, order_class=OrderClass.OTO,
                                 client_order_id=client_order_id, stop_loss=StopLossRequest(stop_price=stop_price))
        try:
            return self._submit(req)
        except b.DuplicateOrderError:
            raise
        except b.BrokerError as e:
            raise b.OTOUnavailableError(str(e)) from e

    def submit_market_order(self, symbol, side, qty, client_order_id):
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

        return order_from_alpaca(self._call(self.trading.get_order_by_id, order_id,
                                            filter=GetOrderByIdRequest(nested=True)))

    def get_order_by_client_id(self, client_order_id):
        from alpaca.common.exceptions import APIError

        try:
            o = self.trading.get_order_by_client_id(client_order_id)
        except APIError as e:
            if e.status_code == 404:
                return None
            raise _translate(e) from e
        return self.get_order(o.id)

    def open_orders(self):
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = self._call(self.trading.get_orders,
                            GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=False, limit=500))
        return [order_from_alpaca(o) for o in orders]

    def cancel_order(self, order_id, wait=10.0):
        from alpaca.common.exceptions import APIError

        try:
            self.trading.cancel_order_by_id(order_id)
        except APIError as e:
            if e.status_code != 422:  # 422: already pending cancel, canceled or filled
                raise _translate(e) from e
        return self.wait_for_fill(order_id, wait)

    def list_positions(self):
        return {p.symbol: b.Position(p.symbol, float(p.qty), float(p.avg_entry_price), _float(p.current_price))
                for p in self._call(self.trading.get_all_positions)}
