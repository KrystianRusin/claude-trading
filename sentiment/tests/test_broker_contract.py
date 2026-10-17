"""The contract every execution backend must meet; the trader relies on all of it.

Each backend runs through a harness that can settle (fill) working market orders:
- sim: SimBroker on flat prices at 100 (it fills market orders at once, so settling does nothing)
- alpaca: AlpacaExecution on tests/fake_alpaca.py, so the Alpaca status and error translation is
  tested without network
A new backend (Interactive Brokers) adds a harness here and must pass before it trades.
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from alpaca_backend import AlpacaExecution  # noqa: E402
from broker import (CANCELED, ET, FILLED, STATUSES, Account, Capabilities, DuplicateOrderError,  # noqa: E402
                    ExecutionBroker, Position)
from fake_alpaca import FakeTrading  # noqa: E402
from simbroker import SimBroker  # noqa: E402

NOW = datetime(2026, 10, 14, 10, 0, tzinfo=ET)


class FlatData:
    def latest_prices(self, symbols):
        return {s: 100.0 for s in symbols}

    def trading_days(self, start, end):
        return [NOW.date()]

    def daily_bars(self, symbols, start, end):
        return {}

    def asset_flags(self, symbol):
        return True, True, True

    def get_news(self, symbols, start, end):
        return []


class SimHarness:
    def __init__(self, tmp_path):
        self.broker = SimBroker(FlatData(), tmp_path / "sim_account.json", clock=lambda: NOW)

    def settle(self):
        pass


class AlpacaHarness:
    def __init__(self, tmp_path):
        self.trading = FakeTrading()
        self.broker = AlpacaExecution(trading=self.trading)

    def settle(self):
        self.trading.settle(100.0)


@pytest.fixture(params=[SimHarness, AlpacaHarness], ids=["sim", "alpaca"])
def h(request, tmp_path):
    return request.param(tmp_path)


def neutral(order):
    """Every status the backend reports, the order's and its legs', is a broker-neutral one."""
    for o in (order, *order.legs):
        assert o.status in STATUSES, o.status
    return order


def test_is_an_execution_broker_on_paper(h):
    assert isinstance(h.broker, ExecutionBroker)
    assert isinstance(h.broker.capabilities, Capabilities)
    assert h.broker.is_paper() is True


def test_account(h):
    a = h.broker.get_account()
    assert isinstance(a, Account) and isinstance(a.account_number, str) and a.account_number
    assert a.equity > 0 and a.last_equity > 0


def test_market_order_fills_into_a_position(h):
    o = neutral(h.broker.submit_market_order("AAPL", "buy", 10, "c-buy"))
    assert (o.symbol, o.side, o.qty, o.client_order_id, o.order_type) == ("AAPL", "buy", 10, "c-buy", "market")
    h.settle()
    filled = neutral(h.broker.wait_for_fill(o.id, wait=0))
    assert filled.status == FILLED and filled.filled_qty == 10 and filled.filled_avg_price == pytest.approx(100)
    assert not filled.is_open and filled.filled_at is not None
    [p] = h.broker.list_positions().values()
    assert isinstance(p, Position) and (p.symbol, p.qty, p.avg_entry_price) == ("AAPL", 10, 100)
    assert h.broker.get_order_by_client_id("c-buy").id == o.id


def test_short_position_has_negative_qty(h):
    h.broker.submit_market_order("AAPL", "sell", 5, "c-short")
    h.settle()
    assert h.broker.list_positions()["AAPL"].qty == -5


def test_reused_client_order_id_raises_duplicate(h):
    h.broker.submit_market_order("AAPL", "buy", 1, "c-dup")
    with pytest.raises(DuplicateOrderError):
        h.broker.submit_market_order("AAPL", "buy", 1, "c-dup")
    with pytest.raises(DuplicateOrderError):
        h.broker.submit_stop_order("AAPL", "sell", 1, 95.0, "c-dup")
    with pytest.raises(DuplicateOrderError):
        h.broker.submit_entry("AAPL", "long", 1, 95.0, "c-dup")


def test_unknown_client_order_id_is_none(h):
    assert h.broker.get_order_by_client_id("never-used") is None


def test_entry_with_attached_stop(h):
    if not h.broker.capabilities.attached_stop:
        pytest.skip("no attached stops")
    o = neutral(h.broker.submit_entry("AAPL", "long", 10, 96.0, "c-entry"))
    assert o.side == "buy" and o.order_type == "market"
    [stop] = o.legs
    assert (stop.order_type, stop.side, stop.qty, stop.stop_price) == ("stop", "sell", 10, 96.0)
    h.settle()
    o = neutral(h.broker.get_order(o.id))
    assert o.status == FILLED and o.legs[0].is_open
    assert h.broker.list_positions()["AAPL"].qty == 10
    open_ids = {x.id for x in h.broker.open_orders()}
    assert stop.id in open_ids and o.id not in open_ids
    assert neutral(h.broker.get_order(stop.id)).stop_price == 96.0  # legs can be fetched by id


def test_short_entry_stop_buys_back(h):
    if not h.broker.capabilities.attached_stop:
        pytest.skip("no attached stops")
    o = h.broker.submit_entry("NKE", "short", 10, 104.0, "c-short-entry")
    assert o.side == "sell" and o.legs[0].side == "buy"


def test_cancel_open_stop_then_cancel_again_is_quiet(h):
    stop = neutral(h.broker.submit_stop_order("AAPL", "sell", 10, 95.0, "c-stop"))
    assert stop.is_open and stop.order_type == "stop" and stop.stop_price == 95.0
    assert stop.id in {o.id for o in h.broker.open_orders()}
    after = neutral(h.broker.cancel_order(stop.id, wait=0))
    assert after.id == stop.id and after.status == CANCELED and not after.is_open
    assert neutral(h.broker.cancel_order(stop.id, wait=0)).status == CANCELED
    assert stop.id not in {o.id for o in h.broker.open_orders()}


def test_cancel_filled_order_is_quiet(h):
    o = h.broker.submit_market_order("AAPL", "buy", 10, "c-filled")
    h.settle()
    assert h.broker.cancel_order(o.id, wait=0).status == FILLED


def test_open_orders_statuses_are_neutral(h):
    h.broker.submit_stop_order("AAPL", "sell", 1, 95.0, "c-s1")
    h.broker.submit_entry("NKE", "long", 1, 95.0, "c-e1")
    h.settle()
    orders = h.broker.open_orders()
    assert orders and all(neutral(o).is_open for o in orders)
