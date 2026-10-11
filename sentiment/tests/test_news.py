from datetime import date, datetime, timedelta

import news
import paths
from broker import ET


def art(i, when, symbols=("AAPL",), headline="h"):
    return {"id": str(i), "published_at": when.isoformat(), "symbols": list(symbols), "headline": headline,
            "summary": "s", "source": "benzinga", "url": f"https://x/{i}"}


class FakeNews:
    def __init__(self, articles):
        self.articles = articles
        self.calls = []

    def get_news(self, symbols, start, end):
        self.calls.append((start, end))
        return [a for a in self.articles if start <= datetime.fromisoformat(a["published_at"]) <= end]


def test_bucketed_by_new_york_date(state):
    late = datetime(2026, 10, 9, 23, 30, tzinfo=ET)
    news.store([art(1, late)])
    assert [a["id"] for a in news.read_day(date(2026, 10, 9))] == ["1"]
    assert not paths.news_file(date(2026, 10, 10)).exists()


def test_dedupe_across_runs(state):
    t = datetime(2026, 10, 9, 10, 0, tzinfo=ET)
    assert news.store([art(1, t), art(2, t + timedelta(hours=1))]) == 2
    assert news.store([art(2, t + timedelta(hours=1)), art(3, t + timedelta(days=1))]) == 1
    assert news.store([art(1, t)]) == 0
    assert len(news.read_day(t.date())) == 2


def test_fetch_uses_cursor_with_overlap(state):
    now = datetime(2026, 10, 9, 12, 0, tzinfo=ET)
    src = FakeNews([art(1, now - timedelta(hours=1)), art(2, now + timedelta(minutes=20))])
    assert news.fetch(src, ["AAPL"], now) == 1
    assert src.calls[0][0] == now - news.FIRST_FETCH
    later = now + timedelta(minutes=30)
    assert news.fetch(src, ["AAPL"], later) == 1
    assert src.calls[1][0] == now - news.OVERLAP


def test_load_window_and_symbols(state):
    t = datetime(2026, 10, 9, 6, 30, tzinfo=ET)
    news.store([art(1, t), art(2, t + timedelta(hours=1), ("MSFT",)), art(3, t + timedelta(days=1))])
    got = news.load(t, t + timedelta(days=1), ["AAPL"])
    assert [a["id"] for a in got] == ["3"]
    assert [a["id"] for a in news.load(t - timedelta(minutes=1), t + timedelta(hours=2))] == ["1", "2"]


def test_status_counts(state):
    t = datetime(2026, 10, 9, 10, 0, tzinfo=ET)
    news.store([art(1, t), art(2, t, ("AAPL", "TSLA", "XYZ"))])
    out = news.status(t.date(), ["AAPL", "TSLA"])
    assert "2026-10-09" in out and "AAPL:2 TSLA:1" in out and "XYZ" not in out
