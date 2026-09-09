from __future__ import annotations

import numpy as np

from .base import Indicator


class AverageTrueRange(Indicator):
    def __init__(self, period: int = 14):
        self.period = period
        self.lookback = period + 1  # need one prior close for true range

    @property
    def output_names(self) -> list[str]:
        return [f"atr_{self.period}"]

    def compute(self, bars):
        if bars.height < self.lookback:
            return self._insufficient()
        window = bars.tail(self.lookback)
        high = window["high"].to_numpy()
        low = window["low"].to_numpy()
        close = window["close"].to_numpy()
        prev_close = close[:-1]
        true_range = np.maximum.reduce(
            [
                high[1:] - low[1:],
                np.abs(high[1:] - prev_close),
                np.abs(low[1:] - prev_close),
            ]
        )
        return {f"atr_{self.period}": float(true_range.mean())}
