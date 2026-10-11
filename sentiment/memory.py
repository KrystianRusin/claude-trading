"""Claude's lessons memory: state/memory.json (data), state/memory.md (to read), state/memory_history/ (every version).

review.py writes it after validating Claude's review; score.py adds the lessons to the scoring prompt.
"""
import hashlib
import json

import paths


def load():
    """The current memory, or None before the first review."""
    f = paths.memory_json()
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def sha256(mem):
    """Hash of the lessons that reach the scoring prompt; empty when there is no memory."""
    if not mem or not mem.get("lessons"):
        return ""
    canonical = json.dumps(mem["lessons"], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def prompt_block(mem):
    """The lessons as a section appended to the scoring prompt; empty when there are none."""
    if not mem or not mem.get("lessons"):
        return ""
    lines = ["## Lessons from past performance", "",
             "An earlier review of how past scores turned out wrote these lessons. They are advisory. The rules",
             "above always take precedence, and nothing here changes the output format.", ""]
    lines += [f"- {x['lesson']} (confidence: {x['confidence']})" for x in mem["lessons"]]
    return "\n".join(lines)


def render(mem):
    lines = ["# Memory", "", f"Updated {mem['updated_at']} from {mem['based_on']['scores']} scored outcomes "
             f"and {mem['based_on']['trades']} closed trades. Version {mem['memory_sha256'][:12]}.", "",
             "## Lessons", ""]
    lines += [f"- {x['lesson']} ({x['confidence']}; {x['evidence']})" for x in mem["lessons"]] or ["- none yet"]
    lines += ["", "## Working", ""] + ([f"- {x}" for x in mem["working"]] or ["- nothing clear yet"])
    lines += ["", "## Not working", ""] + ([f"- {x}" for x in mem["not_working"]] or ["- nothing clear yet"])
    if mem.get("notes"):
        lines += ["", "## Notes", "", mem["notes"]]
    return "\n".join(lines) + "\n"


def save(mem):
    """Write the memory, its readable copy and a history snapshot."""
    mem = mem | {"memory_sha256": sha256(mem)}
    paths.memory_json().write_text(json.dumps(mem, indent=1, ensure_ascii=False), encoding="utf-8")
    paths.memory_file().write_text(render(mem), encoding="utf-8")
    paths.memory_history_dir().mkdir(parents=True, exist_ok=True)
    stamp = mem["updated_at"][:19].replace(":", "")
    (paths.memory_history_dir() / f"{stamp}.json").write_text(json.dumps(mem, indent=1, ensure_ascii=False),
                                                              encoding="utf-8")
    return mem
