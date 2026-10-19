import { useMemo } from "react";
import { Link } from "react-router-dom";

import { type Benchmark, type BotName, type EquityBook, type Holdings, type Hot, qs, type SymbolRow, useApi } from "../api";
import { Legend, type Series, TimeChart } from "../components/TimeChart";
import { Bot, DataTable, Loading, Segmented, Tile } from "../components/ui";
import { BOT_ABOUT, BOT_COLOR, BOT_LABEL, money, num, pct, signClass, signedMoney } from "../format";
import { SymbolLink } from "../sections";
import { useParam } from "../url";

const has = (sections: string[], s: string) => sections.includes(s);

function bookName(b: EquityBook) {
  return b.book === "paper" ? BOT_LABEL[b.bot] : `${BOT_LABEL[b.bot]} (${b.book.replace("_", " ")})`;
}

function BotTiles({ b }: { b: EquityBook }) {
  const s = b.summary;
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}><Bot bot={b.bot} />{b.book !== "paper" && <span className="badge">{b.book.replace("_", " ")}</span>}</h2>
        {s.halted_today && <span className="badge">halted today</span>}
      </div>
      <div className="muted small">{BOT_ABOUT[b.bot]}</div>
      <div className="grid tiles pair">
        <Tile label="Equity" value={money(s.equity)} delta={`started at ${money(s.start_equity)}`} deltaClass="muted" />
        <Tile label="Today" value={signedMoney(s.today_pnl)} delta={pct(s.today_return_pct)} deltaClass={signClass(s.today_pnl)} />
        <Tile label="Total return" value={pct(s.total_return_pct)} delta={`${s.days} trading days`} deltaClass="muted" />
        <Tile label="Max drawdown" value={pct(s.max_drawdown_pct, 2, false)} delta="from the peak" deltaClass="muted" />
      </div>
    </div>
  );
}

function EquityChart({ books, benchmark }: { books: EquityBook[]; benchmark?: Benchmark }) {
  const [range, setRange] = useParam("range", "all", ["all", "today"] as const);
  const series: Series[] = useMemo(() => {
    const out: Series[] = books.map((b) => {
      if (range === "today") {
        const start = (b.summary.equity ?? 0) - (b.summary.today_pnl ?? 0);
        return { id: `${b.bot}-${b.book}`, label: bookName(b), color: BOT_COLOR[b.bot],
                 points: start ? b.intraday.points.filter((p) => p.equity !== null)
                   .map((p) => ({ time: p.ts, value: ((p.equity as number) / start - 1) * 100 })) : [] };
      }
      const base = b.summary.start_equity;
      return { id: `${b.bot}-${b.book}`, label: bookName(b), color: BOT_COLOR[b.bot],
               points: base ? b.daily.filter((d) => d.equity !== null)
                 .map((d) => ({ time: d.date, value: ((d.equity as number) / base - 1) * 100 })) : [] };
    });
    if (range === "all" && benchmark?.daily.length) {
      const first = books.flatMap((b) => b.daily.map((d) => d.date)).sort()[0];
      const rows = benchmark.daily.filter((d) => d.close !== null && (!first || d.date >= first));
      const base = rows[0]?.close;
      if (base) out.push({ id: "spy", label: "SPY", color: "var(--benchmark)", muted: true,
                           points: rows.map((d) => ({ time: d.date, value: ((d.close as number) / base - 1) * 100 })) });
    }
    return out;
  }, [books, benchmark, range]);
  const intradayDate = books.find((b) => b.intraday.points.length)?.intraday.date;
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
        <h2 style={{ margin: 0 }}>{range === "all" ? "Return since start" : `Today${intradayDate ? `, ${intradayDate}` : ""}`}</h2>
        <Segmented label="Range" value={range} onChange={setRange}
                   options={[{ value: "all", label: "Since start" }, { value: "today", label: "Today" }]} />
      </div>
      <Legend series={series} />
      <TimeChart series={series} format={(v) => pct(v)} label="Return of each bot over time, in percent" />
      <div className="muted small">
        {range === "all" ? "Each point is a day's close, as a return on the starting equity."
          : "Snapshots through the day: every minute for Quant, every 5 minutes for Sentiment."}
      </div>
    </div>
  );
}

function HotPanel({ sections }: { sections: string[] }) {
  const hot = useApi<Hot>("/api/hot");
  if (!hot.data) return <div className="card"><h2>Hot right now</h2><Loading error={hot.error} /></div>;
  const h = hot.data;
  return (
    <div className="card">
      <h2>Hot right now</h2>
      <div className="muted small" style={{ marginBottom: 12 }}>The symbols each bot paid most attention to over its last 5 trading days.</div>
      <div className="grid two">
        <div>
          <Bot bot="quant" />
          <DataTable rows={h.quant} empty="No signals yet." columns={[
            { key: "s", label: "Symbol", render: (r) => <><SymbolLink symbol={r.symbol} bold={false} />{r.new_on_watchlist && <span className="badge">new on watchlist</span>}</> },
            { key: "n", label: "Signals", num: true, render: (r) => r.signals },
            { key: "e", label: "Entered", num: true, render: (r) => r.entries },
            { key: "side", label: "Last side", render: (r) => r.side ?? "–" },
          ]} />
        </div>
        {has(sections, "scores") && h.sentiment && (
          <div>
            <Bot bot="sentiment" />
            <DataTable rows={h.sentiment} empty="No scores yet." columns={[
              { key: "s", label: "Symbol", render: (r) => <SymbolLink symbol={r.symbol} bold={false} /> },
              { key: "h", label: "Attention", num: true, render: (r) => num(r.heat, 1) },
              { key: "d", label: "Lean", render: (r) => r.direction },
              { key: "a", label: "Articles", num: true, render: (r) => r.articles },
            ]} />
          </div>
        )}
      </div>
      <div className="muted small" style={{ marginTop: 8 }}>
        Quant: signals fired, entered or skipped. Sentiment: |score| × confidence summed over its daily scores, plus desk ideas.
      </div>
      {h.disagree && h.disagree.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <h2>The bots disagree</h2>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {h.disagree.map((d) => <li key={d.symbol}><SymbolLink symbol={d.symbol} />: Quant went {d.quant}, Sentiment reads it {d.sentiment}.</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

function SymbolsPanel() {
  const [daysParam, setDaysParam] = useParam("days", "30", ["7", "30", "0"] as const);
  const [minParam, setMinParam] = useParam("min", "3", ["1", "3", "5", "10"] as const);
  const days = Number(daysParam) as 7 | 30 | 0, minTrades = Number(minParam);
  const setDays = (d: 7 | 30 | 0) => setDaysParam(String(d) as typeof daysParam);
  const setMinTrades = (n: number) => setMinParam(String(n) as typeof minParam);
  const res = useApi<{ symbols: SymbolRow[] }>(`/api/symbols${qs({ days, min_trades: minTrades })}`);
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>Best and worst symbols</h2>
        <div className="row">
          <Segmented label="Window" value={days} onChange={setDays}
                     options={[{ value: 7, label: "7 days" }, { value: 30, label: "30 days" }, { value: 0, label: "All" }]} />
          <label className="small muted">at least{" "}
            <select value={minTrades} onChange={(e) => setMinTrades(Number(e.target.value))}>
              {[1, 3, 5, 10].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>{" "}trades</label>
        </div>
      </div>
      {res.data ? (
        <DataTable rows={res.data.symbols} initialSort={{ key: "pnl", desc: true }}
                   empty="No symbol has enough trades in this window yet." columns={[
          { key: "symbol", label: "Symbol", render: (r) => <SymbolLink symbol={r.symbol} bold={false} />, sort: (r) => r.symbol },
          { key: "bot", label: "Bot", render: (r) => <Bot bot={r.bot as BotName} />, sort: (r) => r.bot },
          { key: "pnl", label: "P&L", num: true, render: (r) => <span className={signClass(r.pnl)}>{signedMoney(r.pnl)}</span>, sort: (r) => r.pnl },
          { key: "n", label: "Trades", num: true, render: (r) => r.trades, sort: (r) => r.trades },
          { key: "w", label: "Win rate", num: true, render: (r) => pct(r.win_rate === null ? null : r.win_rate * 100, 0, false), sort: (r) => r.win_rate },
          { key: "r", label: "Avg R", num: true, render: (r) => num(r.avg_r), sort: (r) => r.avg_r },
        ]} />
      ) : <Loading error={res.error} />}
    </div>
  );
}

function HoldingsPanel() {
  const res = useApi<Holdings>("/api/positions");
  if (!res.data) return null;
  const books = [...res.data.books].sort((a, b) => (a.bot === b.bot ? 0 : a.bot === "sentiment" ? -1 : 1));
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
        <h2 style={{ margin: 0 }}>Holding now</h2>
        <Link to="/holdings" className="small">All holdings →</Link>
      </div>
      <div className="grid two">
        {books.map((b) => (
          <div key={`${b.bot}-${b.book}`}>
            <Bot bot={b.bot} />
            {b.positions.length ? (
              <p style={{ margin: "6px 0 0" }}>
                {b.positions.length} {b.positions.length === 1 ? "position" : "positions"}
                {" "}({b.positions.slice(0, 5).map((p) => p.symbol).join(", ")}{b.positions.length > 5 ? ", …" : ""}),
                unrealized <span className={signClass(b.totals.unrealized_pnl)}>{signedMoney(b.totals.unrealized_pnl)}</span>,
                today <span className={signClass(b.totals.today_pnl)}>{signedMoney(b.totals.today_pnl)}</span>.
              </p>
            ) : <p className="muted" style={{ margin: "6px 0 0" }}>Flat.</p>}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Overview({ sections }: { sections: string[] }) {
  const eq = useApi<{ books: EquityBook[] }>("/api/equity", has(sections, "equity"));
  const bench = useApi<Benchmark>("/api/benchmark", has(sections, "prices"));
  return (
    <div className="stack">
      <div>
        <h1>Two trading bots, one paper account each</h1>
        <p className="lede">Both run unattended on a Raspberry Pi and trade Alpaca paper accounts, so no real money is at
          risk. Each one reviews its own results every night and adjusts. This page updates every 30 seconds.</p>
      </div>
      {has(sections, "equity") && (eq.data ? (
        <>
          <div className="grid two">{eq.data.books.map((b) => <div className="card" key={`${b.bot}-${b.book}`}><BotTiles b={b} /></div>)}</div>
          {eq.data.books.length > 0 && <EquityChart books={eq.data.books} benchmark={bench.data} />}
        </>
      ) : <Loading error={eq.error} />)}
      {has(sections, "positions") && <HoldingsPanel />}
      {has(sections, "symbols") && <><HotPanel sections={sections} /><SymbolsPanel /></>}
    </div>
  );
}
