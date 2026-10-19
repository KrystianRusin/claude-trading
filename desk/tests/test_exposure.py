"""The owner controls what is public: sections, fields, and things that are never served."""
import json

import pytest
from fastapi.testclient import TestClient

from conftest import SECRET, write_settings
from desk import app as app_mod
from desk import config

ROUTES = {"equity": ["/api/equity"], "trades": ["/api/trades", "/api/trades/options"],
          "symbols": ["/api/symbols", "/api/hot"], "compare": ["/api/compare", "/api/disagreements"],
          "versions": ["/api/versions", "/api/versions/impact?bot=quant", "/api/versions/impact?bot=sentiment"],
          "positions": ["/api/positions"], "ops": ["/api/ops"], "prices": ["/api/benchmark"],
          "replay": ["/api/replay/days?bot=quant", "/api/replay?bot=quant&day=2026-10-13",
                     "/api/replay?bot=sentiment&day=2026-10-14"],
          "journal": ["/api/journal?bot=quant", "/api/journal?bot=sentiment"],
          "memory": ["/api/memory?bot=quant", "/api/memory?bot=sentiment"],
          "version_content": ["/api/versions/diff?bot=quant&id=4"], "downloads": ["/api/downloads"],
          "research": ["/api/research"], "headlines": ["/api/headlines?symbol=NKE"],
          "agent": ["/api/agent?bot=quant", "/api/agent?bot=sentiment"]}
OFF_BY_DEFAULT = ("prices", "journal", "memory", "version_content", "headlines", "agent")


def everything(client):
    """Every public answer as one string, to search for things that must not be in it."""
    paths = ["/api/meta", "/api/symbol?symbol=NKE", "/api/symbol?symbol=SPY", "/api/symbols/known", "/api/trade?bot=quant&id=1",
             "/api/bars?symbol=SPY&timeframe=1Min&start=2026-10-05&end=2026-10-05"] + [p for ps in ROUTES.values() for p in ps]
    return json.dumps([client.get(p).json() for p in paths])


def test_defaults_hide_errors_content_prices_and_the_notebook(client):
    for p in [p for name in OFF_BY_DEFAULT for p in ROUTES[name]]:
        assert client.get(p).status_code == 404, p
    for p in [p for name, ps in ROUTES.items() if name not in OFF_BY_DEFAULT for p in ps]:
        assert client.get(p).status_code == 200, p
    assert set(client.get("/api/meta").json()["sections"]) == {
        "equity", "trades", "symbols", "scores", "compare", "versions", "positions", "ops", "replay", "downloads",
        "research"}


@pytest.mark.parametrize("section", list(ROUTES))
def test_a_section_turned_off_answers_404(bots, section):
    settings = write_settings(bots, sections={s: s != section for s in config.SECTIONS})
    client = TestClient(app_mod.create_app(settings))
    for p in ROUTES[section]:
        assert client.get(p).status_code == 404 and client.get(p).json() == {"detail": "not found"}


def test_nothing_hidden_leaks_with_the_defaults(client):
    text = everything(client)
    for hidden in ("PA3SECRET", ".env", "/home/", "o-secret", "s-secret", "pt-x", "internal_note", "not public",
                   "prompt_sha256", "model text", "secret_param", "trader.db", "ZZZ", "Quiet day", "Late breakouts", "not public", "Nike headline",
                   "Raised the range", "GUARD",
                   "secret_field", "entry_order_id"):
        assert hidden not in text, hidden


def test_nothing_hidden_leaks_with_everything_on(bots):
    """Even with every section public, ids, model text, database paths and unknown fields stay out. (A failed
    job's output, public with ops_errors, may name paths: that is why it is off by default.)"""
    client = TestClient(app_mod.create_app(write_settings(bots, sections={s: True for s in config.SECTIONS})))
    text = everything(client)
    for hidden in ("o-secret", "s-secret", "pt-x", "internal_note", "prompt_sha256", "model text", "trader.db", "ZZZ",
                   "entry_order_id", "full article text", "javascript:", "GUARD", "\"secret\""):
        assert hidden not in text, hidden
    assert "secret_field" not in client.get("/api/memory?bot=sentiment").text  # only the entry fields it names


def test_turning_sections_on_shows_them(bots):
    settings = write_settings(bots, sections={"positions": True, "ops_errors": True, "version_content": True,
                                              "prices": True})
    client = TestClient(app_mod.create_app(settings))
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
