import { useEffect, useMemo, useRef, useState } from "react";

import { type BotName, qs, type Replay as ReplayAnswer, useApi } from "../api";
import { clock, minuteOf, ReplayChart } from "../components/ReplayChart";
import { DataTable, Loading, Segmented, Tile } from "../components/ui";
import { BOT_COLOR, money, num, pct, signClass, signedMoney } from "../format";
import { SymbolLink } from "../sections";
import { useParam, useSetParams } from "../url";

const BOTS = ["quant", "sentiment"] as const;
const SPEEDS = ["1", "4", "16"] as const;

export default function Replay() {
  const [bot] = useParam<BotName>("bot", "quant", BOTS);
  const set = useSetParams();
  const days = useApi<{ days: string[] }>(`/api/replay/days${qs({ bot })}`);
  const [dayParam] = useParam<string>("day", "");
  const day = days.data?.days.includes(dayParam) ? dayParam : days.data?.days[0] ?? "";
  const res = useApi<ReplayAnswer>(`/api/replay${qs({ bot, day })}`, !!day);
  const [at, setAt] = useParam<string>("t", "");
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>("4");
  const [playing, setPlaying] = useState(false);
  const r = res.data && res.data.date === day && res.data.bot === bot ? res.data : undefined;

  const points = useMemo(() => (r?.equity ?? []).filter((p) => p.equity !== null)
    .map((p) => ({ minute: minuteOf(p.ts), value: p.equity as number, ts: p.ts })), [r]);
  const fromParam = at ? points.findIndex((p) => clock(p.minute) >= at) : -1;
  const [cursor, setCursor] = useState(0);
  const loaded = useRef("");
  useEffect(() => {  // a new day starts at the time in the link, else at the end of the day
    const key = `${bot}/${day}/${points.length}`;
    if (points.length && loaded.current !== key) {
      loaded.current = key;
      setCursor(fromParam >= 0 ? fromParam : points.length - 1);
      setPlaying(false);
    }
  }, [bot, day, points.length, fromParam]);
  useEffect(() => {
    if (!playing) return;
    const id = window.setInterval(() => setCursor((c) => {
      if (c >= points.length - 1) { setPlaying(false); return c; }
      return Math.min(points.length - 1, c + Number(speed));
    }), 120);
    return () => window.clearInterval(id);
  }, [playing, speed, points.length]);
  const pause = (c = cursor) => { setPlaying(false); if (points[c]) setAt(clock(points[c].minute)); };

  const now = points[Math.min(cursor, points.length - 1)];
  const frame = useMemo(() => {
    if (!r || !now) return undefined;
    let found: ReplayAnswer["frames"][number] | undefined;
    for (const f of r.frames) { if (minuteOf(f.ts) <= now.minute) found = f; else break; }
    return found;
  }, [r, now]);
  const fillsSoFar = (r?.fills ?? []).filter((f) => now && minuteOf(f.ts) <= now.minute);
  const tradesSoFar = (r?.trades ?? []).filter((t) => now && t.exit_time && minuteOf(t.exit_time) <= now.minute);
  const base = r?.equity_start ?? null;
  const change = now && base ? now.value - base : null;

  return (
    <div className="stack">
      <div>
        <h1>Replay a day</h1>
        <p className="lede">Play back one session: the account's value at each snapshot, every fill as it happened, and what
          the bot held at each moment. Drag across the chart to move through the day.</p>
      </div>
      <div className="filters">
        <Segmented label="Bot" value={bot} onChange={(b) => set({ bot: b === "quant" ? null : b, day: null, t: null })}
                   options={[{ value: "quant", label: "Quant" }, { value: "sentiment", label: "Sentiment" }]} />
        <select aria-label="Day" value={day} onChange={(e) => set({ day: e.target.value, t: null })}>
          {(days.data?.days ?? []).map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
        <button className="btn" onClick={() => {
          if (playing) pause();
          else { if (cursor >= points.length - 1) setCursor(0); setPlaying(true); }
        }} disabled={!points.length}>{playing ? "Pause" : "Play"}</button>
        <Segmented label="Speed" value={speed} onChange={setSpeed}
                   options={SPEEDS.map((s) => ({ value: s, label: `${s}×` }))} />
      </div>
      {!days.data ? <Loading error={days.error} /> : !days.data.days.length ? (
        <div className="empty">No day to replay yet: the bot has not saved intraday snapshots.</div>
      ) : !r || !now ? <Loading error={res.error} /> : (
        <>
          <div className="grid tiles">
            <Tile label="Time" value={`${clock(now.minute)} ET`} delta={day} deltaClass="muted" />
            <Tile label="Equity" value={money(now.value)} />
            <Tile label="Since the open" value={<span className={signClass(change)}>{signedMoney(change)}</span>}
                  delta={change !== null && base ? pct((change / base) * 100) : undefined} deltaClass={signClass(change)} />
            <Tile label="Fills so far" value={r.positions_shown ? fillsSoFar.length : "–"}
                  delta={`${tradesSoFar.length} ${tradesSoFar.length === 1 ? "trade" : "trades"} closed`} deltaClass="muted" />
          </div>
          <div className="card">
            <ReplayChart points={points} cursor={cursor} color={BOT_COLOR[bot]} base={base} format={(v) => money(v)}
                         label={`${bot} equity through ${day}`}
                         fills={r.fills.map((f) => ({ minute: minuteOf(f.ts), entry: !f.leg && f.order_type !== "stop",
                                                     label: `${clock(minuteOf(f.ts))} ${f.side} ${f.qty} ${f.symbol} at ${money(f.price, true)}` }))}
                         onSeek={(i) => { setCursor(i); pause(i); }} />
            <input type="range" className="scrub" min={0} max={points.length - 1} value={cursor} aria-label="Time of day"
                   onChange={(e) => { const i = Number(e.target.value); setCursor(i); setPlaying(false); }}
                   onPointerUp={() => pause()} onKeyUp={() => pause()} />
            <div className="muted small">Filled dots are entries, hollow dots exits. The dashed line is the equity at the open.</div>
          </div>
          <div className="grid two">
            <div className="card">
              <h2>Holding at {clock(now.minute)}</h2>
              {!r.positions_shown ? (
                <div className="empty">Positions and fills for today are shown once the day is over.</div>
              ) : (
                <DataTable rows={frame?.positions ?? []} empty="Nothing held at this moment." columns={[
                  { key: "s", label: "Symbol", render: (p) => <SymbolLink symbol={p.symbol} /> },
                  { key: "q", label: "Qty", num: true, render: (p) => p.qty },
                  { key: "p", label: "Price", num: true, render: (p) => money(p.price, true) },
                  { key: "u", label: "Unrealized", num: true, render: (p) => <span className={signClass(p.unrealized_pnl)}>{signedMoney(p.unrealized_pnl)}</span> },
                ]} />
              )}
            </div>
            <div className="card">
              <h2>Fills and closed trades</h2>
              {!r.positions_shown ? <div className="empty">Shown once the day is over.</div> : (
                <ul className="feed">
                  {[...fillsSoFar].reverse().map((f, i) => (
                    <li key={`f${i}`}><span className="muted">{clock(minuteOf(f.ts))}</span>{" "}
                      {f.side === "buy" ? "Bought" : "Sold"} {f.qty} <strong>{f.symbol}</strong> at {money(f.price, true)}
                      {f.leg && <span className="badge">{f.order_type === "stop" ? "stop" : f.order_type === "limit" ? "target" : "exit"}</span>}
                    </li>
                  ))}
                  {!fillsSoFar.length && <li className="muted">No fills yet at this time.</li>}
                </ul>
              )}
              {tradesSoFar.length > 0 && (
                <>
                  <h3 className="sub" style={{ marginTop: 12 }}>Closed so far</h3>
                  <ul className="feed">
                    {tradesSoFar.map((t, i) => (
                      <li key={i}><strong>{t.symbol}</strong> {t.side}: <span className={signClass(t.pnl_usd)}>{signedMoney(t.pnl_usd)}</span>
                        {t.pnl_r !== null && <span className="muted"> ({num(t.pnl_r)}R)</span>}</li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
