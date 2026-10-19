import { type BotName, type Journal, type Memory, type MemoryEntry, type MemoryStatus, qs, useApi } from "../api";
import { Ladder, STATUS_LABEL, StatusKey } from "../components/Ladder";
import { Markdown } from "../components/Markdown";
import { Loading, Segmented } from "../components/ui";
import { BOT_COLOR, when } from "../format";
import { useParam, useSetParams } from "../url";

const BOTS = ["quant", "sentiment"] as const;
const ORDER: MemoryStatus[] = ["confirmed", "supported", "hypothesis", "retired"];

function EntryCard({ e }: { e: MemoryEntry }) {
  return (
    <div className="entry">
      <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
        <strong>{e.id}: {e.title}</strong>
        <StatusKey status={e.status} />
      </div>
      <dl>
        {e.scope && <><dt>Scope</dt><dd>{e.scope}</dd></>}
        {e.query && <><dt>Tested by</dt><dd><code>{e.query}</code></dd></>}
        {e.evidence && <><dt>Evidence</dt><dd>{e.evidence}</dd></>}
        {e.implication && <><dt>Implication</dt><dd>{e.implication}</dd></>}
        {e.retired_reason && <><dt>Retired</dt><dd>{e.retired_reason}</dd></>}
        <dt>History</dt>
        <dd>{e.history.map((h) => `${STATUS_LABEL[h.status]} ${h.date}`).join(" → ") || "–"}</dd>
      </dl>
    </div>
  );
}

function MemoryCard({ bot }: { bot: BotName }) {
  const res = useApi<Memory>(`/api/memory${qs({ bot })}`);
  const [picked, setPicked] = useParam<string>("entry", "");
  if (!res.data || res.data.bot !== bot) return <div className="card"><h2>Memory</h2><Loading error={res.error} /></div>;
  const m = res.data;
  const start = m.counts[0]?.date;
  const pick = m.entries.find((e) => e.id === picked);
  return (
    <div className="card stack" style={{ gap: 12, ["--accent" as string]: BOT_COLOR[bot] }}>
      <div>
        <h2 style={{ marginBottom: 4 }}>Memory: lessons and hypotheses</h2>
        <p className="muted small" style={{ margin: 0 }}>
          A lesson starts as a hypothesis. It moves up to supported, then confirmed, only when trades or outcomes after
          the day it was written down back it up, and it is retired when they stop doing so. Nothing is deleted.
          {m.as_of && <> Last changed {when(m.as_of)}.</>}
        </p>
      </div>
      {!m.entries.length || !start ? <div className="empty">No memory entries yet.</div> : (
        <>
          <div className="row small">
            {ORDER.map((s) => <span key={s} className="muted">{STATUS_LABEL[s]}: <strong>{m.entries.filter((e) => e.status === s).length}</strong></span>)}
          </div>
          <Ladder entries={m.entries} start={start} end={m.as_of ?? start} color={BOT_COLOR[bot]}
                  picked={pick?.id ?? null} onPick={(id) => setPicked(id === picked ? "" : id)} />
          {pick ? <EntryCard e={pick} /> : <div className="muted small">Pick an entry to read it.</div>}
        </>
      )}
    </div>
  );
}

function JournalCard({ bot }: { bot: BotName }) {
  const [dayParam] = useParam<string>("day", "");
  const set = useSetParams();
  const res = useApi<Journal>(`/api/journal${qs({ bot, day: dayParam || undefined })}`);
  if (!res.data || res.data.bot !== bot) return <div className="card"><h2>Journal</h2><Loading error={res.error} /></div>;
  const j = res.data;
  const i = j.entry ? j.dates.indexOf(j.entry.date) : -1;
  return (
    <div className="card stack" style={{ gap: 12 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>Journal</h2>
        {j.dates.length > 0 && (
          <div className="row" style={{ gap: 6 }}>
            <button className="btn" disabled={i < 0 || i >= j.dates.length - 1} onClick={() => set({ day: j.dates[i + 1] })}>Older</button>
            <select aria-label="Journal day" value={j.entry?.date ?? ""} onChange={(e) => set({ day: e.target.value })}>
              {j.dates.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
            <button className="btn" disabled={i <= 0} onClick={() => set({ day: j.dates[i - 1] })}>Newer</button>
          </div>
        )}
      </div>
      <p className="muted small" style={{ margin: 0 }}>Written by the bot after each session, as it wrote it. Past entries are never
        edited.</p>
      {j.entry?.text ? <Markdown text={j.entry.text} /> : <div className="empty">No journal entries yet.</div>}
    </div>
  );
}

export default function Notebook({ sections }: { sections: string[] }) {
  const [bot] = useParam<BotName>("bot", "quant", BOTS);
  const set = useSetParams();
  return (
    <div className="stack">
      <div>
        <h1>The bots' notebook</h1>
        <p className="lede">After each session a bot writes a journal entry about the day and updates its memory: what it thinks
          it has learned, and how sure it is. This is how they learn between sessions.</p>
      </div>
      <div className="filters">
        <Segmented label="Bot" value={bot} onChange={(b) => set({ bot: b === "quant" ? null : b, day: null, entry: null })}
                   options={[{ value: "quant", label: "Quant" }, { value: "sentiment", label: "Sentiment" }]} />
      </div>
      {sections.includes("memory") && <MemoryCard bot={bot} />}
      {sections.includes("journal") && <JournalCard bot={bot} />}
    </div>
  );
}
