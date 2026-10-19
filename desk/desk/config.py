"""Settings: where the bots' data is, which books are shown, and which sections are public.

defaults/desk.json ships the defaults. data/desk.json (gitignored, made by scripts/setup.sh) holds the
owner's choices and overrides them key by key. Unknown keys and unknown sections are refused, so a
typo cannot quietly leave something public. DESK_CONFIG points at another settings file (tests).

    python -m desk.config      print the settings in effect and what is public
"""
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = ROOT / "defaults" / "desk.json"
SETTINGS = ROOT / "data" / "desk.json"
BOTS = ("quant", "sentiment")
BOOKS = ("paper", "dry_run", "replay")

# Every section the API can serve. A section that is off returns 404 and the UI hides it.
SECTIONS = {
    "equity": "Equity curves and daily P&L",
    "trades": "Closed trades and their statistics",
    "symbols": "Per-symbol results and activity: hot right now, best and worst, agents disagree",
    "scores": "The sentiment bot's news scores per symbol (feeds hot right now)",
    "compare": "The two bots compared: returns, risk and the difference with its 95% range",
    "versions": "When the config, strategies, watchlist, prompts and memory changed, and results by version",
    "version_content": "The content of each version: configs, strategy code, prompts, memory",
    "positions": "Open positions and their unrealized P&L (the Holdings page), and positions in today's replay",
    "ops": "Job runs and deploys: status, timing, failures (without their output)",
    "ops_errors": "The end of a failed job's output, which can hold paths or account details",
    "prices": "Price bars and the SPY benchmark (check the data licences before turning on)",
    "replay": "Replay a past day minute by minute: equity, fills and (once the day is over) positions",
    "journal": "The bots' daily journals, as they wrote them",
    "memory": "The bots' memory: lessons and hypotheses, and how each moved up or down the ladder",
    "downloads": "Nightly CSV and Parquet files of the public trades, daily results and versions",
    "research": "The sentiment bot's research: score calibration, news volume, desk ideas and how they turned out",
    "headlines": "News headlines with their source and link (check the news provider's terms before turning on)",
    "agent": "What the bots did after each session: reviews, guard results, changelog entries, memory changes",
}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    bots: dict  # name -> {"label", "data_dir": Path}
    books: tuple
    sections: dict  # name -> bool
    cache_seconds: int
    rate_limit_per_minute: int

    def public(self, section):
        return self.sections.get(section, False)


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path}: not valid JSON ({e})") from e


def _merge(base, override, where=""):
    out = dict(base)
    for key, value in override.items():
        if key not in base:
            raise ConfigError(f"unknown setting {where}{key}")
        out[key] = _merge(base[key], value, f"{where}{key}.") if isinstance(base[key], dict) else value
    return out


def load(path=None):
    """The settings in effect: the defaults with the owner's file over them, checked."""
    raw = _read(DEFAULTS)
    path = Path(path or os.environ.get("DESK_CONFIG") or SETTINGS)
    if path.exists():
        raw = _merge(raw, _read(path))
    unknown = set(raw["sections"]) - set(SECTIONS)
    if unknown:
        raise ConfigError(f"unknown sections {sorted(unknown)}; known: {sorted(SECTIONS)}")
    sections = {name: raw["sections"].get(name, False) for name in SECTIONS}
    if not all(isinstance(v, bool) for v in sections.values()):
        raise ConfigError("every section must be true or false")
    if set(raw["bots"]) - set(BOTS):
        raise ConfigError(f"unknown bots {sorted(set(raw['bots']) - set(BOTS))}; known: {list(BOTS)}")
    bots = {}
    for name, bot in raw["bots"].items():
        if set(bot) - {"label", "data_dir"}:
            raise ConfigError(f"unknown setting bots.{name}.{sorted(set(bot) - {'label', 'data_dir'})[0]}")
        bots[name] = {"label": str(bot.get("label") or name), "data_dir": Path(bot["data_dir"]).expanduser()}
    books = tuple(raw["books"])
    if not books or set(books) - set(BOOKS):
        raise ConfigError(f"books must be some of {list(BOOKS)}")
    for key in ("port", "cache_seconds", "rate_limit_per_minute"):
        if not isinstance(raw[key], int) or isinstance(raw[key], bool) or raw[key] < 0:
            raise ConfigError(f"{key} must be a whole number")
    return Settings(host=str(raw["host"]), port=raw["port"], bots=bots, books=books, sections=sections,
                    cache_seconds=raw["cache_seconds"], rate_limit_per_minute=raw["rate_limit_per_minute"])


def main():
    try:
        s = load()
    except ConfigError as e:
        print(e)
        return 1
    print(f"listening on {s.host}:{s.port}; books shown: {', '.join(s.books)}")
    for name, bot in s.bots.items():
        print(f"  {name}: {bot['label']} at {bot['data_dir']}")
    for name, about in SECTIONS.items():
        print(f"  [{'public' if s.public(name) else 'hidden'}] {name}: {about}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
