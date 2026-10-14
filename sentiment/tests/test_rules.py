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
    assert trader.exit_reason(row, None, 5, ex) == "time"
    assert trader.exit_reason(row, {"AAPL": e(-1, 0.1)}, 1, ex) == "flip"
    assert trader.exit_reason(row, {"AAPL": e(0)}, 1, ex) is None
    assert trader.exit_reason({"symbol": "AAPL", "side": "short"}, {"AAPL": e(1)}, 1, ex) == "flip"
    assert trader.exit_reason(row, {"AAPL": e(-2)}, 1, ex | {"exit_on_score_flip": False}) is None


def idea(kind, legs, conviction=2, confidence=0.8, pair=None):
    out = {"kind": kind, "legs": [{"symbol": s, "side": side} for s, side in legs], "conviction": conviction,
           "confidence": confidence, "reason": "why", "evidence": ["1"]}
    return out | ({"pair": pair} if pair else {})


def cand(sym, side="long", conviction=2, confidence=1.0, strategy="sentiment_swing"):
    return {"strategy": strategy, "legs": [(sym, side)], "conviction": conviction, "confidence": confidence,
            "reason": "r"}


def test_candidates_from_scores_and_desk_ideas():
    record = {"scores": {"A": e(2, 0.9), "B": e(1, 0.5)},
              "desk": {"ideas": [idea("theme", [("VST", "long")], 1, 0.75), idea("reversal", [("X", "short")], 1, 0.6),
                                 idea("pair", [("KO", "short"), ("PEP", "long")], pair={"pair": "KO/PEP"})]}}
    cands = trader.candidates(record, CFG["entry"])
    assert [(c["strategy"], c["legs"]) for c in cands] == [
        ("sentiment_swing", [("A", "long")]), ("theme", [("VST", "long")]),
        ("pair", [("KO", "short"), ("PEP", "long")])]
    assert cands[2]["pair"] == {"pair": "KO/PEP"}
    no_shorts = trader.candidates(record, CFG["entry"] | {"allow_shorts": False})
    assert [c["strategy"] for c in no_shorts] == ["sentiment_swing", "theme"]


def test_rank_candidates():
    cands = [cand("A", conviction=1, confidence=0.95), cand("B", "short", 2, 0.6), cand("C", confidence=0.9),
             cand("E", confidence=0.9, strategy="theme"), cand("D", confidence=0.9), cand("F")]
    ranked = trader.rank_candidates(cands, skip={"F"})
    assert [c["legs"][0][0] for c in ranked] == ["C", "D", "E", "B", "A"]


def plan(cands, **kw):
    args = dict(prices={}, atrs={}, flags={}, equity=100_000, book_values=[], orders_today=0, cfg=CFG)
    for c in cands:
        for sym, _ in c["legs"]:
            args["prices"].setdefault(sym, 100.0)
            args["atrs"].setdefault(sym, 2.0)
            args["flags"].setdefault(sym, (True, True, True))
    for k, v in kw.items():
        args[k] = args[k] | v if isinstance(v, dict) else v
    return trader.plan_entries(cands, **args)


def test_plan_entries_sizes_and_stops():
    plans, skips = plan([cand("A"), cand("B", "short", confidence=0.9)])
    assert skips == []
    assert plans[0] == {"symbol": "A", "side": "long", "qty": 50, "price": 100.0, "score": 2, "confidence": 1.0,
                        "stop_price": 96.0, "strategy": "sentiment_swing", "reason": "r", "pair": None}
    assert plans[1]["stop_price"] == 104.0 and plans[1]["score"] == -2


def test_plan_entries_skips():
    cands = [cand("A"), cand("B"), cand("C", "short"), cand("D"), cand("E"), cand("F")]
    plans, skips = plan(cands, prices={"A": 4.0, "B": None, "F": 6000.0},
                        flags={"C": (True, True, False), "D": (False, False, False)}, atrs={"E": None})
    assert plans == []
    assert dict(skips) == {"A": "price_under_min", "B": "no_price", "C": "not_easy_to_borrow",
                           "D": "not_tradable", "E": "no_atr", "F": "size_zero"}


def test_plan_entries_respects_book_limits_and_order_cap():
    cands = [cand(s) for s in "ABCDEFGHIJ"]
    plans, skips = plan(cands)
    assert len(plans) == 8 and dict(skips)["I"] == "net_exposure"  # 8 x 5% = 40% net
    plans, skips = plan(cands, book_values=[5000.0, -5000.0] * 7 + [5000.0])  # 75% gross
    assert len(plans) == 1 and dict(skips)["B"] == "gross_exposure"
    plans, skips = plan(cands, orders_today=29)
    assert len(plans) == 1 and dict(skips)["B"] == "order_cap"


def test_pair_enters_both_legs_or_neither():
    pair = {"strategy": "pair", "legs": [("KO", "short"), ("PEP", "long")], "conviction": 2, "confidence": 0.8,
            "reason": "pair: gap", "pair": {"pair": "KO/PEP"}}
    plans, skips = plan([pair])
    assert [(p["symbol"], p["side"], p["score"]) for p in plans] == [("KO", "short", -2), ("PEP", "long", 2)]
    assert plans[0]["stop_price"] == 106.0  # pairs use the wider 3 x ATR stop
    plans, skips = plan([pair, cand("A")], flags={"PEP": (False, False, False)})
    assert [p["symbol"] for p in plans] == ["A"] and skips == [("PEP", "not_tradable")]
    plans, skips = plan([cand("KO"), pair])
    assert [p["symbol"] for p in plans] == ["KO"] and skips == [("KO", "duplicate")]
