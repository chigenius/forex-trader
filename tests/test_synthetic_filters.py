"""Filters suited to round-the-clock synthetic instruments (no named
regional session, no macro news calendar): time_of_day and
volatility_spike_guard.
"""
from __future__ import annotations

from datetime import datetime, timezone

import polars as pl

from forexml.features.context.calendar import EconomicCalendar
from forexml.strategies.primitives.base import EvalContext
from forexml.strategies.primitives.filters import TimeOfDayFilter, VolatilitySpikeGuardFilter

_EMPTY_BARS = pl.DataFrame(schema={"high": pl.Float64, "low": pl.Float64})


def _ctx(timestamp=None, features=None, bars=None):
    return EvalContext(
        symbol="Volatility 100 (1s) Index",
        timestamp=timestamp or datetime(2024, 1, 1, 10, tzinfo=timezone.utc),
        timeframe="M15",
        features=features or {},
        bars=bars if bars is not None else _EMPTY_BARS,
        calendar=EconomicCalendar.empty(),
    )


def test_time_of_day_within_simple_window():
    f = TimeOfDayFilter({"type": "time_of_day", "start_hour": 8, "end_hour": 16})
    ok, reason = f.evaluate(_ctx(timestamp=datetime(2024, 1, 1, 10, tzinfo=timezone.utc)))
    assert ok and reason is None


def test_time_of_day_outside_simple_window():
    f = TimeOfDayFilter({"type": "time_of_day", "start_hour": 8, "end_hour": 16})
    ok, reason = f.evaluate(_ctx(timestamp=datetime(2024, 1, 1, 20, tzinfo=timezone.utc)))
    assert not ok
    assert "time_of_day" in reason


def test_time_of_day_window_wraps_past_midnight():
    f = TimeOfDayFilter({"type": "time_of_day", "start_hour": 22, "end_hour": 4})
    assert f.evaluate(_ctx(timestamp=datetime(2024, 1, 1, 23, tzinfo=timezone.utc)))[0]
    assert f.evaluate(_ctx(timestamp=datetime(2024, 1, 1, 2, tzinfo=timezone.utc)))[0]
    assert not f.evaluate(_ctx(timestamp=datetime(2024, 1, 1, 10, tzinfo=timezone.utc)))[0]


def test_volatility_spike_guard_blocks_outsized_bar():
    f = VolatilitySpikeGuardFilter({"type": "volatility_spike_guard", "atr_key": "atr_14", "multiple": 3.0})
    bars = pl.DataFrame({"high": [110.0], "low": [100.0]})  # range = 10
    ok, reason = f.evaluate(_ctx(features={"atr_14": 2.0}, bars=bars))  # 3x atr = 6 < 10
    assert not ok
    assert "volatility_spike_guard" in reason


def test_volatility_spike_guard_allows_normal_bar():
    f = VolatilitySpikeGuardFilter({"type": "volatility_spike_guard", "atr_key": "atr_14", "multiple": 3.0})
    bars = pl.DataFrame({"high": [102.0], "low": [100.0]})  # range = 2
    ok, reason = f.evaluate(_ctx(features={"atr_14": 2.0}, bars=bars))  # 3x atr = 6 > 2
    assert ok and reason is None


def test_volatility_spike_guard_does_not_block_when_atr_unavailable():
    f = VolatilitySpikeGuardFilter({"type": "volatility_spike_guard"})
    bars = pl.DataFrame({"high": [999.0], "low": [0.0]})
    ok, reason = f.evaluate(_ctx(features={"atr_14": None}, bars=bars))
    assert ok and reason is None
