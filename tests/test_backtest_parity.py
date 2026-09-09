"""Brief acceptance criterion (section 8): "A signal generated in
backtest and the same signal generated in paper mode produce identical
feature vectors, verified by an automated test."

There is no separate "paper mode" code path in Phase 1 — that is the
point. A backtest calls `FeatureEngine.get_feature_vector()` against an
in-memory full-history DataFrame; a live/paper loop calls the exact same
function against a `BarStore`-backed source that only ever returns bars
already written to disk. This test proves those two entry points produce
byte-identical output for the same symbol/timestamp — which is what
"used identically by the backtester and the live loop" (brief 4.2) means
in practice.
"""
from __future__ import annotations

from datetime import timedelta

from conftest import make_bars

from forexml.data.store import BarStore
from forexml.features import DataFrameBarSource, FeatureEngine, StoreBarSource
from forexml.features.indicators import AverageTrueRange, RelativeStrengthIndex, SimpleMovingAverage


def test_backtest_and_store_backed_sources_produce_identical_feature_vectors(tmp_path):
    bars = make_bars(400)
    store = BarStore(tmp_path / "barstore")
    store.write(bars, "EURUSD", "M15")

    engine = FeatureEngine([SimpleMovingAverage(20), AverageTrueRange(14), RelativeStrengthIndex(14)])

    decision_ts = bars["timestamp_utc"][300] + timedelta(minutes=15)

    backtest_source = DataFrameBarSource(bars)
    backtest_vector = engine.get_feature_vector("EURUSD", decision_ts, "M15", backtest_source)

    live_source = StoreBarSource(store)
    live_vector = engine.get_feature_vector("EURUSD", decision_ts, "M15", live_source)

    assert backtest_vector.values == live_vector.values


def test_parity_holds_across_multiple_decision_points(tmp_path):
    bars = make_bars(400)
    store = BarStore(tmp_path / "barstore")
    store.write(bars, "EURUSD", "M15")

    engine = FeatureEngine([SimpleMovingAverage(20), AverageTrueRange(14)])
    backtest_source = DataFrameBarSource(bars)
    live_source = StoreBarSource(store)

    for idx in (50, 150, 250, 350):
        decision_ts = bars["timestamp_utc"][idx] + timedelta(minutes=15)
        a = engine.get_feature_vector("EURUSD", decision_ts, "M15", backtest_source)
        b = engine.get_feature_vector("EURUSD", decision_ts, "M15", live_source)
        assert a.values == b.values, f"parity broke at decision index {idx}"
