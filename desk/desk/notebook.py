"""The bots' notebook: their daily journals, and their memory entries with how each one moved up or down the
status ladder (hypothesis -> supported -> confirmed, or retired).

The memory history comes from the versions table: every time a bot's memory changed, it recorded the whole
memory. claude-quant keeps it as markdown (data/memory.md), claude-sentiment-trading as a JSON list of
entries. Only the fields below are served.
"""
import json
import re

from . import sources

STATUSES = ("hypothesis", "supported", "confirmed", "retired")
ENTRY_FIELDS = ("id", "title", "status", "scope", "since", "last_reviewed", "query", "evidence", "implication",
                "retired_reason")
MAX_FIELD = 1000
MAX_VERSIONS = 2000  # memory versions read for the ladder

QUANT_ENTRY = re.compile(r"^### (M-\d+):[ \t]*(.*)$", re.MULTILINE)
QUANT_LINE = re.compile(r"^- (Status|Since|Scope|Query|Evidence|Implication|Retired):[ \t]*(.*)$", re.MULTILINE)


def _clip(v):
    if v is None:
        return None
    v = str(v).strip()
    return v[:MAX_FIELD] if v else None


def parse_quant(text):
    """Entries of claude-quant's memory.md. Entries under '## Retired' are retired whatever their status says."""
    retired_at = text.find("\n## Retired")
    parts = QUANT_ENTRY.split(text)
    out, pos = [], 0
    for entry_id, title, body in zip(parts[1::3], parts[2::3], parts[3::3]):
        pos = text.find(f"### {entry_id}:", pos)
        fields = {k.lower(): v.strip() for k, v in QUANT_LINE.findall(body.split("\n## ")[0])}
        since, _, reviewed = fields.get("since", "").partition("|")
        status = (fields.get("status") or "hypothesis").split()[0].lower()
        if retired_at != -1 and pos > retired_at:
            status = "retired"
        out.append({"id": entry_id, "title": _clip(title), "status": status if status in STATUSES else "hypothesis",
                    "scope": _clip(fields.get("scope")), "since": _clip(since),
                    "last_reviewed": _clip(reviewed.replace("Last reviewed:", "")),
                    "query": _clip(fields.get("query", "").strip("`")), "evidence": _clip(fields.get("evidence")),
                    "implication": _clip(fields.get("implication")), "retired_reason": _clip(fields.get("retired"))})
    return out


def parse_sentiment(text):
    """Entries of claude-sentiment-trading's memory, recorded as a JSON list."""
    try:
        entries = json.loads(text)
    except (TypeError, ValueError):
        return []
    entries = entries.get("entries", []) if isinstance(entries, dict) else entries
    out = []
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict) or "id" not in e:
            continue
        row = {k: _clip(e.get(k)) for k in ENTRY_FIELDS}
        row["retired_reason"] = row["retired_reason"] or _clip(e.get("reason"))
        row["status"] = row["status"] if row["status"] in STATUSES else "hypothesis"
        out.append(row)
    return out


PARSERS = {"quant": parse_quant, "sentiment": parse_sentiment}


def memory(settings, bot):
    """The current entries, each with its status history, and the number of entries per status over time."""
    rows = sources.query_bot_db(settings, bot, "SELECT effective_from, content FROM versions WHERE kind = 'memory' "
                                               "AND content IS NOT NULL ORDER BY effective_from, id LIMIT ?", (MAX_VERSIONS,))
    by_day = {}
    for r in rows:  # the last version of each day
        by_day[r["effective_from"][:10]] = PARSERS[bot](r["content"])
    history, counts = {}, []
    for day, entries in sorted(by_day.items()):
        for e in entries:
            h = history.setdefault(e["id"], [])
            if not h or h[-1]["status"] != e["status"]:
                h.append({"date": day, "status": e["status"]})
        counts.append({"date": day} | {s: sum(e["status"] == s for e in entries) for s in STATUSES})
    current = by_day[max(by_day)] if by_day else []
    return {"bot": bot, "as_of": rows[-1]["effective_from"] if rows else None,
            "entries": [e | {"history": history.get(e["id"], [])} for e in current], "counts": counts}


def journal(settings, bot, day=None):
    """The dates of a bot's journal and one entry: `day`, or the newest."""
    dates = sources.journal_dates(settings, bot)
    day = day if day in dates else (dates[0] if dates else None)
    return {"bot": bot, "dates": dates[:1000],
            "entry": {"date": day, "text": sources.journal_text(settings, bot, day)} if day else None}
