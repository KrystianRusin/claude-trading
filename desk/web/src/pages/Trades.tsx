import { useSearchParams } from "react-router-dom";

import { type BotName, qs, type TradesAnswer, useApi } from "../api";
import { Histogram } from "../components/Histogram";
import { TradeDetail } from "../components/TradeDetail";
import { Bot, DataTable, Loading, Tile } from "../components/ui";
import { heldFor, money, num, pct, signClass, signedMoney } from "../format";
import { SymbolLink } from "../sections";

const PAGE = 50;
const FILTERS = ["bot", "symbol", "strategy", "exit_reason", "side", "start", "end"] as const;
const REASONS: Record<string, string> = {
  tp: "target", sl: "stop", time: "time", halt: "halt", carryover: "carryover", flip: "score flip",
  converged: "pair converged", diverged: "pair diverged", pair_leg: "pair leg", loss_limit: "loss limit", unknown: "unknown",
};

type Options = { symbol: string[]; strategy: string[]; exit_reason: string[]; side: string[]; bots: string[] };

export default function Trades() {
  const [params, setParams] = useSearchParams();
  const filters = Object.fromEntries(FILTERS.map((k) => [k, params.get(k) ?? ""])) as Record<(typeof FILTERS)[number], string>;
  const offset = Number(params.get("offset") ?? 0);
  const res = useApi<TradesAnswer>(`/api/trades${qs({ ...filters, limit: PAGE, offset })}`);
  const opts = useApi<Options>("/api/trades/options");
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v); else next.delete(k);
    next.delete("offset");
    setParams(next, { replace: true });
  };
  const setTrade = (v: string) => {  // opening a trade keeps the page
    const next = new URLSearchParams(params);
    if (v) next.set("trade", v); else next.delete("trade");
    setParams(next, { replace: true });
  };
  const page = (o: number) => { const next = new URLSearchParams(params); next.set("offset", String(o)); setParams(next); };
  const select = (k: keyof Options & (typeof FILTERS)[number], label: string, show = (v: string) => v) => (
    <select aria-label={label} value={filters[k]} onChange={(e) => set(k, e.target.value)}>
      <option value="">{label}: all</option>
      {(opts.data?.[k] ?? []).map((v) => <option key={v} value={v}>{show(v)}</option>)}
    </select>
  );
  const s = res.data?.stats;
  const picked = (params.get("trade") ?? "").match(/^(quant|sentiment)-(\d+)$/);
  return (
    <div className="stack">
      <div>
        <h1>Trades</h1>
        <p className="lede">Every closed trade from both bots. R is the result in units of the risk taken: −1R means the
          stop was hit, +2R a win twice the size of that risk.</p>
      </div>
      <div className="filters">
        <select aria-label="Bot" value={filters.bot} onChange={(e) => set("bot", e.target.value)}>
          <option value="">Both bots</option><option value="quant">Quant</option><option value="sentiment">Sentiment</option>
        </select>
        {select("symbol", "Symbol")}
        {select("strategy", "Strategy")}
        {select("exit_reason", "Exit", (v) => REASONS[v] ?? v)}
        {select("side", "Side")}
        <label className="small muted">from <input type="date" value={filters.start} onChange={(e) => set("start", e.target.value)} /></label>
        <label className="small muted">to <input type="date" value={filters.end} onChange={(e) => set("end", e.target.value)} /></label>
      </div>
      {!res.data || !s ? <Loading error={res.error} /> : (
        <>
          <div className="grid tiles">
            <Tile label="Trades" value={s.trades} delta={`${s.wins} won, ${s.losses} lost`} deltaClass="muted" />
            <Tile label="Win rate" value={pct(s.win_rate === null ? null : s.win_rate * 100, 0, false)} />
            <Tile label="Total P&L" value={<span className={signClass(s.total_pnl)}>{signedMoney(s.total_pnl)}</span>} />
            <Tile label="Per trade" value={signedMoney(s.expectancy_usd)} delta={`avg ${num(s.avg_r)}R`} deltaClass="muted" />
          </div>
          {picked && <TradeDetail bot={picked[1]} id={Number(picked[2])} onClose={() => setTrade("")} />}
          <div className="card">
            <h2>Results in R</h2>
            <Histogram bins={s.r_histogram} label="How many trades ended at each result in R" />
          </div>
          <div className="card">
            <DataTable rows={res.data.trades} empty="No trades match these filters." columns={[
              { key: "date", label: "Exit date", render: (t) => t.date },
              { key: "bot", label: "Bot", render: (t) => <Bot bot={t.bot as BotName} /> },
              { key: "symbol", label: "Symbol", render: (t) => <SymbolLink symbol={t.symbol} /> },
              { key: "side", label: "Side", render: (t) => t.side },
              { key: "entry", label: "Entry", num: true, render: (t) => money(t.entry_price, true) },
              { key: "exit", label: "Exit", num: true, render: (t) => money(t.exit_price, true) },
              { key: "why", label: "Exit reason", render: (t) => REASONS[t.exit_reason ?? ""] ?? t.exit_reason ?? "–" },
              { key: "pnl", label: "P&L", num: true, render: (t) => <span className={signClass(t.pnl_usd)}>{signedMoney(t.pnl_usd)}</span> },
              { key: "r", label: "R", num: true, render: (t) => num(t.pnl_r) },
              { key: "held", label: "Held", render: (t) => t.hold_days ? `${t.hold_days} days` : heldFor(t.entry_time, t.exit_time) },
              { key: "v", label: "Version", render: (t) => <span className="muted small">{t.strategy} v{t.strategy_version} · config {t.config_version}</span> },
              { key: "d", label: "", render: (t) => <button className="linkish" onClick={() => setTrade(`${t.bot}-${t.id}`)}>details</button> },
            ]} />
            <div className="pager">
              <button disabled={offset === 0} onClick={() => page(Math.max(0, offset - PAGE))}>Newer</button>
              <span className="muted small">{res.data.total ? `${offset + 1}–${Math.min(offset + PAGE, res.data.total)} of ${res.data.total}` : ""}</span>
              <button disabled={offset + PAGE >= res.data.total} onClick={() => page(offset + PAGE)}>Older</button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
