from datetime import date, datetime

import pandas as pd
import pytest

import analyze
import compare
import paths
import trader
from broker import ET
from logger import SUMMARY_FIELDS, TRADE_FIELDS, Book, read_trades_frame, trade_row

DAYS = [date(2026, 10, d) for d in (5, 6, 7, 8, 9)]
PAPER_TRADER_TRADE_FIELDS = ["date", "symbol", "side", "qty", "entry_time", "entry_price", "exit_time", "exit_price",
                             "exit_reason", "pnl_usd", "pnl_r", "range_pct", "entry_minute_after_open",
                             "config_version", "strategy", "strategy_version"]
PAPER_TRADER_SUMMARY_FIELDS = ["date", "trades", "wins", "losses", "gross_pnl", "equity_start", "equity_end",
                               "halted", "notes"]


def position(symbol="AAPL", side="long", entry=100.0, stop=96.0, entry_day=DAYS[0], qty=10, score=2, conf=0.9):
    return {"symbol": symbol, "side": side, "qty": str(qty), "entry_price": str(entry), "stop_price": str(stop),
            "entry_date": entry_day.isoformat(), "entry_time": f"{entry_day}T09:45:10-04:00", "score": str(score),
            "confidence": str(conf), "config_version": "1", "memory_sha256": ""}


def at(day, hh=9, mm=45):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=ET)


def book_with(tmp_path, closes):
    """A Book holding trades closed as (position kwargs, exit day, exit price, reason)."""
    book = Book(tmp_path, "paper")
    for kw, exit_day, price, reason in closes:
        book.trade(trade_row(position(**kw), at(exit_day), price, reason, 2))
    return book


def summary(rets, start=100_000.0):
    rows, eq = [], start
    for d, r in zip(DAYS, rets):
        rows.append({"date": d, "equity_start": eq, "equity_end": eq * (1 + r), "halted": False})
        eq *= 1 + r
    return pd.DataFrame(rows)


def test_files_start_with_the_paper_traders_columns():
    assert TRADE_FIELDS[:16] == PAPER_TRADER_TRADE_FIELDS
    assert SUMMARY_FIELDS[:9] == PAPER_TRADER_SUMMARY_FIELDS


def test_trade_row_r_multiple_and_times():
    row = trade_row(position(), at(DAYS[2]), 104.0, "time", 2)
    assert row["date"] == row["exit_date"] == "2026-10-07" and row["entry_date"] == "2026-10-05"
    assert row["pnl_usd"] == 40.0 and row["pnl_r"] == 1.0 and row["pnl_pct"] == 4.0
    assert row["entry_minute_after_open"] == 15 and row["exit_time"] == "2026-10-07T09:45:00-04:00"
    assert row["strategy"] == "sentiment_swing" and row["range_pct"] == ""
    short = trade_row(position(side="short", stop=104.0), at(DAYS[2]), 98.0, "flip", 2)
    assert short["pnl_usd"] == 20.0 and short["pnl_r"] == 0.5
    unknown = trade_row(position(), at(DAYS[2]), None, "unknown", 2)
    assert unknown["pnl_usd"] == "" and unknown["pnl_r"] == ""


def test_book_dedupes_trades_and_replaces_summary(tmp_path):
    book = book_with(tmp_path, [({}, DAYS[1], 101.0, "time"), ({}, DAYS[1], 101.0, "time")])
    assert len(book.trades()) == 1
    book.daily_summary({"date": "2026-10-06", "notes": "a"})
    book.daily_summary({"date": "2026-10-05"})
    book.daily_summary({"date": "2026-10-06", "notes": "b"})
    assert [(r["date"], r["notes"]) for r in book.summaries()] == [("2026-10-05", ""), ("2026-10-06", "b")]
    df = read_trades_frame(book.trades_path)
    assert df["date"].iloc[0] == DAYS[1] and df["exit_time"].iloc[0].hour == 9


def r_frame(values_by_day):
    rows = [{"date": d, "pnl_r": r, "pnl_usd": 100 * r} for d, rs in values_by_day for r in rs]
    return pd.DataFrame(rows)


def test_expectancy_range_clusters_by_day():
    ci = analyze.expectancy_ci(r_frame([(DAYS[0], [1, -1]), (DAYS[1], [2]), (DAYS[2], [0])]))
    assert ci["n"] == 4 and ci["days"] == 3 and ci["mean"] == pytest.approx(0.5)
    assert ci["lo"] == pytest.approx(0.5 - 2.46313, abs=1e-4) and ci["hi"] == pytest.approx(0.5 + 2.46313, abs=1e-4)
    one_day = analyze.expectancy_ci(r_frame([(DAYS[0], [1, 2])]))
    assert one_day["mean"] == 1.5 and analyze.fmt_range(one_day) == "n/a (needs 2+ days)"


def test_summary_and_equity_stats():
    df = r_frame([(DAYS[0], [1, -1]), (DAYS[1], [-0.5, -0.5]), (DAYS[2], [2])])
    s = analyze.summary_stats(df)
    assert (s["n"], s["wins"], s["losses"]) == (5, 2, 3) and s["profit_factor"] == pytest.approx(300 / 200)
    e = analyze.equity_stats(df, 1000.0)
    assert e["max_drawdown_usd"] == 200.0 and e["longest_losing_streak"] == 3 and e["total_return_pct"] == 10.0


def test_report_sections_match_the_paper_trader(tmp_path):
    closes = [({"symbol": "AAPL"}, DAYS[2], 104.0, "time"),
              ({"symbol": "NKE", "side": "short", "stop": 104.0, "score": -1, "conf": 0.75}, DAYS[2], 102.0, "sl"),
              ({"symbol": "TSLA", "score": 1, "conf": 0.8}, DAYS[3], 101.0, "flip"),
              ({"symbol": "F"}, DAYS[3], None, "unknown")]
    all_trades = read_trades_frame(book_with(tmp_path, closes).trades_path)
    df = analyze.filter_trades(all_trades)
    text = analyze.build_report(df, 100_000.0, 0.5, unpriced=len(all_trades) - len(df))
    headings = [ln for ln in text.splitlines() if ln.startswith("## ")]
    assert headings[:7] == ["## Summary", "## Equity curve", "## SPY buy-and-hold baseline", "## By strategy",
                            "## By config version", "## By ticker", "## By side"]
    assert headings[-1] == "## R-multiple distribution"
    assert {"## By score", "## By confidence", "## By memory version", "## By exit reason"} <= set(headings)
    assert "only 3 trades (< 30)" in text and "1 closed trades have no exit price" in text
    assert "- Trades: 3 (2 wins, 1 losses)" in text and "- Net P&L: $30.00" in text
    assert "| sentiment_swing v1 | 3 | 2 | 66.7% | $30.00 |" in text
    assert "| +2 | 1 | 1 | 100.0% | $40.00 | +1.00R |" in text
    assert "SPY 2026-10-07 open to 2026-10-08 close: +0.50% vs strategy +0.03%" in text


def test_holdings_table_marks_at_live_prices():
    rows = [position() | {"status": "open", "last_price": "99"}, position("NKE") | {"status": "pending"}]
    text = analyze.holdings_table(rows, DAYS[2], prices={"AAPL": 102.0})
    assert "| AAPL | long | 10 | 2026-10-05 @ 100.00 | 102.00 | 96.00 | $20.00 | +2.00% | +0.50R | 2 | 2 |" in text
    assert "NKE" not in text and "1 positions, unrealized $20.00." in text
    assert "| 99.00 |" in analyze.holdings_table(rows, DAYS[2])
    assert analyze.holdings_table([], DAYS[2]) == "No open positions."


def test_day_report(state):
    d = paths.book_dir("dry_run")
    book = book_with(d, [({"symbol": "AAPL"}, DAYS[2], 104.0, "time")])
    book.save_positions([position("NKE", entry_day=DAYS[1]) | {"status": "open", "last_price": "101",
                                                                "unrealized_pnl": "10"}])
    book.daily_summary({"date": DAYS[2].isoformat(), "trades": 1, "wins": 1, "losses": 0, "gross_pnl": 40.0,
                        "equity_start": 100000.0, "equity_end": 100050.0, "halted": False, "notes": "",
                        "day_pnl": 50.0, "day_return_pct": 0.05, "unrealized_pnl": 10.0, "positions": 1,
                        "gross_exposure": 1.0, "net_exposure": 1.0, "entries": 1, "scored": True})
    book.order_event(at(DAYS[2]), "submit", "NKE", side="buy", qty=10, price=100, stop_price=96,
                     client_order_id="st-20261007-NKE-entry", reason="score 2 conf 0.9")
    book.order_event(at(DAYS[2]), "skip", "TSLA", reason="no_atr")
    scores = {"input_articles": 12, "scores": {"NKE": {"score": 2, "confidence": 0.9, "reason": "beat"},
                                               "F": {"score": 0, "confidence": 0.9, "reason": "none"}}}
    text = analyze.build_day_report(d, DAYS[2], "SPY +0.10%", scores)
    assert text.startswith("# Day report 2026-10-07")
    assert "- Equity 100000.0 -> 100050.0, halted: False" in text
    assert "- Day P&L 50.0 (0.05%): closed trades 40.0, open positions' unrealized 10.0" in text
    assert "| AAPL | long | 10 | 2026-10-05 09:45 @ 100.0 | 09:45 @ 104.0 | time | 40.0 | 1.0 | 2 | 2 |" in text
    assert "## Holdings at the close" in text and "| NKE | long | 10 | 2026-10-06 @ 100.00 | 101.00 |" in text
    assert "- 09:45 NKE buy 10 at ~100, stop 96 (score 2 conf 0.9)" in text
    assert "2 symbols scored from 12 articles, 1 non-zero." in text and "- NKE +2 (0.90): beat" in text
    assert "- 09:45 TSLA : skip (no_atr)" in text


class Prices:
    def latest_prices(self, symbols):
        return {s: 102.0 for s in symbols}


def test_show_positions(state):
    book = Book(paths.book_dir("dry_run"), "dry_run")
    book.save_positions([position() | {"status": "open"}, position("NKE") | {"status": "pending"}])
    text = trader.show_positions(Prices(), book)
    assert "# Holdings (dry_run)" in text and "| AAPL | long | 10 |" in text and "$20.00" in text
    assert "Entries waiting for a fill: NKE" in text


def test_book_stats_known_answers():
    r = pd.Series([0.01, -0.01, 0.02, 0.0])
    s = compare.book_stats(r, 3)
    sd = r.std(ddof=1)
    assert s["total_return"] == pytest.approx(1.01 * 0.99 * 1.02 - 1)
    assert s["ann_vol"] == pytest.approx(sd * 252 ** 0.5)
    assert s["sharpe"] == pytest.approx(r.mean() / sd * 252 ** 0.5)
    assert s["worst_day"] == -0.01 and s["days"] == 4


def test_difference_range():
    a, b = pd.Series([0.02, 0.03, 0.02, 0.03]), pd.Series([0.0, 0.0, 0.0, 0.0])
    m, lo, hi = compare.difference(a, b)
    assert m == pytest.approx(0.025) and lo > 0
    assert compare.difference(a[:1], b[:1])[1] is None


def test_comparison_overlap_and_verdicts():
    ours = summary([0.02, 0.03, 0.02, 0.03, 0.025])
    theirs = summary([0.0, 0.001, -0.001, 0.0, 0.0]).iloc[1:]
    their_trades = pd.DataFrame({"date": ["2026-10-05", "2026-10-06", "2026-10-07"]})
    our_trades = pd.DataFrame({"date": [DAYS[2]]})
    text = compare.build_comparison(ours, theirs, our_trades, their_trades, spy=0.4)
    assert "2026-10-06 to 2026-10-09 (4 trading days)" in text
    assert "| Trades | 1 | 2 |" in text and "SPY buy-and-hold, same period: +0.40%" in text
    assert "sentiment-trader did better" in text
    flat = summary([0.01, -0.01, 0.01, -0.01, 0.0])
    assert "Neither book is better" in compare.build_comparison(flat, summary([0.0, 0.0, 0.0, 0.0, 0.001]))
    assert "No overlapping days" in compare.build_comparison(ours, theirs.iloc[0:0])


def test_daily_returns_and_drawdown():
    r = analyze.daily_returns(summary([0.10, -0.20, 0.05, 0.0, 0.0]))
    assert list(r.round(6)) == [0.10, -0.20, 0.05, 0.0, 0.0]
    assert analyze.max_drawdown(r) == pytest.approx(-0.20)
