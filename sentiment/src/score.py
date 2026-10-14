"""Daily sentiment scores: archived news in, one validated score per symbol with news out.

    python src/score.py run          score news since the previous scoring run into data/scores/<today>.json
    python src/score.py show [DATE]  print a score file

Claude runs headless (`claude -p`) with no tools as a one-shot classifier. Its output is parsed and
schema-checked here; nothing it writes is executed. Score files are never edited once written.

Only universe symbols with news in the window are scored (open positions first, then the most
covered), in batches so one bad reply loses one batch rather than the day. The trading desk
(desk.py) then proposes trade ideas from the scores, headlines, price moves and pairs; its ideas
are stored in the same score file under "desk".
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import memory
import news
import paths
from broker import ET, chunks, to_et

MODEL = "claude-haiku-5-5"
MAX_PER_SYMBOL = 15
SUMMARY_CHARS = 800
REASON_CHARS = 300
TIMEOUT_SECONDS = 300
OUTPUT_KEYS = {"score", "confidence", "reason", "article_ids"}


class ScoringError(RuntimeError):
    pass


def prompt_text():
    return paths.SCORING_PROMPT.read_text(encoding="utf-8")


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def previous_weekday(now):
    start = now - timedelta(days=1)
    while start.weekday() >= 5:
        start -= timedelta(days=1)
    return start


def latest_score(before):
    """The newest score file dated before `before`, or None."""
    files = sorted(f for f in paths.scores_dir().glob("*.json") if f.stem < before.isoformat())
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def window_start(now):
    """End of the previous scoring window, or the previous weekday at the same time on the first run."""
    prev = latest_score(now.date())
    return to_et(datetime.fromisoformat(prev["window_end"])) if prev else previous_weekday(now)


def build_input(articles, symbols, start, end):
    """Claude's input: per symbol the newest articles' ids, headlines and summaries. No prices or positions."""
    by_symbol = {s: [] for s in symbols}
    for a in sorted(articles, key=lambda a: a["published_at"], reverse=True):
        for s in a["symbols"]:
            if s in by_symbol and len(by_symbol[s]) < MAX_PER_SYMBOL:
                by_symbol[s].append({"id": str(a["id"]), "published_at": a["published_at"],
                                     "headline": a["headline"], "summary": (a["summary"] or "")[:SUMMARY_CHARS]})
    return {"window_start": start.isoformat(), "window_end": end.isoformat(), "symbols": by_symbol}


def claude_bin():
    return os.environ.get("CLAUDE_BIN") or shutil.which("claude") or str(Path.home() / ".local/bin/claude")


def call_claude(prompt, input_json, model=MODEL, timeout=TIMEOUT_SECONDS):
    """Run headless Claude Code with no tools in an empty folder; returns the model's text."""
    cmd = [claude_bin(), "-p", prompt, "--model", model, "--tools", "", "--output-format", "json"]
    with tempfile.TemporaryDirectory() as cwd:
        try:
            r = subprocess.run(cmd, input=input_json, capture_output=True, text=True, encoding="utf-8",
                               timeout=timeout, cwd=cwd)
        except subprocess.TimeoutExpired as e:
            raise ScoringError(f"claude timed out after {timeout}s") from e
    if r.returncode != 0:
        raise ScoringError(f"claude exited {r.returncode}: {(r.stderr or r.stdout)[-500:]}")
    try:
        envelope = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise ScoringError(f"claude output is not JSON: {r.stdout[:300]}") from e
    if envelope.get("is_error") or "result" not in envelope:
        raise ScoringError(f"claude reported an error: {str(envelope)[:300]}")
    return envelope["result"]


def extract_json(text):
    """The JSON object in the model's reply, tolerating code fences around it."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        first, last = text.find("{"), text.rfind("}")
        if first == -1 or last <= first:
            raise ScoringError("no JSON object in the model's reply")
        try:
            return json.loads(text[first:last + 1])
        except json.JSONDecodeError as e:
            raise ScoringError(f"invalid JSON in the model's reply: {e}") from e


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate(output, model_input):
    """(clean scores, errors). Scores are None unless the output matches the schema exactly."""
    if not isinstance(output, dict):
        return None, ["output is not a JSON object"]
    expected = model_input["symbols"]
    errors = [f"missing symbol {s}" for s in expected if s not in output]
    errors += [f"unexpected key {k!r}" for k in output if k not in expected]
    clean = {}
    for sym in (s for s in expected if s in output):
        v = output[sym]
        if not isinstance(v, dict) or set(v) != OUTPUT_KEYS:
            errors.append(f"{sym}: must have exactly the keys {sorted(OUTPUT_KEYS)}")
            continue
        score, conf, reason, ids = v["score"], v["confidence"], v["reason"], v["article_ids"]
        if not (_is_number(score) and float(score).is_integer() and -2 <= score <= 2):
            errors.append(f"{sym}: score {score!r} is not an integer in -2..2")
        if not (_is_number(conf) and 0 <= conf <= 1):
            errors.append(f"{sym}: confidence {conf!r} is not in 0..1")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{sym}: reason must be a non-empty string")
        if not isinstance(ids, list):
            errors.append(f"{sym}: article_ids must be a list")
            continue
        known = {a["id"] for a in expected[sym]}
        ids = [str(i) for i in ids if isinstance(i, (str, int)) and not isinstance(i, bool)]
        invented = [i for i in ids if i not in known]
        if invented or len(ids) != len(v["article_ids"]):
            errors.append(f"{sym}: article ids not in this symbol's input: {invented or v['article_ids']}")
        if _is_number(score) and score != 0 and not ids:
            errors.append(f"{sym}: non-zero score cites no articles")
        if not any(e.startswith(f"{sym}:") for e in errors):
            clean[sym] = {"score": int(score), "confidence": round(float(conf), 4),
                          "reason": " ".join(reason.split())[:REASON_CHARS], "article_ids": list(dict.fromkeys(ids))}
    return (clean, []) if not errors else (None, errors)


def write_scores(day, record):
    """Write a score file once; refuses to replace an existing one."""
    paths.scores_dir().mkdir(parents=True, exist_ok=True)
    path = paths.score_file(day)
    with open(path, "x", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    return path


def select_symbols(articles, universe, priority=(), limit=60):
    """(symbols to score, how many had news): universe symbols with news, open positions first, then most covered."""
    wanted = set(universe)
    counts = Counter(sym for a in articles for sym in set(a["symbols"]) if sym in wanted)
    held = set(priority)
    ranked = sorted(counts, key=lambda sym: (sym not in held, -counts[sym], sym))
    return ranked[:limit], len(counts)


def score_batch(model_input, prompt, caller, log=None):
    """Validated scores for one batch, retrying once; None when both attempts fail."""
    input_json = json.dumps(model_input, ensure_ascii=False)
    for attempt in (1, 2):
        try:
            scores, errors = validate(extract_json(caller(prompt, input_json)), model_input)
        except ScoringError as e:
            scores, errors = None, [str(e)]
        if scores is not None:
            return scores
        if log:
            log.warning("scoring attempt %d for %s failed: %s", attempt, ",".join(model_input["symbols"]),
                        "; ".join(errors[:10]))
    return None


def score(now, universe, ucfg, priority=(), caller=call_claude, log=None, mem=None, desk=None):
    """Score the news since the previous run in batches. Returns the score file path.

    `mem` is the lessons memory to append to the prompt; prompt_sha256 covers prompts/scoring.md and
    memory_sha256 the lessons, so results can be split by either.

    Symbols in a batch that fails twice are left out and listed in `failed_symbols`. Raises, writing
    nothing, when every batch fails. `desk(scores, articles)` returns the desk record; its failure
    is recorded and never stops the score file.
    """
    if paths.score_file(now.date()).exists():
        raise ScoringError(f"{paths.score_file(now.date())} already exists")
    start = window_start(now)
    articles = news.load(start, now, universe)
    chosen, with_news = select_symbols(articles, universe, priority, ucfg["max_scored_per_day"])
    base = prompt_text()
    block = memory.prompt_block(mem)
    prompt = f"{base}\n\n{block}" if block else base
    batches = chunks(chosen, ucfg["batch_size"])
    record = {"date": now.date().isoformat(), "generated_at": now.isoformat(), "model": MODEL if chosen else "none",
              "prompt_sha256": sha256(base), "memory_sha256": memory.sha256(mem) if block else "",
              "window_start": start.isoformat(), "window_end": now.isoformat(),
              "universe_size": len(universe), "symbols_with_news": with_news, "batches": len(batches)}
    scores, failed, n_articles = {}, [], 0
    for batch in batches:
        model_input = build_input(articles, batch, start, now)
        n_articles += sum(len(v) for v in model_input["symbols"].values())
        result = score_batch(model_input, prompt, caller, log)
        if result is None:
            failed += batch
        else:
            scores.update(result)
    if chosen and not scores:
        raise ScoringError(f"all {len(batches)} scoring batches failed twice, no score file written")
    record |= {"input_articles": n_articles, "failed_symbols": failed, "scores": scores}
    if desk is not None:
        try:
            record["desk"] = desk(scores, articles)
        except Exception as e:
            if log:
                log.warning("trading desk failed; scores only today", exc_info=True)
            record["desk"] = {"ideas": [], "rejected": [], "error": f"{type(e).__name__}: {e}"}
    return write_scores(now.date(), record)


def desk_step(cfg, broker, symbols, holdings, now, log=None):
    """The `desk` callable for score(): market moves and pair divergences up to the last close, then Claude."""
    import desk as desk_
    import pairs
    from broker import load_bars

    def run(scores, articles):
        end = previous_weekday(now).date()
        bars = load_bars(list(dict.fromkeys(symbols + ["SPY"])), end - timedelta(days=45), end, broker)
        closes = {s: float(df["close"].iloc[-1]) for s, df in bars.items() if len(df)}
        divs = pairs.divergences(pairs.load(), closes, cfg["pairs"])
        model_input = desk_.build_input(now.date(), holdings, scores, articles, desk_.market_moves(bars), divs,
                                        cfg["desk"]["max_ideas"], cfg["desk"]["max_headlines"])
        return desk_.propose(model_input, divs, log=log)
    return run


def load_scores(day):
    """{symbol: score entry} plus metadata for `day`, or None when there is no usable score file."""
    path = paths.score_file(day)
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    scores = record.get("scores")
    if not isinstance(scores, dict) or not all(isinstance(v, dict) and OUTPUT_KEYS <= set(v) for v in scores.values()):
        return None
    return record


def main(argv=None):
    import config
    import universe
    from broker import make_broker
    from logger import Book, notify, setup_logging

    ap = argparse.ArgumentParser(description="daily sentiment scoring")
    ap.add_argument("command", choices=["run", "show"])
    ap.add_argument("day", nargs="?", help="YYYY-MM-DD for show (default today)")
    args = ap.parse_args(argv)
    config.init_data()
    log = setup_logging("score")
    now = datetime.now(ET)
    if args.command == "show":
        day = date.fromisoformat(args.day) if args.day else now.date()
        print(paths.score_file(day).read_text(encoding="utf-8") if paths.score_file(day).exists() else "no score file")
        return 0
    cfg = config.load_config()
    symbols = universe.load(config.load_watchlist())
    holdings = Book(paths.book_dir("paper"), "paper").positions()
    held = [r["symbol"] for r in holdings]
    broker = None
    try:
        broker = make_broker(cfg, verify_account=False)
        log.info("news fetch before scoring: %d new articles", news.fetch(broker, symbols, now))
        if now.date() not in broker.trading_days(now.date(), now.date()):
            log.info("market closed today; not scoring, the next run covers this news")
            return 0
    except Exception:
        log.warning("news fetch or calendar check failed; scoring the archive as it is", exc_info=True)
    try:
        mem = memory.load() if cfg["memory"]["use_in_scoring"] else None
        desk = desk_step(cfg, broker, symbols, holdings, now, log) if cfg["desk"]["enabled"] else None
        path = score(now, symbols, cfg["universe"], priority=held, log=log, mem=mem, desk=desk)
    except Exception as e:
        log.exception("scoring failed")
        notify(f"scoring failed, no entries today: {e}")
        return 1
    record = json.loads(path.read_text(encoding="utf-8"))
    nonzero = {s: v["score"] for s, v in record["scores"].items() if v["score"]}
    log.info("wrote %s: %d of %d symbols with news scored from %d articles, non-zero %s", path.name,
             len(record["scores"]), record["symbols_with_news"], record["input_articles"], nonzero)
    d = record.get("desk") or {}
    if d.get("error"):
        notify(f"trading desk failed, scores only today: {d['error'][:200]}")
    elif "ideas" in d:
        log.info("desk: %d ideas %s", len(d["ideas"]),
                 [f"{i['kind']} {'/'.join(leg['side'][0] + leg['symbol'] for leg in i['legs'])}" for i in d["ideas"]])
    if record["failed_symbols"]:
        log.warning("batches failed, not scored today: %s", ",".join(record["failed_symbols"]))
        notify(f"some scoring batches failed: {len(record['failed_symbols'])} symbols not scored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
