import json

import pytest

import config
import paths


def defaults():
    return config.load_json(paths.CONFIG_DEFAULT), config.load_json(paths.CONFIG_BOUNDS)


def test_defaults_are_in_bounds():
    cfg, bounds = defaults()
    assert config.validate(cfg, bounds) == []


def test_default_watchlist_is_valid():
    assert len(config.load_watchlist(paths.WATCHLIST_DEFAULT)) == 20


@pytest.mark.parametrize("section,key,value,fragment", [
    ("risk", "position_pct_of_equity", 25, "outside"),
    ("risk", "max_positions", 3.5, "integer"),
    ("entry", "allow_shorts", 1, "true or false"),
    ("entry", "run_time", "9:45", "HH:MM"),
    ("entry", "run_time", "08:00", "outside"),
    ("exit", "max_hold_days", True, "integer"),
])
def test_out_of_bounds_values_are_reported(section, key, value, fragment):
    cfg, bounds = defaults()
    cfg[section][key] = value
    errors = config.validate(cfg, bounds)
    assert len(errors) == 1 and fragment in errors[0]


def test_unknown_missing_and_enum_keys():
    cfg, bounds = defaults()
    cfg["risk"]["leverage"] = 4
    del cfg["exit"]["atr_days"]
    cfg["mode"] = "live"
    errors = config.validate(cfg, bounds)
    assert any("unknown key risk.leverage" in e for e in errors)
    assert any("missing key exit.atr_days" in e for e in errors)
    assert any("mode=" in e for e in errors)


def test_init_state_copies_defaults_and_never_overwrites(state):
    cfg_path = paths.config_file()
    assert cfg_path.exists() and paths.watchlist_file().exists()
    assert paths.news_dir().is_dir() and paths.scores_dir().is_dir()
    cfg = json.loads(cfg_path.read_text())
    cfg["risk"]["max_positions"] = 4
    cfg_path.write_text(json.dumps(cfg))
    assert config.init_state() == []
    assert config.load_config()["risk"]["max_positions"] == 4


def test_load_config_refuses_out_of_bounds(state):
    cfg = json.loads(paths.config_file().read_text())
    cfg["risk"]["max_daily_loss_pct"] = 50
    paths.config_file().write_text(json.dumps(cfg))
    with pytest.raises(config.ConfigError, match="out of bounds"):
        config.load_config()


def test_watchlist_rejects_bad_symbols(state):
    paths.watchlist_file().write_text(json.dumps({"symbols": ["AAPL", "aapl"]}))
    with pytest.raises(config.ConfigError):
        config.load_watchlist()
    paths.watchlist_file().write_text(json.dumps({"symbols": ["AAPL", "AAPL"]}))
    with pytest.raises(config.ConfigError):
        config.load_watchlist()


def test_state_dir_override(state):
    assert paths.state_dir() == state
    assert paths.book_dir("dry_run") == state / "dry_run"
    assert paths.book_dir("paper") == state
    with pytest.raises(ValueError):
        paths.book_dir("live")
