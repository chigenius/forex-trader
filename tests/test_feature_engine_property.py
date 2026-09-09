"""Property-based tests (Hypothesis) for the feature engine, per the
brief's technology choice: "pytest, with property-based tests via
Hypothesis for the feature engine."
"""
from __future__ import annotations

from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from conftest import make_bars

from forexml.features import DataFrameBarSource, FeatureEngine
from forexml.features.indicators import SimpleMovingAverage


@given(future_offset_bars=st.integers(min_value=1, max_value=100), poison_price=st.floats(min_value=1e-6, max_value=1e6))
@settings(max_examples=30)
def test_arbitrary_future_poisoning_never_changes_the_feature_vector(future_offset_bars, poison_price):
    bars = make_bars(300)
    decision_ts = bars["timestamp_utc"][150] + timedelta(minutes=15)
    engine = FeatureEngine([SimpleMovingAverage(20)])

    baseline = engine.get_feature_vector("EURUSD", decision_ts, "M15", DataFrameBarSource(bars))

    poison_index = 150 + future_offset_bars
    if poison_index >= bars.height:
        return
    poisoned = bars.clone()
    poisoned[poison_index, "close"] = poison_price
    result = engine.get_feature_vector("EURUSD", decision_ts, "M15", DataFrameBarSource(poisoned))

    assert baseline.values["sma_20"] == result.values["sma_20"]


@given(n_bars=st.integers(min_value=25, max_value=200))
@settings(max_examples=15)
def test_feature_vector_never_uses_more_bars_than_are_closed(n_bars):
    bars = make_bars(n_bars)
    engine = FeatureEngine([SimpleMovingAverage(20)])
    decision_ts = bars["timestamp_utc"][0] + timedelta(minutes=15)
    result = engine.get_feature_vector("EURUSD", decision_ts, "M15", DataFrameBarSource(bars))
    assert result.values["bars_available"] == 1
