import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { type Bars, type BotName, type Headlines, type KnownSymbols, qs, type SymbolPage, useApi } from "../api";
import { DailyColumns } from "../components/DailyColumns";
import { PriceChart, type PriceMarker } from "../components/PriceChart";
import { TradeDetail } from "../components/TradeDetail";
import { Bot, DataTable, Loading, Tile } from "../components/ui";
import { BOT_COLOR, BOT_LABEL, money, num, pct, signClass, signedMoney, when } from "../format";
import { usePublic } from "../sections";
import { useParam } from "../url";

const price = (v: number) => money(v, true);
const DAYS = 180;

function daysAgo(n: number) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

function Search({ current }: { current?: string }) {
  const known = useApi<KnownSymbols>("/api/symbols/known");
  const navigate = useNavigate();
  const [text, setText] = useState("");
  const go = (s: string) => { const sym = s.trim().toUpperCase(); if (sym) navigate(`/symbol/${encodeURIComponent(sym)}`); };
  return (
    <form className="row" style={{ gap: 6 }} onSubmit={(e) => { e.preventDefault(); go(text); }}>
      <input className="search" list="known-symbols" placeholder={current ?? "Symbol, e.g. NKE"} value={text}
             aria-label="Find a symbol" onChange={(e) => setText(e.target.value)} />
      <datalist id="known-symbols">{known.data?.symbols.map((s) => <option key={s} value={s} />)}</datalist>
      <button className="btn" type="submit">Go</button>
    </form>
  );
}

function Index() {
  const known = useApi<KnownSymbols>("/api/symbols/known");
  const navigate = useNavigate();
  return (
    <div className="stack">
      <div>
        <h1>Symbols</h1>
        <p className="lede">Every symbol either bot traded, or the sentiment bot scored recently. Pick one to see both bots'
          trades on it, the news scores and signals over time, and its price chart.</p>
      </div>
      <Search />
      {!known.data ? <Loading error={known.error} /> : (
        <div className="row" style={{ gap: 6 }}>
          {known.data.symbols.map((s) => <button key={s} className="btn" onClick={() => navigate(`/symbol/${s}`)}>{s}</button>)}
        </div>
      )}
    </div>
  );
}

export default function Symbol() {
  const { sym } = useParams();
  if (!sym) return <Index />;
  return <SymbolView sym={sym.toUpperCase()} />;
}

function SymbolView({ sym }: { sym: string }) {
  const prices = usePublic("prices"), news = usePublic("headlines"), scoresOn = usePublic("scores");
  const res = useApi<SymbolPage>(`/api/symbol${qs({ symbol: sym })}`);
  const start = useMemo(() => daysAgo(DAYS), []), end = useMemo(() => daysAgo(0), []);
  const bars = useApi<Bars>(`/api/bars${qs({ symbol: sym, start, end })}`, prices);
  const heads = useApi<Headlines>(`/api/headlines${qs({ symbol: sym })}`, news);
  const [trade, setTrade] = useParam<string>("trade", "");
  const [allNews, setAllNews] = useState(false);
  const s = res.data?.symbol === sym ? res.data : undefined;
  const markers: PriceMarker[] = useMemo(() => (s?.trades ?? []).filter((t) => (t.entry_date ?? t.date) >= start).map((t) => ({
    time: t.entry_date ?? t.date, above: t.side === "short", color: BOT_COLOR[t.bot],
    text: `${BOT_LABEL[t.bot][0]} ${t.side === "long" ? "long" : "short"}`,
  })), [s, start]);
  const picked = trade.match(/^(quant|sentiment)-(\d+)$/);
  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1 style={{ margin: 0 }}>{sym}</h1>
        <Search current={sym} />
      </div>
      {!s ? <Loading error={res.error} /> : (
        <>
          <div className="grid two">
            {(["quant", "sentiment"] as BotName[]).map((bot) => {
              const b = s.bots[bot];
              return (
                <div className="card" key={bot}>
                  <h2><Bot bot={bot} /></h2>
                  {!b ? <div className="muted">Has not traded {sym}.</div> : (
                    <div className="grid tiles pair">
                      <Tile label="P&L" value={<span className={signClass(b.total_pnl)}>{signedMoney(b.total_pnl)}</span>}
                            delta={`${b.trades} trades, ${b.first} to ${b.last}`} deltaClass="muted" />
                      <Tile label="Win rate" value={pct(b.win_rate === null ? null : b.win_rate * 100, 0, false)}
                            delta={`avg ${num(b.avg_r)}R`} deltaClass="muted" />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          {picked && <TradeDetail bot={picked[1]} id={Number(picked[2])} onClose={() => setTrade("")} />}
          {prices && (
            <div className="card">
              <h2>Price, last {DAYS} days</h2>
              {bars.data ? <PriceChart bars={bars.data.bars} markers={markers} format={price}
                                       label={`${sym} daily prices with each bot's entries marked`} />
                : <Loading error={bars.error} />}
              <div className="muted small">Daily bars saved by the sentiment bot. Arrows mark entries: Q for Quant, S for Sentiment.</div>
            </div>
          )}
          {scoresOn && (
            <div className="card">
              <h2>News scores</h2>
              <p className="muted small" style={{ marginTop: 0 }}>The sentiment bot's score for {sym} on each day it scored it
                (−3 to +3), from that morning's news.</p>
              <DailyColumns label={`Daily news score of ${sym}`} format={(v) => num(v, 0)}
                            rows={s.scores.map((x) => ({ date: x.date, up: Math.max(0, x.score), down: Math.max(0, -x.score),
                                                         note: `score ${x.score > 0 ? "+" : ""}${x.score}, confidence ${num(x.confidence)}, ${x.articles} articles` }))} />
              {s.ideas.length > 0 && (
                <p className="small" style={{ marginBottom: 0 }}>Desk ideas: {s.ideas.map((i) => `${i.date} ${i.kind} (${i.side})`).join(", ")}</p>
              )}
            </div>
          )}
          {s.signals.length > 0 && (
            <div className="card">
              <h2>Quant's signals</h2>
              <p className="small" style={{ margin: 0 }}>{s.signals.length} in the last {DAYS} days, {s.signals.filter((x) => x.entered).length} entered:
                {" "}{s.signals.slice(-12).map((x) => `${x.date.slice(5)} ${x.side ?? ""}${x.entered ? "" : " (skipped)"}`).join(", ")}</p>
            </div>
          )}
          {news && (
            <div className="card">
              <h2>Headlines, last 30 days</h2>
              {!heads.data ? <Loading error={heads.error} /> : !heads.data.headlines.length ? <div className="empty">No headlines.</div> : (
                <ul className="headlines">
                  {heads.data.headlines.slice(0, allNews ? undefined : 10).map((h, i) => (
                    <li key={i}><span className="muted small">{when(h.published_at)} · {h.source}</span><br />
                      {h.url ? <a href={h.url} target="_blank" rel="noopener noreferrer nofollow">{h.headline}</a> : h.headline}</li>
                  ))}
                  {!allNews && heads.data.headlines.length > 10 && (
                    <li><button className="linkish" onClick={() => setAllNews(true)}>Show all {heads.data.headlines.length}</button></li>
                  )}
                </ul>
              )}
            </div>
          )}
          <div className="card">
            <h2>Trades</h2>
            <DataTable rows={s.trades} empty={`No closed trades in ${sym}.`} columns={[
              { key: "date", label: "Exit date", render: (t) => t.date },
              { key: "bot", label: "Bot", render: (t) => <Bot bot={t.bot} /> },
              { key: "side", label: "Side", render: (t) => t.side },
              { key: "e", label: "Entry", num: true, render: (t) => price(t.entry_price ?? 0) },
              { key: "x", label: "Exit", num: true, render: (t) => price(t.exit_price ?? 0) },
              { key: "pnl", label: "P&L", num: true, render: (t) => <span className={signClass(t.pnl_usd)}>{signedMoney(t.pnl_usd)}</span> },
              { key: "r", label: "R", num: true, render: (t) => num(t.pnl_r) },
              { key: "d", label: "", render: (t) => <button className="linkish" onClick={() => setTrade(`${t.bot}-${t.id}`)}>details</button> },
            ]} />
          </div>
        </>
      )}
    </div>
  );
}
