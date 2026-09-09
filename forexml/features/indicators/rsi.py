from __future__ import annotations

import numpy as np

from .base import Indicator


class RelativeStrengthIndex(Indicator):
    def __init__(self, period: int = 14):
        self.period = period
        self.lookback = period + 1

    @property
    def output_names(self) -> list[str]:
        return [f"rsi_{self.period}"]

    def compute(self, bars):
        if bars.height < self.lookback:
            return self._insufficient()
        closes = bars.tail(self.lookback)["close"].to_numpy()
        deltas = np.diff(closes)
        gains = np.clip(deltas, a_min=0, a_max=None)
        losses = np.clip(-deltas, a_min=0, a_max=None)
        avg_gain = gains.mean()
        avg_loss = losses.mean()
        if avg_loss == 0:
            rsi = 100.0 if avg_gain > 0 else 50.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))
        return {f"rsi_{self.period}": float(rsi)}
