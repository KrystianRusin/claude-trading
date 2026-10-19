// A small Markdown reader for the bots' journals: headings, lists, paragraphs, **bold**, _italic_ and `code`.
// It builds React elements from the text and never injects HTML.
import type { ReactNode } from "react";

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|_[^_]+_|\*[^*]+\*)/g;
  let last = 0, m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    if (t.startsWith("**")) out.push(<strong key={m.index}>{t.slice(2, -2)}</strong>);
    else if (t.startsWith("`")) out.push(<code key={m.index}>{t.slice(1, -1)}</code>);
    else out.push(<em key={m.index}>{t.slice(1, -1)}</em>);
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let para: string[] = [];
  const flush = () => {
    if (para.length) blocks.push(<p key={blocks.length}>{inline(para.join(" "))}</p>);
    para = [];
    if (list) {
      const items = list.items.map((it, i) => <li key={i}>{inline(it)}</li>);
      blocks.push(list.ordered ? <ol key={blocks.length}>{items}</ol> : <ul key={blocks.length}>{items}</ul>);
    }
    list = null;
  };
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (heading) {
      flush();
      const level = Math.min(heading[1].length + 1, 4);
      const H = `h${level}` as "h2" | "h3" | "h4";
      blocks.push(<H key={blocks.length}>{inline(heading[2])}</H>);
    } else if (bullet || numbered) {
      if (para.length) { const p = para; para = []; blocks.push(<p key={blocks.length}>{inline(p.join(" "))}</p>); }
      const ordered = !bullet;
      if (list && list.ordered !== ordered) flush();
      list = list ?? { ordered, items: [] };
      list.items.push((bullet ?? numbered)![1]);
    } else if (!line.trim()) {
      flush();
    } else if (list && /^\s{2,}/.test(raw)) {
      list.items[list.items.length - 1] += " " + line.trim();
    } else {
      if (list) flush();
      para.push(line.trim());
    }
  }
  flush();
  return <div className="prose">{blocks}</div>;
}

// A unified diff, with added and removed lines marked by colour and by their +/- sign.
export function DiffView({ diff }: { diff: string }) {
  if (!diff) return <div className="empty">No difference in the text.</div>;
  return (
    <pre className="diff">
      {diff.split("\n").map((l, i) => {
        const kind = l.startsWith("+++") || l.startsWith("---") ? "head" : l.startsWith("@@") ? "hunk"
          : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : "";
        return <span key={i} className={kind}>{l || " "}{"\n"}</span>;
      })}
    </pre>
  );
}
