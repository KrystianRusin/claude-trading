"""Every file path the system reads or writes, in one place.

All runtime data lives under data/. DATA_DIR in the environment overrides it (tests use a temp folder).
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
CONFIG_DEFAULT = ROOT / "defaults" / "config.json"
CONFIG_BOUNDS = ROOT / "defaults" / "config_bounds.json"
WATCHLIST_DEFAULT = ROOT / "defaults" / "watchlist.json"
SCORING_PROMPT = ROOT / "prompts" / "scoring.md"
REVIEW_PROMPT = ROOT / "prompts" / "review.md"
BOOK_MODES = ("paper", "dry_run", "replay")


def data_dir():
    return Path(os.environ.get("DATA_DIR") or ROOT / "data")


def halt_file():
    return ROOT / "HALT"


def config_file():
    return data_dir() / "config.json"


def watchlist_file():
    return data_dir() / "watchlist.json"


def universe_file():
    return data_dir() / "universe.json"


def news_dir():
    return data_dir() / "news"


def news_file(day):
    return news_dir() / f"{day.isoformat()}.jsonl"


def news_cursor():
    return news_dir() / "cursor.json"


def scores_dir():
    return data_dir() / "scores"


def score_file(day):
    return scores_dir() / f"{day.isoformat()}.json"


def bars_dir():
    return data_dir() / "bars"


def memory_dir():
    return data_dir() / "memory"


def memory_file():
    return memory_dir() / "memory.md"


def memory_json():
    return memory_dir() / "memory.json"


def memory_history_dir():
    return memory_dir() / "history"


def logs_dir():
    return data_dir() / "logs"


def log_file():
    return logs_dir() / "sentiment.log"


def lock_file():
    return data_dir() / "trader.lock"


def comparison_file():
    return data_dir() / "comparison.md"


def book_dir(mode):
    """Folder holding a book's trades, orders, positions, daily summary, reports and lifetime stats."""
    if mode not in BOOK_MODES:
        raise ValueError(f"unknown book mode {mode!r}")
    return data_dir() / "books" / mode


def reports_dir(mode):
    return book_dir(mode) / "reports"


def lifetime_file(mode):
    return book_dir(mode) / "lifetime.md"


def lifetime_json(mode):
    return book_dir(mode) / "lifetime.json"
