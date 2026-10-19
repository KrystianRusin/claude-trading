"""Alpaca translation details, the live and same-account gates, and backend selection.

The behaviour every backend shares is in test_broker_contract.py.
"""
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from alpaca.trading.enums import OrderStatus

sys.path.insert(0, str(Path(__file__).resolve().parent))

import alpaca_backend  # noqa: E402
import broker  # noqa: E402
from alpaca_backend import STATUS_MAP, AlpacaExecution, neutral_status  # noqa: E402
from fake_alpaca import FakeTrading, api_error  # noqa: E402


def test_paper_url_required():
    alpaca_backend.assert_paper_url("https://paper-api.alpaca.markets")
    with pytest.raises(broker.LiveAccountError):
        alpaca_backend.assert_paper_url("https://api.alpaca.markets")


def test_live_alpaca_client_refused():
    with pytest.raises(broker.LiveAccountError):
        AlpacaExecution(trading=FakeTrading(base_url="https://api.alpaca.markets"))


def test_separate_account_check():
    broker.check_separate_account("PA111", "PA222")
    broker.check_separate_account("PA111", "none")
    with pytest.raises(broker.SameAccountError):
        broker.check_separate_account("PA111", "PA111")
    with pytest.raises(broker.SameAccountError, match="not set"):
        broker.check_separate_account("PA111", "")


def test_daily_frames_index_by_et_date():
    idx = pd.MultiIndex.from_tuples([("AAPL", pd.Timestamp("2026-10-08 04:00", tz="UTC")),
                                     ("AAPL", pd.Timestamp("2026-10-09 04:00", tz="UTC"))],
                                    names=["symbol", "timestamp"])
    df = pd.DataFrame({"open": [1, 2], "high": [2, 3], "low": [0.5, 1], "close": [1.5, 2.5],
                       "volume": [10, 20], "vwap": [1, 2]}, index=idx)
    out = alpaca_backend.daily_frames(df)
    assert list(out["AAPL"].index) == [date(2026, 10, 8), date(2026, 10, 9)]
    assert list(out["AAPL"].columns) == broker.BAR_COLUMNS


def test_article_normalised():
    n = SimpleNamespace(id=123, created_at=pd.Timestamp("2026-10-10 02:30", tz="UTC").to_pydatetime(),
                        symbols=["AAPL"], headline="h", summary=None, source="benzinga", url="u")
    a = alpaca_backend.article_from_alpaca(n)
    assert a["id"] == "123" and a["summary"] == ""
    assert a["published_at"].startswith("2026-10-09T22:30")


def test_every_alpaca_status_is_mapped():
    assert {s.value for s in OrderStatus} <= set(STATUS_MAP)
    assert {v for v in STATUS_MAP.values() if v} <= broker.STATUSES


def test_calculated_status_depends_on_the_fill():
    assert neutral_status(OrderStatus.CALCULATED, 10, 10) == broker.FILLED
    assert neutral_status(OrderStatus.CALCULATED, 10, 4) == broker.EXPIRED


def test_unknown_status_is_treated_as_open():
    assert neutral_status("some_new_status", 10, 0) == broker.NEW


def raising(error):
    def fn(*a, **kw):
        raise error
    return fn


def test_rejected_oto_entry_raises_oto_unavailable():
    trading = FakeTrading()
    trading.submit_order = raising(api_error(422, 42210000, "order_class oto not supported"))
    with pytest.raises(broker.OTOUnavailableError):
        AlpacaExecution(trading=trading).submit_entry("AAPL", "long", 10, 95.0, "c-1")


def test_rejected_market_order_raises_order_rejected():
    trading = FakeTrading()
    trading.submit_order = raising(api_error(403, 40310000, "insufficient buying power"))
    with pytest.raises(broker.OrderRejectedError):
        AlpacaExecution(trading=trading).submit_market_order("AAPL", "buy", 10, "c-1")


@pytest.mark.parametrize("status", [404, 500])
def test_cancel_errors_other_than_422_raise(status):
    trading = FakeTrading()
    trading.cancel_order_by_id = raising(api_error(status, 50010000, "boom"))
    with pytest.raises(broker.BrokerError):
        AlpacaExecution(trading=trading).cancel_order("abc", wait=0)


def test_client_id_lookup_errors_other_than_404_raise():
    trading = FakeTrading()
    trading.get_order_by_client_id = raising(api_error(500, 50010000, "boom"))
    with pytest.raises(broker.BrokerError):
        AlpacaExecution(trading=trading).get_order_by_client_id("c-1")


@pytest.fixture
def no_dotenv(monkeypatch):
    monkeypatch.setattr(broker, "load_dotenv", lambda *a: None)
    for name in ("BROKER_BACKEND", "DATA_BACKEND", "ALLOW_LIVE", "IBKR_ACCOUNT", "IBKR_PORT"):
        monkeypatch.delenv(name, raising=False)


class LiveBackend:
    def is_paper(self):
        return False


def test_live_account_needs_exactly_allow_live_1(no_dotenv, monkeypatch):
    with pytest.raises(broker.LiveAccountError):
        broker.check_live(LiveBackend())
    monkeypatch.setenv("ALLOW_LIVE", "yes")
    with pytest.raises(broker.LiveAccountError):
        broker.check_live(LiveBackend())
    monkeypatch.setenv("ALLOW_LIVE", "1")
    assert isinstance(broker.check_live(LiveBackend()), LiveBackend)


def test_make_execution_applies_live_and_same_account_gates(no_dotenv, monkeypatch):
    monkeypatch.setitem(broker.EXECUTION_BACKENDS, "fake-live", LiveBackend)
    monkeypatch.setenv("BROKER_BACKEND", "fake-live")
    with pytest.raises(broker.LiveAccountError):
        broker.make_execution(verify_account=False)

    monkeypatch.setitem(broker.EXECUTION_BACKENDS, "fake", lambda: AlpacaExecution(trading=FakeTrading()))
    monkeypatch.setenv("BROKER_BACKEND", "fake")
    monkeypatch.setenv("PAPER_TRADER_ACCOUNT", "PA000TEST")
    with pytest.raises(broker.SameAccountError):
        broker.make_execution()
    monkeypatch.setenv("PAPER_TRADER_ACCOUNT", "none")
    assert broker.make_execution().is_paper()


def test_unknown_backend_is_refused(no_dotenv, monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "nope")
    with pytest.raises(RuntimeError, match="unknown DATA_BACKEND"):
        broker.make_market_data({})


def test_ibkr_stub_can_be_selected(no_dotenv, monkeypatch):
    from ibkr_backend import IBKRExecution

    monkeypatch.setenv("BROKER_BACKEND", "ibkr")
    ib = broker.make_execution(verify_account=False)
    assert isinstance(ib, IBKRExecution) and ib.is_paper() and ib.capabilities.attached_stop
    with pytest.raises(NotImplementedError):
        ib.get_account()


@pytest.mark.parametrize("env", [{"IBKR_ACCOUNT": "U1234567"}, {"IBKR_PORT": "4001"}])
def test_ibkr_live_account_needs_allow_live(no_dotenv, monkeypatch, env):
    monkeypatch.setenv("BROKER_BACKEND", "ibkr")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(broker.LiveAccountError):
        broker.make_execution(verify_account=False)
    monkeypatch.setenv("ALLOW_LIVE", "1")
    assert not broker.make_execution(verify_account=False).is_paper()


def test_combined_broker_routes_each_role():
    data = SimpleNamespace(latest_prices=lambda symbols: {"X": 1.0}, get_news=lambda *a: ["article"])
    combined = broker.CombinedBroker(AlpacaExecution(trading=FakeTrading()), data)
    assert combined.latest_prices(["X"]) == {"X": 1.0} and combined.get_news([], None, None) == ["article"]
    assert combined.get_account().account_number == "PA000TEST" and combined.capabilities.attached_stop


def test_only_the_backend_imports_alpaca():
    import ast

    src = Path(broker.__file__).parent
    for f in sorted(src.glob("*.py")):
        if f.name == "alpaca_backend.py":
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        modules = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        modules |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        modules = {m.split(".")[0] for m in modules}
        assert "alpaca" not in modules, f.name
        assert "alpaca_backend" not in modules or f.name == "broker.py", f.name


class _AssetsByExchange:
    """A trading client that answers one exchange at a time, as Alpaca does with an exchange filter."""

    def __init__(self):
        from types import SimpleNamespace as NS
        self.asked = []
        self.assets = {"NYSE": [NS(symbol="AAA", name="A", exchange="NYSE", tradable=True, shortable=True, easy_to_borrow=True)],
                       "NASDAQ": [NS(symbol="BBB", name="B", exchange="NASDAQ", tradable=True, shortable=False,
                                     easy_to_borrow=False),
                                  NS(symbol="HALT", name="H", exchange="NASDAQ", tradable=False, shortable=False,
                                     easy_to_borrow=False)]}

    def get_all_assets(self, req):
        ex = req.exchange.value
        self.asked.append(ex)
        return self.assets.get(ex, [])


def test_list_equities_asks_one_exchange_at_a_time():
    """One listing on an exchange alpaca-py does not know (TXSE, October 2026) used to break the whole list."""
    import alpaca_backend

    md = alpaca_backend.AlpacaMarketData.__new__(alpaca_backend.AlpacaMarketData)
    md.trading = _AssetsByExchange()
    got = md.list_equities()
    assert {a["symbol"] for a in got} == {"AAA", "BBB"}  # not tradable is left out
    assert "OTC" not in md.trading.asked and {"NYSE", "NASDAQ", "AMEX"} <= set(md.trading.asked)
