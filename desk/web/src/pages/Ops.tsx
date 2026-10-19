import { type BotName, type Ops as OpsAnswer, useApi } from "../api";
import { Bot, DataTable, Loading, Status } from "../components/ui";
import { duration, when } from "../format";

export default function Ops() {
  const res = useApi<OpsAnswer>("/api/ops");
  if (!res.data) return <Loading error={res.error} />;
  return (
    <div className="stack">
      <div>
        <h1>Operations</h1>
        <p className="lede">Every scheduled job and every deploy on the Pi. A run is <em>lost</em> when it stopped without
          reporting back, for example after a reboot.</p>
      </div>
      {(Object.keys(res.data) as BotName[]).map((bot) => {
        const o = res.data[bot];
        const withErrors = o.runs.some((r) => r.error !== undefined);
        return (
          <div className="card" key={bot}>
            <h2><Bot bot={bot} /></h2>
            <DataTable rows={o.jobs} empty="No runs recorded yet." columns={[
              { key: "job", label: "Job", render: (j) => <strong>{j.job}</strong> },
              { key: "s", label: "Last run", render: (j) => <Status status={j.last_status} /> },
              { key: "when", label: "Started", render: (j) => when(j.last_started) },
              { key: "ok", label: "Last OK", render: (j) => when(j.last_ok) },
              { key: "f", label: "Failures, 7 days", num: true, render: (j) => j.failures_7d },
            ]} />
            <h2 className="small muted" style={{ fontWeight: 600, marginTop: 16 }}>Recent runs</h2>
            <DataTable rows={o.runs.slice(0, 30)} columns={[
              { key: "job", label: "Job", render: (r) => r.job },
              { key: "s", label: "Status", render: (r) => <Status status={r.status} /> },
              { key: "when", label: "Started", render: (r) => when(r.started_at) },
              { key: "d", label: "Took", num: true, render: (r) => duration(r.seconds) },
              { key: "sha", label: "Commit", render: (r) => <code className="small">{r.git_sha ?? "–"}</code> },
              ...(withErrors ? [{ key: "e", label: "Error", render: (r: OpsAnswer[string]["runs"][number]) =>
                r.error ? <code className="small" title={r.error}>{r.error.split("\n").slice(-1)[0].slice(0, 80)}</code> : null }] : []),
            ]} />
          </div>
        );
      })}
    </div>
  );
}
