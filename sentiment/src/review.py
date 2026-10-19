"""Daily review: Claude writes the day's journal and updates its memory of hypotheses and lessons.

    python src/review.py run     journal today (trading days only) and update the memory
    python src/review.py show    print the current memory

Python builds the input (today's day report, every memory entry with its latest evidence check,
lifetime stats, calibration and examples of scores whose outcome is new). Claude replies with JSON
only: a journal and a list of memory operations. Python validates both, applies the operations,
re-runs the evidence test that sets each entry's status (memory.py), and writes
data/journal/<date>.md once. A failed or invalid review leaves the memory unchanged.
"""
import argparse
import json
import sys
from datetime import date, datetime, timedelta

import analyze
import db
import evidence
import lifetime
import memory
import news
import paths
import score
from broker import ET
from logger import read_trades_frame

MODEL = "claude-sonnet-5-5"
TIMEOUT_SECONDS = 600
EXAMPLES = 12
HEADLINES = 3
OUTPUT_KEYS = {"journal", "memory_ops"}
JOURNAL_FIELDS = {"market": 300, "result": 300, "went_well": 800, "went_wrong": 800, "luck_vs_logic": 800,
                  "execution": 800}
JOURNAL_HEADINGS = {"went_well": "What went well", "went_wrong": "What went wrong",
                    "luck_vs_logic": "Luck vs logic", "execution": "Execution and operations"}


def prompt_text():
    return paths.REVIEW_PROMPT.read_text(encoding="utf-8")


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
             "missed": df[df["score"] == 0].assign(size=lambda d: d["fwd_return"].abs()).nlargest(EXAMPLES // 2, "size")}
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


def build_input(day_report, mem, stats, new_outcomes, records):
    keep = {k: v for k, v in stats.items() if k not in ("book",)}
    entries = [{k: e.get(k) for k in ("id", "title", "status", "scope", "since", "query", "evidence", "implication",
                                      "check")} for e in memory.active(mem)]
    return {"day_report": day_report,
            "memory": {"active": entries, "active_count": len(entries), "max_active": memory.MAX_ACTIVE},
            "query_tables": {name: sorted(t["columns"]) for name, t in evidence.TABLES.items()},
            "lifetime": keep,
            "new_outcomes": {"scores": 0 if new_outcomes is None else int(len(new_outcomes)),
                             "calibration": {} if new_outcomes is None or not len(new_outcomes)
                             else lifetime.calibration(new_outcomes)},
            "examples": examples(new_outcomes, records) if new_outcomes is not None and len(new_outcomes) else {}}


def validate(output, mem):
    """(journal, ops, errors). journal and ops are None unless the reply matches the schema."""
    if not isinstance(output, dict) or set(output) != OUTPUT_KEYS:
        return None, None, [f"output must be an object with exactly the keys {sorted(OUTPUT_KEYS)}"]
    errors = []
    j = output["journal"]
    if not isinstance(j, dict) or set(j) != set(JOURNAL_FIELDS):
        errors.append(f"journal must have exactly the keys {sorted(JOURNAL_FIELDS)}")
        journal = None
    else:
        journal = {}
        for key, limit in JOURNAL_FIELDS.items():
            if not isinstance(j[key], str) or not j[key].strip():
                errors.append(f"journal.{key} must be a non-empty string")
            else:
                journal[key] = j[key].strip()[:limit]
    ops, op_errors = memory.validate_ops(output["memory_ops"], mem)
    errors += op_errors
    return (journal, ops, []) if not errors else (None, None, errors)


def render_journal(day, journal, notes):
    lines = [f"# {day}", "", f"Market: {journal['market']}", f"Result: {journal['result']}", ""]
    for key, heading in JOURNAL_HEADINGS.items():
        lines += [f"## {heading}", "", journal[key], ""]
    lines += ["## Memory updates", ""] + ([f"- {n}" for n in notes] or ["None."])
    return "\n".join(lines) + "\n"


def day_report(book, day):
    """Today's facts: the daily file written at 18:30, or built now without the market move."""
    f = paths.daily_file(book, day)
    if f.exists():
        return f.read_text(encoding="utf-8")
    return analyze.build_day_report(paths.book_dir(book), day, None, score.load_scores(day))


def tables_for(book, outcomes):
    trades = analyze.filter_trades(read_trades_frame(paths.book_dir(book) / "trades.csv"))
    attention = db.read_attention()
    if len(trades):
        entry = trades["entry_date"].where(trades["entry_date"].astype(str) != "", trades["date"])
        trades = evidence.with_attention(trades.assign(_entry=entry), attention, "_entry").drop(columns="_entry")
    return {"scores": evidence.score_rows(outcomes, attention), "trades": trades}


def review(now, book, cfg, caller=None, log=None, outcomes=None, fetch=True):
    """Run one daily review. Returns the new memory, or None when there is nothing to journal.

    With fetch=False nothing is downloaded and `outcomes` must be given.
    """
    caller = caller or (lambda p, i: score.call_claude(p, i, model=MODEL, timeout=TIMEOUT_SECONDS))
    today = now.date()
    journal_path = paths.journal_file(book, today)
    if journal_path.exists():
        if log:
            log.info("review skipped: %s already exists", journal_path.name)
        return None
    if score.load_scores(today) is None and not paths.daily_file(book, today).exists():
        if log:
            log.info("review skipped: no session today")
        return None
    horizon = cfg["exit"]["max_hold_days"]
    records = lifetime.read_score_files()
    if outcomes is None and fetch and records:
        outcomes = lifetime.load_outcomes(records, horizon)
    stats = lifetime.build(book, horizon, log, outcomes=outcomes, fetch=fetch)
    tables = tables_for(book, outcomes)
    current, _ = memory.refresh_status(memory.load() or memory.empty(), tables, today)
    through = current.get("outcomes_through")
    since = date.fromisoformat(through) if through else date.min
    new = outcomes[outcomes["date"] > since] if outcomes is not None and len(outcomes) else None
    input_json = json.dumps(build_input(day_report(book, today), current, stats, new, records),
                            ensure_ascii=False, default=str)
    prompt = prompt_text()
    problems = []
    for attempt in (1, 2):
        try:
            journal, ops, errors = validate(score.extract_json(caller(prompt, input_json)), current)
        except score.ScoringError as e:
            journal, ops, errors = None, None, [str(e)]
        if journal is not None:
            before = {e["id"]: e["status"] for e in memory.active(current)}
            mem, notes = memory.apply_ops(current, ops, today)
            mem, _ = memory.refresh_status(mem, tables, today)
            notes += [f"{e['id']} {before[e['id']]} -> {e['status']} (evidence test)"
                      for e in memory.active(mem) if e["id"] in before and before[e["id"]] != e["status"]]
            through = max(outcomes["date"]).isoformat() if outcomes is not None and len(outcomes) else through
            mem = memory.save(mem | {"updated_at": now.isoformat(), "model": MODEL, "prompt_sha256": score.sha256(prompt),
                                     "outcomes_through": through})
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            with open(journal_path, "x", encoding="utf-8") as f:
                f.write(render_journal(today, journal, notes))
            return mem
        problems.append(f"attempt {attempt}: " + "; ".join(errors[:10]))
        if log:
            log.warning("review attempt %d failed: %s", attempt, "; ".join(errors[:10]))
    raise score.ScoringError("review failed twice, memory unchanged: " + " | ".join(problems))


def main(argv=None):
    import config
    from logger import notify, setup_logging

    ap = argparse.ArgumentParser(description="daily journal and memory review")
    ap.add_argument("command", choices=["run", "show"])
    ap.add_argument("--book", choices=["paper", "dry_run"], default="paper")
    args = ap.parse_args(argv)
    config.init_data()
    log = setup_logging("review")
    if args.command == "show":
        f = paths.memory_file()
        print(f.read_text(encoding="utf-8") if f.exists() else "no memory yet")
        return 0
    cfg = config.load_config()
    try:
        mem = review(datetime.now(ET), args.book, cfg, log=log)
    except Exception as e:
        log.exception("review failed")
        notify(f"daily review failed, memory unchanged: {e}")
        return 1
    if mem:
        counts = {s: sum(e["status"] == s for e in mem["entries"]) for s in memory.STATUSES}
        log.info("journal written; memory %s, prompt version %s", counts, mem["memory_sha256"][:12] or "none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
