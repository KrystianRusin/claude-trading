// One column per day from a zero line: an "up" value above it in blue and a "down" value below it in red (the
// diverging pair). A day can have both, such as bullish and bearish counts. Every column has a tooltip.
import { useEffect, useRef, useState } from "react";

export interface DayColumn { date: string; up: number; down: number; note: string }

export function DailyColumns({ rows, label, height = 180, format = (v: number) => String(v) }: {
  rows: DayColumn[]; label: string; height?: number; format?: (v: number) => string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const obs = new ResizeObserver(() => setWidth(Math.max(260, el.clientWidth)));
    obs.observe(el);
    return () => obs.disconnect();
  }, []);
  if (!rows.length) return <div className="empty">Nothing yet.</div>;
  const pad = { l: 36, r: 8, t: 8, b: 22 };
  const top = Math.max(1e-9, ...rows.map((r) => r.up)), bottom = Math.max(0, ...rows.map((r) => r.down));
  const plotH = height - pad.t - pad.b;
  const zero = pad.t + plotH * (top / (top + bottom || 1));
  const scale = plotH / (top + bottom || 1);
  const slot = (width - pad.l - pad.r) / rows.length;
  const bar = Math.max(2, Math.min(18, slot - 2));
  const every = Math.ceil(rows.length / Math.max(2, Math.floor((width - pad.l) / 70)));
  const path = (x: number, y0: number, h: number, upward: boolean) => {
    const r = Math.min(3, h / 2, bar / 2);
    return upward
      ? `M${x},${y0} v${-(h - r)} q0,${-r} ${r},${-r} h${bar - 2 * r} q${r},0 ${r},${r} v${h - r} z`
      : `M${x},${y0} v${h - r} q0,${r} ${r},${r} h${bar - 2 * r} q${r},0 ${r},${-r} v${-(h - r)} z`;
  };
  return (
    <div ref={box} style={{ position: "relative" }}>
      <svg width={width} height={height} role="img" aria-label={label}>
        <text x={pad.l - 6} y={pad.t + 10} textAnchor="end" fontSize={11} fill="var(--muted)">{format(top)}</text>
        {bottom > 0 && <text x={pad.l - 6} y={height - pad.b} textAnchor="end" fontSize={11} fill="var(--muted)">−{format(bottom)}</text>}
        {rows.map((r, i) => {
          const x = pad.l + i * slot + (slot - bar) / 2;
          return (
            <g key={r.date} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} opacity={hover === null || hover === i ? 1 : 0.55}>
              <rect x={pad.l + i * slot} y={pad.t} width={slot} height={plotH} fill="transparent" />
              {r.up > 0 && <path d={path(x, zero, r.up * scale, true)} fill="var(--pos-bar)" />}
              {r.down > 0 && <path d={path(x, zero, r.down * scale, false)} fill="var(--neg-bar)" />}
              {i % every === 0 && <text x={pad.l + (i + 0.5) * slot} y={height - 6} textAnchor="middle" fontSize={11} fill="var(--muted)">{r.date.slice(5)}</text>}
            </g>
          );
        })}
        <line x1={pad.l} x2={width - pad.r} y1={zero} y2={zero} stroke="var(--axis)" />
      </svg>
      {hover !== null && (
        <div className="tooltip" style={{ left: Math.min(pad.l + (hover + 0.5) * slot, width - 200), top: 0 }}>
          <strong>{rows[hover].date}</strong> {rows[hover].note}
        </div>
      )}
    </div>
  );
}
