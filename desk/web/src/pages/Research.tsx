import { useMemo } from "react";
import { Link } from "react-router-dom";

import { type Research as ResearchAnswer, useApi } from "../api";
import { DivergingBars } from "../components/Bars";
import { DailyColumns } from "../components/DailyColumns";
import { type Series, TimeChart } from "../components/TimeChart";
import { DataTable, Loading } from "../components/ui";
import { num, pct, signClass, signedMoney, when } from "../format";
import { SymbolLink, usePublic } from "../sections";

const OUTCOME: Record<string, string> = { closed: "closed", open: "still open", "not traded": "not traded" };

function GroupTable({ groups, label }: { groups: ResearchAnswer["by_score"]; label: string }) {
  const rows = Object.entries(groups).map(([key, g]) => ({ key, ...g }));
  return (
    <DataTable rows={rows} empty="No trades yet." columns={[
      { key: "k", label, render: (r) => r.key },
      { key: "n", label: "Trades", num: true, render: (r) => num(r.n, 0) },
      { key: "w", label: "Win rate", num: true, render: (r) => pct(r.win_rate === null ? null : r.win_rate * 100, 0, false) },
      { key: "e", label: "Avg R", num: true, render: (r) => num(r.expectancy_r) },
      { key: "p", label: "P&L", num: true, render: (r) => <span className={signClass(r.pnl_usd)}>{signedMoney(r.pnl_usd)}</span> },
    ]} />
  );
}

export default function Research() {
  const res = useApi<ResearchAnswer>("/api/research");
  const memory = usePublic("memory");
  const news: Series[] = useMemo(() => [{ id: "news", label: "Articles", color: "var(--sentiment)",
    points: (res.data?.news ?? []).map((n) => ({ time: n.date, value: n.articles })) }], [res.data]);
  if (!res.data) return <Loading error={res.error} />;
  const r = res.data;
  return (
    <div className="stack">
      <div>
        <h1>Sentiment research</h1>
        <p className="lede">The sentiment bot scores each symbol from −3 to +3 every morning from the news, and its desk
          proposes a few trade ideas. Here is how well those scores have called the next days' moves, how much news it reads,
          and what came of the ideas.{r.as_of && <> Calibration as of {when(r.as_of)}.</>}</p>
      </div>
      <div className="card">
        <h2 style={{ marginBottom: 4 }}>Do the scores call the move?</h2>
        <p className="muted small" style={{ margin: "0 0 12px" }}>
          Average return over the holding window after each score, across {r.scored_outcomes} scores whose window has
          finished. A useful score has positive returns for positive scores and negative for negative ones. Hit rate is how
          often the move went the score's way.</p>
        {!r.calibration.length ? <div className="empty">No scores with a finished window yet.</div> : (
          <div className="grid two">
            <DivergingBars label="Average forward return after each score, in percent" format={(v) => pct(v)}
                           rows={[...r.calibration].reverse().map((c) => ({ key: String(c.score), label: `${c.score > 0 ? "+" : ""}${c.score}`,
                                                                             value: c.mean_fwd_return_pct ?? 0,
                                                                             note: `${c.n} scores, hit rate ${pct(c.hit_rate === null ? null : c.hit_rate * 100, 0, false)}` }))} />
            <DataTable rows={r.calibration} columns={[
              { key: "s", label: "Score", render: (c) => `${c.score > 0 ? "+" : ""}${c.score}` },
              { key: "n", label: "Scores", num: true, render: (c) => c.n },
              { key: "f", label: "Avg return", num: true, render: (c) => <span className={signClass(c.mean_fwd_return_pct)}>{pct(c.mean_fwd_return_pct)}</span> },
              { key: "h", label: "Hit rate", num: true, render: (c) => pct(c.hit_rate === null ? null : c.hit_rate * 100, 0, false) },
            ]} />
          </div>
        )}
      </div>
      <div className="grid two">
        <div className="card"><h2>Trades by score</h2><GroupTable groups={r.by_score} label="Score" /></div>
        <div className="card"><h2>Trades by confidence</h2><GroupTable groups={r.by_confidence} label="Confidence" /></div>
      </div>
      <div className="grid two">
        <div className="card">
          <h2>News read per day</h2>
          <TimeChart series={news} format={(v) => num(v, 0)} height={200} label="Number of news articles the bot read each day" />
        </div>
        <div className="card">
          <h2>Bullish and bearish calls per day</h2>
          <DailyColumns label="Number of bullish (up) and bearish (down) scores each day"
                        rows={r.leaning.map((l) => ({ date: l.date, up: l.bullish, down: l.bearish,
                                                      note: `${l.bullish} bullish, ${l.bearish} bearish, ${l.neutral} neutral` }))} />
        </div>
      </div>
      <div className="card">
        <h2 style={{ marginBottom: 4 }}>Desk ideas and what came of them</h2>
        <p className="muted small" style={{ margin: "0 0 12px" }}>Themes, pairs and momentum ideas from the morning desk. An idea
          is traded only if it passes the same risk rules as the scores.</p>
        <DataTable rows={r.idea_kinds} empty="No desk ideas yet." columns={[
          { key: "k", label: "Kind", render: (k) => <strong>{k.kind}</strong> },
          { key: "i", label: "Ideas", num: true, render: (k) => k.ideas },
          { key: "t", label: "Traded", num: true, render: (k) => k.traded },
          { key: "c", label: "Closed", num: true, render: (k) => k.closed },
          { key: "r", label: "Avg R", num: true, render: (k) => num(k.avg_r) },
        ]} />
        <h3 className="sub" style={{ marginTop: 16 }}>Recent ideas</h3>
        <DataTable rows={r.ideas.slice(0, 40)} columns={[
          { key: "d", label: "Day", render: (i) => i.date },
          { key: "k", label: "Kind", render: (i) => i.kind },
          { key: "l", label: "Legs", render: (i) => <>{i.legs.map((l, n) => <span key={n}>{n > 0 && ", "}{l.side} <SymbolLink symbol={l.symbol} /></span>)}</> },
          { key: "c", label: "Conviction", num: true, render: (i) => num(i.conviction, 0) },
          { key: "o", label: "Outcome", render: (i) => OUTCOME[i.outcome] ?? i.outcome },
          { key: "r", label: "R", num: true, render: (i) => i.pnl_r === null ? "–" : <span className={signClass(i.pnl_r)}>{num(i.pnl_r)}</span> },
        ]} />
      </div>
      <div className="card">
        <h2 style={{ marginBottom: 4 }}>Attention and discovery</h2>
        <p className="muted small" style={{ margin: "0 0 12px" }}>
          How unusual the interest in each stock is, from trading volume, news, Wikipedia page views and Reddit mentions,
          each against the stock's own normal. Breadth is how many of the four are unusually high. The discovery list is
          smaller stocks outside the core universe with a surge in at least two of them, including volume or news.
          {r.attention.collect_only && " It is collected and measured only; nothing trades it yet."}</p>
        {!r.attention.discovery_date ? <div className="empty">No discovery list yet: collection starts the morning after the
          first full-market volume snapshot.</div> : (
          <>
            <h3 className="sub">Discovered on {r.attention.discovery_date}</h3>
            <DataTable rows={r.attention.discovery} empty="Nothing passed the filters that day." columns={[
              { key: "s", label: "Symbol", render: (n) => <SymbolLink symbol={n.symbol} /> },
              { key: "r", label: "Why", render: (n) => <span className="small">{n.reasons.join(", ")}</span> },
              { key: "c", label: "Close", num: true, render: (n) => n.close === null ? "–" : `$${num(n.close)}` },
              { key: "d", label: "Day's move", num: true, render: (n) => <span className={signClass(n.ret_1d)}>{pct(n.ret_1d, 1)}</span> },
              { key: "v", label: "Volume vs normal", num: true, render: (n) => n.rvol === null ? "–" : `${num(n.rvol, 1)}×` },
            ]} />
          </>
        )}
        {r.attention.breadth.length > 0 && (
          <>
            <h3 className="sub" style={{ marginTop: 16 }}>Returns after attention, by breadth</h3>
            <DataTable rows={r.attention.breadth} columns={[
              { key: "b", label: "Sources elevated", render: (b) => b.breadth },
              { key: "n", label: "Stock-days", num: true, render: (b) => b.n },
              { key: "1", label: "Next day", num: true, render: (b) => <span className={signClass(b.d1)}>{pct(b.d1)}</span> },
              { key: "3", label: "3 days", num: true, render: (b) => <span className={signClass(b.d3)}>{pct(b.d3)}</span> },
              { key: "5", label: "5 days", num: true, render: (b) => <span className={signClass(b.d5)}>{pct(b.d5)}</span> },
              { key: "u", label: "Up next day", num: true, render: (b) => pct(b.up_rate_1d === null ? null : b.up_rate_1d * 100, 0, false) },
            ]} />
            <div className="muted small">Average return from the next session's open. Paper results; small samples say little.</div>
          </>
        )}
        {r.attention.discovery_history.length > 0 && (
          <>
            <h3 className="sub" style={{ marginTop: 16 }}>How past lists did</h3>
            <DataTable rows={r.attention.discovery_history.slice(0, 20)} columns={[
              { key: "d", label: "List", render: (h) => h.date },
              { key: "n", label: "Names", num: true, render: (h) => h.names },
              { key: "1", label: "Next day", num: true, render: (h) => <span className={signClass(h.d1)}>{pct(h.d1)}</span> },
              { key: "3", label: "3 days", num: true, render: (h) => <span className={signClass(h.d3)}>{pct(h.d3)}</span> },
              { key: "5", label: "5 days", num: true, render: (h) => <span className={signClass(h.d5)}>{pct(h.d5)}</span> },
            ]} />
          </>
        )}
      </div>
      {memory && <p className="muted small">The bot's hypotheses and how they moved up the ladder are in the <Link to="/notebook?bot=sentiment">notebook</Link>.</p>}
    </div>
  );
}
