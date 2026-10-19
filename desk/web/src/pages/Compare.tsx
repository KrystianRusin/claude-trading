import { useMemo, useState } from "react";

import { type BotName, type Compare as CompareAnswer, qs, useApi } from "../api";
import { Legend, type Series, TimeChart } from "../components/TimeChart";
import { Bot, Loading, Segmented } from "../components/ui";
import { BOT_COLOR, BOT_LABEL, num, pct } from "../format";

const ROWS: { key: string; label: string; show: (v: number | null | undefined) => string }[] = [
  { key: "total_return_pct", label: "Return", show: (v) => pct(v) },
  { key: "volatility_annual_pct", label: "Volatility (annualised)", show: (v) => pct(v, 1, false) },
  { key: "sharpe", label: "Sharpe ratio", show: (v) => num(v) },
  { key: "max_drawdown_pct", label: "Max drawdown", show: (v) => pct(v, 2, false) },
  { key: "worst_day_pct", label: "Worst day", show: (v) => pct(v) },
  { key: "best_day_pct", label: "Best day", show: (v) => pct(v) },
  { key: "days", label: "Trading days", show: (v) => String(v ?? 0) },
];

function startFor(range: string) {
  if (range === "all") return undefined;
  const d = new Date();
  d.setDate(d.getDate() - Number(range));
  return d.toISOString().slice(0, 10);
}

export default function Compare() {
  const [range, setRange] = useState("all");
  const res = useApi<CompareAnswer>(`/api/compare${qs({ start: startFor(range) })}`);
  const c = res.data;
  const series: Series[] = useMemo(() => c ? (Object.keys(c.curves) as BotName[]).map((bot) => ({
    id: bot, label: BOT_LABEL[bot], color: BOT_COLOR[bot],
    points: (c.curves[bot] ?? []).map((p) => ({ time: p.date, value: p.return_pct })),
  })) : [], [c]);
  const bots = c ? (Object.keys(c.bots) as BotName[]) : [];
  const d = c?.difference;
  return (
    <div className="stack">
      <div>
        <h1>Compare the bots</h1>
        <p className="lede">Daily returns of the two paper accounts. A bot is called ahead only when the 95% range of the
          daily-return difference leaves out zero; until then the gap could be luck.</p>
      </div>
      <div className="filters">
        <Segmented label="Date range" value={range} onChange={setRange}
                   options={[{ value: "30", label: "30 days" }, { value: "90", label: "90 days" }, { value: "all", label: "All" }]} />
      </div>
      {!c ? <Loading error={res.error} /> : (
        <>
          {d && (
            <div className="card">
              <h2>Who is ahead?</h2>
              <div style={{ fontSize: 22, fontWeight: 600 }}>{d.verdict[0].toUpperCase() + d.verdict.slice(1)}</div>
              {d.mean_diff_pct !== undefined ? (
                <p className="muted" style={{ marginBottom: 0 }}>
                  Over {d.days} days both traded ({d.first} to {d.last}), Sentiment's daily return minus Quant's averaged{" "}
                  {pct(d.mean_diff_pct, 3)}, with a 95% range of {pct(d.low_pct, 3)} to {pct(d.high_pct, 3)}.
                  {d.correlation !== null && d.correlation !== undefined && <> Their daily returns correlate at {num(d.correlation)}.</>}
                </p>
              ) : <p className="muted" style={{ marginBottom: 0 }}>They need at least two trading days in common.</p>}
            </div>
          )}
          <div className="card">
            <h2>Cumulative return</h2>
            <Legend series={series} />
            <TimeChart series={series} format={(v) => pct(v)} label="Cumulative return of each bot, in percent" />
          </div>
          <div className="card scroll">
            <table>
              <thead><tr><th></th>{bots.map((b) => <th key={b} className="num"><Bot bot={b} /></th>)}</tr></thead>
              <tbody>
                {ROWS.map((r) => (
                  <tr key={r.key}><td>{r.label}</td>
                    {bots.map((b) => <td key={b} className="num">{r.show((c.bots[b] as unknown as Record<string, number | null>)[r.key])}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
