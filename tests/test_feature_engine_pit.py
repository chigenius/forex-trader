"""Point-in-time correctness tests (brief acceptance criterion, section 8):
"The feature engine test suite includes at least five deliberate
look-ahead scenarios, all of which fail as expected [i.e. as designed —
they would fail loudly if look-ahead existed]."

Each test below poisons a specific piece of future or not-yet-closed
data with an extreme, unmistakable value and asserts the computed feature
vector is byte-for-byte identical to the unpoisoned baseline. If the
engine ever leaked a peek at that data, these assertions fail with a
large, obvious diff — that is what "fail loudly" means here.
"""
from __future__ import annotations

from datetime import timedelta

import polars as pl
import pytest

from forexml.features import DataFrameBarSource, FeatureEngine
from forexml.features.indicators import (
    AverageTrueRange,
    BollingerBands,
    PivotLevels,
    RelativeStrengthIndex,
    RollingATRMedian,
    SimpleMovingAverage,
    SwingStructure,
)

from conftest import make_bars

ALL_INDICATORS = [
    SimpleMovingAverage(20),
    AverageTrueRange(14),
    RollingATRMedian(14, 20),
    RelativeStrengthIndex(14),
    BollingerBands(20, 2.0),
    PivotLevels(),
    SwingStructure(confirm=3, window=50),
]


@pytest.fixture
def bars():
    return make_bars(300)


@pytest.fixture
def engine():
    return FeatureEngine(ALL_INDICATORS)


@pytest.fixture
def decision_ts(bars):
    return bars["timestamp_utc"][150] + timedelta(minutes=15)


def _poison(bars: pl.DataFrame, row_index: int, price: float) -> pl.DataFrame:
    poisoned = bars.clone()
    for col in ("open", "high", "low", "close"):
        poisoned[row_index, col] = price
    return poisoned


def test_future_price_spike_does_not_affect_moving_average_or_atr(engine, bars, decision_ts):
    baseline = engine.get_feature_vector("EURUSD", decision_ts, "M15", DataFrameBarSource(bars))
    poisoned = _poison(bars, 250, 999_999.0)  # far in the future relative to decision_ts
    result = engine.get_feature_vector("EURUSD", decision_ts, "M15", DataFrameBarSource(poisoned))
    assert baseline.values["sma_20"] == result.values["sma_20"]
    assert baseline.values["atr_14"] == result.values["atr_14"]


def test_future_price_spike_does_not_affect_rsi_or_bollinger(engine, bars, decision_ts):
    baseline = engine.get_feature_vector("EURUSD", decision_ts, "M15", DataFrameBarSource(bars))
    poisoned = _poison(bars, 200, 0.000001)  # far-future crash
    result = engine.get_feature_vector("EURUSD", decision_ts, "M15", DataFrameBarSource(poisoned))
    assert baseline.values["rsi_14"] == result.values["rsi_14"]
    assert baseline.values["bb_20_2.0_mid"] == result.values["bb_20_2.0_mid"]
    assert baseline.values["bb_20_2.0_upper"] == result.values["bb_20_2.0_upper"]


def test_future_price_spike_does_not_affect_volatility_regime(engine, bars, decision_ts):
    baseline = engine.get_feature_vector("EURUSD", decision_ts, "M15", DataFrameBarSource(bars))
    poisoned = _poison(bars, 280, 50.0)  # extreme future volatility
    result = engine.get_feature_vector("EURUSD", decision_ts, "M15", DataFrameBarSource(poisoned))
    assert baseline.values["volatility_regime"] == result.values["volatility_regime"]
    assert baseline.values["atr_percentile"] == result.values["atr_percentile"]


def test_future_extreme_does_not_create_a_swing_point_early(engine, bars, decision_ts):
    baseline = engine.get_feature_vector("EURUSD", decision_ts, "M15", DataFrameBarSource(bars))
    # A poisoned extreme placed after the decision timestamp must never be
    # reported as the "most recent confirmed" swing high/low as of an
    # earlier decision timestamp.
    poisoned = bars.clone()
    poisoned[160, "high"] = 999.0
    poisoned[160, "low"] = 998.0
    result = engine.get_feature_vector("EURUSD", decision_ts, "M15", DataFrameBarSource(poisoned))
    assert baseline.values["swing_high"] == result.values["swing_high"]
    assert baseline.values["swing_low"] == result.values["swing_low"]


def test_next_day_bars_do_not_affect_pivot_levels(engine, bars, decision_ts):
    baseline = engine.get_feature_vector("EURUSD", decision_ts, "M15", DataFrameBarSource(bars))
    # Poison bars belonging to a day strictly after the decision timestamp.
    future_day_mask = bars["timestamp_utc"].dt.date() > decision_ts.date()
    poisoned = bars.with_columns(
        pl.when(future_day_mask).then(pl.lit(123456.0)).otherwise(pl.col("high")).alias("high")
    )
    result = engine.get_feature_vector("EURUSD", decision_ts, "M15", DataFrameBarSource(poisoned))
    assert baseline.values["pivot"] == result.values["pivot"]
    assert baseline.values["pivot_r1"] == result.values["pivot_r1"]
    assert baseline.values["pivot_s1"] == result.values["pivot_s1"]


def test_still_forming_current_bar_is_excluded(engine, bars):
    """The classic off-by-one look-ahead bug: using the bar that is still
    open AT the decision timestamp (it closes at decision_ts + interval,
    not before). Poisoning that one bar must not move the feature vector
    computed for a decision made the instant it opens.
    """
    decision_ts = bars["timestamp_utc"][100]  # exactly the open time of bar 100
    baseline = engine.get_feature_vector("EURUSD", decision_ts, "M15", DataFrameBarSource(bars))
    poisoned = _poison(bars, 100, 777.0)  # bar 100 itself: not yet closed at decision_ts
    result = engine.get_feature_vector("EURUSD", decision_ts, "M15", DataFrameBarSource(poisoned))
    assert baseline.values == result.values


def test_bar_exactly_at_cutoff_boundary_is_included(engine, bars):
    """Sanity check on the other side of the boundary: the bar that closed
    EXACTLY at the decision timestamp must be used — this is not a
    look-ahead scenario, but an engine that is too conservative here would
    silently discard real, legitimately available data.
    """
    decision_ts = bars["timestamp_utc"][100] + timedelta(minutes=15)
    result = engine.get_feature_vector("EURUSD", decision_ts, "M15", DataFrameBarSource(bars))
    assert result.values["last_closed_bar_ts"] == bars["timestamp_utc"][100]
