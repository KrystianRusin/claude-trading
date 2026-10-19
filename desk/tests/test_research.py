"""The symbol page, trade detail and bars, headlines, sentiment research and agent activity (ticket 007)."""
from fastapi.testclient import TestClient

from conftest import DAYS, TODAY, write_settings
from desk import app as app_mod
from desk import research


def test_symbol_page(settings):
    s = research.symbol(settings, "NKE")
    assert s["bots"]["sentiment"]["trades"] == 3 and s["bots"]["sentiment"]["total_pnl"] == 300 and "quant" not in s["bots"]
    assert [x["date"] for x in s["scores"]] == [DAYS[-2].isoformat(), TODAY.isoformat()] and s["scores"][0]["articles"] == 3
    spy = research.symbol(settings, "SPY")
    assert len(spy["signals"]) == 5 and all(x["entered"] for x in spy["signals"])
    assert research.symbol(settings, "AMD")["ideas"][0] == {"date": DAYS[-2].isoformat(), "kind": "theme", "side": "long",
                                                            "conviction": 2, "legs": 1}


def test_symbol_page_without_scores(bots):
    s = research.symbol(write_settings(bots, sections={"scores": False}), "NKE")
    assert s["scores"] == [] and s["ideas"] == []


def test_symbols_are_validated(client):
    assert client.get("/api/symbol?symbol=nke").json()["symbol"] == "NKE"
    for bad in ("../x", "NKE;", "", "A" * 11):
        assert client.get(f"/api/symbol?symbol={bad}").status_code == 422, bad
    assert "NKE" in client.get("/api/symbols/known").json()["symbols"]


def test_trade_detail_has_stop_target_and_its_window(settings):
    first = next(t for t in research.symbol(settings, "SPY")["trades"] if t["date"] == DAYS[0].isoformat())
    d = research.trade_detail(settings, "quant", first["id"])
    assert d["target_price"] == 101.5 and d["window"] == {"timeframe": "1Min", "start": DAYS[0].isoformat(),
                                                          "end": DAYS[0].isoformat()}
    nke = next(t for t in research.symbol(settings, "NKE")["trades"])
    w = research.trade_detail(settings, "sentiment", nke["id"])["window"]
    assert w["timeframe"] == "1Day" and w["start"] < nke["entry_date"] and w["end"] > nke["date"]
    assert research.trade_detail(settings, "quant", 9999) is None


def test_trade_detail_of_a_hidden_book_is_not_found(settings):
    replay_trade = 5  # sentiment's ZZZ trade is in the replay book
    assert research.trade_detail(settings, "sentiment", replay_trade) is None


def test_bars_need_prices_and_a_bounded_range(bots):
    client = TestClient(app_mod.create_app(write_settings(bots)))
    assert client.get(f"/api/bars?symbol=SPY&start={DAYS[0]}&end={DAYS[0]}").status_code == 404
    client = TestClient(app_mod.create_app(write_settings(bots, sections={"prices": True})))
    minute = client.get(f"/api/bars?symbol=SPY&timeframe=1Min&start={DAYS[0]}&end={DAYS[0]}").json()["bars"]
    assert len(minute) == 70 and minute[0]["ts"].endswith("09:30:00-04:00") and minute[0]["high"] == 101
    daily = client.get(f"/api/bars?symbol=NKE&start={DAYS[0]}&end={TODAY}").json()["bars"]
    assert len(daily) == len(DAYS) and daily[-1]["close"] == 100 + len(DAYS) - 1
    assert client.get(f"/api/bars?symbol=SPY&timeframe=1Min&start={DAYS[0]}&end={TODAY}").status_code == 422
    assert client.get(f"/api/bars?symbol=SPY&start={TODAY}&end={DAYS[0]}").status_code == 422


def test_headlines_have_no_summary_and_only_web_links(settings):
    h = research.headlines(settings, "NKE")["headlines"]
    assert len(h) == 5 and h[0]["published_at"] >= h[-1]["published_at"]
    assert all(set(x) == {"published_at", "headline", "source", "url"} for x in h)
    assert [x["url"] for x in h if x["headline"] == "Nike headline 1"] == [None, None]
    assert research.headlines(settings, "TSLA")["headlines"] == []


def test_research(settings):
    r = research.research(settings)
    assert [c["score"] for c in r["calibration"]] == [-2, 2] and r["scored_outcomes"] == 40
    assert r["by_score"]["2"] == {"n": 3, "win_rate": 0.66, "pnl_usd": 300, "expectancy_r": 0.5}
    assert r["news"] == [{"date": DAYS[-2].isoformat(), "articles": 4}, {"date": TODAY.isoformat(), "articles": 3}]
    assert r["leaning"][-1] == {"date": TODAY.isoformat(), "bullish": 1, "bearish": 1, "neutral": 0}
    by_date = {i["date"]: i for i in r["ideas"]}
    assert by_date[DAYS[-2].isoformat()]["outcome"] == "closed" and by_date[DAYS[-2].isoformat()]["pnl_r"] == 0.5
    assert by_date[TODAY.isoformat()]["outcome"] == "not traded"
    assert r["idea_kinds"] == [{"kind": "theme", "ideas": 2, "traded": 1, "closed": 1, "avg_r": 0.5}]
    assert "reason" not in str(r["ideas"])


def test_changelog_entries():
    text = "# Changelog\n\n## 2026-10-14 (review)\n\nA\n\n## 2026-10-13\n\nB\n\n## 2026-10-14 (guard)\n\nC\n"
    assert research.changelog_entries(text) == {"2026-10-14": "A\nC", "2026-10-13": "B"}


def test_agent_activity(settings):
    q = {d["date"]: d for d in research.agent_activity(settings, "quant")["days"]}
    today = q[TODAY.isoformat()]
    assert today["guard"] == {"passed": False, "problems": 2} and today["reviews"][0]["status"] == "failed"
    assert today["changelog"] == "Raised the range filter." and today["journal"]
    assert {(c["kind"], c["reason"]) for c in today["changes"]} == {("watchlist", "review"), ("config", "review")}
    memory_day = q[DAYS[4].isoformat()]["memory"]
    assert {(m["id"], m["status"], m["previous"]) for m in memory_day} == {("M-1", "supported", "hypothesis"),
                                                                          ("M-2", "retired", None)}
    s = research.agent_activity(settings, "sentiment")
    assert all(d["guard"] is None and d["changelog"] is None for d in s["days"])
