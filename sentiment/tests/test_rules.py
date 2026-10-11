from datetime import date

import config
import paths
import trader

CFG = config.load_json(paths.CONFIG_DEFAULT)


def e(s, c=0.8):
    return {"score": s, "confidence": c, "reason": "r", "article_ids": ["1"] if s else []}


def test_signal():
    entry = CFG["entry"]
    assert trader.signal(e(2), entry) == "long"
    assert trader.signal(e(-2), entry) == "short"
    assert trader.signal(e(1, 0.7), entry) == "long"
    assert trader.signal(e(1, 0.69), entry) is None
    assert trader.signal(e(-1, 0.9), entry) == "short"
    assert trader.signal(e(0, 1.0), entry) is None
    assert trader.signal(e(1, 0.9), entry | {"allow_score_1": False}) is None
    assert trader.signal(e(-2), entry | {"allow_shorts": False}) is None


def test_hold_days_counts_trading_days_after_entry():
    days = [date(2026, 10, d) for d in (5, 6, 7, 8, 9, 13, 14)]  # 12 Oct is a holiday
    assert trader.hold_days(date(2026, 10, 5), date(2026, 10, 5), days) == 0
    assert trader.hold_days(date(2026, 10, 5), date(2026, 10, 13), days) == 5


def test_exit_reason():
    row = {"symbol": "AAPL", "side": "long"}
    ex = CFG["exit"]
    assert trader.exit_reason(row, None, 5, ex) == "hold"
    assert trader.exit_reason(row, {"AAPL": e(-1, 0.1)}, 1, ex) == "flip"
    assert trader.exit_reason(row, {"AAPL": e(0)}, 1, ex) is None
    assert trader.exit_reason({"symbol": "AAPL", "side": "short"}, {"AAPL": e(1)}, 1, ex) == "flip"
    assert trader.exit_reason(row, {"AAPL": e(-2)}, 1, ex | {"exit_on_score_flip": False}) is None


def test_rank_candidates():
    scores = {"A": e(1, 0.95), "B": e(-2, 0.6), "C": e(2, 0.9), "D": e(1, 0.5), "E": e(2, 0.9), "F": e(2, 1.0)}
    ranked = trader.rank_candidates(scores, CFG["entry"], skip={"F"})
    assert [c[0] for c in ranked] == ["C", "E", "B", "A"]
    assert ranked[2][1] == "short"


def plan(cands, **kw):
    args = dict(prices={}, atrs={}, flags={}, equity=100_000, book_values=[], orders_today=0, cfg=CFG)
    for sym, *_ in cands:
        args["prices"].setdefault(sym, 100.0)
        args["atrs"].setdefault(sym, 2.0)
        args["flags"].setdefault(sym, (True, True, True))
    for k, v in kw.items():
        args[k] = args[k] | v if isinstance(v, dict) else v
    return trader.plan_entries(cands, **args)


def test_plan_entries_sizes_and_stops():
    plans, skips = plan([("A", "long", 2, 0.9), ("B", "short", -2, 0.9)])
    assert skips == []
    assert plans[0] | {} == {"symbol": "A", "side": "long", "qty": 50, "price": 100.0, "score": 2,
                             "confidence": 0.9, "stop_price": 96.0}
    assert plans[1]["stop_price"] == 104.0


def test_plan_entries_skips():
    cands = [("A", "long", 2, 1), ("B", "long", 2, 1), ("C", "short", -2, 1), ("D", "long", 2, 1),
             ("E", "long", 2, 1), ("F", "long", 2, 1)]
    plans, skips = plan(cands, prices={"A": 4.0, "B": None, "F": 6000.0},
                        flags={"C": (True, True, False), "D": (False, False, False)}, atrs={"E": None})
    assert plans == []
    assert dict(skips) == {"A": "price_under_min", "B": "no_price", "C": "not_easy_to_borrow",
                           "D": "not_tradable", "E": "no_atr", "F": "size_zero"}


def test_plan_entries_respects_book_limits_and_order_cap():
    cands = [(s, "long", 2, 1) for s in "ABCDEFGHIJ"]
    plans, skips = plan(cands)
    assert len(plans) == 8 and dict(skips)["I"] == "net_exposure"  # 8 x 5% = 40% net
    plans, skips = plan(cands, book_values=[5000.0, -5000.0] * 4 + [5000.0])
    assert len(plans) == 1 and dict(skips)["B"] == "max_positions"
    plans, skips = plan(cands, orders_today=29)
    assert len(plans) == 1 and dict(skips)["B"] == "order_cap"
