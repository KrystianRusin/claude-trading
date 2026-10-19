// A time-series line chart (TradingView lightweight-charts). Colours come from the CSS tokens and the
// chart is rebuilt when the theme changes. One tooltip lists every series at the crosshair.
import { ColorType, createChart, CrosshairMode, type IChartApi, type ISeriesApi, type Time, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";

export interface Series {
  id: string;
  label: string;
  color: string; // a CSS value such as var(--quant)
  points: { time: string; value: number }[]; // time: YYYY-MM-DD or an ISO timestamp with offset
  muted?: boolean; // a reference line (the benchmark): thinner
}

function cssValue(value: string, el: HTMLElement) {
  const m = value.match(/^var\((--[\w-]+)\)$/);
  return m ? getComputedStyle(el).getPropertyValue(m[1]).trim() : value;
}

// Intraday points are shown on the market's clock: lightweight-charts draws times as UTC, so shift each
// timestamp by its own offset.
function toTime(t: string): Time {
  if (t.length === 10) return t as Time;
  const offset = t.match(/([+-])(\d\d):(\d\d)$/);
  const shift = offset ? (offset[1] === "-" ? -1 : 1) * (Number(offset[2]) * 3600 + Number(offset[3]) * 60) : 0;
  return (Math.floor(new Date(t).getTime() / 1000) + shift) as UTCTimestamp;
}

// The latest value at or before `t`: series sampled at different rates (every minute vs every 5
// minutes) still all show in the tooltip.
function valueAt(points: { t: Time; value: number }[], t: Time) {
  let lo = 0, hi = points.length - 1, found: number | undefined;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (points[mid].t <= t) { found = points[mid].value; lo = mid + 1; } else hi = mid - 1;
  }
  return found;
}

function useThemeKey() {
  const [key, setKey] = useState(0);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const bump = () => setKey((k) => k + 1);
    mq.addEventListener("change", bump);
    const obs = new MutationObserver(bump);
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => { mq.removeEventListener("change", bump); obs.disconnect(); };
  }, []);
  return key;
}

export function TimeChart({ series, format, height = 280, label }: {
  series: Series[]; format: (v: number) => string; height?: number; label: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const tip = useRef<HTMLDivElement>(null);
  const theme = useThemeKey();
  const formatRef = useRef(format);
  formatRef.current = format;

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const chart: IChartApi = createChart(el, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: cssValue("var(--surface)", el) },
                textColor: cssValue("var(--muted)", el), fontFamily: getComputedStyle(el).fontFamily, fontSize: 12 },
      grid: { vertLines: { visible: false }, horzLines: { color: cssValue("var(--grid)", el) } },
      rightPriceScale: { borderColor: cssValue("var(--axis)", el), scaleMargins: { top: 0.1, bottom: 0.12 } },
      timeScale: { borderColor: cssValue("var(--axis)", el), timeVisible: series.some((s) => s.points[0]?.time.length > 10),
                   secondsVisible: false },
      crosshair: { mode: CrosshairMode.Magnet, vertLine: { color: cssValue("var(--axis)", el), labelVisible: false },
                   horzLine: { visible: false, labelVisible: false } },
      localization: { priceFormatter: (v: number) => formatRef.current(v) },
      handleScroll: false, handleScale: false,
    });
    const made: [Series, ISeriesApi<"Line">, { t: Time; value: number }[]][] = series.map((s) => {
      const line = chart.addLineSeries({ color: cssValue(s.color, el), lineWidth: s.muted ? 1 : 2,
                                         priceLineVisible: false, lastValueVisible: false,
                                         crosshairMarkerRadius: 4, crosshairMarkerBorderColor: cssValue("var(--surface)", el) });
      const data = s.points.map((p) => ({ time: toTime(p.time), value: p.value }));
      line.setData(data);
      return [s, line, data.map((d) => ({ t: d.time, value: d.value }))];
    });
    chart.timeScale().fitContent();
    chart.subscribeCrosshairMove((param) => {
      const t = tip.current;
      if (!t) return;
      if (!param.time || !param.point || param.point.x < 0) { t.style.display = "none"; return; }
      t.replaceChildren();
      const head = document.createElement("div");
      head.className = "muted";
      head.textContent = typeof param.time === "string" ? param.time
        : new Date((param.time as number) * 1000).toISOString().slice(11, 16) + " ET";
      t.appendChild(head);
      for (const [s, line, data] of made) {
        const d = param.seriesData.get(line) as { value?: number } | undefined;
        const value = d?.value ?? valueAt(data, param.time);
        if (value === undefined) continue;
        const row = document.createElement("div");
        row.className = "key";
        const k = document.createElement("i");
        k.style.background = cssValue(s.color, el);
        const v = document.createElement("strong");
        v.textContent = formatRef.current(value);
        const name = document.createElement("span");
        name.textContent = s.label;  // labels are data: textContent, never HTML
        row.append(k, v, name);
        t.appendChild(row);
      }
      t.style.display = "block";
      const x = Math.min(param.point.x + 14, el.clientWidth - t.offsetWidth - 4);
      t.style.left = `${Math.max(x, 0)}px`;
      t.style.top = "8px";
    });
    return () => chart.remove();
  }, [series, theme]);

  const empty = series.every((s) => s.points.length === 0);
  return (
    <div className="chart" style={{ height }} role="img" aria-label={label}>
      {empty ? <div className="empty">No data yet.</div> : <div ref={box} style={{ position: "absolute", inset: 0 }} />}
      <div ref={tip} className="tooltip" style={{ display: "none" }} />
    </div>
  );
}

export function Legend({ series }: { series: Pick<Series, "id" | "label" | "color">[] }) {
  if (series.length < 2) return null;
  return (
    <div className="legend">
      {series.map((s) => <span key={s.id} className="key"><i style={{ background: s.color }} />{s.label}</span>)}
    </div>
  );
}
