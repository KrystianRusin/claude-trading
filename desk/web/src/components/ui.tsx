import { useMemo, useState, type ReactNode } from "react";

import type { BotName } from "../api";
import { BOT_COLOR, BOT_LABEL } from "../format";

export function Tile({ label, value, delta, deltaClass }: { label: string; value: ReactNode; delta?: ReactNode; deltaClass?: string }) {
  return (
    <div className="card tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {delta !== undefined && <div className={`delta ${deltaClass ?? ""}`}>{delta}</div>}
    </div>
  );
}

export function Bot({ bot }: { bot: BotName }) {
  return <span className="key"><span className="dot" style={{ background: BOT_COLOR[bot] }} />{BOT_LABEL[bot]}</span>;
}

export function Status({ status }: { status: string }) {
  const word = { ok: "OK", failed: "Failed", lost: "Lost", running: "Running" }[status] ?? status;
  return <span className="status"><span className={`dot ${status}`} />{word}</span>;
}

export function Loading({ error }: { error?: unknown }) {
  return <div className="empty">{error ? `Could not load this: ${(error as Error).message}` : "Loading…"}</div>;
}

export interface Column<T> {
  key: string;
  label: string;
  num?: boolean;
  render: (row: T) => ReactNode;
  sort?: (row: T) => number | string | null;
}

// A table with sortable columns (click a header; again to reverse).
export function DataTable<T>({ rows, columns, initialSort, empty = "Nothing yet." }: {
  rows: T[]; columns: Column<T>[]; initialSort?: { key: string; desc: boolean }; empty?: string;
}) {
  const [sort, setSort] = useState(initialSort);
  const sorted = useMemo(() => {
    const col = columns.find((c) => c.key === sort?.key);
    if (!col?.sort || !sort) return rows;
    const get = col.sort;
    return [...rows].sort((a, b) => {
      const x = get(a), y = get(b);
      if (x === y) return 0;
      if (x === null) return 1;
      if (y === null) return -1;
      return (x < y ? -1 : 1) * (sort.desc ? -1 : 1);
    });
  }, [rows, columns, sort]);
  if (!rows.length) return <div className="empty">{empty}</div>;
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} className={c.num ? "num" : ""} aria-sort={sort?.key === c.key ? (sort.desc ? "descending" : "ascending") : undefined}>
                {c.sort ? (
                  <button onClick={() => setSort({ key: c.key, desc: sort?.key === c.key ? !sort.desc : true })}>
                    {c.label}{sort?.key === c.key ? (sort.desc ? " ↓" : " ↑") : ""}
                  </button>
                ) : c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r, i) => (
            <tr key={i}>{columns.map((c) => <td key={c.key} className={c.num ? "num" : ""}>{c.render(r)}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Segmented<T extends string | number>({ value, options, onChange, label }: {
  value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={String(o.value)} className={o.value === value ? "on" : ""} aria-pressed={o.value === value}
                onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  );
}
