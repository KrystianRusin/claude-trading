import pandas as pd
import pytest

import risk

LIMITS = {"max_positions": 3, "max_gross_exposure_pct": 60, "max_net_exposure_pct": 40}


def bars(n, high=101.0, low=99.0, close=100.0):
    return pd.DataFrame({"open": [close] * n, "high": [high] * n, "low": [low] * n, "close": [close] * n})


def test_atr_simple_average_true_range():
    assert risk.atr(bars(15), 14) == pytest.approx(2.0)
    gap = bars(15)
    gap.loc[14, ["high", "low", "close"]] = [110.0, 108.0, 109.0]  # gap up: TR = 110 - 100
    assert risk.atr(gap, 14) == pytest.approx((13 * 2 + 10) / 14)
    assert risk.atr(bars(14), 14) is None
    assert risk.atr(None, 14) is None


def test_stop_price_sides():
    assert risk.stop_price("long", 100, 2.0, 2.0) == 96.0
    assert risk.stop_price("short", 100, 2.0, 2.0) == 104.0


def test_position_qty_rounds_down_and_caps():
    assert risk.position_qty(100_000, 333, 5) == 15
    assert risk.position_qty(100_000, 6000, 5) == 0
    assert risk.position_qty(100_000, 100, 50) == 100  # hard cap of 10%


def test_exposure_and_book_limits():
    assert risk.exposure_pct([30_000, -10_000], 100_000) == (40.0, 20.0)
    assert risk.fits_book([5000, 5000], 5000, 100_000, LIMITS) is None
    assert risk.fits_book([5000, 5000, 5000], 5000, 100_000, LIMITS) == "max_positions"
    assert risk.fits_book([], 11_000, 100_000, LIMITS) == "position_cap"
    assert risk.fits_book([30_000, -25_000], 9000, 100_000, LIMITS | {"max_gross_exposure_pct": 60}) == "gross_exposure"
    assert risk.fits_book([20_000, 18_000], 5000, 100_000, LIMITS | {"max_positions": 5}) == "net_exposure"
    assert risk.fits_book([-20_000, -18_000], -5000, 100_000, LIMITS | {"max_positions": 5}) == "net_exposure"


def test_daily_loss():
    assert risk.daily_pnl_pct(98_000, 100_000) == pytest.approx(-2.0)
    assert risk.loss_limit_breached(98_000, 100_000, 2.0)
    assert not risk.loss_limit_breached(98_100, 100_000, 2.0)


def test_short_needs_easy_to_borrow():
    assert risk.can_short((True, True, True))
    assert not risk.can_short((True, True, False))
    assert not risk.can_short((True, False, True))
