"""Data folder setup, config validation against defaults/config_bounds.json, and the watchlist.

Run `python src/config.py` to create the data folder from the defaults and validate it.
"""
import json
import re
import shutil
import sys

import paths

SYMBOL_RE = re.compile(r"^[A-Z][A-Z.]{0,5}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ConfigError(RuntimeError):
    pass


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def init_data():
    """Create the data folder and copy missing defaults into it. Never overwrites. Returns what it created."""
    created = []
    dirs = [paths.data_dir(), paths.news_dir(), paths.scores_dir(), paths.memory_dir(), paths.logs_dir()]
    for d in dirs + [paths.reports_dir(m) for m in ("paper", "dry_run")]:
        if not d.exists():
            d.mkdir(parents=True)
            created.append(str(d))
    for src, dst in ((paths.CONFIG_DEFAULT, paths.config_file()),
                     (paths.WATCHLIST_DEFAULT, paths.watchlist_file())):
        if not dst.exists():
            shutil.copyfile(src, dst)
            created.append(str(dst))
    return created


def _flatten(d, prefix=""):
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def _check(key, value, rule):
    if "enum" in rule:
        return None if value in rule["enum"] else f"{key}={value!r} must be one of {rule['enum']}"
    kind = rule["type"]
    if kind == "bool":
        return None if isinstance(value, bool) else f"{key} must be true or false"
    if kind == "time":
        if not isinstance(value, str) or not TIME_RE.match(value):
            return f"{key} must be HH:MM"
        in_range = rule["min"] <= value <= rule["max"]
    else:
        ok_type = isinstance(value, int) if kind == "int" else isinstance(value, (int, float))
        if isinstance(value, bool) or not ok_type:
            return f"{key} must be {'an integer' if kind == 'int' else 'a number'}"
        in_range = rule["min"] <= value <= rule["max"]
    return None if in_range else f"{key}={value} is outside [{rule['min']}, {rule['max']}]"


def validate(cfg, bounds):
    """List of problems with cfg; empty when every key is known, present and in bounds."""
    flat = _flatten(cfg)
    errors = [f"unknown key {k}" for k in flat if k not in bounds]
    for key, rule in bounds.items():
        if key not in flat:
            errors.append(f"missing key {key}")
        else:
            err = _check(key, flat[key], rule)
            if err:
                errors.append(err)
    return errors


def load_config(path=None):
    """The active config, refusing to return one that breaks config_bounds.json."""
    path = path or paths.config_file()
    if not path.exists():
        raise ConfigError(f"{path} not found; run `python src/config.py` or scripts/setup.sh")
    cfg = load_json(path)
    errors = validate(cfg, load_json(paths.CONFIG_BOUNDS))
    if errors:
        raise ConfigError(f"{path} is out of bounds: " + "; ".join(errors))
    return cfg


def load_watchlist(path=None):
    path = path or paths.watchlist_file()
    if not path.exists():
        raise ConfigError(f"{path} not found; run `python src/config.py` or scripts/setup.sh")
    symbols = load_json(path).get("symbols")
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 40:
        raise ConfigError(f"{path}: 'symbols' must be a list of 1 to 40 tickers")
    bad = [s for s in symbols if not isinstance(s, str) or not SYMBOL_RE.match(s)]
    if bad or len(set(symbols)) != len(symbols):
        raise ConfigError(f"{path}: invalid or duplicate symbols {bad}")
    return symbols


def main():
    for p in init_data():
        print(f"created {p}")
    try:
        cfg = load_config()
        symbols = load_watchlist()
    except ConfigError as e:
        print(e)
        return 1
    print(f"config OK: version {cfg['version']}, mode {cfg['mode']}; watchlist: {len(symbols)} symbols")
    return 0


if __name__ == "__main__":
    sys.exit(main())
