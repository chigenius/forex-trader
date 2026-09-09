"""Rolling statistics over another indicator's own time series — e.g. the
20-period median of ATR(14), which the brief's example strategy
references directly (`atr_14_median_20`). Computed from the same closed
bars every other indicator sees, so it inherits the point-in-time
guarantee for free.
"""
from __future__ import annotations

import numpy as np

from .base import Indicator


class RollingATRMedian(Indicator):
    def __init__(self, atr_period: int = 14, window: int = 20):
        self.atr_period = atr_period
        self.window = window
        self.lookback = atr_period + window

    @property
    def output_names(self) -> list[str]:
        return [f"atr_{self.atr_period}_median_{self.window}"]

    def compute(self, bars):
        if bars.height < self.lookback:
            return self._insufficient()
        high = bars["high"].to_numpy()
        low = bars["low"].to_numpy()
        close = bars["close"].to_numpy()
        prev_close = close[:-1]
        true_range = np.maximum.reduce(
            [
                high[1:] - low[1:],
                np.abs(high[1:] - prev_close),
                np.abs(low[1:] - prev_close),
            ]
        )
        atr_series = np.convolve(true_range, np.ones(self.atr_period) / self.atr_period, mode="valid")
        if len(atr_series) < self.window:
            return self._insufficient()
        median = float(np.median(atr_series[-self.window :]))
        return {f"atr_{self.atr_period}_median_{self.window}": median}
