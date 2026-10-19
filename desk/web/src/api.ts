// The API's answers (desk/views.py) and the hooks that poll them. Every query refreshes every
// POLL_MS and keeps showing the previous answer while it reloads.
import { keepPreviousData, useQuery } from "@tanstack/react-query";

export const POLL_MS = 30_000;
export type BotName = "quant" | "sentiment";

export interface Meta {
  bots: { name: BotName; label: string; books: Record<string, "ok" | "missing" | "unreadable"> }[];
  books: string[];
  sections: string[];
}

export interface EquityBook {
  bot: BotName;
  book: string;
  daily: { date: string; equity: number | null; pnl: number | null; trades: number | null; halted: boolean }[];
  intraday: { date: string; points: { ts: string; equity: number | null }[] };
  summary: {
    as_of: string; equity: number | null; start_equity: number | null; total_return_pct: number | null;
    today_pnl: number | null; today_return_pct: number | null; max_drawdown_pct: number; days: number;
    halted_today: boolean;
  };
}

export interface Trade {
  bot: BotName; book: string; date: string; symbol: string; side: string; qty: number | null;
  entry_date: string | null; entry_time: string | null; entry_price: number | null; exit_time: string | null;
  exit_price: number | null; exit_reason: string | null; pnl_usd: number | null; pnl_r: number | null;
  pnl_pct: number | null; hold_days: number | null; strategy: string | null; strategy_version: string | null;
  config_version: string | null; score?: number; confidence?: number;
}

export interface TradeStats {
  trades: number; wins: number; losses: number; win_rate: number | null; total_pnl: number;
  expectancy_usd: number | null; avg_r: number | null; r_histogram: { from: number; to: number; count: number }[];
}

export interface TradesAnswer { total: number; offset: number; trades: Trade[]; stats: TradeStats }

export interface SymbolRow { bot: BotName; symbol: string; trades: number; pnl: number; win_rate: number | null; avg_r: number | null }

export interface Hot {
  quant: { symbol: string; signals: number; entries: number; side: string | null; new_on_watchlist: boolean }[];
  sentiment?: { symbol: string; heat: number; articles: number; desk_ideas: number; direction: string }[];
  disagree?: { symbol: string; quant: string; sentiment: string }[];
}

export interface ReturnStats {
  days: number; total_return_pct?: number; mean_daily_pct?: number; volatility_annual_pct?: number | null;
  sharpe?: number | null; max_drawdown_pct?: number; worst_day_pct?: number; best_day_pct?: number;
}

export interface Compare {
  bots: Partial<Record<BotName, ReturnStats>>;
  curves: Partial<Record<BotName, { date: string; return_pct: number }[]>>;
  difference?: { days: number; first: string | null; last: string | null; mean_diff_pct?: number; low_pct?: number;
                 high_pct?: number; correlation?: number | null; verdict: string };
}

export interface Versions {
  [bot: string]: {
    changes: { kind: string; name: string; version: string | null; effective_from: string; reason: string | null;
               removed: boolean; content?: string | null }[];
    results: { strategy: string | null; strategy_version: string | null; config_version: string | null; first: string;
               last: string; trades: number; pnl: number; win_rate: number | null; avg_r: number | null }[];
  };
}

export interface Run { job: string; started_at: string; ended_at: string | null; status: string; exit_code: number | null;
                       seconds: number | null; git_sha: string | null; error?: string | null }
export interface Ops {
  [bot: string]: { runs: Run[]; jobs: { job: string; runs: number; failures_7d: number; last_ok: string | null;
                                       last_status: string; last_started: string }[] };
}

export interface Positions {
  books: { bot: BotName; book: string; as_of: string;
           positions: { symbol: string; qty: number; avg_entry_price: number | null; price: number | null;
                        market_value: number | null; unrealized_pnl: number | null }[] }[];
}

export interface Benchmark { symbol: string; daily: { date: string; close: number | null }[] }

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
