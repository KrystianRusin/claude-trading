import { Link } from "react-router-dom";

import { type Agent as AgentAnswer, type AgentDay, type BotName, qs, useApi } from "../api";
import { STATUS_LABEL } from "../components/Ladder";
import { Markdown } from "../components/Markdown";
import { Loading, Segmented, Status } from "../components/ui";
import { duration } from "../format";
import { usePublic } from "../sections";
import { useParam, useSetParams } from "../url";

const BOTS = ["quant", "sentiment"] as const;
const KIND: Record<string, string> = { config: "config", strategy: "strategy", watchlist: "watchlist", prompt: "prompt", memory: "memory" };

function Day({ d, bot }: { d: AgentDay; bot: BotName }) {
  const journal = usePublic("journal");
  return (
    <div className="day-card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>{d.date}</strong>
        <div className="row" style={{ gap: 12 }}>
          {d.reviews.map((r, i) => <span key={i} className="small row" style={{ gap: 4 }}>{r.job}: <Status status={r.status} /> {duration(r.seconds)}</span>)}
          {d.guard && (
            <span className="small"><span className="status"><span className={`dot ${d.guard.passed ? "ok" : "failed"}`} />
              Guard {d.guard.passed ? "passed" : `rejected the changes (${d.guard.problems} ${d.guard.problems === 1 ? "problem" : "problems"})`}</span></span>
          )}
        </div>
      </div>
      {(d.changes.length > 0 || d.memory.length > 0 || d.journal) && (
        <div className="chipline">
          {d.changes.map((c, i) => <span key={i} className="badge">{c.removed ? "removed " : "changed "}{KIND[c.kind] ?? c.kind}{c.name ? ` ${c.name}` : ""}</span>)}
          {d.memory.map((m, i) => (
            <span key={`m${i}`} className="badge" title={m.title ?? ""}>
              {m.id}: {m.previous ? `${STATUS_LABEL[m.previous]} → ${STATUS_LABEL[m.status]}` : `new ${STATUS_LABEL[m.status].toLowerCase()}`}</span>
          ))}
          {d.journal && (journal ? <Link className="badge" to={`/notebook?bot=${bot}&day=${d.date}`}>journal</Link>
            : <span className="badge">wrote a journal entry</span>)}
        </div>
      )}
      {d.changelog && <div style={{ marginTop: 8 }}><Markdown text={d.changelog} /></div>}
    </div>
  );
}

export default function Agent() {
  const [bot] = useParam<BotName>("bot", "quant", BOTS);
  const set = useSetParams();
  const res = useApi<AgentAnswer>(`/api/agent${qs({ bot })}`);
  const days = res.data?.bot === bot ? res.data.days : undefined;
  return (
    <div className="stack">
      <div>
        <h1>Agent activity</h1>
        <p className="lede">What each bot did after its sessions over the last 30 days: its review, what it changed, how its
          memory moved, and for Quant whether the automated guard accepted the changes. The guard checks every change against
          the rules and reverts the review if any fails.</p>
      </div>
      <div className="filters">
        <Segmented label="Bot" value={bot} onChange={(b) => set({ bot: b === "quant" ? null : b })}
                   options={[{ value: "quant", label: "Quant" }, { value: "sentiment", label: "Sentiment" }]} />
      </div>
      {!days ? <Loading error={res.error} /> : !days.length ? <div className="empty">Nothing in the last 30 days.</div> : (
        <div className="timeline">{days.map((d) => <Day key={d.date} d={d} bot={bot} />)}</div>
      )}
    </div>
  );
}
