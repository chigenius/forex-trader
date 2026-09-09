from __future__ import annotations

from .base import Indicator


class SimpleMovingAverage(Indicator):
    def __init__(self, period: int):
        self.period = period
        self.lookback = period

    @property
    def output_names(self) -> list[str]:
        return [f"sma_{self.period}"]

    def compute(self, bars):
        if bars.height < self.period:
            return self._insufficient()
        value = float(bars["close"].tail(self.period).mean())
        return {f"sma_{self.period}": value}


class ExponentialMovingAverage(Indicator):
    def __init__(self, period: int):
        self.period = period
        # A few extra periods of warm-up so the EMA has converged rather
        # than starting cold at the SMA seed.
        self.lookback = period * 3

    @property
    def output_names(self) -> list[str]:
        return [f"ema_{self.period}"]

    def compute(self, bars):
        if bars.height < self.period:
            return self._insufficient()
        window = bars.tail(min(bars.height, self.lookback))
        ema = window["close"].ewm_mean(span=self.period, adjust=False)
        return {f"ema_{self.period}": float(ema[-1])}
