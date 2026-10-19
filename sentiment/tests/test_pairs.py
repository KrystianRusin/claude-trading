import json
import math

import numpy as np
import pandas as pd
import pytest

import config
import paths
import pairs

PCFG = config.load_json(paths.CONFIG_DEFAULT)["pairs"]
DAYS = [d.date() for d in pd.bdate_range("2025-06-02", periods=260)]


def frame(closes):
    c = pd.Series(closes, index=pd.Index(DAYS, name="date"))
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1000})


def market(seed=1):
    """A and B share a random walk plus a fast-reverting gap; C walks alone; A2 is a near copy of A."""
    rng = np.random.default_rng(seed)
    common = np.cumsum(rng.normal(0, 0.01, len(DAYS)))
    gap = np.zeros(len(DAYS))
    for t in range(1, len(DAYS)):
        gap[t] = 0.85 * gap[t - 1] + rng.normal(0, 0.004)
    own = np.cumsum(rng.normal(0, 0.01, len(DAYS)))
    a = 100 * np.exp(common + gap)
    b = 50 * np.exp(common)
    return {"A": frame(a), "B": frame(b), "C": frame(80 * np.exp(own)), "A2": frame(a * 1.0001)}


def test_discover_finds_the_reverting_pair_and_skips_share_classes():
    found = pairs.discover(market(), PCFG)
    assert [p["pair"] for p in found] in (["A/B"], ["B/A"], ["A2/B"], ["B/A2"])
    p = found[0]
    assert 0.5 < p["beta"] < 2 and 1 <= p["half_life"] <= PCFG["max_half_life_days"]
    assert PCFG["min_correlation"] <= p["correlation"] <= PCFG["max_correlation"]


def test_spread_stats_half_life():
    rng = np.random.default_rng(3)
    s = np.zeros(2000)
    for t in range(1, len(s)):
        s[t] = 0.5 * s[t - 1] + rng.normal(0, 0.01)
    lb = np.cumsum(rng.normal(0, 0.01, len(s)))
    beta, mean, std, half_life = pairs.spread_stats(lb + s, lb)
    assert abs(beta - 1) < 0.05 and abs(half_life - 1.0) < 0.3


def test_zscore_and_divergences():
    p = {"pair": "KO/PEP", "a": "KO", "b": "PEP", "beta": 1.0, "mean": 0.0, "std": 0.01}
    assert pairs.zscore(1.0, 0.0, 0.01, 100 * math.exp(0.025), 100) == pytest.approx(2.5)
    rich = pairs.divergences([p], {"KO": 100 * math.exp(0.025), "PEP": 100}, PCFG)
    assert rich[0]["rich"] == "KO" and rich[0]["cheap"] == "PEP" and rich[0]["z"] == 2.5
    cheap = pairs.divergences([p], {"KO": 100 * math.exp(-0.03), "PEP": 100}, PCFG)
    assert cheap[0]["rich"] == "PEP" and cheap[0]["z"] == -3.0
    assert pairs.divergences([p], {"KO": 100.0, "PEP": 100.0}, PCFG) == []
    assert pairs.divergences([p], {"KO": 100 * math.exp(0.05), "PEP": 100}, PCFG) == []  # past stop_z
    assert pairs.divergences([p], {"KO": 100.0}, PCFG) == []


def test_build_writes_pairs_file(state, monkeypatch):
    bars = market()
    monkeypatch.setattr(pairs, "load_bars", lambda symbols, start, end, broker: {s: bars[s] for s in symbols})
    record = pairs.build(None, config.load_config(), ["A", "B", "C"], pd.Timestamp("2026-06-01", tz="UTC"))
    assert [p["pair"] for p in record["pairs"]] in (["A/B"], ["B/A"])
    assert json.loads(paths.pairs_file().read_text())["pairs"] == record["pairs"] == pairs.load()
