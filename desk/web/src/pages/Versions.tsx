import { useMemo } from "react";

import { type BotName, type Change, type Diff, type EquityBook, type Impact, qs, useApi,
         type Versions as VersionsAnswer } from "../api";
import { DiffView } from "../components/Markdown";
import { type Marker, type Series, TimeChart } from "../components/TimeChart";
import { DataTable, Loading, Segmented } from "../components/ui";
import { BOT_COLOR, BOT_LABEL, num, pct, signClass, signedMoney, when } from "../format";
import { useParam, useSetParams } from "../url";

const BOTS = ["quant", "sentiment"] as const;
const KINDS = ["config", "strategy", "watchlist", "prompt", "memory"] as const;
const KIND: Record<string, string> = { config: "Config", strategy: "Strategy", watchlist: "Watchlist", prompt: "Prompt", memory: "Memory" };
const DEFAULT_KINDS = "config,strategy,watchlist,prompt";
const VERDICT: Record<string, string> = {
  better: "Better since the change", worse: "Worse since the change", "no clear difference": "No clear difference yet",
  "too early to tell": "Too early to tell", "nothing comparable before": "Nothing comparable before it",
};

function what(c: Change) {
  return `${KIND[c.kind] ?? c.kind}${c.name ? ` ${c.name}` : ""}${c.removed ? " removed" : c.version ? ` ${/^\d+$/.test(c.version) ? "v" : ""}${c.version.slice(0, 12)}` : ""}`;
}

function StatsTable({ c }: { c: Change }) {
  const rows: { label: string; show: (w: Change["after"]) => React.ReactNode }[] = [
    { label: "Trades", show: (w) => w.trades },
    { label: "Win rate", show: (w) => pct(w.win_rate === null ? null : w.win_rate * 100, 0, false) },
    { label: "Avg R", show: (w) => num(w.avg_r) },
    { label: "P&L", show: (w) => <span className={signClass(w.pnl)}>{signedMoney(w.pnl)}</span> },
    { label: "Return", show: (w) => pct(w.return_pct) },
    { label: "Trading days", show: (w) => w.days },
  ];
  return (
    <div className="scroll">
      <table>
        <thead><tr><th></th><th className="num">Before</th><th className="num">After</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.label}><td>{r.label}</td><td className="num">{c.before ? r.show(c.before) : "–"}</td><td className="num">{r.show(c.after)}</td></tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function ChangeDetail({ bot, c, minTrades }: { bot: BotName; c: Change; minTrades: number }) {
  const [showDiff, setShowDiff] = useParam<string>("diff", "");
  const diff = useApi<Diff>(`/api/versions/diff${qs({ bot, id: c.id })}`, showDiff === "1" && !!c.has_diff);
  return (
    <div className="card stack" style={{ gap: 12 }}>
      <div>
        <div className="muted small">{when(c.effective_from)} · recorded at {c.reason ?? "–"}</div>
        <h2 style={{ margin: "2px 0 0" }}>{what(c)}</h2>
      </div>
      <div>
        <div className="verdict">{VERDICT[c.verdict] ?? c.verdict}</div>
        <p className="muted small" style={{ margin: "4px 0 0" }}>
          {c.verdict === "too early to tell"
            ? `${c.after.trades} of the ${minTrades} trades needed under this version so far.`
            : c.verdict === "nothing comparable before"
              ? `The version before it had fewer than ${minTrades} trades, or there was none.`
              : `Average R before and after, with a 95% range on the difference. Needs ${minTrades} trades on each side.`}
          {" "}Before is the previous version of the same thing; after runs until the next one{c.current ? " (it is still current)" : ""}.
          Other changes in the same period also count, so read this as a hint, not proof.
        </p>
      </div>
      <StatsTable c={c} />
      {c.has_diff && (
        <div>
          <button className="btn" onClick={() => setShowDiff(showDiff === "1" ? "" : "1")}>
            {showDiff === "1" ? "Hide what changed" : "Show what changed"}</button>
          {showDiff === "1" && (diff.data ? <DiffView diff={diff.data.diff} /> : <Loading error={diff.error} />)}
          {diff.data?.truncated && <div className="muted small">The diff is cut short.</div>}
        </div>
      )}
    </div>
  );
}

export default function Versions() {
  const [bot] = useParam<BotName>("bot", "quant", BOTS);
  const [kindsParam, setKinds] = useParam<string>("kinds", DEFAULT_KINDS);
  const [changeParam] = useParam<string>("change", "");
  const set = useSetParams();
  const kinds = kindsParam.split(",").filter((k) => (KINDS as readonly string[]).includes(k));
  const impact = useApi<Impact>(`/api/versions/impact${qs({ bot })}`);
  const eq = useApi<{ books: EquityBook[] }>("/api/equity");
  const res = useApi<VersionsAnswer>("/api/versions");
  const changes = useMemo(() => (impact.data?.bot === bot ? impact.data.changes : []).filter((c) => kinds.includes(c.kind)),
    [impact.data, bot, kinds]);
  const picked = changes.find((c) => String(c.id) === changeParam) ?? changes[0];
  const book = eq.data?.books.find((b) => b.bot === bot && b.book === "paper") ?? eq.data?.books.find((b) => b.bot === bot);
  const series: Series[] = useMemo(() => {
    const base = book?.summary.start_equity;
    return book && base ? [{ id: bot, label: BOT_LABEL[bot], color: BOT_COLOR[bot],
      points: book.daily.filter((d) => d.equity !== null).map((d) => ({ time: d.date, value: ((d.equity as number) / base - 1) * 100 })) }] : [];
  }, [book, bot]);
  const markers: Marker[] = useMemo(() => changes.map((c) => ({
    id: String(c.id), time: c.effective_from, text: (KIND[c.kind] ?? c.kind)[0], title: `${what(c)} · ${VERDICT[c.verdict] ?? c.verdict}`,
  })), [changes]);
  const toggle = (k: string) => {
    const next = kinds.includes(k) ? kinds.filter((x) => x !== k) : [...kinds, k];
    setKinds(KINDS.filter((x) => next.includes(x)).join(","));
  };
  const v = res.data?.[bot];
  return (
    <div className="stack">
      <div>
        <h1>What changed, and did it work?</h1>
        <p className="lede">Each bot tunes its own config, strategies, watchlist, prompts and memory after its nightly review.
          Every change is a marker on the return curve. Pick one to see the results before and after it.</p>
      </div>
      <div className="filters">
        <Segmented label="Bot" value={bot} onChange={(b) => set({ bot: b === "quant" ? null : b, change: null, diff: null })}
                   options={[{ value: "quant", label: "Quant" }, { value: "sentiment", label: "Sentiment" }]} />
        <div className="chips" role="group" aria-label="Kinds of change">
          {KINDS.map((k) => (
            <button key={k} className={kinds.includes(k) ? "on" : ""} aria-pressed={kinds.includes(k)} onClick={() => toggle(k)}>
              {KIND[k]}</button>
          ))}
        </div>
      </div>
      {!impact.data ? <Loading error={impact.error} /> : (
        <>
          <div className="card">
            <h2>Return since start, with each change</h2>
            <TimeChart series={series} format={(v) => pct(v)} label={`Return of ${BOT_LABEL[bot]} with its changes marked`}
                       markers={markers} selected={picked ? String(picked.id) : null}
                       onMarker={(id) => set({ change: id, diff: null })} />
            <div className="muted small">Markers: C config, S strategy, W watchlist, P prompt, M memory. A change made after the
              close shows on the next day. Click a marker to pick it.</div>
          </div>
          {picked ? <ChangeDetail key={picked.id} bot={bot} c={picked} minTrades={impact.data.min_trades} />
            : <div className="empty">No changes of these kinds yet.</div>}
          <div className="card">
            <h2>Every change, newest first</h2>
            <DataTable rows={changes} empty="No changes of these kinds yet." columns={[
              { key: "when", label: "When", render: (c) => when(c.effective_from) },
              { key: "what", label: "What", render: (c) => (
                <button className={`linkish${picked?.id === c.id ? " on" : ""}`} onClick={() => set({ change: String(c.id), diff: null })}>{what(c)}</button>) },
              { key: "n", label: "Trades after", num: true, render: (c) => c.after.trades },
              { key: "r", label: "Avg R before → after", num: true, render: (c) => `${num(c.before?.avg_r ?? null)} → ${num(c.after.avg_r)}` },
              { key: "v", label: "Verdict", render: (c) => VERDICT[c.verdict] ?? c.verdict },
            ]} />
          </div>
        </>
      )}
      {v && (
        <div className="card">
          <h2>Results by strategy and config version</h2>
          <DataTable rows={v.results} empty="No trades yet." initialSort={{ key: "first", desc: true }} columns={[
            { key: "s", label: "Strategy", render: (r) => `${r.strategy ?? "–"} v${r.strategy_version ?? "?"}`, sort: (r) => `${r.strategy}${r.strategy_version}` },
            { key: "c", label: "Config", render: (r) => r.config_version ?? "–", sort: (r) => Number(r.config_version) },
            { key: "first", label: "From", render: (r) => r.first, sort: (r) => r.first },
            { key: "last", label: "To", render: (r) => r.last },
            { key: "n", label: "Trades", num: true, render: (r) => r.trades, sort: (r) => r.trades },
            { key: "pnl", label: "P&L", num: true, render: (r) => <span className={signClass(r.pnl)}>{signedMoney(r.pnl)}</span>, sort: (r) => r.pnl },
            { key: "w", label: "Win rate", num: true, render: (r) => pct(r.win_rate === null ? null : r.win_rate * 100, 0, false), sort: (r) => r.win_rate },
            { key: "r", label: "Avg R", num: true, render: (r) => num(r.avg_r), sort: (r) => r.avg_r },
          ]} />
        </div>
      )}
    </div>
  );
}
