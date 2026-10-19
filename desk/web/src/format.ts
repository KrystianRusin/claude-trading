import type { BotName } from "./api";

export const BOT_LABEL: Record<BotName, string> = { quant: "Quant", sentiment: "Sentiment" };
export const BOT_ABOUT: Record<BotName, string> = {
  quant: "Intraday opening-range strategy, flat by the close",
  sentiment: "News-sentiment swing trades held for days",
};
export const BOT_COLOR: Record<BotName, string> = { quant: "var(--quant)", sentiment: "var(--sentiment)" };

const usd0 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const usd2 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function money(v: number | null | undefined, cents = false) {
  if (v === null || v === undefined) return "–";
  return (cents ? usd2 : usd0).format(v);
}

export function signedMoney(v: number | null | undefined) {
  if (v === null || v === undefined) return "–";
  return (v > 0 ? "+" : v < 0 ? "−" : "") + usd0.format(Math.abs(v));
}

export function pct(v: number | null | undefined, digits = 2, signed = true) {
  if (v === null || v === undefined) return "–";
  const s = Math.abs(v).toFixed(digits) + "%";
  return signed ? (v > 0 ? "+" : v < 0 ? "−" : "") + s : s;
}

export function num(v: number | null | undefined, digits = 2) {
  if (v === null || v === undefined) return "–";
  return v.toFixed(digits);
}

export function signClass(v: number | null | undefined) {
  if (!v) return "";
  return v > 0 ? "up" : "down";
}

export function when(ts: string | null | undefined) {
  if (!ts) return "–";
  if (ts.length === 10) return ts;
  const d = new Date(ts);
  return isNaN(d.getTime()) ? ts : d.toLocaleString("en-CA", { timeZone: "America/New_York", month: "short", day: "numeric",
                                                               hour: "2-digit", minute: "2-digit", hour12: false }) + " ET";
}

export function heldFor(entry: string | null, exit: string | null) {
  if (!entry || !exit) return "–";
  const s = (new Date(exit).getTime() - new Date(entry).getTime()) / 1000;
  return isNaN(s) ? "–" : duration(Math.round(s));
}

export function duration(seconds: number | null | undefined) {
  if (seconds === null || seconds === undefined) return "–";
  if (seconds < 90) return `${seconds}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}
