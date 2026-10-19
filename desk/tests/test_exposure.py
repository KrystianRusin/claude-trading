"""The owner controls what is public: sections, fields, and things that are never served."""
import json

import pytest
from fastapi.testclient import TestClient

from conftest import SECRET, write_settings
from desk import app as app_mod
from desk import config

ROUTES = {"equity": ["/api/equity"], "trades": ["/api/trades", "/api/trades/options"],
          "symbols": ["/api/symbols", "/api/hot"], "compare": ["/api/compare"], "versions": ["/api/versions"],
          "positions": ["/api/positions"], "ops": ["/api/ops"], "prices": ["/api/benchmark"]}


def everything(client):
    """Every public answer as one string, to search for things that must not be in it."""
    paths = ["/api/meta"] + [p for ps in ROUTES.values() for p in ps]
    return json.dumps([client.get(p).json() for p in paths])


def test_defaults_hide_positions_errors_content_and_prices(client):
    for p in ROUTES["positions"] + ROUTES["prices"]:
        assert client.get(p).status_code == 404
    for p in [p for name, ps in ROUTES.items() if name not in ("positions", "prices") for p in ps]:
        assert client.get(p).status_code == 200, p
    assert set(client.get("/api/meta").json()["sections"]) == {
        "equity", "trades", "symbols", "scores", "compare", "versions", "ops"}


@pytest.mark.parametrize("section", list(ROUTES))
def test_a_section_turned_off_answers_404(bots, section):
    settings = write_settings(bots, sections={section: False})
    client = TestClient(app_mod.create_app(settings))
    for p in ROUTES[section]:
        assert client.get(p).status_code == 404 and client.get(p).json() == {"detail": "not found"}


def test_nothing_hidden_leaks_with_the_defaults(client):
    text = everything(client)
    for hidden in ("PA3SECRET", ".env", "/home/", "o-secret-1", "pt-x", "internal_note", "not public",
                   "prompt_sha256", "model text", "secret_param", "unrealized_pnl", "trader.db", "ZZZ"):
        assert hidden not in text, hidden


def test_turning_sections_on_shows_them(bots):
    settings = write_settings(bots, sections={"positions": True, "ops_errors": True, "version_content": True,
                                              "prices": True})
    client = TestClient(app_mod.create_app(settings))
    assert client.get("/api/positions").json()["books"][0]["positions"][0]["symbol"] == "SPY"
    errors = [r.get("error") for r in client.get("/api/ops").json()["quant"]["runs"]]
    assert SECRET in errors
    contents = [c.get("content") for c in client.get("/api/versions").json()["quant"]["changes"]]
    assert any(c and "secret_param" in c for c in contents)
    assert client.get("/api/benchmark").json()["daily"][0] == {"date": "2026-10-05", "close": 500}


def test_hot_without_scores_leaves_out_the_sentiment_side(bots):
    client = TestClient(app_mod.create_app(write_settings(bots, sections={"scores": False})))
    hot = client.get("/api/hot").json()
    assert set(hot) == {"quant"}


def test_only_the_configured_books_are_shown(bots):
    client = TestClient(app_mod.create_app(write_settings(bots)))
    assert {b["book"] for b in client.get("/api/equity").json()["books"]} == {"paper"}
    client = TestClient(app_mod.create_app(write_settings(bots, books=["paper", "dry_run"])))
    books = {(b["bot"], b["book"]) for b in client.get("/api/equity").json()["books"]}
    assert books == {("quant", "paper"), ("quant", "dry_run"), ("sentiment", "paper")}


def test_unknown_settings_are_refused(bots, tmp_path):
    for bad in ({"sections": {"equtiy": True}}, {"sections": {"equity": "yes"}}, {"secret": 1},
                {"books": ["live"]}, {"bots": {"quant": {"data_dri": "x"}}}, {"bots": {"other": {"data_dir": "x"}}}):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(bad))
        with pytest.raises(config.ConfigError):
            config.load(path)


def test_meta_reports_status_without_paths(bots):
    (bots / "sentiment" / "trader.db").unlink()
    for suffix in ("-wal", "-shm"):
        (bots / "sentiment" / f"trader.db{suffix}").unlink(missing_ok=True)
    client = TestClient(app_mod.create_app(write_settings(bots)))
    meta = client.get("/api/meta").json()
    assert {b["name"]: b["books"] for b in meta["bots"]} == {"quant": {"paper": "ok"}, "sentiment": {"paper": "missing"}}
    assert "/" not in json.dumps(meta["bots"])
    assert client.get("/api/trades").json()["total"] == 7  # the quant trades still show
