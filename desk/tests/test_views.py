"""The numbers each view shows."""
import hashlib

import pytest

from conftest import DAYS, SCHEMA, TODAY
from desk import views

# The bots' shared core migrations, as the bots pin them in their tests/test_db.py. If a bot changes
# one, copy the new file into tests/schema/ and update this too.
CORE_SHA256 = {
    "001_core.sql": "07ee2f1a050a5fca03d6c8bcd7ba56ced6fbb9f56addf6e799e4519142593581",
    "002_dashboard.sql": "859a3602ba3a4e4e6274d22450b791222cf56011b824198473bdcb2f59299b05",
    "003_bars.sql": "4ffdf41d3b4d2f948e9b31ba6767efa7462734a1f5903806bd2c7351177d53d3",
}


def test_schema_copy_matches_the_bots():
    assert {f.name for f in SCHEMA.glob("*.sql")} == set(CORE_SHA256)
    for name, sha in CORE_SHA256.items():
        assert hashlib.sha256((SCHEMA / name).read_bytes()).hexdigest() == sha


def test_equity_summary(settings):
    books = {(b["bot"], b["book"]): b for b in views.equity(settings)["books"]}
    q = books[("quant", "paper")]
    assert len(q["daily"]) == len(DAYS) and q["intraday"]["date"] == TODAY.isoformat()
    assert len(q["intraday"]["points"]) == 6
    s = q["summary"]
    last_close = q["daily"][-1]["equity"]
    assert s["equity"] == pytest.approx(last_close + 25)  # the last snapshot of the day
    assert s["start_equity"] == 100_000
    assert s["today_pnl"] == pytest.approx(25 + last_close - q["daily"][-2]["equity"])  # since the day's start
    assert s["max_drawdown_pct"] == pytest.approx(-0.5, abs=0.01)
    assert [p["ts"] for p in books[("sentiment", "paper")]["intraday"]["points"]] == [f"{TODAY}T10:02:00-04:00"]


def test_max_drawdown():
    assert views.max_drawdown_pct([100, 110, 99, 120, 108]) == pytest.approx(-10)
    assert views.max_drawdown_pct([100, 101, None, 102]) == 0


def test_trades_filters_and_stats(settings):
    all_ = views.trades(settings)
    assert all_["total"] == 12 and all_["trades"][0]["date"] >= all_["trades"][-1]["date"]
    q = views.trades(settings, bot="quant", symbol="QQQ")
    assert q["total"] == 3 and q["stats"]["total_pnl"] == 120 and q["stats"]["win_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert views.trades(settings, exit_reason="sl")["total"] == 3
    assert views.trades(settings, side="short")["total"] == 1
    page = views.trades(settings, limit=2, offset=11)
    assert len(page["trades"]) == 1 and page["total"] == 12
    t = views.trades(settings, bot="sentiment", symbol="NKE")["trades"][0]
    assert (t["score"], t["confidence"]) == (2, 0.9) and "prompt_sha256" not in t and "reason" not in t


def test_r_histogram_bins():
    stats = views.trade_stats([{"pnl_usd": 1, "pnl_r": r} for r in (-5, -0.2, 0.0, 0.3, 0.6, 9)])
    counts = {(b["from"], b["to"]): b["count"] for b in stats["r_histogram"]}
    assert counts[(-3.0, -2.5)] == 1 and counts[(2.5, 3.0)] == 1  # outliers go in the end bins
    assert counts[(-0.5, 0.0)] == 1 and counts[(0.0, 0.5)] == 2 and counts[(0.5, 1.0)] == 1
    assert sum(counts.values()) == 6


def test_symbol_results(settings):
    rows = views.symbol_results(settings, days=None, min_trades=3)["symbols"]
    assert [(r["bot"], r["symbol"]) for r in rows] == [("sentiment", "NKE"), ("quant", "QQQ"), ("quant", "SPY")]
    nke = rows[0]
    assert (nke["trades"], nke["pnl"], nke["avg_r"]) == (3, 300, pytest.approx(0.5))
    assert views.symbol_results(settings, days=7, min_trades=1)["symbols"]  # a window still finds trades
    assert len(views.symbol_results(settings, days=None, min_trades=1)["symbols"]) == 6


def test_hot_right_now_and_disagreement(settings):
    hot = views.hot(settings)
    assert [(s["symbol"], s["signals"], s["entries"]) for s in hot["quant"]] == [("NVDA", 5, 0), ("SPY", 5, 5)]
    assert {s["symbol"]: s["new_on_watchlist"] for s in hot["quant"]} == {"NVDA": True, "SPY": False}
    sent = {s["symbol"]: s for s in hot["sentiment"]}
    assert sent["NKE"]["direction"] == "bullish" and sent["NKE"]["heat"] == pytest.approx(2 * 0.9 + 1 * 0.5)
    assert sent["NKE"]["articles"] == 5 and sent["SPY"]["direction"] == "bearish"
    assert sent["AMD"]["desk_ideas"] == 2
    assert hot["disagree"] == [{"symbol": "SPY", "quant": "long", "sentiment": "bearish"}]


def test_compare(settings):
    c = views.compare(settings)
    assert c["bots"]["quant"]["days"] == len(DAYS) and c["bots"]["sentiment"]["days"] == len(DAYS)
    assert c["bots"]["quant"]["worst_day_pct"] == pytest.approx(-0.5)
    d = c["difference"]
    assert d["days"] == len(DAYS) and d["low_pct"] < d["mean_diff_pct"] < d["high_pct"]
    assert d["verdict"] == "no clear winner yet"
    assert c["curves"]["quant"][0] == {"date": DAYS[0].isoformat(), "return_pct": pytest.approx(1.0)}
    short = views.compare(settings, start=TODAY, end=TODAY)
    assert short["difference"]["verdict"] == "not enough overlapping days"


def test_return_stats_on_a_known_series():
    s = views.return_stats([0.01, -0.01, 0.02])
    assert s["total_return_pct"] == pytest.approx((1.01 * 0.99 * 1.02 - 1) * 100, abs=1e-4)
    assert s["max_drawdown_pct"] == pytest.approx(-1.0) and s["best_day_pct"] == 2.0
    assert views.return_stats([])["days"] == 0


def test_versions(settings):
    v = views.versions(settings)
    changes = v["quant"]["changes"]
    assert [c["kind"] for c in changes] == ["config", "watchlist", "memory", "memory", "config"]
    assert changes[0]["removed"] and changes[0]["name"] == "shadow" and "content" not in changes[0]
    results = {(r["strategy_version"], r["config_version"]): r for r in v["quant"]["results"]}
    assert results[("1", "3")]["trades"] == 4 and results[("2", "3")]["trades"] == 3


def test_ops(settings):
    o = views.ops(settings)
    jobs = {j["job"]: j for j in o["quant"]["jobs"]}
    assert jobs["review"]["failures_7d"] == 1 and jobs["trader"]["last_status"] == "ok"
    trader = next(r for r in o["quant"]["runs"] if r["job"] == "trader")
    assert trader["seconds"] == 6 * 3600 + 50 * 60 and "error" not in trader
    assert {j["job"]: j["last_status"] for j in o["sentiment"]["jobs"]} == {"snapshot": "ok", "score": "lost"}
