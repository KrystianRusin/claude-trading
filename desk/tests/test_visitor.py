"""Holdings, replay, did-the-change-work, the disagreement feed, the notebook and the downloads (ticket 008)."""
import csv
import json

import pytest
from fastapi.testclient import TestClient

from conftest import DAYS, TODAY, write_settings
from desk import app as app_mod
from desk import export, notebook, views


def by_bot(answer):
    return {b["bot"]: b for b in answer["books"]}


def test_holdings_unrealized_today_stops_and_entry(settings):
    h = by_bot(views.holdings(settings))
    s = h["sentiment"]
    nke, tsla = (next(p for p in s["positions"] if p["symbol"] == sym) for sym in ("NKE", "TSLA"))
    # held since before today: today's move is from the last snapshot of the previous session
    assert nke["side"] == "long" and nke["unrealized_pnl"] == 300 and nke["unrealized_pct"] == 6
    assert nke["today_pnl"] == 100
    # the entry and the stop come from the bot's positions file when no order says otherwise
    assert nke["entry"] == DAYS[-4].isoformat() and nke["days_held"] == 3 and nke["stop_price"] == 95
    assert nke["score"] == 2 and nke["strategy"] == "sentiment_swing" and "reason" not in nke
    # opened today, short: qty is shown positive, the stop is the open stop order, today's P&L is all of it
    assert tsla["side"] == "short" and tsla["qty"] == 20 and tsla["stop_price"] == 262.5
    assert tsla["today_pnl"] == tsla["unrealized_pnl"] == 200 and tsla["entry"].startswith(TODAY.isoformat())
    assert tsla["stop_distance_pct"] == pytest.approx(9.375)
    assert s["totals"]["unrealized_pnl"] == 500 and s["totals"]["today_pnl"] == 300 and s["totals"]["positions"] == 2
    assert [p["unrealized_pnl"] for p in s["history"]] == [200, 500]
    q = h["quant"]["positions"][0]
    assert (q["symbol"], q["stop_price"], q["target_price"]) == ("SPY", 99, 103)


def test_holdings_leave_out_scores_when_scores_are_private(bots):
    s = by_bot(views.holdings(write_settings(bots, sections={"scores": False})))["sentiment"]
    assert all("score" not in p and "confidence" not in p for p in s["positions"])


def test_replay_of_today_hides_positions_and_fills_unless_public(bots):
    client = TestClient(app_mod.create_app(write_settings(bots, sections={"positions": False})))
    today = client.get(f"/api/replay?bot=quant&day={TODAY}").json()
    assert len(today["equity"]) == 6 and today["frames"] == [] and today["fills"] == [] and not today["positions_shown"]
    past = client.get(f"/api/replay?bot=sentiment&day={DAYS[-2]}").json()
    assert past["over"] and past["frames"][0]["positions"][0]["symbol"] == "NKE"
    client = TestClient(app_mod.create_app(write_settings(bots)))
    today = client.get(f"/api/replay?bot=quant&day={TODAY}").json()
    assert len(today["frames"]) == 6 and today["fills"] == [
        {"ts": f"{TODAY}T09:46:00-04:00", "symbol": "SPY", "side": "buy", "qty": 10, "price": 100,
         "order_type": "market", "leg": False}]


def test_replay_days_need_more_than_one_snapshot(settings):
    assert views.replay_days(settings, "quant")["days"] == [TODAY.isoformat()]
    assert views.replay_days(settings, "sentiment")["days"] == []


def test_replay_lists_the_trades_closed_that_day(settings):
    r = views.replay(settings, "quant", DAYS[0])
    assert [(t["symbol"], t["pnl_usd"]) for t in r["trades"]] == [("SPY", 120)]
    assert r["equity_start"] == 100_000 and r["equity"] == []


def test_after_change():
    ef = "2026-10-09T17:30:00-04:00"
    assert not views._after_change("2026-10-09", ef) and views._after_change("2026-10-12", ef)
    assert views._after_change("2026-10-09", "2026-10-09T08:00:00-04:00")
    assert views._after_change("2026-10-09T17:31:00-04:00", ef) and not views._after_change("2026-10-09T10:00:00-04:00", ef)


def test_version_impact_before_and_after(settings):
    changes = {(c["kind"], c["effective_from"][:10]): c for c in views.version_impact(settings, "quant")["changes"]}
    second = changes[("memory", DAYS[4].isoformat())]
    # trades entered on DAYS[2..4] fall under the first memory version, DAYS[5..] under the second
    assert second["before"]["trades"] == 3 and second["after"]["trades"] == 2
    assert second["verdict"] == "too early to tell" and not second["first"] and second["current"]
    first = changes[("memory", DAYS[1].isoformat())]
    assert first["before"] is None and first["first"] and not first["current"]
    assert "has_diff" not in second  # version content is private by default


def test_verdict_needs_the_minimum_sample_and_a_clear_difference():
    def w(rs):
        return {"trades": len(rs), "_r": rs}
    assert views._verdict(w([1.0] * 5), w([1.0] * 10)) == "too early to tell"
    assert views._verdict(None, w([1.0, -1.0] * 15)) == "nothing comparable before"
    assert views._verdict(w([-1.0, -0.8] * 15), w([1.0, 1.2] * 15)) == "better"
    assert views._verdict(w([1.0, 1.2] * 15), w([-1.0, -0.8] * 15)) == "worse"
    assert views._verdict(w([1.0, -1.0] * 15), w([-1.0, 1.0] * 15)) == "no clear difference"


def test_version_diff_needs_version_content(bots):
    client = TestClient(app_mod.create_app(write_settings(bots)))
    assert client.get("/api/versions/diff?bot=quant&id=2").status_code == 404
    client = TestClient(app_mod.create_app(write_settings(bots, sections={"version_content": True})))
    d = client.get("/api/versions/diff?bot=quant&id=2").json()
    assert "-- Status: hypothesis" in d["diff"] and "+- Status: supported" in d["diff"]
    assert client.get("/api/versions/diff?bot=quant&id=999").status_code == 404
    impact = client.get("/api/versions/impact?bot=quant").json()["changes"]
    assert {c["id"]: c["has_diff"] for c in impact}[2] is True
    assert {c["id"]: c["has_diff"] for c in impact}[5] is False  # a removal has no content


def test_disagreement_feed(settings):
    feed = views.disagreement_feed(settings)
    assert [(i["date"], i["symbol"], i["quant"], i["sentiment"]) for i in feed["items"]] == [
        (TODAY.isoformat(), "SPY", "long", "bearish"), (DAYS[-2].isoformat(), "SPY", "long", "bearish")]
    assert "reason" not in json.dumps(feed)


def test_disagreements_need_scores(bots):
    client = TestClient(app_mod.create_app(write_settings(bots, sections={"scores": False})))
    assert client.get("/api/disagreements").status_code == 404


def test_memory_ladder_from_versions(settings):
    m = notebook.memory(settings, "quant")
    entries = {e["id"]: e for e in m["entries"]}
    assert [h["status"] for h in entries["M-1"]["history"]] == ["hypothesis", "supported"]
    assert entries["M-2"]["status"] == "retired" and entries["M-2"]["retired_reason"] == "2026-10-09, contradicted"
    assert entries["M-1"]["query"] == "entry_minute_after_open >= 60"
    assert m["counts"][-1] == {"date": DAYS[4].isoformat(), "hypothesis": 0, "supported": 1, "confirmed": 0, "retired": 1}
    s = notebook.memory(settings, "sentiment")
    assert s["entries"][0]["status"] == "supported" and "secret_field" not in s["entries"][0]


def test_quant_memory_parser_ignores_the_template():
    assert notebook.parse_quant("# Trading memory\n- One entry per lesson, `### M-<number>: <short title>`\n## Active\n") == []


def test_journal_dates_and_entries(settings):
    j = notebook.journal(settings, "quant")
    assert j["dates"] == [TODAY.isoformat(), DAYS[-2].isoformat()] and "SPY broke out" in j["entry"]["text"]
    assert notebook.journal(settings, "quant", DAYS[-2].isoformat())["entry"]["text"].endswith("Quiet day.")
    assert notebook.journal(settings, "quant", "../../etc/passwd")["entry"]["date"] == TODAY.isoformat()


def test_export_writes_only_public_files_and_the_api_serves_them(bots, export_dir):
    settings = write_settings(bots, sections={"versions": False})
    m = export.build(settings)
    names = {f["name"] for f in m["files"]}
    assert {"trades.csv", "daily.csv"} <= names and not any(n.startswith("versions") for n in names)
    rows = list(csv.DictReader(open(export_dir / "trades.csv")))
    assert len(rows) == 12 and "extra" not in rows[0] and "internal_note" not in json.dumps(rows)
    daily = list(csv.DictReader(open(export_dir / "daily.csv")))
    assert {r["book"] for r in daily} == {"paper"}
    client = TestClient(app_mod.create_app(settings))
    listed = client.get("/api/downloads").json()
    assert {f["name"] for f in listed["files"]} == names
    r = client.get("/api/downloads/trades.csv")
    assert r.status_code == 200 and r.text.startswith("bot,book,date,symbol")
    assert "attachment" in r.headers["content-disposition"]
    for bad in ("versions.csv", "manifest.json", "..%2Fdesk.json", "trades.csv.tmp"):
        assert client.get(f"/api/downloads/{bad}").status_code == 404, bad
    # a section made private stops being served at once, before the next export removes its files
    client = TestClient(app_mod.create_app(write_settings(bots, sections={"versions": False, "trades": False})))
    assert client.get("/api/downloads/trades.csv").status_code == 404
    assert "trades.csv" not in {f["name"] for f in client.get("/api/downloads").json()["files"]}
    export.build(write_settings(bots, sections={"versions": False, "trades": False}))
    assert not (export_dir / "trades.csv").exists()


def test_downloads_before_the_first_export(client):
    assert client.get("/api/downloads").json() == {"generated_at": None, "files": []}


def test_parquet_has_the_same_rows_as_the_csv(settings, export_dir):
    pq = pytest.importorskip("pyarrow.parquet")
    export.build(settings)
    table = pq.read_table(export_dir / "trades.parquet")
    assert table.num_rows == 12 and table.column_names[:3] == ["bot", "book", "date"]
    client = TestClient(app_mod.create_app(settings))
    assert client.get("/api/downloads/trades.parquet").content[:4] == b"PAR1"


def test_without_pyarrow_the_export_is_csv_only(settings, export_dir, monkeypatch):
    (export_dir).mkdir()
    (export_dir / "trades.parquet").write_bytes(b"old")
    monkeypatch.setattr(export, "_parquet", lambda: False)
    m = export.build(settings)
    assert {f["format"] for f in m["files"]} == {"csv"} and not (export_dir / "trades.parquet").exists()


def test_disagreements_over_all_time(client):
    """The Compare page asks for 3650 days when its range is All."""
    assert client.get("/api/disagreements?days=3650").status_code == 200
    assert client.get("/api/disagreements?days=3651").status_code == 422
