"""Read-only, bounded and polite: what the app does for every request."""
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from conftest import write_settings
from desk import app as app_mod
from desk import sources


@pytest.mark.parametrize("method", ["post", "put", "delete", "patch"])
def test_only_get_is_answered(client, method):
    assert getattr(client, method)("/api/trades").status_code == 405


def test_the_app_never_writes_the_databases(bots, client):
    before = {p: p.read_bytes() for p in bots.rglob("trader.db")}
    for p in ("/api/meta", "/api/equity", "/api/trades", "/api/hot", "/api/compare", "/api/versions", "/api/ops"):
        assert client.get(p).status_code == 200
    assert {p: p.read_bytes() for p in bots.rglob("trader.db")} == before


def test_connections_are_read_only(bots):
    with sources.connect(bots / "quant" / "trader.db") as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM trades")


def test_a_live_bot_writing_while_the_api_reads(bots, settings):
    writer = sqlite3.connect(bots / "quant" / "trader.db")
    writer.execute("INSERT INTO trades (book, date, symbol, side, qty, entry_time, pnl_usd) "
                   "VALUES ('paper', '2026-10-16', 'IWM', 'long', 1, 'live', 5)")
    writer.commit()  # still open: the change sits in the WAL
    client = TestClient(app_mod.create_app(settings))
    assert "IWM" in [t["symbol"] for t in client.get("/api/trades?symbol=iwm").json()["trades"]]
    writer.close()


def test_slow_queries_are_stopped(bots, monkeypatch):
    monkeypatch.setattr(sources, "QUERY_SECONDS", 0.05)
    with pytest.raises(sources.QueryTimeout):
        with sources.connect(bots / "quant" / "trader.db") as conn:
            conn.execute("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) SELECT MAX(x) FROM n").fetchall()


def test_answers_are_cached(bots):
    client = TestClient(app_mod.create_app(write_settings(bots, cache_seconds=60)))
    first = client.get("/api/trades").json()["total"]
    writer = sqlite3.connect(bots / "quant" / "trader.db")
    writer.execute("DELETE FROM trades")
    writer.commit()
    writer.close()
    assert client.get("/api/trades").json()["total"] == first
    assert client.get("/api/trades").headers["cache-control"] == "public, max-age=30"
    client = TestClient(app_mod.create_app(write_settings(bots, cache_seconds=0)))
    assert client.get("/api/trades").json()["total"] < first


def test_rate_limit_per_client(bots):
    client = TestClient(app_mod.create_app(write_settings(bots, rate_limit_per_minute=5)))
    codes = [client.get("/api/meta").status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]


def test_rate_limit_tells_funnel_visitors_apart():
    limit = app_mod.RateLimit(2)

    class Req:
        def __init__(self, peer, fwd):
            self.client = type("C", (), {"host": peer})
            self.headers = {"x-forwarded-for": fwd} if fwd else {}

    assert app_mod.client_ip(Req("127.0.0.1", "1.1.1.1, 9.9.9.9")) == "9.9.9.9"
    assert app_mod.client_ip(Req("10.0.0.5", "9.9.9.9")) == "10.0.0.5"  # not from the local proxy: ignored
    assert [limit.allow("a"), limit.allow("a"), limit.allow("a"), limit.allow("b")] == [True, True, False, True]
    limit.buckets["a"] = (0, time.monotonic() - 30)  # half a minute later: one more token
    assert limit.allow("a")


def test_bad_parameters_are_refused(client):
    for bad in ("/api/trades?limit=10000", "/api/trades?bot=other", "/api/trades?start=yesterday",
                "/api/trades?symbol=" + "A" * 50, "/api/symbols?days=3", "/api/trades?offset=-1"):
        assert client.get(bad).status_code == 422, bad


def test_unknown_api_paths_are_404(client):
    assert client.get("/api/sql?q=select").status_code == 404


def test_frontend_is_served_with_routing_fallback(client, tmp_path, monkeypatch):
    build = tmp_path / "web"
    (build / "assets").mkdir(parents=True)
    (build / "index.html").write_text("<html>desk</html>")
    (build / "assets" / "app-123.js").write_text("js")
    (tmp_path / "outside.txt").write_text("private")
    monkeypatch.setattr(app_mod, "WEB_DIR", build)
    assert client.get("/").text == "<html>desk</html>"
    assert client.get("/trades/SPY").text == "<html>desk</html>"  # a browser route
    r = client.get("/assets/app-123.js")
    assert r.text == "js" and "immutable" in r.headers["cache-control"]
    assert client.get("/../outside.txt").text != "private"
    assert client.get("/%2e%2e/outside.txt").text != "private"


def test_missing_frontend_is_explained(client, monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, "WEB_DIR", tmp_path / "nothing")
    assert client.get("/").status_code == 503


def test_check_command(bots, monkeypatch, capsys):
    import json
    import sqlite3

    from desk import check

    settings = bots / "desk.json"
    settings.write_text(json.dumps({"bots": {"quant": {"data_dir": str(bots / "quant")},
                                             "sentiment": {"data_dir": str(bots / "nowhere")}}}))
    monkeypatch.setenv("DESK_CONFIG", str(settings))
    assert check.main() == 0
    out = capsys.readouterr().out
    assert "quant paper: OK" in out and "sentiment paper: no database yet" in out
    conn = sqlite3.connect(bots / "quant" / "trader.db")
    conn.execute("DROP TABLE bars")
    conn.commit()
    conn.close()
    assert check.main() == 1 and "no bars table" in capsys.readouterr().out


def test_every_query_the_ui_sends_is_answered(client):
    """The exact requests web/src makes, so a parameter type the API cannot parse shows up here."""
    for path in ("/api/symbols?days=7&min_trades=3", "/api/symbols?days=30&min_trades=1", "/api/symbols?days=0&min_trades=10",
                 "/api/compare?start=2026-09-01", "/api/compare",
                 "/api/trades?bot=quant&symbol=SPY&strategy=opening_range_breakout&exit_reason=tp&side=long"
                 "&start=2026-10-01&end=2026-10-16&limit=50&offset=0",
                 "/api/trades?limit=50&offset=50", "/api/trades/options", "/api/hot", "/api/equity", "/api/versions",
                 "/api/ops", "/api/meta"):
        assert client.get(path).status_code == 200, path
