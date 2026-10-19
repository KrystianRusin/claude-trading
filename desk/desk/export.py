"""The nightly download files: whatever is public of the trades, the daily results and the versions, as CSV
and (when pyarrow is installed) Parquet, in data/exports/ with a manifest.json. The app serves them at
/api/downloads/<file> while their section stays public; the rows come from the same views as the API, so
they carry the same fields.

    .venv/bin/python -m desk.export      write the files now (cron runs it nightly; see scripts/cron.sh)
"""
import csv
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone

from . import config, sources, views

EXPORT_DIR = config.ROOT / "data" / "exports"
DAILY_FIELDS = ("bot", "book", "date", "equity_start", "equity_end", "return_pct", "gross_pnl", "trades", "halted")


def trades_rows(settings):
    rows = sorted(views.all_trades(settings), key=lambda t: (t["date"], t["exit_time"] or "", t["bot"]))
    columns = ("bot", "book", *views.TRADE_FIELDS, *views.TRADE_EXTRA)
    return columns, [{c: t.get(c) for c in columns} for t in rows]


def daily_rows(settings):
    out = []
    for bot in settings.bots:
        for book in settings.books:
            for r in sources.query(settings, bot, book, "SELECT date, equity_start, equity_end, gross_pnl, trades, halted "
                                                        "FROM daily_summary WHERE book = ? ORDER BY date", (book,)):
                ret = (r["equity_end"] / r["equity_start"] - 1) * 100 if r["equity_start"] and r["equity_end"] is not None else None
                out.append({"bot": bot, "book": book, "date": r["date"], "equity_start": r["equity_start"],
                            "equity_end": r["equity_end"], "return_pct": round(ret, 6) if ret is not None else None,
                            "gross_pnl": r["gross_pnl"], "trades": r["trades"], "halted": bool(r["halted"])})
    return DAILY_FIELDS, sorted(out, key=lambda r: (r["date"], r["bot"], r["book"]))


def versions_rows(settings):
    with_content = settings.public("version_content")
    columns = ("bot", "kind", "name", "version", "effective_from", "reason", "removed") + (("content",) if with_content else ())
    out = []
    for bot, v in views.versions(settings, with_content=with_content).items():
        out += [{"bot": bot} | {c: ch.get(c) for c in columns if c != "bot"} for ch in v["changes"]]
    return columns, sorted(out, key=lambda r: (r["effective_from"], r["bot"]))


# file stem -> (the section that must be public, the rows, what it holds)
FILES = {
    "trades": ("trades", trades_rows, "Every closed trade of the shown books, one row each"),
    "daily": ("equity", daily_rows, "Each bot's equity at the start and end of every trading day, and its return"),
    "versions": ("versions", versions_rows, "Every change to a bot's config, strategies, watchlist, prompts and memory"),
}


def _parquet():
    """Whether pyarrow is installed (it is left out where pip has no prebuilt wheel for it)."""
    return importlib.util.find_spec("pyarrow") is not None


def _replace(path, write):
    """Write to a temporary file and rename it over `path`, so a download never sees half a file."""
    tmp = path.with_name(path.name + ".tmp")
    write(tmp)
    os.replace(tmp, path)


def write_csv(path, columns, rows):
    def write(tmp):
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=columns)
            w.writeheader()
            w.writerows(rows)
    _replace(path, write)


def write_parquet(path, columns, rows):
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table({c: [r.get(c) for r in rows] for c in columns})
    _replace(path, lambda tmp: pq.write_table(table, tmp))


def build(settings, out_dir=None):
    """Write every public file and the manifest; remove the files of sections that are no longer public."""
    out_dir = out_dir or EXPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    parquet = _parquet()
    files = []
    for stem, (section, make, about) in FILES.items():
        targets = [out_dir / f"{stem}.csv", out_dir / f"{stem}.parquet"]
        if not settings.public(section):
            for t in targets:
                t.unlink(missing_ok=True)
            continue
        columns, rows = make(settings)
        write_csv(targets[0], columns, rows)
        written = [targets[0]]
        if parquet:
            write_parquet(targets[1], columns, rows)
            written.append(targets[1])
        else:
            targets[1].unlink(missing_ok=True)
        for t in written:
            files.append({"name": t.name, "section": section, "format": t.suffix[1:], "rows": len(rows),
                          "bytes": t.stat().st_size, "columns": list(columns), "about": about})
    manifest = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "files": files}
    _replace(out_dir / "manifest.json", lambda tmp: tmp.write_text(json.dumps(manifest, indent=1), encoding="utf-8"))
    return manifest


def read_manifest(out_dir=None):
    try:
        out_dir = out_dir or EXPORT_DIR
        return json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main():
    settings = config.load()
    m = build(settings)
    for f in m["files"]:
        print(f"{f['name']}: {f['rows']} rows, {f['bytes']} bytes")
    if not _parquet():
        print("pyarrow is not installed: CSV only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
