"""An in-memory stand-in for alpaca-py's TradingClient, for testing AlpacaExecution without network.

It returns objects shaped like alpaca-py's models (enum fields, string quantities, nested legs) and
raises alpaca-py's APIError the way the paper API does, so the tests exercise the real translation
code in alpaca_backend.py. `settle(price)` fills every accepted market order at `price` and
activates its attached stop.
"""
import itertools
from datetime import datetime, timezone
from types import SimpleNamespace

import requests
from alpaca.common.exceptions import APIError
from alpaca.trading.enums import OrderClass, OrderSide, OrderStatus, OrderType

PAPER_URL = "https://paper-api.alpaca.markets"
WORKING = {OrderStatus.ACCEPTED, OrderStatus.NEW, OrderStatus.HELD}


def api_error(status, code, message):
    response = requests.Response()
    response.status_code = status
    return APIError(f'{{"code":{code},"message":"{message}"}}', requests.HTTPError(response=response))


class FakeTrading:
    def __init__(self, base_url=PAPER_URL, account_number="PA000TEST"):
        self._base_url = base_url
        self.account_number = account_number
        self.orders = []  # top-level orders
        self.positions = {}  # symbol -> [qty, avg]
        self._ids = itertools.count(1)

    def _order(self, symbol, qty, side, order_type, status, stop=None, cid=None):
        return SimpleNamespace(
            id=f"alp-{next(self._ids)}", client_order_id=cid or f"leg-{next(self._ids)}", symbol=symbol,
            side=side, qty=str(qty), order_type=order_type, type=order_type, status=status, filled_qty="0",
            filled_avg_price=None, filled_at=None, created_at=datetime.now(timezone.utc),
            stop_price=None if stop is None else str(stop), legs=None)

    def _all(self):
        for o in self.orders:
            yield o
            yield from o.legs or []

    def _find(self, order_id):
        for o in self._all():
            if o.id == order_id:
                return o
        raise api_error(404, 40410000, "order not found")

    # orders

    def submit_order(self, req):
        if any(o.client_order_id == req.client_order_id for o in self._all()):
            raise api_error(422, 40010001, "client_order_id must be unique")
        if req.qty <= 0:
            raise api_error(422, 40010001, "qty must be > 0")
        if getattr(req, "stop_price", None) is not None:  # a plain stop order
            order = self._order(req.symbol, req.qty, req.side, OrderType.STOP, OrderStatus.NEW,
                                stop=req.stop_price, cid=req.client_order_id)
        else:
            order = self._order(req.symbol, req.qty, req.side, OrderType.MARKET, OrderStatus.ACCEPTED,
                                cid=req.client_order_id)
            if req.order_class == OrderClass.OTO:
                exit_side = OrderSide.SELL if req.side == OrderSide.BUY else OrderSide.BUY
                order.legs = [self._order(req.symbol, req.qty, exit_side, OrderType.STOP, OrderStatus.HELD,
                                          stop=req.stop_loss.stop_price)]
        self.orders.append(order)
        return order

    def get_order_by_id(self, order_id, filter=None):
        return self._find(order_id)

    def get_order_by_client_id(self, client_order_id):
        for o in self._all():
            if o.client_order_id == client_order_id:
                return o
        raise api_error(404, 40410000, "order not found")

    def get_orders(self, req):
        # status=open, nested=False: every open order, legs included as orders of their own
        return [o for o in self._all() if o.status in WORKING]

    def cancel_order_by_id(self, order_id):
        order = self._find(order_id)
        if order.status not in WORKING:
            raise api_error(422, 42210000, f"order is already in {order.status.value} state")
        order.status = OrderStatus.CANCELED
        for leg in order.legs or []:
            if leg.status in WORKING:
                leg.status = OrderStatus.CANCELED

    # account

    def get_account(self):
        return SimpleNamespace(account_number=self.account_number, equity="100000", last_equity="100000",
                               shorting_enabled=True, trading_blocked=False, account_blocked=False)

    def get_all_positions(self):
        return [SimpleNamespace(symbol=s, qty=str(q), avg_entry_price=str(avg), current_price=str(avg))
                for s, (q, avg) in self.positions.items()]

    # test control

    def settle(self, price=100.0):
        for o in self.orders:
            if o.status == OrderStatus.ACCEPTED and o.order_type == OrderType.MARKET:
                o.status, o.filled_qty, o.filled_avg_price = OrderStatus.FILLED, o.qty, str(price)
                o.filled_at = datetime.now(timezone.utc)
                signed = float(o.qty) * (1 if o.side == OrderSide.BUY else -1)
                qty, _ = self.positions.get(o.symbol, [0.0, price])
                if qty + signed:
                    self.positions[o.symbol] = [qty + signed, price]
                else:
                    self.positions.pop(o.symbol, None)
                for leg in o.legs or []:
                    if leg.status == OrderStatus.HELD:
                        leg.status = OrderStatus.NEW
