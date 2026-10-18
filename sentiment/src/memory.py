"""Claude's memory in data/memory/: memory.json (data), memory.md (to read), history/ (every version).

The memory is a list of entries M-<n>, each a lesson or hypothesis with a scope, its evidence and,
ideally, a query (see evidence.py) that can test it. The daily review (review.py) sends operations
(add, update, retire); Python validates and applies them. Entries are never deleted, ids are never
reused, and the active list is capped.

Claude never sets a status. Python sets it from the evidence test on outcomes after the entry was
registered: hypothesis -> supported -> confirmed, and back down if later evidence fails. Entries
without a query stay hypotheses. Only supported and confirmed entries reach the scoring prompt, so
a hypothesis under test cannot change the scores that test it.
"""
import hashlib
import json
from datetime import date

import db
import evidence
import paths

STATUSES = ("hypothesis", "supported", "confirmed", "retired")
SCOPES = ("scoring", "desk", "trading", "ops")
PROMPT_STATUSES = ("supported", "confirmed")
MAX_ACTIVE = 40
MAX_OPS = 10
LIMITS = {"title": 160, "evidence": 400, "implication": 240, "reason": 240, "query": 200}
OP_KEYS = {"add": {"op", "title", "scope", "query", "evidence", "implication"},
           "update": {"op", "id"}, "retire": {"op", "id", "reason"}}
UPDATABLE = {"evidence", "implication", "query"}


def empty():
    return {"entries": [], "next_id": 1}


def from_lessons(old):
    """Convert a memory written by the old weekly review: each lesson becomes a hypothesis entry."""
    since = old.get("updated_at", "")[:10] or date.today().isoformat()
    entries = [{"id": f"M-{i}", "title": x["lesson"], "status": "hypothesis", "scope": "scoring",
                "since": since, "last_reviewed": since, "query": None,
                "evidence": f"{x['evidence']} (weekly-review lesson, confidence {x['confidence']}; add a query to test it)",
                "implication": ""} for i, x in enumerate(old.get("lessons", []), start=1)]
    return {"entries": entries, "next_id": len(entries) + 1, "updated_at": old.get("updated_at"),
            "migrated_from": old.get("memory_sha256", "")}


def load():
    """The current memory, or None before the first review. Converts the old lessons format."""
    f = paths.memory_json()
    if not f.exists():
        return None
    mem = json.loads(f.read_text(encoding="utf-8"))
    return from_lessons(mem) if "entries" not in mem else mem


def active(mem):
    return [e for e in (mem or {}).get("entries", []) if e["status"] != "retired"]


def prompt_entries(mem, scope="scoring"):
    return [e for e in active(mem) if e["status"] in PROMPT_STATUSES and e["scope"] == scope]


def sha256(mem):
    """Hash of the entries that reach the scoring prompt; empty when none do."""
    entries = prompt_entries(mem)
    if not entries:
        return ""
    canonical = json.dumps([{k: e[k] for k in ("id", "title", "implication", "status")} for e in entries],
                           sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def prompt_block(mem):
    """Proven lessons as a section appended to the scoring prompt; empty when there are none."""
    entries = prompt_entries(mem)
    if not entries:
        return ""
    lines = ["## Lessons from past performance", "",
             "Each of these was written down as a hypothesis by an earlier review and has since held up on",
             "later outcomes. They are advisory. The rules above always take precedence, and nothing here",
             "changes the output format.", ""]
    lines += [f"- {e['title']}{' ' + e['implication'] if e['implication'] else ''} ({e['status']})" for e in entries]
    return "\n".join(lines)


def _text(value, key, errors, where, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{where}: {key} must be a non-empty string")
        return None
    return " ".join(value.split())[:LIMITS[key]]


def _query(value, errors, where):
    """A valid query string, or None. Records an error for an invalid one."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str) or len(value) > LIMITS["query"]:
        errors.append(f"{where}: query must be a string under {LIMITS['query']} characters")
        return None
    try:
        evidence.parse(value)
    except evidence.QueryError as e:
        errors.append(f"{where}: {e}")
        return None
    return " ".join(value.split())


def validate_ops(ops, mem):
    """(clean ops, errors). Checks shape, ids, lengths and queries against the current memory."""
    if not isinstance(ops, list) or len(ops) > MAX_OPS:
        return [], [f"memory_ops must be a list of at most {MAX_OPS}"]
    by_id = {e["id"]: e for e in (mem or empty())["entries"]}
    clean, errors, touched = [], [], set()
    for i, op in enumerate(ops):
        where = f"memory_ops[{i}]"
        kind = op.get("op") if isinstance(op, dict) else None
        if kind not in OP_KEYS:
            errors.append(f"{where}: op must be one of {sorted(OP_KEYS)}")
            continue
        keys = set(op)
        if kind == "update":
            if not keys > {"op", "id"} or not keys <= {"op", "id"} | UPDATABLE:
                errors.append(f"{where}: update takes id plus one or more of {sorted(UPDATABLE)}")
                continue
        elif keys != OP_KEYS[kind]:
            errors.append(f"{where}: {kind} must have exactly the keys {sorted(OP_KEYS[kind])}")
            continue
        if kind == "add":
            if op["scope"] not in SCOPES:
                errors.append(f"{where}: scope must be one of {SCOPES}")
                continue
            c = {"op": "add", "scope": op["scope"], "title": _text(op["title"], "title", errors, where),
                 "evidence": _text(op["evidence"], "evidence", errors, where),
                 "implication": _text(op["implication"], "implication", errors, where, required=False) or "",
                 "query": _query(op["query"], errors, where)}
        else:
            entry = by_id.get(op["id"])
            if entry is None or entry["status"] == "retired":
                errors.append(f"{where}: {op['id']!r} is not an active entry")
                continue
            if op["id"] in touched:
                errors.append(f"{where}: {op['id']} is changed twice")
                continue
            touched.add(op["id"])
            c = {"op": kind, "id": op["id"]}
            if kind == "retire":
                c["reason"] = _text(op["reason"], "reason", errors, where)
            else:
                for k in UPDATABLE & keys:
                    c[k] = (_query(op[k], errors, where) if k == "query"
                            else _text(op[k], k, errors, where, required=(k != "implication")) or "")
        clean.append(c)
    adds = sum(c["op"] == "add" for c in clean)
    retires = sum(c["op"] == "retire" for c in clean)
    if len(active(mem)) + adds - retires > MAX_ACTIVE:
        errors.append(f"at most {MAX_ACTIVE} active entries; retire or merge some before adding")
    return (clean, []) if not errors else ([], errors)


def apply_ops(mem, ops, today):
    """Apply validated ops. Returns (memory, one line per change for the journal)."""
    mem = json.loads(json.dumps(mem or empty()))
    by_id = {e["id"]: e for e in mem["entries"]}
    notes, day = [], today.isoformat()
    for op in ops:
        if op["op"] == "add":
            entry = {"id": f"M-{mem['next_id']}", "title": op["title"], "status": "hypothesis", "scope": op["scope"],
                     "since": day, "last_reviewed": day, "query": op["query"], "evidence": op["evidence"],
                     "implication": op["implication"]}
            mem["next_id"] += 1
            mem["entries"].append(entry)
            notes.append(f"added {entry['id']} ({entry['scope']}): {entry['title']}"
                         + (f" [query: {entry['query']}]" if entry["query"] else ""))
        elif op["op"] == "retire":
            e = by_id[op["id"]]
            e |= {"status": "retired", "retired_on": day, "retired_reason": op["reason"], "last_reviewed": day}
            notes.append(f"retired {e['id']}: {op['reason']}")
        else:
            e = by_id[op["id"]]
            changed = sorted(k for k in UPDATABLE if k in op)
            if "query" in op and op["query"] != e.get("query"):
                # A new query is a new hypothesis: only outcomes from today on can support it.
                e |= {"since": day, "status": "hypothesis"}
            e |= {k: op[k] for k in changed} | {"last_reviewed": day}
            notes.append(f"updated {e['id']} ({', '.join(changed)})")
    return mem, notes


def refresh_status(mem, tables, today):
    """Set every active entry's status from its evidence. Returns (memory, one line per status change)."""
    notes = []
    for e in active(mem):
        if not e.get("query"):
            new, check = "hypothesis", "no query yet, so it cannot be tested"
        else:
            try:
                result = evidence.evaluate(e["query"], tables, date.fromisoformat(e["since"]))
                new, check = result["status"], evidence.summary(result)
            except (evidence.QueryError, KeyError) as err:
                new, check = "hypothesis", f"query cannot run: {err}"
        if new != e["status"]:
            notes.append(f"{e['id']} {e['status']} -> {new}")
        e |= {"status": new, "check": check, "checked_on": today.isoformat()}
    return mem, notes


def render(mem):
    lines = ["# Memory", "", f"Updated {mem.get('updated_at', '?')}. Version {mem.get('memory_sha256', '')[:12] or 'none'} "
             "(the hash of the entries that reach the scoring prompt).", "",
             "Statuses are set by the evidence test, not by the reviewer. Only supported and confirmed",
             "scoring entries reach the scoring prompt.", ""]
    for status in ("confirmed", "supported", "hypothesis"):
        group = [e for e in active(mem) if e["status"] == status]
        lines += [f"## {status.capitalize()} ({len(group)})", ""]
        for e in group:
            lines += [f"### {e['id']}: {e['title']}",
                      f"- Scope: {e['scope']} | Since: {e['since']} | Last reviewed: {e['last_reviewed']}",
                      f"- Query: `{e['query']}`" if e.get("query") else "- Query: none",
                      f"- Evidence: {e['evidence']}"]
            if e.get("implication"):
                lines.append(f"- Implication: {e['implication']}")
            if e.get("check"):
                lines.append(f"- Check ({e.get('checked_on', '?')}): {e['check']}")
            lines.append("")
        if not group:
            lines += ["_None._", ""]
    retired = [e for e in mem["entries"] if e["status"] == "retired"]
    lines += [f"## Retired ({len(retired)})", ""]
    lines += [f"- {e['id']}: {e['title']} (retired {e.get('retired_on', '?')}: {e.get('retired_reason', '')})"
              for e in retired] or ["_None._"]
    return "\n".join(lines) + "\n"


def save(mem):
    """Write the memory, its readable copy and a history snapshot, and record the version in trader.db."""
    mem = mem | {"memory_sha256": sha256(mem)}
    paths.memory_history_dir().mkdir(parents=True, exist_ok=True)
    paths.memory_json().write_text(json.dumps(mem, indent=1, ensure_ascii=False), encoding="utf-8")
    paths.memory_file().write_text(render(mem), encoding="utf-8")
    stamp = (mem.get("updated_at") or "")[:19].replace(":", "")
    (paths.memory_history_dir() / f"{stamp}.json").write_text(json.dumps(mem, indent=1, ensure_ascii=False),
                                                              encoding="utf-8")
    db.note_versions("memory save")
    return mem
