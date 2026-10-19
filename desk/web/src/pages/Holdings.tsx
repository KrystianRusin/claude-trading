import { useMemo } from "react";

import { type Holding, type Holdings as HoldingsAnswer, type HoldingsBook, useApi } from "../api";
import { DivergingBars } from "../components/Bars";
import { type Series, TimeChart } from "../components/TimeChart";
import { Bot, DataTable, Loading, Tile } from "../components/ui";
import { BOT_COLOR, money, num, pct, signClass, signedMoney, when } from "../format";
import { SymbolLink } from "../sections";

const EMPTY: Record<string, string> = {
  quant: "Flat. Quant closes every position by 15:45, so it holds nothing overnight.",
  sentiment: "Flat: no open positions right now.",
};

function entered(h: Holding) {
  if (!h.entry) return "–";
  const day = h.entry.slice(0, 10);
  if (h.days_held === 0) return h.entry.length > 10 ? `today, ${when(h.entry).split(", ")[1] ?? ""}` : "today";
  return `${day} · ${h.days_held} ${h.days_held === 1 ? "day" : "days"}`;
}

function stop(h: Holding) {
  if (h.stop_price === null) return "–";
  return <>{money(h.stop_price, true)} <span className="muted small">{pct(h.stop_distance_pct, 1)}</span></>;
}

function BookCard({ b }: { b: HoldingsBook }) {
  const t = b.totals;
  const series: Series[] = useMemo(() => [{
    id: b.bot, label: "Unrealized P&L", color: BOT_COLOR[b.bot],
    points: b.history.map((h) => ({ time: h.ts, value: h.unrealized_pnl })),
  }], [b]);
  const scored = b.positions.some((p) => p.score !== undefined && p.score !== null);
  const longs = b.positions.filter((p) => p.side === "long").length;
  return (
    <div className="card stack">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}><Bot bot={b.bot} />{b.book !== "paper" && <span className="badge">{b.book.replace("_", " ")}</span>}</h2>
        <span className="muted small">as of {when(b.as_of)}</span>
      </div>
      {!b.positions.length ? <div className="empty">{EMPTY[b.bot]}</div> : (
        <>
          <div className="grid tiles">
            <Tile label="Positions" value={t.positions} delta={`${longs} long, ${t.positions - longs} short`} deltaClass="muted" />
            <Tile label="Unrealized P&L" value={<span className={signClass(t.unrealized_pnl)}>{signedMoney(t.unrealized_pnl)}</span>}
                  delta={t.equity && t.unrealized_pnl !== null ? `${pct((t.unrealized_pnl / t.equity) * 100)} of equity` : undefined}
                  deltaClass="muted" />
            <Tile label="Today" value={<span className={signClass(t.today_pnl)}>{signedMoney(t.today_pnl)}</span>}
                  delta="move of the open positions today" deltaClass="muted" />
            <Tile label="Exposure" value={pct(t.gross_exposure_pct, 1, false)}
                  delta={`${money(t.long_value)} long, ${money(Math.abs(t.short_value ?? 0))} short`} deltaClass="muted" />
          </div>
          <div>
            <h3 className="sub">Unrealized P&L by position</h3>
            <DivergingBars label="Unrealized profit or loss of each open position, in dollars"
                           format={(v) => signedMoney(v)}
                           rows={[...b.positions].filter((p) => p.unrealized_pnl !== null)
                             .sort((x, y) => (y.unrealized_pnl ?? 0) - (x.unrealized_pnl ?? 0))
                             .map((p) => ({ key: p.symbol, label: p.symbol, value: p.unrealized_pnl as number,
                                            note: `${p.side} ${p.qty} at ${money(p.avg_entry_price, true)}, now ${money(p.price, true)} (${pct(p.unrealized_pct)})` }))} />
          </div>
          <DataTable rows={b.positions} initialSort={{ key: "u", desc: true }} columns={[
            { key: "s", label: "Symbol", render: (p) => <SymbolLink symbol={p.symbol} />, sort: (p) => p.symbol },
            { key: "side", label: "Side", render: (p) => p.side, sort: (p) => p.side },
            { key: "u", label: "Unrealized", num: true, render: (p) => <span className={signClass(p.unrealized_pnl)}>{signedMoney(p.unrealized_pnl)}</span>, sort: (p) => p.unrealized_pnl },
            { key: "up", label: "%", num: true, render: (p) => <span className={signClass(p.unrealized_pct)}>{pct(p.unrealized_pct, 1)}</span>, sort: (p) => p.unrealized_pct },
            { key: "d", label: "Today", num: true, render: (p) => <span className={signClass(p.today_pnl)}>{signedMoney(p.today_pnl)}</span>, sort: (p) => p.today_pnl },
            { key: "in", label: "Entered", render: entered, sort: (p) => p.entry },
            { key: "q", label: "Qty", num: true, render: (p) => p.qty, sort: (p) => p.qty },
            { key: "e", label: "Entry", num: true, render: (p) => money(p.avg_entry_price, true) },
            { key: "p", label: "Price", num: true, render: (p) => money(p.price, true) },
            { key: "stop", label: "Stop (distance)", num: true, render: stop, sort: (p) => p.stop_distance_pct === null ? null : Math.abs(p.stop_distance_pct) },
            { key: "w", label: "Weight", num: true, render: (p) => pct(p.weight_pct, 1, false), sort: (p) => Math.abs(p.weight_pct ?? 0) },
            ...(scored ? [{ key: "sc", label: "Score", num: true, render: (p: Holding) => p.score === null || p.score === undefined ? "–"
              : <span title={`confidence ${num(p.confidence ?? null)}`}>{p.score > 0 ? "+" : ""}{p.score}</span>,
              sort: (p: Holding) => p.score ?? null }] : []),
          ]} />
          <div className="muted small">
            Today is the move since the previous session's close, or since entry for positions opened today. The stop
            distance is how far the price has to move to hit the stop.{scored && " Score is the news score the entry was based on (−3 to +3)."}
          </div>
        </>
      )}
      {b.history.length > 1 && (
        <div>
          <h3 className="sub">Unrealized P&L of the open positions, recent sessions</h3>
          <TimeChart series={series} format={(v) => signedMoney(v)} height={200}
                     label="Total unrealized profit or loss of the open positions over recent sessions" />
        </div>
      )}
    </div>
  );
}

export default function Holdings() {
  const res = useApi<HoldingsAnswer>("/api/positions");
  if (!res.data) return <Loading error={res.error} />;
  // the swing trader first: it is the one holding positions overnight
  const books = [...res.data.books].sort((a, b) => (a.bot === b.bot ? 0 : a.bot === "sentiment" ? -1 : 1));
  return (
    <div className="stack">
      <div>
        <h1>Holdings</h1>
        <p className="lede">What each bot holds right now and how those positions are doing. Unrealized P&L is the gain or
          loss on paper if each position were closed at the latest price. Prices update with each snapshot: every minute for
          Quant while it trades, every 5 minutes for Sentiment during market hours.</p>
      </div>
      {books.length ? books.map((b) => <BookCard key={`${b.bot}-${b.book}`} b={b} />)
        : <div className="empty">No snapshots yet.</div>}
    </div>
  );
}
