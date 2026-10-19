// One session's equity, drawn up to the replay cursor in the bot's colour, with the rest of the day as a faint
// line. Fills appear as dots once the cursor passes them. Clicking or dragging on the chart moves the cursor.
import { useEffect, useRef, useState } from "react";

export interface ReplayPoint { minute: number; value: number }
export interface ReplayFill { minute: number; entry: boolean; label: string }

// Minutes since 09:30 on the clock written in the timestamp (the bots write Eastern time).
export function minuteOf(ts: string) {
  const hh = Number(ts.slice(11, 13)), mm = Number(ts.slice(14, 16));
  return hh * 60 + mm - 570;
}

export function clock(minute: number) {
  const m = minute + 570;
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

export function ReplayChart({ points, cursor, fills, color, base, format, onSeek, label }: {
  points: ReplayPoint[]; cursor: number; fills: ReplayFill[]; color: string; base: number | null;
  format: (v: number) => string; onSeek: (index: number) => void; label: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  const dragging = useRef(false);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const obs = new ResizeObserver(() => setWidth(Math.max(280, el.clientWidth)));
    obs.observe(el);
    return () => obs.disconnect();
  }, []);
  if (!points.length) return <div className="empty">No snapshots for this day.</div>;
  const height = 260, pad = { l: 68, r: 24, t: 12, b: 26 };
  const x0 = Math.min(0, points[0].minute), x1 = Math.max(390, points[points.length - 1].minute);
  const values = points.map((p) => p.value).concat(base !== null ? [base] : []);
  let lo = Math.min(...values), hi = Math.max(...values);
  if (hi - lo < 1) { lo -= 1; hi += 1; }
  const padY = (hi - lo) * 0.08;
  lo -= padY; hi += padY;
  const x = (m: number) => pad.l + ((m - x0) / (x1 - x0)) * (width - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - (v - lo) / (hi - lo)) * (height - pad.t - pad.b);
  const path = (ps: ReplayPoint[]) => ps.map((p, i) => `${i ? "L" : "M"}${x(p.minute).toFixed(1)},${y(p.value).toFixed(1)}`).join("");
  const now = points[Math.min(cursor, points.length - 1)];
  const valueAt = (m: number) => {
    let v = points[0].value;
    for (const p of points) { if (p.minute > m) break; v = p.value; }
    return v;
  };
  const seek = (clientX: number) => {
    const rect = box.current!.getBoundingClientRect();
    const m = x0 + ((clientX - rect.left - pad.l) / (width - pad.l - pad.r)) * (x1 - x0);
    let best = 0;
    points.forEach((p, i) => { if (Math.abs(p.minute - m) < Math.abs(points[best].minute - m)) best = i; });
    onSeek(best);
  };
  const ticks = [lo + padY, (lo + hi) / 2, hi - padY];
  const hours = [30, 90, 150, 210, 270, 330, 390].filter((m) => m >= x0 && m <= x1 && (width > 520 || m % 120 === 30));
  return (
    <div ref={box} className="clickable" style={{ position: "relative", touchAction: "pan-y" }}
         onPointerDown={(e) => { dragging.current = true; (e.target as Element).setPointerCapture?.(e.pointerId); seek(e.clientX); }}
         onPointerMove={(e) => { if (dragging.current) seek(e.clientX); }}
         onPointerUp={() => { dragging.current = false; }}>
      <svg width={width} height={height} role="img" aria-label={label}>
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} stroke="var(--grid)" />
            <text x={pad.l - 6} y={y(t) + 4} textAnchor="end" fontSize={11} fill="var(--muted)">{format(t)}</text>
          </g>
        ))}
        {hours.map((m) => (
          <text key={m} x={x(m)} y={height - 8} textAnchor="middle" fontSize={11} fill="var(--muted)">{clock(m)}</text>
        ))}
        {base !== null && (
          <g>
            <line x1={pad.l} x2={width - pad.r} y1={y(base)} y2={y(base)} stroke="var(--axis)" strokeDasharray="4 4" />
            <text x={width - pad.r} y={y(base) - 4} textAnchor="end" fontSize={11} fill="var(--muted)">open</text>
          </g>
        )}
        <path d={path(points)} fill="none" stroke="var(--axis)" strokeWidth={1.5} />
        <path d={path(points.slice(0, cursor + 1))} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" />
        {fills.filter((f) => f.minute <= now.minute).map((f, i) => (
          <circle key={i} cx={x(f.minute)} cy={y(valueAt(f.minute))} r={4.5} fill={f.entry ? "var(--ink-2)" : "var(--surface)"}
                  stroke={f.entry ? "var(--surface)" : "var(--ink-2)"} strokeWidth={2}><title>{f.label}</title></circle>
        ))}
        <line x1={x(now.minute)} x2={x(now.minute)} y1={pad.t} y2={height - pad.b} stroke="var(--ink-2)" strokeWidth={1} />
        <circle cx={x(now.minute)} cy={y(now.value)} r={5} fill={color} stroke="var(--surface)" strokeWidth={2} />
      </svg>
    </div>
  );
}
