"""Every file path the system reads or writes, in one place.

STATE_DIR in the environment overrides the state folder (tests use a temp folder).
"""
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent
CONFIG_DEFAULT = REPO / "config.default.json"
CONFIG_BOUNDS = REPO / "config_bounds.json"
WATCHLIST_DEFAULT = REPO / "watchlist.default.json"
SCORING_PROMPT = REPO / "SCORING_PROMPT.md"


def state_dir():
    return Path(os.environ.get("STATE_DIR") or REPO / "state")


def halt_file():
    return REPO / "HALT"


def config_file():
    return state_dir() / "config.json"


def watchlist_file():
    return state_dir() / "watchlist.json"


def universe_file():
    return state_dir() / "universe.json"


def news_dir():
    return state_dir() / "news"


def news_file(day):
    return news_dir() / f"{day.isoformat()}.jsonl"


def news_cursor():
    return news_dir() / "cursor.json"


def scores_dir():
    return state_dir() / "scores"


def score_file(day):
    return scores_dir() / f"{day.isoformat()}.json"


def bars_dir():
    return state_dir() / "bars"


def reports_dir():
    return state_dir() / "reports"


def comparison_file():
    return state_dir() / "comparison.md"


def log_file():
    return state_dir() / "sentiment.log"


def lock_file():
    return state_dir() / "trader.lock"


def book_dir(mode):
    """Folder holding a book's trades, orders, positions and daily summary."""
    if mode == "paper":
        return state_dir()
    if mode == "dry_run":
        return state_dir() / "dry_run"
    if mode == "replay":
        return state_dir() / "replay"
    raise ValueError(f"unknown book mode {mode!r}")
