// The API's answers (desk/views.py) and the hooks that poll them. Every query refreshes every
// POLL_MS and keeps showing the previous answer while it reloads.
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import type { components } from "./schema";

export const POLL_MS = 30_000;

// The answers' types are generated from the API's OpenAPI schema (scripts/gen_types.sh writes schema.d.ts);
// these are short names for them.
type S = components["schemas"];
export type BotName = S["MetaBot"]["name"];
export type Meta = S["Meta"];
export type EquityBook = S["EquityBook"];
export type Equity = S["Equity"];
export type Benchmark = S["Benchmark"];
export type Trade = S["Trade"];
export type TradeStats = S["TradeStats"];
export type TradesAnswer = S["Trades"];
export type TradeOptions = S["TradeOptions"];
export type TradeDetail = S["TradeDetail"];
export type SymbolRow = S["SymbolRow"];
export type Symbols = S["Symbols"];
export type Hot = S["Hot"];
export type KnownSymbols = S["KnownSymbols"];
export type SymbolPage = S["SymbolPage"];
export type Bars = S["Bars"];
export type Headlines = S["Headlines"];
export type ReturnStats = S["ReturnStats"];
export type Compare = S["Compare"];
export type Disagreements = S["Disagreements"];
export type Versions = Record<string, S["BotVersions"]>;
export type Change = S["Change"];
export type WindowStats = S["WindowStats"];
export type Impact = S["Impact"];
export type Diff = S["Diff"];
export type Holding = S["Holding"];
export type HoldingsBook = S["HoldingsBook"];
export type Holdings = S["Holdings"];
export type Replay = S["Replay"];
export type MemoryStatus = S["MemoryEntry"]["status"];
export type MemoryEntry = S["MemoryEntry"];
export type Memory = S["Memory"];
export type Journal = S["Journal"];
export type Research = S["Research"];
export type Agent = S["Agent"];
export type AgentDay = S["AgentDay"];
export type Run = S["Run"];
export type Ops = Record<string, S["BotOps"]>;
export type Downloads = S["Downloads"];

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(r.status === 429 ? "Too many requests; slowing down." : `${r.status} ${r.statusText}`);
  return r.json() as Promise<T>;
}

export function useApi<T>(path: string, enabled = true) {
  return useQuery<T>({
    queryKey: [path], queryFn: () => get<T>(path), enabled, refetchInterval: POLL_MS,
    placeholderData: keepPreviousData, staleTime: 15_000, retry: 1,
  });
}

export function qs(params: Record<string, string | number | undefined | null>) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}
