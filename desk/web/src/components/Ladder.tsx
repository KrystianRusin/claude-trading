// The memory ladder over time: one row per entry, coloured by its status on each day from when it was first
// written down. One hue, light to dark, for hypothesis -> supported -> confirmed; retired is grey and striped,
// so the ladder still reads without colour.
import { useState } from "react";

import type { MemoryEntry, MemoryStatus } from "../api";

export const STATUS_LABEL: Record<MemoryStatus, string> = {
  hypothesis: "Hypothesis", supported: "Supported", confirmed: "Confirmed", retired: "Retired",
};
const ORDER: MemoryStatus[] = ["hypothesis", "supported", "confirmed", "retired"];

export function StatusKey({ status }: { status: MemoryStatus }) {
  return <span className="key"><i className={`swatch ${status}`} />{STATUS_LABEL[status]}</span>;
}

export function Ladder({ entries, start, end, color, onPick, picked }: {
  entries: MemoryEntry[]; start: string; end: string; color: string; onPick: (id: string) => void; picked: string | null;
}) {
  const [hover, setHover] = useState<{ id: string; text: string } | null>(null);
  const t0 = Date.parse(start), t1 = Math.max(Date.parse(end), t0 + 86_400_000);
  const pos = (d: string) => ((Date.parse(d) - t0) / (t1 - t0)) * 100;
  const rows = [...entries].sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status)
    || (a.history[0]?.date ?? "").localeCompare(b.history[0]?.date ?? ""));
  return (
    <div className="ladder" style={{ ["--accent" as string]: color }}>
      <div className="legend">{ORDER.map((s) => <StatusKey key={s} status={s} />)}</div>
      {rows.map((e) => (
        <button key={e.id} className={`ladder-row${picked === e.id ? " on" : ""}`} onClick={() => onPick(e.id)}
                aria-label={`${e.id} ${e.title ?? ""}: ${STATUS_LABEL[e.status]}`}>
          <span className="ladder-label"><strong>{e.id}</strong> {e.title}</span>
          <span className="ladder-track">
            {e.history.map((h, i) => {
              const from = pos(h.date), to = i + 1 < e.history.length ? pos(e.history[i + 1].date) : 100;
              const text = `${e.id}: ${STATUS_LABEL[h.status]} from ${h.date}${i + 1 < e.history.length ? ` to ${e.history[i + 1].date}` : ""}`;
              return <span key={i} className={`seg ${h.status}`} style={{ left: `${from}%`, width: `max(${to - from}%, 3px)` }}
                           onMouseEnter={() => setHover({ id: e.id, text })} onMouseLeave={() => setHover(null)} />;
            })}
            {hover?.id === e.id && <span className="tooltip" style={{ right: 0, top: -30 }}>{hover.text}</span>}
          </span>
        </button>
      ))}
      <div className="ladder-axis"><span>{start}</span><span>{end.slice(0, 10)}</span></div>
    </div>
  );
}
