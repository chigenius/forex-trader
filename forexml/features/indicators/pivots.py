"""Classic floor pivots, computed from the prior *completed* trading day.

Point-in-time note: "prior day" means the last full UTC day that ended
strictly before the decision timestamp's own day started — never the
still-forming current day, and never a day whose last bar hasn't closed
yet as of the cutoff already applied by the feature engine.
"""
from __future__ import annotations

import polars as pl

from .base import Indicator


class PivotLevels(Indicator):
    def __init__(self, lookback_days: int = 3):
        # Bars, not days: caller must supply enough trailing bars to cover
        # at least one full prior calendar day at the working timeframe.
        self.lookback_days = lookback_days
        self.lookback = 1  # engine-level guard is bar-count based; see compute()

    @property
    def output_names(self) -> list[str]:
        return ["pivot", "pivot_r1", "pivot_s1"]

    def compute(self, bars: pl.DataFrame):
        if bars.is_empty():
            return self._insufficient()
        last_ts = bars["timestamp_utc"][-1]
        current_day = last_ts.date()
        prior = bars.filter(pl.col("timestamp_utc").dt.date() < current_day)
        if prior.is_empty():
            return self._insufficient()
        prior_day = prior["timestamp_utc"].dt.date()[-1]
        prior_day_bars = prior.filter(pl.col("timestamp_utc").dt.date() == prior_day)
        high = float(prior_day_bars["high"].max())
        low = float(prior_day_bars["low"].min())
        close = float(prior_day_bars["close"][-1])
        pivot = (high + low + close) / 3
        return {
            "pivot": pivot,
            "pivot_r1": 2 * pivot - low,
            "pivot_s1": 2 * pivot - high,
        }
