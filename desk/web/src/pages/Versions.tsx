import { useState } from "react";

import { type BotName, useApi, type Versions as VersionsAnswer } from "../api";
import { Bot, DataTable, Loading } from "../components/ui";
import { num, pct, signClass, signedMoney, when } from "../format";

const KIND: Record<string, string> = { config: "Config", strategy: "Strategy", watchlist: "Watchlist", prompt: "Prompt", memory: "Memory" };

export default function Versions() {
  const res = useApi<VersionsAnswer>("/api/versions");
  const [open, setOpen] = useState<string | null>(null);
  if (!res.data) return <Loading error={res.error} />;
  return (
    <div className="stack">
      <div>
        <h1>What changed</h1>
        <p className="lede">Each bot tunes its own config, strategies, watchlist, prompts and memory after its nightly review.
          Every change is listed here, with the results traded under each version.</p>
      </div>
      {(Object.keys(res.data) as BotName[]).map((bot) => {
        const v = res.data[bot];
        return (
          <div className="card" key={bot}>
            <h2><Bot bot={bot} /></h2>
            <h2 className="small muted" style={{ fontWeight: 600 }}>Results by version</h2>
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
            <h2 className="small muted" style={{ fontWeight: 600, marginTop: 16 }}>Changes, newest first</h2>
            <DataTable rows={v.changes} empty="No changes recorded yet." columns={[
              { key: "when", label: "When", render: (c) => when(c.effective_from) },
              { key: "kind", label: "What", render: (c) => `${KIND[c.kind] ?? c.kind}${c.name ? ` ${c.name}` : ""}` },
              { key: "v", label: "Version", render: (c) => c.removed ? <span className="muted">removed</span>
                : <span title={c.version ?? ""}>{(c.version ?? "").slice(0, 12) || "–"}</span> },
              { key: "why", label: "Recorded at", render: (c) => c.reason ?? "–" },
              ...(v.changes.some((c) => c.content !== undefined) ? [{
                key: "content", label: "", render: (c: VersionsAnswer[string]["changes"][number]) => c.content ? (
                  <button className="badge" onClick={() => setOpen(open === c.effective_from + c.kind ? null : c.effective_from + c.kind)}>
                    {open === c.effective_from + c.kind ? "hide" : "show"}
                  </button>) : null,
              }] : []),
            ]} />
            {v.changes.filter((c) => open === c.effective_from + c.kind && c.content).map((c) => (
              <pre key="content" className="card small" style={{ overflowX: "auto", whiteSpace: "pre-wrap", marginTop: 12 }}>{c.content}</pre>
            ))}
          </div>
        );
      })}
    </div>
  );
}
