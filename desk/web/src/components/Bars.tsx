// Horizontal bars from one zero line: losses to the left in red, gains to the right in blue (the diverging
// pair the R histogram uses). Every bar is labelled with its value, so colour is never the only cue.
import { useEffect, useRef, useState } from "react";

export function DivergingBars({ rows, format, label }: {
  rows: { key: string; label: string; value: number; note?: string }[]; format: (v: number) => string; label: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(560);
  const [hover, setHover] = useState<string | null>(null);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const obs = new ResizeObserver(() => setWidth(Math.max(260, el.clientWidth)));
    obs.observe(el);
    return () => obs.disconnect();
  }, []);
  if (!rows.length) return null;
  const rowH = 28, bar = 16, labelW = 64, valueW = 76;
  const plot = width - labelW - 2 * valueW;
  const max = Math.max(...rows.map((r) => Math.abs(r.value)), 1e-9);
  const hasNeg = rows.some((r) => r.value < 0), hasPos = rows.some((r) => r.value > 0);
  // zero sits in the middle when there are both signs, else at the edge the bars grow from
  const zero = labelW + valueW + (hasNeg && hasPos ? plot / 2 : hasNeg ? plot : 0);
  const scale = (hasNeg && hasPos ? plot / 2 : plot) / max;
  const height = rows.length * rowH + 4;
  return (
    <div ref={box} style={{ position: "relative" }}>
      <svg width={width} height={height} role="img" aria-label={label}>
        {rows.map((r, i) => {
          const y = i * rowH + (rowH - bar) / 2;
          const w = Math.max(Math.abs(r.value) * scale, r.value ? 2 : 0);
          const x = r.value < 0 ? zero - w : zero;
          const rad = Math.min(4, w / 2);
          // rounded only at the data end, square at the zero line
          const d = r.value < 0
            ? `M${zero},${y} h${-(w - rad)} q${-rad},0 ${-rad},${rad} v${bar - 2 * rad} q0,${rad} ${rad},${rad} h${w - rad} z`
            : `M${x},${y} h${w - rad} q${rad},0 ${rad},${rad} v${bar - 2 * rad} q0,${rad} ${-rad},${rad} h${-(w - rad)} z`;
          const valueX = r.value < 0 ? x - 6 : x + w + 6;
          return (
            <g key={r.key} onMouseEnter={() => setHover(r.key)} onMouseLeave={() => setHover(null)}>
              <rect x={0} y={i * rowH} width={width} height={rowH} fill={hover === r.key ? "var(--hover)" : "transparent"} />
              <text x={0} y={y + bar / 2 + 4} fontSize={12} fill="var(--ink)" fontWeight={600}>{r.label}</text>
              {w > 0 && <path d={d} fill={r.value < 0 ? "var(--neg-bar)" : "var(--pos-bar)"} />}
              <text x={valueX} y={y + bar / 2 + 4} fontSize={12} fill="var(--ink-2)" textAnchor={r.value < 0 ? "end" : "start"}
                    style={{ fontVariantNumeric: "tabular-nums" }}>{format(r.value)}</text>
            </g>
          );
        })}
        <line x1={zero} x2={zero} y1={0} y2={height} stroke="var(--axis)" strokeWidth={1} />
      </svg>
      {hover && rows.find((r) => r.key === hover)?.note && (
        <div className="tooltip" style={{ right: 0, top: rows.findIndex((r) => r.key === hover) * rowH + rowH }}>
          {rows.find((r) => r.key === hover)!.note}
        </div>
      )}
    </div>
  );
}
