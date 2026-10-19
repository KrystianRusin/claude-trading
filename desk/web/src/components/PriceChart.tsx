// Price bars as candles (rising in blue, falling in red, the same diverging pair as everywhere else), with trade
// markers above or below them and horizontal lines for a stop or a target. Bars come from the bots' saved data.
import { ColorType, createChart, CrosshairMode, LineStyle, type SeriesMarker, type Time } from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { Bars } from "../api";
import { cssValue, toTime, useThemeKey } from "./TimeChart";

export interface PriceMarker { time: string; above: boolean; color: string; text: string; shape?: "arrowUp" | "arrowDown" | "circle" }
export interface PriceLine { price: number; label: string; color: string }

export function PriceChart({ bars, markers = [], lines = [], height = 300, label, format }: {
  bars: Bars["bars"]; markers?: PriceMarker[]; lines?: PriceLine[]; height?: number; label: string; format: (v: number) => string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const tip = useRef<HTMLDivElement>(null);
  const theme = useThemeKey();
  useEffect(() => {
    const el = box.current;
    if (!el || !bars.length) return;
    const intraday = bars[0].ts.length > 10;
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: cssValue("var(--surface)", el) },
                textColor: cssValue("var(--muted)", el), fontFamily: getComputedStyle(el).fontFamily, fontSize: 12 },
      grid: { vertLines: { visible: false }, horzLines: { color: cssValue("var(--grid)", el) } },
      rightPriceScale: { borderColor: cssValue("var(--axis)", el) },
      timeScale: { borderColor: cssValue("var(--axis)", el), timeVisible: intraday, secondsVisible: false },
      crosshair: { mode: CrosshairMode.Magnet, vertLine: { color: cssValue("var(--axis)", el), labelVisible: false },
                   horzLine: { visible: false, labelVisible: false } },
      localization: { priceFormatter: format },
      handleScroll: false, handleScale: false,
    });
    const up = cssValue("var(--pos-bar)", el), down = cssValue("var(--neg-bar)", el);
    const series = chart.addCandlestickSeries({ upColor: up, downColor: down, borderVisible: false, wickUpColor: up,
                                                wickDownColor: down, priceLineVisible: false, lastValueVisible: false });
    const data = bars.filter((b) => b.open !== null && b.high !== null && b.low !== null && b.close !== null)
      .map((b) => ({ time: toTime(b.ts), open: b.open as number, high: b.high as number, low: b.low as number,
                     close: b.close as number, ts: b.ts }));
    series.setData(data);
    // each marker sits on the last bar at or before its time
    const placed: SeriesMarker<Time>[] = [];
    for (const m of markers) {
      const key = intraday ? m.time : m.time.slice(0, 10);
      let at: (typeof data)[number] | undefined;
      for (const d of data) { if (d.ts <= key) at = d; else break; }
      if (at) placed.push({ time: at.time, position: m.above ? "aboveBar" : "belowBar", color: cssValue(m.color, el),
                            shape: m.shape ?? (m.above ? "arrowDown" : "arrowUp"), text: m.text,
                            size: intraday ? 1 : 0.5 });  // markers grow with the bar spacing; daily bars are wide
    }
    placed.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
    series.setMarkers(placed);
    for (const l of lines) {
      series.createPriceLine({ price: l.price, color: cssValue(l.color, el), lineWidth: 1, lineStyle: LineStyle.Dashed,
                               axisLabelVisible: true, title: l.label });
    }
    chart.timeScale().fitContent();
    chart.subscribeCrosshairMove((param) => {
      const t = tip.current;
      if (!t) return;
      const d = param.seriesData.get(series) as { open: number; high: number; low: number; close: number } | undefined;
      if (!param.point || !d) { t.style.display = "none"; return; }
      const i = data.findIndex((x) => x.time === param.time);
      t.textContent = `${i >= 0 ? (intraday ? data[i].ts.slice(11, 16) + " ET" : data[i].ts) : ""}  O ${format(d.open)}  H ${format(d.high)}  L ${format(d.low)}  C ${format(d.close)}`;
      t.style.display = "block";
      t.style.left = "8px";
      t.style.top = "8px";
    });
    return () => chart.remove();
  }, [bars, markers, lines, theme, format]);
  if (!bars.length) return <div className="empty">No saved prices for this period.</div>;
  return (
    <div className="chart" style={{ height }} role="img" aria-label={label}>
      <div ref={box} style={{ position: "absolute", inset: 0 }} />
      <div ref={tip} className="tooltip" style={{ display: "none" }} />
    </div>
  );
}
