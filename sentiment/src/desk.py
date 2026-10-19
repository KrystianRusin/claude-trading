"""Morning trading desk: Claude reads the day like a retail trader and proposes a few trade ideas.

Runs inside `score.py run` after the news scores. Claude (no tools) gets today's non-zero scores,
the newest headlines, each tradable stock's 1, 5 and 20-session returns, the pair divergences from
pairs.py and the current holdings, and replies with JSON ideas: a kind (theme, pair, momentum,
reversal, sector, news), one or two legs, conviction, confidence, a reason and cited evidence.

Python checks every idea against the input and drops those that break a rule; nothing Claude
writes is executed. The trader sizes and stops the surviving ideas with the same rules as scores.
"""
import json

import paths
import score

MODEL = "claude-sonnet-5-5"
TIMEOUT_SECONDS = 300
KINDS = ("theme", "pair", "momentum", "reversal", "sector", "news")
IDEA_KEYS = {"kind", "legs", "conviction", "confidence", "reason", "evidence"}
REASON_CHARS = 400


def prompt_text():
    return paths.DESK_PROMPT.read_text(encoding="utf-8")


def market_moves(bars):
    """{symbol: {r1, r5, r20}} percent returns over the last 1, 5 and 20 sessions of each bar frame."""
    out = {}
    for sym, df in bars.items():
        closes = df["close"].dropna() if df is not None else []
        if len(closes) < 21:
            continue
        last = float(closes.iloc[-1])
        out[sym] = {f"r{n}": round((last / float(closes.iloc[-1 - n]) - 1) * 100, 1) for n in (1, 5, 20)}
    return out


def build_input(day, holdings, scores, articles, moves, divs, max_ideas, max_headlines):
    """Claude's input. `divs` are pairs.divergences(); pairs touching a held symbol are left out."""
    held = {h["symbol"] for h in holdings}
    newest = sorted(articles, key=lambda a: a["published_at"], reverse=True)[:max_headlines]
    return {
        "date": day.isoformat(), "max_ideas": max_ideas,
        "holdings": [{k: h.get(k, "") for k in ("symbol", "side", "entry_date", "strategy")} for h in holdings],
        "scores": {s: {k: v[k] for k in ("score", "confidence", "reason")} for s, v in scores.items() if v["score"]},
        "headlines": [{"id": str(a["id"]), "symbols": a["symbols"], "headline": a["headline"]} for a in newest],
        "market": moves,
        "pairs": [{k: p[k] for k in ("pair", "z", "rich", "cheap", "correlation", "half_life")}
                  for p in divs if p["a"] not in held and p["b"] not in held],
    }


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check_idea(idea, model_input):
    """None when the idea follows every rule, else the first problem found."""
    if not isinstance(idea, dict) or set(idea) != IDEA_KEYS:
        return f"must have exactly the keys {sorted(IDEA_KEYS)}"
    kind, legs, ev = idea["kind"], idea["legs"], idea["evidence"]
    if kind not in KINDS:
        return f"unknown kind {kind!r}"
    if not isinstance(legs, list) or len(legs) != (2 if kind == "pair" else 1):
        return f"a {kind} idea needs {2 if kind == 'pair' else 1} leg(s)"
    for leg in legs:
        if not isinstance(leg, dict) or set(leg) != {"symbol", "side"} or leg["side"] not in ("long", "short"):
            return "each leg needs exactly a symbol and a side of long or short"
        if leg["symbol"] not in model_input["market"]:
            return f"{leg['symbol']!r} is not tradable"
        if leg["symbol"] in {h["symbol"] for h in model_input["holdings"]}:
            return f"{leg['symbol']} is already held"
    if len({leg["symbol"] for leg in legs}) != len(legs):
        return "the legs repeat a symbol"
    if idea["conviction"] not in (1, 2) or isinstance(idea["conviction"], bool):
        return f"conviction {idea['conviction']!r} is not 1 or 2"
    if not (_number(idea["confidence"]) and 0 <= idea["confidence"] <= 1):
        return f"confidence {idea['confidence']!r} is not in 0..1"
    if not isinstance(idea["reason"], str) or not idea["reason"].strip():
        return "reason must be a non-empty string"
    if not isinstance(ev, list) or not ev or not all(isinstance(e, str) for e in ev):
        return "evidence must be a non-empty list of strings"
    ids = {h["id"] for h in model_input["headlines"]}
    pairs = {p["pair"]: p for p in model_input["pairs"]}
    for e in ev:
        kind_, _, ref = e.partition(":")
        ok = (e in ids or (kind_ == "score" and ref in model_input["scores"])
              or (kind_ == "move" and ref in model_input["market"]) or (kind_ == "pair" and ref in pairs))
        if not ok:
            return f"evidence {e!r} is not in the input"
    if kind == "pair":
        cited = [pairs[e[5:]] for e in ev if e.startswith("pair:")]
        sides = {leg["symbol"]: leg["side"] for leg in legs}
        if not any(sides == {p["rich"]: "short", p["cheap"]: "long"} for p in cited):
            return "a pair idea must short the rich leg and buy the cheap leg of a cited pair"
    return None


def validate(output, model_input, divs):
    """(ideas, problems). Ideas that break a rule are dropped one by one; None when the reply is unusable."""
    if not isinstance(output, dict) or set(output) != {"ideas"} or not isinstance(output["ideas"], list):
        return None, ['reply must be {"ideas": [...]}']
    by_pair = {p["pair"]: p for p in divs}
    ideas, problems = [], []
    for n, idea in enumerate(output["ideas"][:model_input["max_ideas"]]):
        problem = check_idea(idea, model_input)
        if problem:
            problems.append(f"idea {n + 1}: {problem}")
            continue
        clean = {"kind": idea["kind"], "legs": [dict(leg) for leg in idea["legs"]], "conviction": idea["conviction"],
                 "confidence": round(float(idea["confidence"]), 4),
                 "reason": " ".join(idea["reason"].split())[:REASON_CHARS], "evidence": list(dict.fromkeys(idea["evidence"]))}
        if idea["kind"] == "pair":
            p = next(by_pair[e[5:]] for e in idea["evidence"] if e.startswith("pair:") and e[5:] in by_pair)
            clean["pair"] = {k: p[k] for k in ("pair", "a", "b", "beta", "mean", "std", "z")}
        ideas.append(clean)
    if len(output["ideas"]) > model_input["max_ideas"]:
        problems.append(f"{len(output['ideas']) - model_input['max_ideas']} ideas over max_ideas ignored")
    return ideas, problems


def propose(model_input, divs, caller=None, log=None):
    """The desk record for the score file: ideas, dropped-idea problems, and an error if the reply was unusable."""
    caller = caller or (lambda p, i: score.call_claude(p, i, model=MODEL, timeout=TIMEOUT_SECONDS))
    prompt = prompt_text()
    record = {"model": MODEL, "prompt_sha256": score.sha256(prompt), "ideas": [], "rejected": [], "error": None}
    if not model_input["market"] or model_input["max_ideas"] == 0:
        return record | {"model": "none"}
    input_json = json.dumps(model_input, ensure_ascii=False)
    for attempt in (1, 2):
        try:
            ideas, problems = validate(score.extract_json(caller(prompt, input_json)), model_input, divs)
        except score.ScoringError as e:
            ideas, problems = None, [str(e)]
        if ideas is not None:
            if problems and log:
                log.warning("desk ideas dropped: %s", "; ".join(problems))
            return record | {"ideas": ideas, "rejected": problems}
        if log:
            log.warning("desk attempt %d failed: %s", attempt, "; ".join(problems))
    return record | {"error": "; ".join(problems)}
