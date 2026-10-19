import { useMemo } from "react";

import { type BotName, type Compare as CompareAnswer, type Disagreements, qs, useApi } from "../api";
import { Legend, type Series, TimeChart } from "../components/TimeChart";
import { Bot, DataTable, Loading, Segmented } from "../components/ui";
import { BOT_COLOR, BOT_LABEL, num, pct } from "../format";
import { SymbolLink } from "../sections";
import { useParam } from "../url";

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

function result(r: number | null) {
  if (r === null) return <span className="muted">did not trade</span>;
  return <span className={r > 0 ? "up" : r < 0 ? "down" : ""}>{r > 0 ? "+" : ""}{num(r)}R</span>;
}

function DisagreeCard({ days, period }: { days: number; period: string }) {
  const res = useApi<Disagreements>(`/api/disagreements${qs({ days })}`);
  if (!res.data) return <div className="card"><h2>When they disagree</h2><Loading error={res.error} /></div>;
  const d = res.data;
  const tally = (bot: BotName, t: Disagreements["quant"]) => t.traded
    ? <>{BOT_LABEL[bot]} traded {t.traded} of them and won {t.won} ({pct((t.won / t.traded) * 100, 0, false)}), averaging {num(t.avg_r)}R.</>
    : <>{BOT_LABEL[bot]} traded none of them.</>;
  return (
    <div className="card">
      <h2 style={{ marginBottom: 4 }}>When they disagree</h2>
      <p className="muted small" style={{ margin: "0 0 12px" }}>
        Days when Quant's signal on a symbol pointed one way and Sentiment's news score for that day pointed the other.
        {d.items.length > 0 && <> {d.items.length} times {period}. {tally("quant", d.quant)} {tally("sentiment", d.sentiment)}</>}
      </p>
      <DataTable rows={d.items} empty="They have not disagreed in this period." columns={[
        { key: "d", label: "Day", render: (i) => i.date, sort: (i) => i.date },
        { key: "s", label: "Symbol", render: (i) => <SymbolLink symbol={i.symbol} />, sort: (i) => i.symbol },
        { key: "q", label: "Quant", render: (i) => <>{i.quant}{!i.quant_entered && <span className="badge">skipped</span>}</> },
        { key: "n", label: "Sentiment", render: (i) => <>{i.sentiment} <span className="muted small">{i.score > 0 ? "+" : ""}{i.score} × {num(i.confidence)}</span></> },
        { key: "qr", label: "Quant's result", num: true, render: (i) => result(i.quant_r), sort: (i) => i.quant_r },
        { key: "sr", label: "Sentiment's result", num: true, render: (i) => result(i.sentiment_r), sort: (i) => i.sentiment_r },
      ]} />
    </div>
  );
}

export default function Compare({ sections }: { sections: string[] }) {
  const [range, setRange] = useParam("range", "all", ["30", "90", "all"] as const);
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
          {sections.includes("scores") && <DisagreeCard days={range === "all" ? 3650 : Number(range)}
                                                                   period={range === "all" ? "so far" : `in the last ${range} days`} />}
        </>
      )}
    </div>
  );
}
