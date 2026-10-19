"""Attention signals and the discovery list (ticket 009). No network: HTTP and market data are faked."""
import json
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

import attention
import config
import db
import evidence
import news
import paths
from broker import ET

SESSIONS = [d for d in (date(2026, 9, 1) + timedelta(days=i) for i in range(60)) if d.weekday() < 5]
SESSION = SESSIONS[-1]  # 2026-10-23, a Friday
NOW = datetime.combine(SESSION + timedelta(days=3), datetime.min.time(), ET).replace(hour=6)  # Monday 06:00


def frame(closes, volumes, days=SESSIONS):
    days = days[-len(closes):]
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": volumes},
                        index=pd.Index(days, name="date"))


def steady(n=30, price=50.0, volume=1_000_000):
    return frame([price] * n, [volume * (1 + 0.05 * (i % 3)) for i in range(n)])


@pytest.fixture
def cfg(state):
    return config.load_config()


def archive_volume(cfg, bars):
    session, metrics = attention.volume_metrics(bars, cfg["attention"])
    attention.write_raw("volume", session, metrics)
    return metrics


def test_zscore_is_in_log_space_with_a_floor():
    assert attention.zscore(10, [10] * 20, 0.5) == 0
    assert attention.zscore(100, [10] * 20, 0.5) > 4  # a flat baseline uses the floor, not a zero std
    assert attention.zscore(5, [1, 2], 0.5) is None  # too short to mean anything
    assert attention.zscore(None, [1] * 10, 0.5) is None


def test_volume_metrics(cfg):
    spike = steady()
    spike.iloc[-1, spike.columns.get_loc("volume")] = 8_000_000
    spike.iloc[-1, spike.columns.get_loc("close")] = 55.0
    bars = {"SPIKE": spike, "QUIET": steady(), "PENNY": steady(price=0.5), "THIN": steady(volume=100),
            "STALE": frame([10.0] * 20, [1e6] * 20, SESSIONS[:-1])}
    session, m = attention.volume_metrics(bars, cfg["attention"])
    assert session == SESSION and set(m) == {"SPIKE", "QUIET"}  # cheap, thin and stale symbols are dropped
    assert m["SPIKE"]["volume_z"] > 3 and m["SPIKE"]["rvol"] > 6 and m["SPIKE"]["ret_1d"] == 10.0
    assert abs(m["QUIET"]["volume_z"]) < 1.5 and m["QUIET"]["history"] == 30


def test_archive_is_gzipped_and_keeps_every_fetch(state):
    attention.write_raw("reddit", SESSION, {"AAA": {"mentions": 1}}, stamp="0600")
    attention.write_raw("reddit", SESSION, {"AAA": {"mentions": 5}}, stamp="1800")
    assert [s for _, s, _ in attention.raw_files("reddit")] == ["0600", "1800"]
    assert attention.read_raw("reddit", SESSION) == {"AAA": {"mentions": 5}}  # the day's last fetch
    assert attention.read_raw("reddit", SESSION - timedelta(days=1)) is None


def test_apewisdom_collects_every_page_politely(state, monkeypatch):
    pages = {1: {"pages": 2, "results": [{"ticker": "gme", "mentions": 50, "upvotes": 9, "rank": 1,
                                          "mentions_24h_ago": 10, "rank_24h_ago": 4, "name": "x"}]},
             2: {"pages": 2, "results": [{"ticker": "AMC", "mentions": 3}]}}
    calls = []
    monkeypatch.setattr(attention, "get_json", lambda url, **k: calls.append(url) or pages[int(url.rsplit("/", 1)[1])])
    monkeypatch.setattr(attention.time, "sleep", lambda s: None)
    attention.collect_apewisdom(NOW)
    data = attention.read_raw("reddit", NOW.date())
    assert len(calls) == 2 and data["GME"]["mentions"] == 50 and "name" not in data["GME"]


def test_wikipedia_uses_the_map_and_skips_symbols_without_an_article(cfg, monkeypatch):
    sparql = {"results": {"bindings": [{"ticker": {"value": "AAA"}, "article": {"value": "https://en.wikipedia.org/wiki/Aaa_Inc."}}]}}
    days = [(NOW.date() - timedelta(days=i)).strftime("%Y%m%d00") for i in range(33, 0, -1)]
    views = {"items": [{"timestamp": t, "views": 100} for t in days[:-1]] + [{"timestamp": days[-1], "views": 1500}]}
    urls = []

    def fake(url, **k):
        urls.append(url)
        return sparql if "wikidata" in url else views
    monkeypatch.setattr(attention, "get_json", fake)
    attention.collect_wikipedia(["AAA", "NOARTICLE"], cfg, NOW, pause=0)
    got = attention.read_raw("wikipedia", NOW.date() - timedelta(days=1))
    assert set(got) == {"AAA"} and got["AAA"]["views"] == 1500 and got["AAA"]["wiki_z"] > 3
    assert any("Aaa_Inc." in u for u in urls)
    attention.collect_wikipedia(["AAA"], cfg, NOW, pause=0)
    assert sum("wikidata" in u for u in urls) == 1  # the map is cached for a week


def article(i, day, sym):
    return {"id": f"{sym}{i}{day}", "published_at": datetime.combine(day, datetime.min.time(), ET).replace(hour=10).isoformat(),
            "symbols": [sym], "headline": "h", "summary": "", "source": "x", "url": "u"}


def reddit_history(days, counts):
    for i, d in enumerate(days):
        attention.write_raw("reddit", d, {s: {"mentions": n(i) if callable(n) else n} for s, n in counts.items()}, stamp="1800")


def build_world(cfg, monkeypatch):
    """A universe name (AAA), a genuine small-cap surge (HOT: volume, news and Reddit), a Reddit-only pump
    (PUMP), a word ticker (DTE) and one that already ran up (RAN)."""
    paths.universe_file().write_text(json.dumps({"symbols": ["AAA"]}))
    surge = steady(price=12.0, volume=400_000)
    surge.iloc[-1, surge.columns.get_loc("volume")] = 4_000_000
    pump = steady(price=8.0, volume=900_000)
    ran = steady(price=10.0, volume=900_000)
    ran.iloc[-6:, ran.columns.get_loc("close")] = [10, 11, 12, 13, 14, 16]
    ran.iloc[-1, ran.columns.get_loc("volume")] = 9_000_000
    dte = steady(price=90.0, volume=500_000)
    bars = {"AAA": steady(), "HOT": surge, "PUMP": pump, "RAN": ran, "DTE": dte}
    archive_volume(cfg, bars)
    news.store([article(i, SESSION, "HOT") for i in range(6)] + [article(i, SESSION, "RAN") for i in range(6)]
               + [article(0, SESSIONS[-10], "HOT")])
    days = [SESSION - timedelta(days=i) for i in range(15, 0, -1)]
    reddit_history(days, {"PUMP": 2, "HOT": 1, "DTE": 3})
    attention.write_raw("reddit", NOW.date(), {"PUMP": {"mentions": 400}, "HOT": {"mentions": 60}, "DTE": {"mentions": 300},
                                               "RAN": {"mentions": 80}}, stamp="0600")
    return bars


def test_features(cfg, monkeypatch):
    build_world(cfg, monkeypatch)
    rows = {r["symbol"]: r for r in attention.features(SESSION, cfg, {"AAA"}, NOW, denylist={"DTE"})}
    assert rows["AAA"]["in_universe"] == 1 and rows["AAA"]["breadth"] == 0
    hot = rows["HOT"]
    assert hot["in_universe"] == 0 and hot["volume_z"] > 2 and hot["news_z"] > 2 and hot["reddit_z"] > 2
    assert hot["breadth"] == 3 and hot["articles"] == 6 and hot["mentions"] == 60
    assert rows["PUMP"]["reddit_z"] > 2 and rows["PUMP"]["breadth"] == 1
    assert rows["DTE"]["reddit_z"] is None and rows["DTE"]["mentions"] == 300  # a word: needs volume or news too


def test_reddit_waits_for_enough_history(cfg, monkeypatch):
    build_world(cfg, monkeypatch)
    for _, _, f in attention.raw_files("reddit")[:-9]:
        f.unlink()
    rows = {r["symbol"]: r for r in attention.features(SESSION, cfg, {"AAA"}, NOW)}
    assert rows["PUMP"]["reddit_z"] is None and rows["PUMP"]["mentions"] == 400


def test_discovery_keeps_only_genuine_tradeable_surges(cfg, monkeypatch):
    build_world(cfg, monkeypatch)
    cfg["discovery"]["min_dollar_volume"] = 1_000_000
    rows = attention.features(SESSION, cfg, {"AAA"}, NOW, denylist={"DTE"})
    rec = attention.discover(rows, SESSION, cfg, NOW.date())
    assert [n["symbol"] for n in rec["names"]] == ["HOT"]
    assert rec["collect_only"] and rec["session"] == SESSION.isoformat()
    assert {"volume", "news", "reddit"} == {r.split()[0] for r in rec["names"][0]["reasons"]}
    assert rec["rejected"]["already ran up"] == 1 and rec["rejected"]["too few sources"] >= 1
    assert attention.latest_discovery(NOW.date()) == ["HOT"]
    saved = json.loads((paths.discovery_dir() / f"{NOW.date()}.json").read_text())
    assert saved["names"][0]["symbol"] == "HOT"


def test_discovery_respects_the_dollar_volume_floor(cfg, monkeypatch):
    build_world(cfg, monkeypatch)  # HOT trades about $5M a day on average, under the default floor with its quiet days
    cfg["discovery"]["min_dollar_volume"] = 50_000_000
    rows = attention.features(SESSION, cfg, {"AAA"}, NOW)
    assert attention.discover(rows, SESSION, cfg, NOW.date())["names"] == []


def test_features_go_to_the_database_and_acceleration_uses_the_session_before(cfg, monkeypatch):
    build_world(cfg, monkeypatch)
    prev = SESSIONS[-2]
    db.note_attention([{"date": prev.isoformat(), "symbol": "HOT", "in_universe": 0, "volume_z": 1.0, "breadth": 0}])
    attention.write_raw("volume", prev, {"HOT": {"open": 12, "close": 12, "volume_z": 1.0}})
    rows = attention.run_features(cfg, SESSION, NOW)
    hot = next(r for r in rows if r["symbol"] == "HOT")
    top = max(hot["volume_z"], hot["news_z"], hot["reddit_z"])
    assert hot["acceleration"] == pytest.approx(top - 1.0, abs=1e-3)
    stored = [r for r in db.read_attention() if r["date"] == SESSION.isoformat()]
    assert {r["symbol"] for r in stored} >= {"AAA", "HOT"}


def test_morning_keeps_going_when_a_source_fails(cfg, monkeypatch):
    build_world(cfg, monkeypatch)

    def broken(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(attention, "get_json", broken)
    monkeypatch.setattr(attention.time, "sleep", lambda s: None)
    messages, failures = attention.morning(cfg, NOW)
    assert failures == 2  # Reddit and Wikipedia (no ticker map yet)
    assert any(m.startswith("features:") for m in messages) and any(m.startswith("discovery:") for m in messages)


def test_morning_skips_a_stale_session(cfg, monkeypatch):
    build_world(cfg, monkeypatch)
    monkeypatch.setattr(attention, "get_json", lambda *a, **k: {"pages": 1, "results": []})
    monkeypatch.setattr(attention.time, "sleep", lambda s: None)
    messages, _ = attention.morning(cfg, NOW + timedelta(days=7))
    assert any("too old" in m for m in messages) and not any(m.startswith("discovery") for m in messages)


def test_forward_returns_enter_at_the_next_open(state):
    for i, d in enumerate(SESSIONS[-4:]):
        attention.write_raw("volume", d, {"AAA": {"open": 100 + i, "close": 101 + i}})
    fwd = attention.forward_returns(today=SESSION + timedelta(days=1))
    first = fwd[(SESSIONS[-4].isoformat(), "AAA")]
    assert first[1] == pytest.approx((102 / 101 - 1) * 100, abs=1e-3)  # open of the next session, close that day
    assert first[3] == pytest.approx((104 / 101 - 1) * 100, abs=1e-3)
    assert 5 not in first


def test_calibration(cfg, monkeypatch):
    for i, d in enumerate(SESSIONS[-6:]):
        attention.write_raw("volume", d, {"AAA": {"open": 100 + i, "close": 100.5 + i}, "BBB": {"open": 50, "close": 50}})
    day = SESSIONS[-6].isoformat()
    db.note_attention([{"date": day, "symbol": "AAA", "in_universe": 1, "volume_z": 3.2, "breadth": 2, "ret_1d": 1.0},
                       {"date": day, "symbol": "BBB", "in_universe": 1, "volume_z": 0.1, "breadth": 0, "ret_1d": -1.0}])
    paths.discovery_dir().mkdir(parents=True)
    (paths.discovery_dir() / f"{SESSIONS[-5]}.json").write_text(json.dumps(
        {"date": SESSIONS[-5].isoformat(), "session": day, "names": [{"symbol": "AAA"}]}))
    cal = attention.calibration(today=SESSION + timedelta(days=1))
    assert cal["by"]["breadth"]["2"]["all"]["1d"]["n"] == 1 and cal["by"]["breadth"]["2"]["all"]["1d"]["mean_pct"] > 0
    assert cal["by"]["volume_z"]["3 and up"]["after an up day"]["3d"]["up_rate"] == 1
    assert cal["by"]["breadth"]["0"]["after a down day"]["1d"]["mean_pct"] == 0
    assert cal["discovery"][0]["names"] == 1 and cal["discovery"][0]["1d"]["n"] == 1


def test_evidence_tables_carry_the_attention_known_before(state):
    rows = [{"date": "2026-10-01", "symbol": "AAA", "volume_z": 1.0, "news_z": None, "wiki_z": None, "reddit_z": 2.5,
             "breadth": 1},
            {"date": "2026-10-02", "symbol": "AAA", "volume_z": 9.0, "news_z": None, "wiki_z": None, "reddit_z": 9.0,
             "breadth": 4}]
    out = pd.DataFrame({"date": ["2026-10-02", "2026-10-03", "2026-10-01"], "symbol": ["AAA", "AAA", "AAA"],
                        "score": [2, 2, -1], "confidence": [0.8] * 3, "fwd_return": [0.01, 0.02, -0.01]})
    scores = evidence.score_rows(out, rows)
    assert list(scores["attention_breadth"].fillna(-1)) == [1, 4, -1]  # the session before, never the same day
    table, conditions = evidence.parse("scores: attention_breadth >= 2 and reddit_z > 3")
    assert len(evidence.apply(scores, table, conditions)) == 1


def test_discovery_cannot_be_enabled_yet(state):
    cfg = json.loads(paths.config_file().read_text())
    cfg["discovery"]["enabled"] = True
    paths.config_file().write_text(json.dumps(cfg))
    with pytest.raises(config.ConfigError):
        config.load_config()


def test_news_fetch_includes_the_discovery_list(state, monkeypatch):
    paths.discovery_dir().mkdir(parents=True)
    (paths.discovery_dir() / "2026-10-26.json").write_text(json.dumps({"names": [{"symbol": "HOT"}]}))
    (paths.discovery_dir() / "2026-10-30.json").write_text(json.dumps({"names": [{"symbol": "LATER"}]}))
    assert attention.latest_discovery(date(2026, 10, 27)) == ["HOT"]


def test_cron_runs_the_jobs():
    import subprocess

    lines = subprocess.run(["bash", str(paths.ROOT / "scripts" / "cron.sh"), "print"], capture_output=True,
                           text=True, check=True).stdout
    assert "attention.py morning" in lines and "collect volume" in lines and "collect apewisdom" in lines
    morning = next(line for line in lines.splitlines() if "attention.py morning" in line)
    assert morning.startswith("0 6 ")  # before the 06:30 scoring run


def test_wikipedia_compares_weekdays_with_weekdays(cfg, monkeypatch):
    friday = datetime(2026, 10, 24, 6, 0, tzinfo=ET)  # collects Friday 10-23
    days = [date(2026, 10, 23) - timedelta(days=i) for i in range(33, -1, -1)]
    items = [{"timestamp": d.strftime("%Y%m%d00"), "views": 100 if d.weekday() >= 5 else 1000} for d in days]
    sparql = {"results": {"bindings": [{"ticker": {"value": "AAA"}, "article": {"value": "https://en.wikipedia.org/wiki/A"}}]}}
    monkeypatch.setattr(attention, "get_json", lambda url, **k: sparql if "wikidata" in url else {"items": items})
    attention.collect_wikipedia(["AAA"], cfg, friday, pause=0)
    assert attention.read_raw("wikipedia", date(2026, 10, 23))["AAA"]["wiki_z"] == 0  # normal for a weekday


def test_a_session_is_featured_once(cfg, monkeypatch):
    build_world(cfg, monkeypatch)
    monkeypatch.setattr(attention, "get_json", lambda *a, **k: {"pages": 1, "results": []})
    monkeypatch.setattr(attention.time, "sleep", lambda s: None)
    saturday = datetime.combine(SESSION + timedelta(days=1), datetime.min.time(), ET).replace(hour=6)
    attention.morning(cfg, saturday)
    first = {r["symbol"]: r for r in db.read_attention()}
    attention.write_raw("reddit", NOW.date(), {"HOT": {"mentions": 9999}}, stamp="0500")
    messages, _ = attention.morning(cfg, NOW)  # Monday: Friday's features stay as Saturday computed them
    assert any("already computed" in m for m in messages)
    assert {r["symbol"]: r for r in db.read_attention()}["HOT"]["mentions"] == first["HOT"]["mentions"]
    assert (paths.discovery_dir() / f"{NOW.date()}.json").exists()
    assert not (paths.discovery_dir() / f"{saturday.date()}.json").exists()  # no list on a weekend
