"""Simulated account for dry-run and replay: real or replayed prices, simulated fills.

Market orders fill at once at the data source's current price. Stops trigger in `on_close` against the
day's bar: at the stop price, or at the open when the bar gaps through a stop placed on an earlier day.
"""
import json
from datetime import datetime

from broker import ET, Account, DuplicateOrderError, Order, Position

SIM_ACCOUNT = "SIMULATED"


class SimBroker:
    def __init__(self, data, state_path, starting_cash=100_000.0, clock=None):
        self.data = data
        self.path = state_path
        self.clock = clock or (lambda: datetime.now(ET))
        if state_path.exists():
            self.s = json.loads(state_path.read_text())
        else:
            self.s = {"cash": starting_cash, "positions": {}, "orders": {}, "marks": {}, "equity_close": {},
                      "next_id": 1}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.s, indent=1))

    def _equity(self):
        return self.s["cash"] + sum(p["qty"] * self.s["marks"].get(sym, p["avg"])
                                    for sym, p in self.s["positions"].items())

    def get_account(self):
        today = self.clock().date().isoformat()
        equity = self._equity()
        earlier = [d for d in self.s["equity_close"] if d < today]
        last = self.s["equity_close"][max(earlier)] if earlier else equity
        return Account(account_number=SIM_ACCOUNT, equity=equity, last_equity=last)

    def trading_days(self, start, end):
        return self.data.trading_days(start, end)

    def daily_bars(self, symbols, start, end):
        return self.data.daily_bars(symbols, start, end)

    def asset_flags(self, symbol):
        return self.data.asset_flags(symbol)

    def get_news(self, symbols, start, end):
        return self.data.get_news(symbols, start, end)

    def latest_prices(self, symbols):
        prices = self.data.latest_prices(symbols)
        self.s["marks"].update(prices)
        self._save()
        return prices

    def _order(self, symbol, side, qty, order_type, client_order_id, stop_price=None, status="new"):
        if any(o["client_order_id"] == client_order_id for o in self.s["orders"].values()):
            raise DuplicateOrderError(client_order_id)
        oid = f"sim-{self.s['next_id']}"
        self.s["next_id"] += 1
        o = {"id": oid, "client_order_id": client_order_id, "symbol": symbol, "side": side, "qty": float(qty),
             "order_type": order_type, "status": status, "filled_qty": 0.0, "filled_avg_price": None,
             "filled_at": None, "stop_price": stop_price, "legs": [], "created": self.clock().date().isoformat()}
        self.s["orders"][oid] = o
        return o

    def _fill(self, o, price):
        signed = o["qty"] if o["side"] == "buy" else -o["qty"]
        pos = self.s["positions"].get(o["symbol"], {"qty": 0.0, "avg": price})
        new_qty = pos["qty"] + signed
        if pos["qty"] == 0 or (pos["qty"] > 0) == (signed > 0):
            avg = (pos["qty"] * pos["avg"] + signed * price) / new_qty
        elif (new_qty > 0) != (pos["qty"] > 0) and new_qty != 0:
            avg = price
        else:
            avg = pos["avg"]
        self.s["cash"] -= signed * price
        if new_qty == 0:
            self.s["positions"].pop(o["symbol"], None)
        else:
            self.s["positions"][o["symbol"]] = {"qty": new_qty, "avg": avg}
        self.s["marks"][o["symbol"]] = price
        o.update(status="filled", filled_qty=o["qty"], filled_avg_price=price,
                 filled_at=self.clock().isoformat())

    def _market(self, symbol, side, qty, client_order_id):
        o = self._order(symbol, side, qty, "market", client_order_id)
        self._fill(o, self.data.latest_prices([symbol])[symbol])
        return o

    def submit_entry(self, symbol, side, qty, stop_price, client_order_id):
        o = self._market(symbol, "buy" if side == "long" else "sell", qty, client_order_id)
        leg = self._order(symbol, "sell" if side == "long" else "buy", qty, "stop", client_order_id + "-leg",
                          stop_price=stop_price)
        o["legs"] = [leg["id"]]
        self._save()
        return self._as_order(o)

    def submit_market_order(self, symbol, side, qty, client_order_id):
        o = self._market(symbol, side, qty, client_order_id)
        self._save()
        return self._as_order(o)

    def submit_stop_order(self, symbol, side, qty, stop_price, client_order_id):
        o = self._order(symbol, side, qty, "stop", client_order_id, stop_price=stop_price)
        self._save()
        return self._as_order(o)

    def _as_order(self, o):
        filled_at = datetime.fromisoformat(o["filled_at"]) if o["filled_at"] else None
        return Order(id=o["id"], client_order_id=o["client_order_id"], symbol=o["symbol"], side=o["side"],
                     qty=o["qty"], order_type=o["order_type"], status=o["status"], filled_qty=o["filled_qty"],
                     filled_avg_price=o["filled_avg_price"], filled_at=filled_at, stop_price=o["stop_price"],
                     legs=tuple(self._as_order(self.s["orders"][i]) for i in o["legs"]))

    def get_order(self, order_id):
        return self._as_order(self.s["orders"][order_id])

    def get_order_by_client_id(self, client_order_id):
        for o in self.s["orders"].values():
            if o["client_order_id"] == client_order_id:
                return self._as_order(o)
        return None

    def open_orders(self):
        return [self._as_order(o) for o in self.s["orders"].values() if o["status"] == "new"]

    def cancel_order(self, order_id, wait=0):
        o = self.s["orders"][order_id]
        if o["status"] == "new":
            o["status"] = "canceled"
            self._save()
        return self._as_order(o)

    def wait_for_fill(self, order_id, wait=0):
        return self.get_order(order_id)

    def list_positions(self):
        return {sym: Position(sym, p["qty"], p["avg"], self.s["marks"].get(sym))
                for sym, p in self.s["positions"].items()}

    def on_close(self, day):
        """Trigger stops against the day's bars, mark positions at the close and record the closing equity."""
        symbols = sorted(set(self.s["positions"]) | {o["symbol"] for o in self.s["orders"].values()
                                                     if o["status"] == "new"})
        start = datetime.combine(day, datetime.min.time(), ET)
        bars = self.data.daily_bars(symbols, start, start.replace(hour=23, minute=59)) if symbols else {}
        for o in list(self.s["orders"].values()):
            bar = bars.get(o["symbol"])
            if o["status"] != "new" or o["order_type"] != "stop" or bar is None or day not in bar.index:
                continue
            if o["symbol"] not in self.s["positions"]:
                o["status"] = "canceled"
                continue
            b = bar.loc[day]
            same_day = o["created"] == day.isoformat()
            if o["side"] == "sell" and b["low"] <= o["stop_price"]:
                self._fill(o, o["stop_price"] if same_day else min(b["open"], o["stop_price"]))
            elif o["side"] == "buy" and b["high"] >= o["stop_price"]:
                self._fill(o, o["stop_price"] if same_day else max(b["open"], o["stop_price"]))
        for sym, bar in bars.items():
            if day in bar.index:
                self.s["marks"][sym] = float(bar.loc[day, "close"])
        self.s["equity_close"][day.isoformat()] = self._equity()
        self._save()
