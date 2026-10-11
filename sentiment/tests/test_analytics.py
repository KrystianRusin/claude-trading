from datetime import date

import pandas as pd
import pytest

import analyze
import compare
from logger import Book, read_trades_frame, trade_row

DAYS = [date(2026, 10, d) for d in (5, 6, 7, 8, 9)]


def summary(rets, start=100_000.0):
    rows, eq = [], start
    for d, r in zip(DAYS, rets):
        rows.append({"date": d, "equity_start": eq, "equity_end": eq * (1 + r)})
        eq *= 1 + r
    return pd.DataFrame(rows)


def trades(*rows):
    cols = ["entry_date", "exit_date", "symbol", "side", "pnl_pct", "pnl_usd", "score", "confidence",
            "exit_reason", "prompt_sha256"]
    return pd.DataFrame([dict(zip(cols, r)) for r in rows], columns=cols)


def test_trade_row_long_and_short():
    pos = {"symbol": "AAPL", "side": "short", "qty": "10", "entry_price": "100", "entry_date": "2026-10-05",
           "score": "-2", "confidence": "0.9"}
    row = trade_row(pos, date(2026, 10, 7), 95.0, "flip", 2)
    assert row["pnl_usd"] == 50.0 and row["pnl_pct"] == 5.0 and row["qty"] == 10
    row = trade_row(pos | {"side": "long"}, date(2026, 10, 7), None, "unknown", 2)
    assert row["exit_price"] == "" and row["pnl_usd"] == ""


def test_book_dedupes_trades_and_replaces_summary(tmp_path):
    book = Book(tmp_path, "paper")
    pos = {"symbol": "AAPL", "side": "long", "qty": "1", "entry_price": "1", "entry_date": "2026-10-05"}
    assert book.trade(trade_row(pos, date(2026, 10, 6), 2.0, "hold", 1))
    assert not book.trade(trade_row(pos, date(2026, 10, 6), 2.0, "hold", 1))
    book.daily_summary({"date": "2026-10-06", "notes": "a"})
    book.daily_summary({"date": "2026-10-05"})
    book.daily_summary({"date": "2026-10-06", "notes": "b"})
    assert [(r["date"], r["notes"]) for r in book.summaries()] == [("2026-10-05", ""), ("2026-10-06", "b")]
    assert read_trades_frame(book.trades_path)["exit_date"].iloc[0] == date(2026, 10, 6)


def test_daily_returns_and_drawdown():
    r = analyze.daily_returns(summary([0.10, -0.20, 0.05, 0.0, 0.0]))
    assert list(r.round(6)) == [0.10, -0.20, 0.05, 0.0, 0.0]
    assert analyze.max_drawdown(r) == pytest.approx(-0.20)
    assert analyze.max_drawdown(pd.Series([-0.1, -0.1])) == pytest.approx(-0.19)


def test_expectancy_range_by_entry_date():
    t = trades(*[(DAYS[i % 3], DAYS[4], "A", "long", p, p, 2, 0.9, "hold", "x") for i, p in enumerate([1, 2, 3, 1, 2, 3])])
    mean, lo, hi = analyze.expectancy_range(t)
    assert mean == pytest.approx(2.0) and lo <= mean <= hi
    same = trades((DAYS[0], DAYS[1], "A", "long", 1.0, 1, 2, 0.9, "hold", "x"))
    assert analyze.expectancy_range(same) == (1.0, None, None)


def test_spy_return_from_previous_close():
    spy = pd.DataFrame({"open": [99, 100, 105], "close": [100, 104, 110]}, index=[DAYS[0], DAYS[1], DAYS[2]])
    assert analyze.spy_return(spy, DAYS[1], DAYS[2]) == pytest.approx(0.10)
    assert analyze.spy_return(spy, DAYS[0], DAYS[0]) == pytest.approx(100 / 99 - 1)
    assert analyze.spy_return(None, DAYS[0], DAYS[1]) is None


def test_report_breakdowns_and_warning():
    t = trades((DAYS[0], DAYS[2], "AAPL", "long", 4.0, 400.0, 2, 0.95, "hold", "aaa"),
               (DAYS[0], DAYS[2], "NKE", "short", -2.0, -200.0, -1, 0.75, "stop", "aaa"),
               (DAYS[1], DAYS[3], "TSLA", "long", 1.0, 100.0, 1, 0.8, "flip", "bbb"),
               (DAYS[1], DAYS[3], "F", "long", None, None, 2, 0.9, "unknown", "bbb"))
    text = analyze.build_report(t, summary([0.01, 0.0, -0.005, 0.002, 0.0]))
    assert "only 3 priced trades" in text
    assert "Closed trades: 4 (1 without an exit price)" in text
    assert "Win rate: 67%" in text and "Profit factor: 2.50" in text
    assert "| +2 | 1 | 100% | +4.00 | +400.00 |" in text
    assert "| -1 | 1 | 0% | -2.00 | -200.00 |" in text
    assert "| >=0.9 | 1 |" in text and "| 0.7-0.9 | 2 |" in text
    assert "## By prompt sha256" in text and "Worst day: -0.50%" in text


def test_book_stats_known_answers():
    r = pd.Series([0.01, -0.01, 0.02, 0.0])
    s = compare.book_stats(r, 3)
    sd = r.std(ddof=1)
    assert s["total_return"] == pytest.approx(1.01 * 0.99 * 1.02 - 1)
    assert s["ann_vol"] == pytest.approx(sd * 252 ** 0.5)
    assert s["sharpe"] == pytest.approx(r.mean() / sd * 252 ** 0.5)
    assert s["worst_day"] == -0.01 and s["days"] == 4


def test_difference_range():
    a = pd.Series([0.02, 0.03, 0.02, 0.03])
    b = pd.Series([0.0, 0.0, 0.0, 0.0])
    m, lo, hi = compare.difference(a, b)
    assert m == pytest.approx(0.025) and lo > 0
    assert compare.difference(a[:1], b[:1])[1] is None


def test_comparison_overlap_and_verdicts():
    ours = summary([0.02, 0.03, 0.02, 0.03, 0.025])
    theirs = summary([0.0, 0.001, -0.001, 0.0, 0.0]).iloc[1:]
    their_trades = pd.DataFrame({"date": ["2026-10-05", "2026-10-06", "2026-10-07"]})
    our_trades = trades((DAYS[0], DAYS[2], "A", "long", 1, 1, 2, 0.9, "hold", "x"))
    text = compare.build_comparison(ours, theirs, our_trades, their_trades)
    assert "2026-10-06 to 2026-10-09 (4 trading days)" in text
    assert "| Trades | 0 | 2 |" not in text and "| Trades | 1 | 2 |" in text
    assert "sentiment-trader did better" in text
    flat = summary([0.01, -0.01, 0.01, -0.01, 0.0])
    assert "Neither book is better" in compare.build_comparison(flat, summary([0.0, 0.0, 0.0, 0.0, 0.001]))
    assert "No overlapping days" in compare.build_comparison(ours, theirs.iloc[0:0])
