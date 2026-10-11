from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

import broker


def test_paper_url_required():
    broker.assert_paper_url("https://paper-api.alpaca.markets")
    with pytest.raises(broker.LiveEndpointError):
        broker.assert_paper_url("https://api.alpaca.markets")


def test_separate_account_check():
    broker.check_separate_account("PA111", "PA222")
    broker.check_separate_account("PA111", "none")
    with pytest.raises(broker.SameAccountError):
        broker.check_separate_account("PA111", "PA111")
    with pytest.raises(broker.SameAccountError, match="not set"):
        broker.check_separate_account("PA111", "")


def test_live_client_refused(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setattr(broker.AlpacaBroker, "base_url", property(lambda self: "https://api.alpaca.markets"))
    with pytest.raises(broker.LiveEndpointError):
        broker.AlpacaBroker()


def test_daily_frames_index_by_et_date():
    idx = pd.MultiIndex.from_tuples([("AAPL", pd.Timestamp("2026-10-08 04:00", tz="UTC")),
                                     ("AAPL", pd.Timestamp("2026-10-09 04:00", tz="UTC"))],
                                    names=["symbol", "timestamp"])
    df = pd.DataFrame({"open": [1, 2], "high": [2, 3], "low": [0.5, 1], "close": [1.5, 2.5],
                       "volume": [10, 20], "vwap": [1, 2]}, index=idx)
    out = broker.daily_frames(df)
    assert list(out["AAPL"].index) == [date(2026, 10, 8), date(2026, 10, 9)]
    assert list(out["AAPL"].columns) == broker.BAR_COLUMNS


def test_article_normalised():
    n = SimpleNamespace(id=123, created_at=pd.Timestamp("2026-10-10 02:30", tz="UTC").to_pydatetime(),
                        symbols=["AAPL"], headline="h", summary=None, source="benzinga", url="u")
    a = broker.article_from_alpaca(n)
    assert a["id"] == "123" and a["summary"] == ""
    assert a["published_at"].startswith("2026-10-09T22:30")
