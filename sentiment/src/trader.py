"""Once-a-day trader: entries and exits on today's scores and desk ideas, the end-of-day reconcile, and replays.

    python src/trader.py run [--dry-run] [--force]      09:45: exits, then entries
    python src/trader.py close [--dry-run] [--force]    16:15: fills, stop-outs, marks, daily summary
    python src/trader.py replay --from DATE --to DATE   rerun the rules over saved score files and daily bars
    python src/trader.py positions [--dry-run]           current holdings at live prices (read-only)
    python src/trader.py snapshot [--dry-run] [--force]  equity, positions and order states into the database
                                                         (read-only at the broker; cron every 5 minutes)

A file named HALT in the repo root makes every job place no entries and close all positions.
"""
import argparse
import contextlib
import json
import sys
from datetime import date, datetime, time, timedelta

import pairs
import paths
import risk
import score
from broker import ET, FILLED, DuplicateOrderError, OTOUnavailableError, load_bars
from logger import STRATEGY, Book, notify, setup_logging, trade_row

FETCH_DAYS = 45


def _iso(ts):
    return ts.isoformat(timespec="seconds")


# ---- rules (pure) ----

def signal(entry, entry_cfg):
    """"long", "short" or None for one symbol's score entry."""
    s, conf = entry["score"], entry["confidence"]
    strong = abs(s) >= entry_cfg["min_score"]
    weak = abs(s) == 1 and entry_cfg["allow_score_1"] and conf >= entry_cfg["min_confidence_at_score_1"]
    if s == 0 or not (strong or weak):
        return None
    if s < 0 and not entry_cfg["allow_shorts"]:
        return None
    return "long" if s > 0 else "short"


def hold_days(entry_date, day, trading_days):
    """Trading days from the entry date (not counted) through `day`."""
    return sum(1 for d in trading_days if entry_date < d <= day)


def exit_reason(row, scores, held, exit_cfg):
    """"time", "flip" or None for an open position, given today's scores (None when there are none)."""
    if held >= exit_cfg["max_hold_days"]:
        return "time"
    entry = (scores or {}).get(row["symbol"])
    if exit_cfg["exit_on_score_flip"] and entry:
        if (entry["score"] > 0 and row["side"] == "short") or (entry["score"] < 0 and row["side"] == "long"):
            return "flip"
    return None


def candidates(record, entry_cfg):
    """Entry candidates from a score file: one per tradable news score and one per desk idea.

    Desk ideas pass the same thresholds as scores, with conviction in place of |score|.
    """
    out = []
    for sym, entry in record["scores"].items():
        side = signal(entry, entry_cfg)
        if side:
            out.append({"strategy": STRATEGY, "legs": [(sym, side)], "conviction": abs(entry["score"]),
                        "confidence": entry["confidence"],
                        "reason": f"score {entry['score']:+d} conf {entry['confidence']}"})
    for idea in (record.get("desk") or {}).get("ideas", []):
        legs = [(leg["symbol"], leg["side"]) for leg in idea["legs"]]
        if signal({"score": idea["conviction"], "confidence": idea["confidence"]}, entry_cfg) is None:
            continue
        if any(side == "short" for _, side in legs) and not entry_cfg["allow_shorts"]:
            continue
        out.append({"strategy": idea["kind"], "legs": legs, "conviction": idea["conviction"],
                    "confidence": idea["confidence"], "reason": f"{idea['kind']}: {idea['reason']}",
                    "pair": idea.get("pair")})
    return out


def rank_candidates(cands, skip):
    """Candidates touching no `skip` symbol, by conviction then confidence; news scores win ties."""
    keep = [c for c in cands if not any(sym in skip for sym, _ in c["legs"])]
    return sorted(keep, key=lambda c: (-c["conviction"], -c["confidence"], c["strategy"] != STRATEGY,
                                       c["legs"][0][0]))


def _leg_problem(sym, side, price, atr_value, flags):
    if price is None:
        return "no_price"
    if price < risk.MIN_PRICE:
        return "price_under_min"
    if not flags[sym][0]:
        return "not_tradable"
    if side == "short" and not risk.can_short(flags[sym]):
        return "not_easy_to_borrow"
    if atr_value is None:
        return "no_atr"
    return None


def plan_entries(cands, *, prices, atrs, flags, equity, book_values, orders_today, cfg):
    """(plans, skips): one plan per leg of each ranked candidate that fits sizing, stop and book limits.

    A candidate enters all its legs or none; the skip names the leg that failed.
    """
    plans, skips = [], []
    values, taken = list(book_values), set()
    for c in cands:
        legs, added = [], []
        multiple = cfg["pairs"]["stop_atr_multiple"] if c["strategy"] == "pair" else cfg["exit"]["stop_atr_multiple"]
        for sym, side in c["legs"]:
            price, a = prices.get(sym), atrs.get(sym)
            if sym in taken:
                reason = "duplicate"
            elif orders_today + len(plans) + len(legs) >= risk.MAX_ORDERS_PER_DAY:
                reason = "order_cap"
            else:
                reason = _leg_problem(sym, side, price, a, flags)
            if not reason:
                qty = risk.position_qty(equity, price, cfg["risk"]["position_pct_of_equity"])
                value = qty * price * (1 if side == "long" else -1)
                reason = "size_zero" if qty == 0 else risk.fits_book(values + added, value, equity, cfg["risk"])
            if reason:
                skips.append((sym, reason))
                break
            added.append(value)
            legs.append({"symbol": sym, "side": side, "qty": qty, "price": price,
                         "score": c["conviction"] * (1 if side == "long" else -1), "confidence": c["confidence"],
                         "stop_price": risk.stop_price(side, price, a, multiple), "strategy": c["strategy"],
                         "reason": c["reason"], "pair": c.get("pair")})
        else:
            values += added
            plans += legs
            taken |= {leg["symbol"] for leg in legs}
    return plans, skips


# ---- jobs ----

@contextlib.contextmanager
def job_lock(path):
    """Exclusive lock on `path` for the life of the job; raises if another job holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+")
    try:
        try:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        f.close()
        raise RuntimeError(f"another trader job holds {path}")
    try:
        yield
    finally:
        f.close()


class Trader:
    def __init__(self, broker, cfg, book, now, log, halt_path=None, scores_loader=score.load_scores):
        self.broker, self.cfg, self.book, self.now, self.log = broker, cfg, book, now, log
        self.today = now.date()
        self.halt_path = halt_path
        self.load_scores = scores_loader
        self._calendar = None
        self.rows = []

    # -- helpers

    def cid(self, symbol, kind):
        return f"st-{self.today:%Y%m%d}-{symbol}-{kind}"

    def calendar(self):
        if self._calendar is None:
            self._calendar = self.broker.trading_days(self.today - timedelta(days=60), self.today + timedelta(days=14))
        return self._calendar

    def event(self, event, symbol="", **fields):
        self.book.order_event(self.now, event, symbol, **fields)

    def seen(self, order):
        """Record an order's current state in the database (orders table); returns the order."""
        self.book.order_state(self.now, order)
        return order

    def get_order(self, order_id):
        return self.seen(self.broker.get_order(order_id))

    def open_orders(self):
        return [self.seen(o) for o in self.broker.open_orders()]

    def order_event(self, event, order, reason="", price=None):
        self.seen(order)
        self.event(event, order.symbol, side=order.side, qty=order.qty, order_type=order.order_type,
                   price=price if price is not None else (order.filled_avg_price or ""),
                   stop_price=order.stop_price or "", order_id=order.id, client_order_id=order.client_order_id,
                   status=order.status, reason=reason)

    def halted(self):
        return self.halt_path is not None and self.halt_path.exists()

    # -- reconcile

    def reconcile(self):
        """Match positions.csv with the account: record fills and closed trades, adopt unknown positions."""
        positions = self.broker.list_positions()
        rows = self.book.positions()
        kept = []
        for row in rows:
            pos = positions.get(row["symbol"])
            if row["status"] == "pending" and not pos:
                o = self.get_order(row["entry_order_id"])
                if o.is_open:
                    kept.append(row)
                    continue
                if o.status != FILLED:
                    self.order_event(f"entry_{o.status}", o, reason="entry not filled")
                    continue
                row.update(status="open", entry_price=o.filled_avg_price, qty=o.filled_qty,
                           entry_time=_iso(o.filled_at or self.now))
            if pos:
                if row["status"] == "pending":
                    o = self.get_order(row["entry_order_id"])
                    self.order_event("fill", o)
                    row.update(status="open", entry_time=_iso(o.filled_at or self.now))
                    if o.legs and not row["stop_order_id"]:
                        row["stop_order_id"] = o.legs[0].id
                row.update(qty=abs(pos.qty), entry_price=pos.avg_entry_price)
                kept.append(row)
            else:
                self._record_closed(row)
        known = {r["symbol"] for r in kept}
        stops = {o.symbol: o for o in self.open_orders() if o.order_type == "stop"}
        for sym, pos in positions.items():
            if sym not in known:
                self.log.warning("adopting %s position found in the account but not in positions.csv", sym)
                self.event("adopt", sym, qty=abs(pos.qty), side="long" if pos.qty > 0 else "short",
                           price=pos.avg_entry_price, reason="position not in positions.csv")
                stop = stops.get(sym)
                kept.append({"symbol": sym, "side": "long" if pos.qty > 0 else "short", "qty": abs(pos.qty),
                             "status": "open", "entry_date": self.today.isoformat(), "entry_time": _iso(self.now),
                             "entry_price": pos.avg_entry_price,
                             "stop_order_id": stop.id if stop else "", "stop_price": stop.stop_price if stop else "",
                             "config_version": self.cfg["version"]})
        self.rows = kept
        self.book.save_positions(kept)

    def _record_closed(self, row):
        exit_price, reason, exit_time = None, row.get("exit_reason") or "", self.now
        for oid, why in ((row.get("exit_order_id"), reason), (row.get("stop_order_id"), "sl")):
            if not oid or exit_price is not None:
                continue
            o = self.get_order(oid)
            if o.status == FILLED:
                exit_price, reason = o.filled_avg_price, why
                exit_time = o.filled_at or self.now
                self.order_event("fill", o, reason=why)
        if exit_price is None:
            reason = "unknown"
            self.log.warning("%s is no longer in the account and no exit fill was found", row["symbol"])
            with contextlib.suppress(Exception):
                exit_price = self.broker.latest_prices([row["symbol"]]).get(row["symbol"])
        held = hold_days(date.fromisoformat(row["entry_date"]), exit_time.date(), self.calendar())
        self.book.trade(trade_row(row, exit_time, exit_price, reason, held))

    def repair_stops(self):
        """Give every open position a live GTC stop."""
        for row in self.rows:
            if row["status"] != "open":
                continue
            if row.get("stop_order_id"):
                o = self.get_order(row["stop_order_id"])
                if o.is_open or o.status == FILLED:
                    continue
            if not row.get("stop_price"):
                row["stop_price"] = self._stop_for(row["symbol"], row["side"], float(row["entry_price"]))
                if row["stop_price"] is None:
                    self.log.warning("%s has no stop: not enough bars for the ATR", row["symbol"])
                    continue
            side = "sell" if row["side"] == "long" else "buy"
            cid = self.cid(row["symbol"], "stop")
            try:
                o = self.broker.submit_stop_order(row["symbol"], side, float(row["qty"]), float(row["stop_price"]), cid)
                self.order_event("submit", o, reason="protective stop")
            except DuplicateOrderError:
                o = self.broker.get_order_by_client_id(cid)
                if not o or not o.is_open:
                    self.log.warning("%s: stop %s was used today and is not open; no stop", row["symbol"], cid)
                    continue
            row["stop_order_id"] = o.id
        self.book.save_positions(self.rows)

    def _bars(self, symbols):
        start = datetime.combine(self.today - timedelta(days=FETCH_DAYS), time(0), ET)
        bars = self.broker.daily_bars(symbols, start, self.now)
        return {s: df[df.index < self.today] for s, df in bars.items()}

    def _stop_for(self, symbol, side, reference):
        a = risk.atr(self._bars([symbol]).get(symbol), self.cfg["exit"]["atr_days"])
        return None if a is None else risk.stop_price(side, reference, a, self.cfg["exit"]["stop_atr_multiple"])

    # -- actions

    def exit_position(self, row, reason):
        if row["status"] == "closing":
            return False
        if row["status"] == "pending":
            o = self.broker.cancel_order(row["entry_order_id"])
            self.order_event("cancel", o, reason=reason)
            return False
        if row.get("stop_order_id"):
            stop = self.broker.cancel_order(row["stop_order_id"])
            if stop.status == FILLED:
                return False
            self.order_event("cancel", stop, reason=reason)
        cid = self.cid(row["symbol"], "exit")
        try:
            o = self.broker.submit_market_order(row["symbol"], "sell" if row["side"] == "long" else "buy",
                                                float(row["qty"]), cid)
            self.order_event("submit", o, reason=reason)
        except DuplicateOrderError:
            o = self.broker.get_order_by_client_id(cid)
        row.update(status="closing", exit_reason=reason, exit_order_id=o.id)
        self.log.info("exit %s %s %s (%s)", row["symbol"], row["side"], row["qty"], reason)
        self.book.save_positions(self.rows)
        return True

    def close_all(self, reason):
        for row in self.rows:
            self.exit_position(row, reason)
        for o in self.open_orders():
            if o.order_type == "stop" and not any(r.get("stop_order_id") == o.id for r in self.rows):
                self.order_event("cancel", self.broker.cancel_order(o.id), reason=reason)

    def enter(self, plan, record):
        sym = plan["symbol"]
        cid = self.cid(sym, "entry")
        fallback = not self.broker.capabilities.attached_stop
        try:
            if not fallback:
                o = self.broker.submit_entry(sym, plan["side"], plan["qty"], plan["stop_price"], cid)
        except DuplicateOrderError:
            self.log.info("%s entry %s already submitted", sym, cid)
            return
        except OTOUnavailableError as e:
            self.log.warning("%s OTO entry rejected (%s); market entry with a separate stop", sym, e)
            fallback = True
        if fallback:
            try:
                o = self.broker.submit_market_order(sym, "buy" if plan["side"] == "long" else "sell", plan["qty"], cid)
            except DuplicateOrderError:
                self.log.info("%s entry %s already submitted", sym, cid)
                return
        self.order_event("submit", o, reason=plan["reason"][:200], price=plan["price"])
        self.log.info("entry %s %s %d at ~%.2f, stop %.2f (%s)", sym, plan["side"], plan["qty"], plan["price"],
                      plan["stop_price"], plan["reason"][:120])
        p = plan.get("pair") or {}
        row = {"symbol": sym, "side": plan["side"], "qty": plan["qty"], "status": "pending",
               "entry_date": self.today.isoformat(), "entry_price": plan["price"], "entry_order_id": o.id,
               "stop_order_id": o.legs[0].id if o.legs else "", "stop_price": plan["stop_price"],
               "score": plan["score"], "confidence": plan["confidence"], "score_date": record["date"],
               "config_version": self.cfg["version"], "prompt_sha256": record["prompt_sha256"],
               "memory_sha256": record.get("memory_sha256", ""), "strategy": plan["strategy"],
               "reason": plan["reason"][:300], "pair": p.get("pair", ""), "pair_beta": p.get("beta", ""),
               "pair_mean": p.get("mean", ""), "pair_std": p.get("std", "")}
        self.rows.append(row)
        self.book.save_positions(self.rows)
        if fallback:
            self.broker.wait_for_fill(o.id)

    def run(self, force=False):
        if not force and self.today not in self.calendar():
            self.log.info("market closed today; nothing to do (--force runs anyway)")
            return
        run_at = time.fromisoformat(self.cfg["entry"]["run_time"])
        if not force and self.now.time() < run_at:
            self.log.info("before the %s run time; nothing to do (--force runs anyway)", run_at)
            return
        self.reconcile()
        acct = self.broker.get_account()
        max_loss = self.cfg["risk"]["max_daily_loss_pct"]
        if self.halted():
            self.log.warning("HALT file present: closing all positions, no entries")
            notify("HALT: closing all positions")
            self.close_all("halt")
        elif risk.loss_limit_breached(acct.equity, acct.last_equity, max_loss):
            self.log.warning("daily loss limit hit (%.2f%%): closing all, no entries",
                             risk.daily_pnl_pct(acct.equity, acct.last_equity))
            notify("daily loss limit hit: closing all positions")
            self.close_all("loss_limit")
        else:
            self._exits_and_entries(acct)
        self.reconcile()
        self.repair_stops()
        events = self.book.order_events(self.today)
        self.log.info("run done: %d positions held, %d entries and %d exits submitted today, %d candidates skipped",
                      len(self.rows), sum(e["event"] == "submit" and e["client_order_id"].endswith("-entry")
                                          for e in events),
                      sum(e["event"] == "submit" and e["client_order_id"].endswith("-exit") for e in events),
                      sum(e["event"] == "skip" for e in events))

    def _exits_and_entries(self, acct):
        record = self.load_scores(self.today)
        scores = record["scores"] if record else None
        if record is None:
            self.log.warning("no valid score file for %s: exits only", self.today)
        exited = set()
        for row in list(self.rows):
            if row.get("strategy") == "pair":
                continue
            held = hold_days(date.fromisoformat(row["entry_date"]), self.today, self.calendar())
            reason = exit_reason(row, scores, held, self.cfg["exit"])
            if reason and self.exit_position(row, reason):
                exited.add(row["symbol"])
        exited |= self._pair_exits()
        if record is None:
            return
        if self.book.state().get("entries_blocked_on") == self.today.isoformat():
            self.log.warning("entries blocked today by yesterday's loss limit")
            return
        positions = self.broker.list_positions()
        held = {r["symbol"] for r in self.rows} | set(positions) | exited
        cands = rank_candidates(candidates(record, self.cfg["entry"]), held)
        if not cands:
            return
        syms = sorted({sym for c in cands for sym, _ in c["legs"]})
        prices = self.broker.latest_prices(syms)
        bars = self._bars(syms)
        atrs = {s: risk.atr(bars.get(s), self.cfg["exit"]["atr_days"]) for s in syms}
        flags = {s: self.broker.asset_flags(s) for s in syms}
        values = [p.qty * (p.current_price or p.avg_entry_price) for p in positions.values()]
        values += [float(r["qty"]) * float(r["entry_price"]) * (1 if r["side"] == "long" else -1)
                   for r in self.rows if r["symbol"] not in positions and r["status"] == "pending"]
        submits = sum(1 for e in self.book.order_events(self.today) if e["event"] == "submit")
        plans, skips = plan_entries(cands, prices=prices, atrs=atrs, flags=flags, equity=acct.equity,
                                    book_values=values, orders_today=submits, cfg=self.cfg)
        for sym, why in skips:
            self.event("skip", sym, reason=why)
        if skips:
            self.log.info("skipped: %s", ", ".join(f"{sym} ({why})" for sym, why in skips))
        for plan in plans:
            self.enter(plan, record)

    def _pair_exits(self):
        """Exit pairs whose gap closed, widened past stop_z or ran out of time, and lone legs of broken pairs."""
        pcfg = self.cfg["pairs"]
        groups = {}
        for row in self.rows:
            if row.get("strategy") == "pair" and row.get("pair"):
                groups.setdefault(row["pair"], []).append(row)
        open_legs = sorted({r["symbol"] for g in groups.values() for r in g if r["status"] == "open"})
        prices = self.broker.latest_prices(open_legs) if open_legs else {}
        exited = set()
        for pair_id, legs in groups.items():
            if any(r["status"] != "open" for r in legs):
                continue
            reason = "pair_leg" if len(legs) == 1 else self._pair_exit_reason(pair_id, legs, prices, pcfg)
            if reason:
                exited |= {r["symbol"] for r in legs if self.exit_position(r, reason)}
        return exited

    def _pair_exit_reason(self, pair_id, legs, prices, pcfg):
        a, b = pair_id.split("/")
        by = {r["symbol"]: r for r in legs}
        if a in by and b in by and prices.get(a) and prices.get(b):
            r = by[a]
            z = pairs.zscore(r["pair_beta"], r["pair_mean"], r["pair_std"], prices[a], prices[b])
            toward_entry = z if r["side"] == "short" else -z
            if toward_entry <= pcfg["exit_z"]:
                return "converged"
            if toward_entry >= pcfg["stop_z"]:
                return "diverged"
        held = hold_days(date.fromisoformat(legs[0]["entry_date"]), self.today, self.calendar())
        return "time" if held >= pcfg["max_hold_days"] else None

    def close(self, force=False):
        if not force and self.today not in self.calendar():
            self.log.info("market closed today; nothing to do (--force runs anyway)")
            return
        self.broker.on_close(self.today)
        self.reconcile()
        self.repair_stops()
        acct = self.broker.get_account()
        notes = []
        halted = self.halted()
        if halted:
            notes.append("halt")
            self.close_all("halt")
        elif risk.loss_limit_breached(acct.equity, acct.last_equity, self.cfg["risk"]["max_daily_loss_pct"]):
            nxt = [d for d in self.calendar() if d > self.today]
            if nxt:
                self.book.set_state(entries_blocked_on=nxt[0].isoformat())
            notes.append("loss_limit")
            notify("daily loss limit hit at the close: closing all, no entries next session")
            self.close_all("loss_limit")
        if notes:
            self.reconcile()
        self.mark_to_market()
        values = [float(r["qty"]) * float(r.get("last_price") or r["entry_price"]) * (1 if r["side"] == "long" else -1)
                  for r in self.rows if r["status"] != "pending"]
        gross, net = risk.exposure_pct(values, acct.equity)
        events = self.book.order_events(self.today)
        closed = [t for t in self.book.trades() if t["date"] == self.today.isoformat()]
        pnls = [float(t["pnl_usd"]) for t in closed if t["pnl_usd"] not in ("", None)]
        realized = sum(pnls)
        unrealized = sum(float(r.get("unrealized_pnl") or 0) for r in self.rows if r["status"] != "pending")
        self.book.daily_summary({
            "date": self.today.isoformat(), "trades": len(closed), "wins": sum(p > 0 for p in pnls),
            "losses": sum(p < 0 for p in pnls), "gross_pnl": round(realized, 2),
            "equity_start": round(acct.last_equity, 2), "equity_end": round(acct.equity, 2), "halted": halted,
            "notes": " ".join(notes), "day_pnl": round(acct.equity - acct.last_equity, 2),
            "day_return_pct": round(risk.daily_pnl_pct(acct.equity, acct.last_equity), 4),
            "unrealized_pnl": round(unrealized, 2), "positions": len(values), "gross_exposure": round(gross, 2),
            "net_exposure": round(net, 2),
            "entries": sum(1 for e in events if e["event"] == "submit" and e["client_order_id"].endswith("-entry")),
            "scored": self.load_scores(self.today) is not None})
        self.book.snapshot(self.now, self.broker.get_account().equity, self.broker.list_positions())
        self.log.info("close done: equity %.2f, day P&L %+.2f (realized %+.2f), %d positions, gross %.1f%%, net %.1f%%; "
                      "summary in %s", acct.equity, acct.equity - acct.last_equity, realized, len(values), gross, net,
                      self.book.summary_path)

    def snapshot(self, force=False):
        """Equity and positions into the database, and the final state of orders that were open last time."""
        if not force and self.today not in self.calendar():
            self.log.info("market closed today; nothing to do (--force runs anyway)")
            return
        self.book.snapshot(self.now, self.broker.get_account().equity, self.broker.list_positions())
        still_open = {o.id for o in self.open_orders()}
        for order_id in sorted(set(self.book.open_order_ids()) - still_open):
            self.get_order(order_id)

    def mark_to_market(self):
        held = [r["symbol"] for r in self.rows if r["status"] != "pending"]
        prices = self.broker.latest_prices(held) if held else {}
        for r in self.rows:
            p = prices.get(r["symbol"])
            if p is not None:
                direction = 1 if r["side"] == "long" else -1
                r["last_price"] = p
                r["unrealized_pnl"] = round((p - float(r["entry_price"])) * direction * float(r["qty"]), 2)
        self.book.save_positions(self.rows)


# ---- replay ----

class ReplayData:
    """Saved daily bars served as if it were `day`: open prices for the run, closes for the close."""

    def __init__(self, bars):
        self.bars = bars
        self.day, self.phase = None, "open"

    def set(self, day, phase):
        self.day, self.phase = day, phase

    def trading_days(self, start, end):
        return sorted({d for df in self.bars.values() for d in df.index if start <= d <= end})

    def daily_bars(self, symbols, start, end):
        last = self.day if self.phase == "close" else self.day - timedelta(days=1)
        hi = min(end.date(), last)
        return {s: self.bars[s][(self.bars[s].index >= start.date()) & (self.bars[s].index <= hi)]
                for s in symbols if s in self.bars}

    def latest_prices(self, symbols):
        col = "open" if self.phase == "open" else "close"
        return {s: float(self.bars[s].loc[self.day, col]) for s in symbols
                if s in self.bars and self.day in self.bars[s].index}

    def asset_flags(self, symbol):
        return True, True, True


def scored_symbols(start, end):
    """Every symbol with a score in the score files dated from `start` to `end`."""
    out = set()
    for f in paths.scores_dir().glob("*.json"):
        if start.isoformat() <= f.stem <= end.isoformat():
            out |= set(json.loads(f.read_text(encoding="utf-8")).get("scores", {}))
    return sorted(out)


def replay(start, end, cfg, bars, log):
    """Run the rules day by day over saved score files and bars into data/books/replay/. Returns the book."""
    from simbroker import SimBroker

    book = Book(paths.book_dir("replay"), "replay")
    book.clear()
    sim_path = book.dir / "sim_account.json"
    sim_path.unlink(missing_ok=True)
    data = ReplayData(bars)
    clock = {"now": None}
    sim = SimBroker(data, sim_path, clock=lambda: clock["now"])
    for day in data.trading_days(start, end):
        for phase, at, job in (("open", time(9, 45), "run"), ("close", time(16, 15), "close")):
            data.set(day, phase)
            clock["now"] = datetime.combine(day, at, ET)
            t = Trader(sim, cfg, book, clock["now"], log)
            getattr(t, job)(force=True)
    return book


def show_positions(broker, book):
    """Holdings of a book marked at live prices, plus today's entries and exits so far."""
    from analyze import holdings_table

    now = datetime.now(ET)
    rows = book.positions()
    held = [r["symbol"] for r in rows if r["status"] != "pending"]
    prices = broker.latest_prices(held) if held else {}
    lines = [f"# Holdings ({book.mode}) at {now:%Y-%m-%d %H:%M} ET", "", f"From {book.positions_path}", "",
             holdings_table(rows, now.date(), prices)]
    pending = [r["symbol"] for r in rows if r["status"] == "pending"]
    if pending:
        lines += ["", f"Entries waiting for a fill: {', '.join(pending)}"]
    events = book.order_events(now.date())
    entries = [e["symbol"] for e in events if e["event"] == "submit" and e["client_order_id"].endswith("-entry")]
    exits = [t for t in book.trades() if t["date"] == now.date().isoformat()]
    closed = ", ".join(f"{t['symbol']} {t['exit_reason']} {t['pnl_usd']}" for t in exits) or "none"
    lines += ["", f"Today: {len(entries)} entries ({', '.join(entries) or 'none'}), {len(exits)} closed trades "
              f"({closed})."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    import config
    from broker import make_broker, make_market_data
    from simbroker import SimBroker

    ap = argparse.ArgumentParser(description="sentiment swing trader")
    ap.add_argument("command", choices=["run", "close", "replay", "positions", "snapshot"])
    ap.add_argument("--dry-run", action="store_true", help="simulated fills instead of paper orders")
    ap.add_argument("--force", action="store_true", help="ignore the calendar and run time checks")
    ap.add_argument("--from", dest="start", type=date.fromisoformat)
    ap.add_argument("--to", dest="end", type=date.fromisoformat)
    args = ap.parse_args(argv)
    config.init_data()
    log = setup_logging("trader")
    try:
        cfg = config.load_config()
        if args.command == "replay":
            if not (args.start and args.end):
                ap.error("replay needs --from and --to")
            symbols = scored_symbols(args.start, args.end)
            try:
                source = make_market_data(cfg)
            except Exception:
                log.info("no Alpaca access; replaying from cached bars only")
                source = None
            bars = load_bars(symbols, args.start - timedelta(days=FETCH_DAYS), args.end, source)
            book = replay(args.start, args.end, cfg, bars, log)
            trades = book.trades()
            pnl = sum(float(t["pnl_usd"] or 0) for t in trades)
            print(f"replay {args.start}..{args.end}: {len(trades)} closed trades, P&L {pnl:.2f} USD; "
                  f"books in {book.dir}")
            return 0
        mode = "dry_run" if args.dry_run else "paper"
        if args.command == "positions":
            print(show_positions(make_broker(cfg, verify_account=False), Book(paths.book_dir(mode), mode)))
            return 0
        if args.command == "snapshot":  # read-only at the broker, so it runs beside the other jobs
            book = Book(paths.book_dir(mode), mode)
            real = make_broker(cfg, verify_account=False)
            broker = SimBroker(real, book.dir / "sim_account.json") if mode == "dry_run" else real
            Trader(broker, cfg, book, datetime.now(ET), log).snapshot(force=args.force)
            return 0
        with job_lock(paths.lock_file()):
            real = make_broker(cfg)
            book = Book(paths.book_dir(mode), mode)
            broker = SimBroker(real, book.dir / "sim_account.json") if mode == "dry_run" else real
            t = Trader(broker, cfg, book, datetime.now(ET), log, halt_path=paths.halt_file())
            log.info("%s (%s)", args.command, mode)
            getattr(t, args.command)(force=args.force)
    except Exception as e:
        log.exception("trader %s failed", args.command)
        if args.command != "snapshot":  # every 5 minutes; the runs table shows its failures
            notify(f"trader {args.command} failed: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
