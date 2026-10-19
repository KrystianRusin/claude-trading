// Which sections the owner made public (from /api/meta), for components deep in a page, and a link to a
// symbol's page that is plain text when the symbol page is hidden.
import { createContext, useContext } from "react";
import { Link } from "react-router-dom";

export const SectionsContext = createContext<string[]>([]);

export function usePublic(section: string) {
  return useContext(SectionsContext).includes(section);
}

export function SymbolLink({ symbol, bold = true }: { symbol: string; bold?: boolean }) {
  const on = usePublic("symbols");
  const text = bold ? <strong>{symbol}</strong> : symbol;
  return on ? <Link className="sym" to={`/symbol/${encodeURIComponent(symbol)}`}>{text}</Link> : text;
}
