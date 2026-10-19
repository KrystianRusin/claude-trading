// Trade outcomes in R as columns from one baseline: losses in red, wins in blue (diverging around zero),
// 4px rounded tops, a 2px gap between columns, and a tooltip per column.
import { useEffect, useRef, useState } from "react";

export function Histogram({ bins, label }: { bins: { from: number; to: number; count: number }[]; label: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(560);
  useEffect(() => {  // draw at the real pixel width, so text and bar sizes stay what the specs say
    const el = box.current;
    if (!el) return;
    const obs = new ResizeObserver(() => setWidth(Math.max(240, el.clientWidth)));
    obs.observe(el);
    return () => obs.disconnect();
  }, []);
  const height = 200, pad = { l: 32, r: 8, t: 12, b: 28 };
  const max = Math.max(1, ...bins.map((b) => b.count));
  const slot = (width - pad.l - pad.r) / Math.max(bins.length, 1);
  const bar = Math.min(24, slot - 2);
  const y = (v: number) => pad.t + (height - pad.t - pad.b) * (1 - v / max);
  const ticks = [0, Math.ceil(max / 2), max].filter((v, i, a) => a.indexOf(v) === i);
  const total = bins.reduce((s, b) => s + b.count, 0);
  return (
    <div ref={box} style={{ position: "relative" }}>
      {!total ? <div className="empty">No trades with an R value yet.</div> : (
      <svg width={width} height={height} role="img" aria-label={label}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} stroke="var(--grid)" strokeWidth={1} />
            <text x={pad.l - 6} y={y(t) + 4} textAnchor="end" fontSize={11} fill="var(--muted)">{t}</text>
          </g>
        ))}
        {bins.map((b, i) => {
          const x = pad.l + i * slot + (slot - bar) / 2;
          const h = y(0) - y(b.count);
          const r = Math.min(4, h / 2);
          const fill = b.to <= 0 ? "var(--neg-bar)" : "var(--pos-bar)";
          return (
            <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <rect x={pad.l + i * slot} y={pad.t} width={slot} height={height - pad.t - pad.b} fill="transparent" />
              {b.count > 0 && (
                <path d={`M${x},${y(0)} v${-(h - r)} q0,${-r} ${r},${-r} h${bar - 2 * r} q${r},0 ${r},${r} v${h - r} z`}
                      fill={fill} opacity={hover === null || hover === i ? 1 : 0.55} />
              )}
            </g>
          );
        })}
        <line x1={pad.l} x2={width - pad.r} y1={y(0)} y2={y(0)} stroke="var(--axis)" strokeWidth={1} />
        {bins.map((b, i) => (i % 2 === 0 ? (
          <text key={i} x={pad.l + i * slot} y={height - 10} textAnchor="middle" fontSize={11} fill="var(--muted)">
            {b.from > 0 ? `+${b.from}` : b.from}R
          </text>
        ) : null))}
      </svg>)}
      {hover !== null && total > 0 && (
        <div className="tooltip" style={{ left: Math.min(pad.l + (hover + 0.5) * slot, width - 180), top: 0 }}>
          <strong>{bins[hover].count}</strong> trades from {bins[hover].from}R to {bins[hover].to}R
        </div>
      )}
    </div>
  );
}
