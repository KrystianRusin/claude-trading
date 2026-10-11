"""News archive: Alpaca News API articles for the watchlist, one JSON line each in state/news/<date>.jsonl.

    python news.py fetch     archive articles published since the last fetch
    python news.py status    articles per day and symbol for the last week

Articles are bucketed by publish date in America/New_York and deduplicated by id across runs.
Article text is stored as data and never interpreted.
"""
import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta

import paths
from broker import ET, to_et

FIELDS = ["id", "published_at", "symbols", "headline", "summary", "source", "url"]
OVERLAP = timedelta(hours=2)
FIRST_FETCH = timedelta(days=3)


def published(article):
    return to_et(datetime.fromisoformat(article["published_at"]))


def read_day(day):
    f = paths.news_file(day)
    if not f.exists():
        return []
    with open(f, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _days(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def store(articles):
    """Append articles not yet archived. Returns how many were new."""
    articles = [{k: a.get(k) for k in FIELDS} | {"id": str(a["id"])} for a in articles]
    if not articles:
        return 0
    days = [published(a).date() for a in articles]
    seen = {a["id"] for d in _days(min(days), max(days)) for a in read_day(d)}
    paths.news_dir().mkdir(parents=True, exist_ok=True)
    new = 0
    for article, day in sorted(zip(articles, days), key=lambda x: x[0]["published_at"]):
        if article["id"] in seen:
            continue
        seen.add(article["id"])
        with open(paths.news_file(day), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(article, ensure_ascii=False) + "\n")
        new += 1
    return new


def _read_cursor():
    f = paths.news_cursor()
    return to_et(datetime.fromisoformat(json.loads(f.read_text())["last_fetch"])) if f.exists() else None


def fetch(broker, symbols, now):
    """Archive articles published since the previous fetch (with an overlap); returns the count of new ones."""
    last = _read_cursor()
    start = last - OVERLAP if last else now - FIRST_FETCH
    new = store(broker.get_news(symbols, start, now))
    paths.news_dir().mkdir(parents=True, exist_ok=True)
    paths.news_cursor().write_text(json.dumps({"last_fetch": now.isoformat()}))
    return new


def load(start, end, symbols=None):
    """Archived articles published in (start, end], oldest first, optionally limited to some symbols."""
    wanted = set(symbols) if symbols else None
    out = []
    for d in _days(start.astimezone(ET).date(), end.astimezone(ET).date()):
        for a in read_day(d):
            if start < published(a) <= end and (wanted is None or wanted & set(a["symbols"])):
                out.append(a)
    return sorted(out, key=lambda a: a["published_at"])


def status(today, symbols, days=7):
    lines = [f"{'date':<12}{'articles':>9}  per symbol"]
    for d in _days(today - timedelta(days=days - 1), today):
        articles = read_day(d)
        counts = Counter(s for a in articles for s in a["symbols"] if s in symbols)
        per = " ".join(f"{s}:{n}" for s, n in sorted(counts.items()))
        lines.append(f"{d.isoformat():<12}{len(articles):>9}  {per}")
    last = _read_cursor()
    lines.append(f"last fetch: {last.isoformat(timespec='minutes') if last else 'never'}")
    return "\n".join(lines)


def main(argv=None):
    import config
    from broker import make_broker
    from logger import notify, setup_logging

    ap = argparse.ArgumentParser(description="news archive")
    ap.add_argument("command", choices=["fetch", "status"])
    args = ap.parse_args(argv)
    config.init_state()
    log = setup_logging("news")
    symbols = config.load_watchlist()
    now = datetime.now(ET)
    if args.command == "status":
        print(status(now.date(), symbols))
        return 0
    try:
        cfg = config.load_config()
        new = fetch(make_broker(cfg, verify_account=False), symbols, now)
    except Exception as e:
        log.exception("news fetch failed")
        notify(f"news fetch failed: {e}")
        return 1
    log.info("news fetch: %d new articles", new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
