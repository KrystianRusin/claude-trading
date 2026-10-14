"""Position sizing, ATR stops, book limits and the daily loss limit. Pure functions."""
import math

MAX_ORDERS_PER_DAY = 30
MAX_POSITION_PCT = 10.0
MIN_PRICE = 5.0


def atr(bars, days):
    """Simple average true range over the last `days` daily bars; None without days + 1 bars."""
    if bars is None or len(bars) < days + 1:
        return None
    high, low, close = bars["high"].to_numpy(), bars["low"].to_numpy(), bars["close"].to_numpy()
    prev = close[:-1]
    tr = [max(h - lo, abs(h - p), abs(lo - p)) for h, lo, p in zip(high[1:], low[1:], prev)]
    return float(sum(tr[-days:]) / days)


def stop_price(side, reference, atr_value, multiple):
    """Protective stop `multiple` x ATR away from the reference price, on the losing side."""
    distance = multiple * atr_value
    price = reference - distance if side == "long" else reference + distance
    return round(price, 2)


def position_qty(equity, price, pct):
    """Whole shares worth `pct` of equity, never more than the hard cap."""
    if price <= 0 or equity <= 0:
        return 0
    return int(math.floor(equity * min(pct, MAX_POSITION_PCT) / 100 / price))


def exposure_pct(signed_values, equity):
    """(gross, net) exposure as % of equity from signed position values (shorts negative)."""
    if equity <= 0:
        return float("inf"), float("inf")
    return 100 * sum(abs(v) for v in signed_values) / equity, 100 * sum(signed_values) / equity


def fits_book(signed_values, add_value, equity, risk_cfg):
    """None if adding a position of `add_value` (signed) keeps the book within limits, else the reason."""
    if len(signed_values) + 1 > risk_cfg["max_positions"]:
        return "max_positions"
    if abs(add_value) > equity * MAX_POSITION_PCT / 100:
        return "position_cap"
    gross, net = exposure_pct(list(signed_values) + [add_value], equity)
    if gross > risk_cfg["max_gross_exposure_pct"]:
        return "gross_exposure"
    if abs(net) > risk_cfg["max_net_exposure_pct"]:
        return "net_exposure"
    return None


def daily_pnl_pct(equity, last_equity):
    """Realized plus unrealized P&L since the previous close, as % of that close's equity."""
    return 0.0 if last_equity <= 0 else 100 * (equity / last_equity - 1)


def loss_limit_breached(equity, last_equity, max_daily_loss_pct):
    return daily_pnl_pct(equity, last_equity) <= -max_daily_loss_pct


def can_short(flags):
    tradable, shortable, easy_to_borrow = flags
    return tradable and shortable and easy_to_borrow
