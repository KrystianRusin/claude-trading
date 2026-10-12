import json
import logging
from datetime import datetime, time

import pandas as pd
import pytest

import config
import paths
import trader
from broker import ET, OTOUnavailableError
from logger import Book
from simbroker import SimBroker

DAYS = [d.date() for d in pd.bdate_range("2026-08-03", "2026-10-30")]
D0 = DAYS[40]
LOG = logging.getLogger("test")


def flat_bars(symbols, price=100.0):
    return {s: pd.DataFrame({"open": price, "high": price + 1, "low": price - 1, "close": price, "volume": 1000},
                            index=pd.Index(DAYS, name="date")) for s in symbols}


def write_scores(day, **scores):
    entries = {s: {"score": v[0], "confidence": v[1], "reason": "r", "article_ids": ["1"] if v[0] else []}
               for s, v in scores.items()}
    paths.score_file(day).write_text(json.dumps({"date": day.isoformat(), "prompt_sha256": "abc", "model": "m",
                                                 "scores": entries}))


class Env:
    def __init__(self, symbols=("AAPL", "NKE", "TSLA"), broker_cls=SimBroker):
        self.cfg = config.load_config()
        self.bars = flat_bars(symbols)
        self.data = trader.ReplayData(self.bars)
        self.clock = {"now": None}
        self.book = Book(paths.book_dir("dry_run"), "dry_run")
        self.sim = broker_cls(self.data, self.book.dir / "sim_account.json", clock=lambda: self.clock["now"])
        self.halt = paths.halt_file()

    def job(self, day, name):
        phase, at = ("open", time(9, 45)) if name == "run" else ("close", time(16, 15))
        self.data.set(day, phase)
        self.clock["now"] = datetime.combine(day, at, ET)
        t = trader.Trader(self.sim, self.cfg, self.book, self.clock["now"], LOG, halt_path=self.halt)
        getattr(t, name)()
        return t

    def day(self, day):
        self.job(day, "run")
        self.job(day, "close")

    def rows(self):
        return {r["symbol"]: r for r in self.book.positions()}


@pytest.fixture
def env(state):
    return Env()


def test_entry_places_market_order_with_stop(env):
    write_scores(D0, AAPL=(2, 0.9), NKE=(-2, 0.8), TSLA=(1, 0.5))
    env.job(D0, "run")
    rows = env.rows()
    assert set(rows) == {"AAPL", "NKE"}
    assert rows["AAPL"]["status"] == "open" and float(rows["AAPL"]["qty"]) == 50
    assert float(rows["AAPL"]["stop_price"]) == 96.0 and float(rows["NKE"]["stop_price"]) == 104.0
    pos = env.sim.list_positions()
    assert pos["AAPL"].qty == 50 and pos["NKE"].qty == -50
    stop = env.sim.get_order(rows["AAPL"]["stop_order_id"])
    assert stop.order_type == "stop" and stop.is_open and stop.side == "sell"
    submits = [e for e in env.book.order_events(D0) if e["event"] == "submit"]
    assert [e["client_order_id"] for e in submits] == [f"st-{D0:%Y%m%d}-AAPL-entry", f"st-{D0:%Y%m%d}-NKE-entry"]


def test_rerun_same_day_never_double_submits(env):
    write_scores(D0, AAPL=(2, 0.9))
    env.job(D0, "run")
    env.job(D0, "run")
    assert env.sim.list_positions()["AAPL"].qty == 50
    assert sum(e["event"] == "submit" for e in env.book.order_events(D0)) == 1


def test_exit_after_max_hold_days(env):
    write_scores(D0, AAPL=(2, 0.9))
    env.day(D0)
    for d in DAYS[41:45]:
        env.day(d)
        assert "AAPL" in env.rows()
    env.day(DAYS[45])
    assert "AAPL" not in env.rows() and "AAPL" not in env.sim.list_positions()
    (t,) = env.book.trades()
    assert t["exit_reason"] == "time" and t["hold_days"] == "5" and t["score"] == "2"
    assert t["date"] == t["exit_date"] == DAYS[45].isoformat() and float(t["pnl_usd"]) == 0
    assert t["strategy"] == "sentiment_swing" and t["entry_minute_after_open"] == "15"
    assert t["entry_time"].startswith(f"{D0}T09:45") and t["exit_time"].startswith(f"{DAYS[45]}T09:45")
    assert not env.sim.open_orders()


def test_flip_exit_without_same_day_reentry(env):
    write_scores(D0, AAPL=(2, 0.9))
    env.day(D0)
    write_scores(DAYS[41], AAPL=(-2, 0.9))
    env.day(DAYS[41])
    (t,) = env.book.trades()
    assert t["exit_reason"] == "flip"
    assert env.rows() == {} and env.sim.list_positions() == {}


def test_stop_out_recorded_at_close(env):
    write_scores(D0, AAPL=(2, 0.9))
    env.day(D0)
    env.bars["AAPL"].loc[DAYS[41], ["open", "low"]] = [99.0, 90.0]
    env.day(DAYS[41])
    (t,) = env.book.trades()
    assert t["exit_reason"] == "sl" and float(t["exit_price"]) == 96.0 and float(t["pnl_usd"]) == -200.0
    assert float(t["pnl_r"]) == -1.0
    day = env.book.summaries()[-1]
    assert float(day["gross_pnl"]) == -200.0 and float(day["day_pnl"]) == -200.0
    assert (day["trades"], day["wins"], day["losses"]) == ("1", "0", "1")
    assert float(day["day_return_pct"]) == -0.2 and float(day["unrealized_pnl"]) == 0


def test_gap_through_stop_fills_at_open(env):
    write_scores(D0, AAPL=(2, 0.9))
    env.day(D0)
    env.bars["AAPL"].loc[DAYS[41], ["open", "low"]] = [92.0, 90.0]
    env.day(DAYS[41])
    assert float(env.book.trades()[0]["exit_price"]) == 92.0


def test_no_score_file_means_no_entries(env):
    env.day(D0)
    assert env.rows() == {} and env.book.order_events(D0) == []
    (summary,) = env.book.summaries()
    assert summary["scored"] == "False" and float(summary["equity_end"]) == 100_000


def test_halt_closes_everything_and_blocks_entries(env):
    write_scores(D0, AAPL=(2, 0.9))
    env.day(D0)
    env.halt.write_text("")
    write_scores(DAYS[41], NKE=(2, 0.9))
    env.job(DAYS[41], "run")
    assert env.sim.list_positions() == {}
    assert [t["exit_reason"] for t in env.book.trades()] == ["halt"]


def test_loss_limit_closes_all_and_blocks_next_session(env):
    write_scores(D0, AAPL=(2, 0.9))
    env.job(D0, "run")
    env.sim.s["equity_close"][DAYS[39].isoformat()] = 110_000.0
    env.job(D0, "close")
    assert [t["exit_reason"] for t in env.book.trades()] == ["loss_limit"]
    assert env.book.state()["entries_blocked_on"] == DAYS[41].isoformat()
    write_scores(DAYS[41], NKE=(2, 0.9))
    env.day(DAYS[41])
    assert env.sim.list_positions() == {}
    write_scores(DAYS[42], NKE=(2, 0.9))
    env.day(DAYS[42])
    assert "NKE" in env.sim.list_positions()


def test_reconcile_adopts_unknown_and_closes_missing(env):
    env.data.set(D0, "open")
    env.clock["now"] = datetime.combine(D0, time(9, 45), ET)
    env.sim.submit_market_order("TSLA", "buy", 10, "manual-1")
    env.book.save_positions([{"symbol": "NKE", "side": "long", "qty": 5, "status": "open",
                              "entry_date": DAYS[38].isoformat(), "entry_price": 90.0}])
    env.job(D0, "run")
    rows = env.rows()
    assert rows["TSLA"]["status"] == "open" and rows["TSLA"]["stop_order_id"]
    assert float(rows["TSLA"]["stop_price"]) == 96.0
    (t,) = env.book.trades()
    assert t["symbol"] == "NKE" and t["exit_reason"] == "unknown" and float(t["exit_price"]) == 100.0
    assert any(e["event"] == "adopt" for e in env.book.order_events(D0))


class NoOTO(SimBroker):
    def submit_entry(self, *a, **kw):
        raise OTOUnavailableError("order_class oto not supported")


def test_oto_fallback_submits_separate_stop(state):
    env = Env(broker_cls=NoOTO)
    write_scores(D0, AAPL=(2, 0.9))
    env.job(D0, "run")
    row = env.rows()["AAPL"]
    stop = env.sim.get_order(row["stop_order_id"])
    assert stop.client_order_id == f"st-{D0:%Y%m%d}-AAPL-stop" and stop.stop_price == 96.0 and stop.is_open


def test_daily_summary_row(env):
    write_scores(D0, AAPL=(2, 0.9), NKE=(-2, 0.9))
    env.day(D0)
    (s,) = env.book.summaries()
    assert s["entries"] == "2" and s["positions"] == "2" and float(s["gross_exposure"]) == 10
    assert float(s["net_exposure"]) == 0 and s["scored"] == "True" and s["halted"] == "False"


def test_market_closed_does_nothing(env):
    saturday = datetime(2026, 10, 10, 9, 45, tzinfo=ET)
    env.data.set(saturday.date(), "open")
    trader.Trader(env.sim, env.cfg, env.book, saturday, LOG).run()
    assert not env.book.positions_path.exists()


def test_replay_uses_saved_scores_only(state):
    write_scores(D0, AAPL=(2, 0.9))
    write_scores(DAYS[41], AAPL=(-2, 0.9))
    book = trader.replay(DAYS[39], DAYS[43], config.load_config(), flat_bars(["AAPL", "NKE"]), LOG)
    (t,) = book.trades()
    assert t["exit_reason"] == "flip" and t["entry_date"] == D0.isoformat()
    assert len(book.summaries()) == 5
    assert not paths.book_dir("dry_run").joinpath("trades.csv").exists()


def test_job_lock_is_exclusive(state):
    with trader.job_lock(paths.lock_file()):
        with pytest.raises(RuntimeError, match="another trader job"):
            with trader.job_lock(paths.lock_file()):
                pass
