import json
from datetime import date, datetime

import pandas as pd

import config
import paths
import universe
from broker import ET, chunks


def bars(close, volume, n=20):
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": volume},
                        index=pd.Index([date(2026, 9, 1 + i) for i in range(n)], name="date"))


UCFG = {"max_symbols": 2, "min_price": 5, "min_dollar_volume": 1_000_000, "volume_days": 20,
        "max_scored_per_day": 60, "batch_size": 20}


def test_screen_ranks_by_dollar_volume_and_filters():
    assets = [{"symbol": s} for s in ("BIG", "MID", "SMALL", "PENNY", "THIN", "BRK.B", "BAD/X", "NOBARS")]
    data = {"BIG": bars(100, 1_000_000), "MID": bars(50, 100_000), "SMALL": bars(20, 60_000),
            "PENNY": bars(3, 10_000_000), "THIN": bars(100, 1_000_000, n=5), "BRK.B": bars(400, 4000),
            "BAD/X": bars(100, 1_000_000)}
    assert universe.screen(assets, data, UCFG | {"max_symbols": 10}) == ["BIG", "MID", "BRK.B", "SMALL"]
    assert universe.screen(assets, data, UCFG) == ["BIG", "MID"]


class FakeBroker:
    def list_equities(self):
        return [{"symbol": "AAA"}, {"symbol": "BBB"}]

    def daily_bars(self, symbols, start, end):
        return {"AAA": bars(10, 200_000), "BBB": bars(10, 10)}


def test_build_writes_universe_and_load_adds_watchlist(state):
    cfg = config.load_config() | {"universe": UCFG}
    assert universe.load(["AAPL"]) == ["AAPL"]
    rec = universe.build(FakeBroker(), cfg, datetime(2026, 10, 9, 19, 0, tzinfo=ET))
    assert rec["symbols"] == ["AAA"] and rec["listed"] == 2
    assert json.loads(paths.universe_file().read_text())["symbols"] == ["AAA"]
    assert universe.load(["AAPL", "AAA"]) == ["AAPL", "AAA"]
    assert universe.load(["NKE"]) == ["NKE", "AAA"]


def test_chunks():
    assert chunks(list("abcde"), 2) == [["a", "b"], ["c", "d"], ["e"]]
