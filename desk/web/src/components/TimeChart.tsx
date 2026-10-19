// A time-series line chart (TradingView lightweight-charts). Colours come from the CSS tokens and the
// chart is rebuilt when the theme changes. One tooltip lists every series at the crosshair.
import { ColorType, createChart, CrosshairMode, type IChartApi, type ISeriesApi, type SeriesMarker, type Time,
         type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";

export interface Series {
  id: string;
  label: string;
  color: string; // a CSS value such as var(--quant)
  points: { time: string; value: number }[]; // time: YYYY-MM-DD or an ISO timestamp with offset
  muted?: boolean; // a reference line (the benchmark): thinner
}

// A point event drawn on the first series, such as a config change. Clicking one calls onMarker.
export interface Marker { id: string; time: string; text: string; title: string }

export function cssValue(value: string, el: HTMLElement) {
  const m = value.match(/^var\((--[\w-]+)\)$/);
  return m ? getComputedStyle(el).getPropertyValue(m[1]).trim() : value;
}

// Intraday points are shown on the market's clock: lightweight-charts draws times as UTC, so shift each
// timestamp by its own offset.
export function toTime(t: string): Time {
  if (t.length === 10) return t as Time;
  const offset = t.match(/([+-])(\d\d):(\d\d)$/);
  const shift = offset ? (offset[1] === "-" ? -1 : 1) * (Number(offset[2]) * 3600 + Number(offset[3]) * 60) : 0;
  return (Math.floor(new Date(t).getTime() / 1000) + shift) as UTCTimestamp;
}

// One spelling for a time however the chart reports it: daily points given as "YYYY-MM-DD" come back from
// clicks and the crosshair as {year, month, day}.
function timeKey(t: Time): string {
  if (typeof t === "object") return `${t.year}-${String(t.month).padStart(2, "0")}-${String(t.day).padStart(2, "0")}`;
  return String(t);
}

// The latest value at or before `t`: series sampled at different rates (every minute vs every 5
// minutes) still all show in the tooltip.
function valueAt(points: { t: Time; value: number }[], t: Time) {
  let lo = 0, hi = points.length - 1, found: number | undefined;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const at = points[mid].t;
    const before = typeof at === "number" && typeof t === "number" ? at <= t : timeKey(at) <= timeKey(t);
    if (before) { found = points[mid].value; lo = mid + 1; } else hi = mid - 1;
  }
  return found;
}

export function useThemeKey() {
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

export function TimeChart({ series, format, height = 280, label, markers, selected, onMarker }: {
  series: Series[]; format: (v: number) => string; height?: number; label: string;
  markers?: Marker[]; selected?: string | null; onMarker?: (id: string) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const tip = useRef<HTMLDivElement>(null);
  const theme = useThemeKey();
  const formatRef = useRef(format);
  formatRef.current = format;
  const onMarkerRef = useRef(onMarker);
  onMarkerRef.current = onMarker;

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
    // Markers sit on the first series, at the first point on or after their time (a change made in the evening
    // shows on the next day's close).
    const atTime = new Map<string, Marker[]>();
    if (markers?.length && made.length) {
      const [first, line, data] = made[0];
      const times = first.points.map((p) => p.time);
      const placed: SeriesMarker<Time>[] = [];
      for (const m of [...markers].sort((a, b) => (a.time < b.time ? -1 : 1))) {
        const i = times.findIndex((t) => t.slice(0, 10) >= m.time.slice(0, 10));
        if (i < 0) continue;
        const key = timeKey(data[i].t);
        atTime.set(key, [...(atTime.get(key) ?? []), m]);
        const on = m.id === selected;
        placed.push({ time: data[i].t, position: "aboveBar", shape: on ? "arrowDown" : "circle", id: m.id, text: m.text,
                      color: cssValue(on ? first.color : "var(--ink-2)", el), size: on ? 1.4 : 0.8 });
      }
      placed.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
      line.setMarkers(placed);
    }
    chart.subscribeClick((param) => {
      const hit = typeof param.hoveredObjectId === "string" ? param.hoveredObjectId
        : param.time !== undefined ? atTime.get(timeKey(param.time))?.[0]?.id : undefined;
      if (hit) onMarkerRef.current?.(hit);
    });
    chart.timeScale().fitContent();
    chart.subscribeCrosshairMove((param) => {
      const t = tip.current;
      if (!t) return;
      if (!param.time || !param.point || param.point.x < 0) { t.style.display = "none"; return; }
      t.replaceChildren();
      const head = document.createElement("div");
      head.className = "muted";
      head.textContent = typeof param.time === "number"
        ? new Date(param.time * 1000).toISOString().slice(11, 16) + " ET" : timeKey(param.time);
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
      for (const m of atTime.get(timeKey(param.time)) ?? []) {
        const row = document.createElement("div");
        row.className = "muted";
        row.textContent = m.title;
        t.appendChild(row);
      }
      t.style.display = "block";
      const x = Math.min(param.point.x + 14, el.clientWidth - t.offsetWidth - 4);
      t.style.left = `${Math.max(x, 0)}px`;
      t.style.top = "8px";
    });
    return () => chart.remove();
  }, [series, theme, markers, selected]);

  const empty = series.every((s) => s.points.length === 0);
  return (
    <div className={`chart${markers?.length ? " clickable" : ""}`} style={{ height }} role="img" aria-label={label}>
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
