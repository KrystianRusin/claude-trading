"""Weekly self-review: Claude reads how its scores turned out and updates its lessons memory.

    python review.py run     review if enough new outcomes exist since the last review
    python review.py show    print the current memory

Python builds the evidence (calibration, trade stats, examples with headlines), Claude replies with
JSON only, and Python validates it before writing state/memory.json and state/memory.md. A failed
or invalid review leaves the previous memory in place.
"""
import argparse
import json
import sys
from datetime import date, datetime, timedelta

import lifetime
import memory
import news
import paths
import score
from broker import ET

MODEL = "claude-sonnet-5-5"
TIMEOUT_SECONDS = 600
MAX_LESSONS = 12
LESSON_CHARS = 240
EVIDENCE_CHARS = 200
MAX_LINES = 5
LINE_CHARS = 200
NOTES_CHARS = 600
EXAMPLES = 12
HEADLINES = 3
CONFIDENCE = ("low", "medium", "high")
OUTPUT_KEYS = {"lessons", "working", "not_working", "notes"}
LESSON_KEYS = {"lesson", "evidence", "confidence"}


def prompt_text():
    return (paths.REPO / "REVIEW_PROMPT.md").read_text(encoding="utf-8")


def _articles_by_id(day):
    """Archived articles from the week before a score date, by id."""
    out = {}
    for i in range(8):
        for a in news.read_day(day - timedelta(days=i)):
            out[a["id"]] = a
    return out


def examples(outcomes, records):
    """The most instructive new scores: biggest moves with, against, and without a non-zero score."""
    by_key = {(r["date"], s): e for r in records for s, e in r.get("scores", {}).items()}
    df = outcomes.copy()
    df["signed"] = df["fwd_return"] * df["score"].clip(-1, 1)
    picks = {"right": df[df["score"] != 0].nlargest(EXAMPLES, "signed"),
             "wrong": df[(df["score"] != 0) & (df["signed"] < 0)].nsmallest(EXAMPLES, "signed"),
             "missed": df[df["score"] == 0].assign(size=df["fwd_return"].abs()).nlargest(EXAMPLES // 2, "size")}
    out = {}
    for name, rows in picks.items():
        items = []
        for r in rows.itertuples():
            entry = by_key.get((r.date.isoformat(), r.symbol), {})
            known = _articles_by_id(r.date)
            cited = [known[i] for i in entry.get("article_ids", []) if i in known]
            if not cited:
                cited = sorted((a for a in known.values() if r.symbol in a["symbols"]
                                and a["published_at"][:10] <= r.date.isoformat()),
                               key=lambda a: a["published_at"], reverse=True)
            heads = [a["headline"] for a in cited][:HEADLINES]
            items.append({"date": r.date.isoformat(), "symbol": r.symbol, "score": int(r.score),
                          "confidence": r.confidence, "reason": entry.get("reason", ""), "headlines": heads,
                          "fwd_return_pct": round(100 * r.fwd_return, 2)})
        out[name] = items
    return out


def build_input(stats, new_outcomes, records, current):
    keep = {k: v for k, v in stats.items() if k not in ("book",)}
    return {"current_memory": None if not current else {k: current[k] for k in ("lessons", "working", "not_working")},
            "lifetime": keep,
            "since_last_review": {"scores": int(len(new_outcomes)),
                                  "calibration": lifetime.calibration(new_outcomes)},
            "examples": examples(new_outcomes, records)}


def _line(text, limit):
    return " ".join(str(text).split())[:limit]


def validate(output):
    """(clean memory fields, errors). Clean is None unless the reply matches the schema."""
    if not isinstance(output, dict) or set(output) != OUTPUT_KEYS:
        return None, [f"output must be an object with exactly the keys {sorted(OUTPUT_KEYS)}"]
    errors = []
    lessons = output["lessons"]
    if not isinstance(lessons, list) or len(lessons) > MAX_LESSONS:
        errors.append(f"lessons must be a list of at most {MAX_LESSONS}")
        lessons = []
    clean_lessons = []
    for i, x in enumerate(lessons):
        if not isinstance(x, dict) or set(x) != LESSON_KEYS:
            errors.append(f"lesson {i}: must have exactly the keys {sorted(LESSON_KEYS)}")
        elif not all(isinstance(x[k], str) and x[k].strip() for k in LESSON_KEYS):
            errors.append(f"lesson {i}: every field must be a non-empty string")
        elif x["confidence"] not in CONFIDENCE:
            errors.append(f"lesson {i}: confidence must be one of {CONFIDENCE}")
        else:
            clean_lessons.append({"lesson": _line(x["lesson"], LESSON_CHARS),
                                  "evidence": _line(x["evidence"], EVIDENCE_CHARS), "confidence": x["confidence"]})
    lists = {}
    for key in ("working", "not_working"):
        v = output[key]
        if not isinstance(v, list) or len(v) > MAX_LINES or not all(isinstance(t, str) for t in v):
            errors.append(f"{key} must be a list of at most {MAX_LINES} strings")
        else:
            lists[key] = [_line(t, LINE_CHARS) for t in v if t.strip()]
    if not isinstance(output["notes"], str):
        errors.append("notes must be a string")
    if errors:
        return None, errors
    return {"lessons": clean_lessons, **lists, "notes": _line(output["notes"], NOTES_CHARS)}, []


def review(now, book, cfg, caller=None, log=None, outcomes=None, fetch=True):
    """Run one review. Returns the new memory, or None when there is too little new evidence.

    With fetch=False nothing is downloaded and `outcomes` must be given.
    """
    caller = caller or (lambda p, i: score.call_claude(p, i, model=MODEL, timeout=TIMEOUT_SECONDS))
    horizon = cfg["exit"]["max_hold_days"]
    records = lifetime.read_score_files()
    if outcomes is None and fetch and records:
        outcomes = lifetime.load_outcomes(records, horizon)
    stats = lifetime.build(book, horizon, log, outcomes=outcomes, fetch=fetch)
    current = memory.load()
    since = date.fromisoformat(current["outcomes_through"]) if current else date.min
    new = outcomes[outcomes["date"] > since] if outcomes is not None and len(outcomes) else None
    need = cfg["memory"]["min_new_outcomes"]
    if new is None or len(new) < need:
        if log:
            log.info("review skipped: %d new scored outcomes, need %d", 0 if new is None else len(new), need)
        return None
    input_json = json.dumps(build_input(stats, new, records, current), ensure_ascii=False, default=str)
    prompt = prompt_text()
    problems = []
    for attempt in (1, 2):
        try:
            clean, errors = validate(score.extract_json(caller(prompt, input_json)))
        except score.ScoringError as e:
            clean, errors = None, [str(e)]
        if clean is not None:
            return memory.save(clean | {
                "updated_at": now.isoformat(), "model": MODEL, "prompt_sha256": score.sha256(prompt),
                "outcomes_through": max(new["date"]).isoformat(),
                "based_on": {"scores": int(len(outcomes)), "new_scores": int(len(new)),
                             "trades": stats["priced_trades"]}})
        problems.append(f"attempt {attempt}: " + "; ".join(errors[:10]))
        if log:
            log.warning("review attempt %d failed: %s", attempt, "; ".join(errors[:10]))
    raise score.ScoringError("review failed twice, memory unchanged: " + " | ".join(problems))


def main(argv=None):
    import config
    from logger import notify, setup_logging

    ap = argparse.ArgumentParser(description="weekly self-review")
    ap.add_argument("command", choices=["run", "show"])
    ap.add_argument("--book", choices=["paper", "dry_run"])
    args = ap.parse_args(argv)
    config.init_state()
    log = setup_logging("review")
    if args.command == "show":
        f = paths.memory_file()
        print(f.read_text(encoding="utf-8") if f.exists() else "no memory yet")
        return 0
    cfg = config.load_config()
    try:
        mem = review(datetime.now(ET), args.book or cfg["mode"], cfg, log=log)
    except Exception as e:
        log.exception("review failed")
        notify(f"weekly review failed, memory unchanged: {e}")
        return 1
    if mem:
        log.info("memory updated: %d lessons, version %s", len(mem["lessons"]), mem["memory_sha256"][:12])
    return 0


if __name__ == "__main__":
    sys.exit(main())
