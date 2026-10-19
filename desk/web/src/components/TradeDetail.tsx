// One trade: its numbers, and (when prices are public) its price chart from the bots' saved bars with the entry,
// exit, stop and target marked.
import { useEffect, useMemo, useRef } from "react";

import { type Bars, qs, type TradeDetail as Detail, useApi } from "../api";
import { BOT_COLOR, BOT_LABEL, heldFor, money, num, pct, signClass, signedMoney, when } from "../format";
import { usePublic } from "../sections";
import { PriceChart, type PriceLine, type PriceMarker } from "./PriceChart";
import { Loading } from "./ui";

const price = (v: number) => money(v, true);

export function TradeDetail({ bot, id, onClose }: { bot: string; id: number; onClose: () => void }) {
  const prices = usePublic("prices");
  const card = useRef<HTMLDivElement>(null);
  useEffect(() => { card.current?.scrollIntoView({ block: "nearest" }); }, [bot, id]);
  const res = useApi<Detail>(`/api/trade${qs({ bot, id })}`);
  const d = res.data && res.data.trade.id === id && res.data.trade.bot === bot ? res.data : undefined;
  const w = d?.window;
  const bars = useApi<Bars>(`/api/bars${qs({ symbol: d?.trade.symbol, timeframe: w?.timeframe, start: w?.start, end: w?.end })}`,
                            prices && !!w);
  const t = d?.trade;
  const markers: PriceMarker[] = useMemo(() => {
    if (!t) return [];
    const long = t.side === "long";
    const out: PriceMarker[] = [];
    if (t.entry_time || t.entry_date) out.push({ time: (t.entry_time ?? t.entry_date) as string, above: !long, color: BOT_COLOR[t.bot],
                                                 text: `${long ? "buy" : "sell"} ${price(t.entry_price ?? 0)}` });
    if (t.exit_time) out.push({ time: t.exit_time, above: long, color: "var(--ink-2)", shape: "circle",
                                text: `exit ${price(t.exit_price ?? 0)}` });
    return out;
  }, [t]);
  const lines: PriceLine[] = useMemo(() => [
    ...(t?.stop_price ? [{ price: t.stop_price, label: "stop", color: "var(--neg-bar)" }] : []),
    ...(d?.target_price ? [{ price: d.target_price, label: "target", color: "var(--pos-bar)" }] : []),
  ], [t, d]);
  return (
    <div className="card stack" style={{ gap: 12 }} ref={card}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>{t ? <>{BOT_LABEL[t.bot]}: {t.side} {t.symbol}, {t.date}</> : "Trade"}</h2>
        <button className="btn" onClick={onClose}>Close</button>
      </div>
      {!t ? <Loading error={res.error} /> : (
        <>
          <div className="facts">
            <div><span>Result</span><strong className={signClass(t.pnl_usd)}>{signedMoney(t.pnl_usd)}</strong> <span className="muted">{num(t.pnl_r)}R{t.pnl_pct !== null && `, ${pct(t.pnl_pct)}`}</span></div>
            <div><span>Entry</span>{price(t.entry_price ?? 0)} <span className="muted">{when(t.entry_time ?? t.entry_date)}</span></div>
            <div><span>Exit</span>{price(t.exit_price ?? 0)} <span className="muted">{when(t.exit_time)} · {t.exit_reason ?? "–"}</span></div>
            <div><span>Stop / target</span>{t.stop_price ? price(t.stop_price) : "–"} / {d?.target_price ? price(d.target_price) : "–"}</div>
            <div><span>Size</span>{t.qty} shares · held {t.hold_days ? `${t.hold_days} days` : heldFor(t.entry_time, t.exit_time)}</div>
            <div><span>Version</span>{t.strategy} v{t.strategy_version} · config {t.config_version}
              {t.score !== undefined && t.score !== null && <> · score {t.score > 0 ? "+" : ""}{t.score}</>}</div>
          </div>
          {!prices ? <div className="muted small">The price chart is not public.</div>
            : bars.data ? (
              <PriceChart bars={bars.data.bars} markers={markers} lines={lines} format={price}
                          label={`${t.symbol} price around the trade, with its entry, exit, stop and target`} />
            ) : <Loading error={bars.error} />}
          {prices && <div className="muted small">{w?.timeframe === "1Min" ? "Minute bars of the session." : "Daily bars around the trade."}
            {" "}Dashed lines: the stop (red) and the target (blue).</div>}
        </>
      )}
    </div>
  );
}
