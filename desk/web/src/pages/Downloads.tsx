import { type Downloads as DownloadsAnswer, useApi } from "../api";
import { Loading } from "../components/ui";
import { when } from "../format";

function size(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function Downloads() {
  const res = useApi<DownloadsAnswer>("/api/downloads");
  if (!res.data) return <Loading error={res.error} />;
  const d = res.data;
  const stems = [...new Set(d.files.map((f) => f.name.split(".")[0]))];
  return (
    <div className="stack">
      <div>
        <h1>Download the data</h1>
        <p className="lede">The same data this site shows, as files to analyse yourself: CSV for spreadsheets, Parquet for
          pandas, Polars or DuckDB. They are rebuilt every night{d.generated_at && <>; these were made {when(d.generated_at)}</>}.
          All of it is paper trading by two bots, with no real money involved.</p>
      </div>
      {!stems.length ? <div className="empty">No files yet: the first ones are made tonight.</div> : stems.map((stem) => {
        const files = d.files.filter((f) => f.name.startsWith(`${stem}.`));
        const first = files[0];
        return (
          <div className="card" key={stem}>
            <h2 style={{ marginBottom: 4 }}>{stem[0].toUpperCase() + stem.slice(1)}</h2>
            <p className="muted small" style={{ margin: "0 0 12px" }}>{first.about}. {first.rows.toLocaleString()} rows.</p>
            <div className="row">
              {files.map((f) => (
                <a key={f.name} className="btn" href={`/api/downloads/${f.name}`} download>
                  {f.format.toUpperCase()} <span className="muted small">{size(f.bytes)}</span></a>
              ))}
            </div>
            <details style={{ marginTop: 12 }}>
              <summary className="small">Columns</summary>
              <p className="small"><code>{first.columns.join(", ")}</code></p>
            </details>
          </div>
        );
      })}
    </div>
  );
}
