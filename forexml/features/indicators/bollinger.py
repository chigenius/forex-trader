from __future__ import annotations

from .base import Indicator


class BollingerBands(Indicator):
    def __init__(self, period: int = 20, num_std: float = 2.0):
        self.period = period
        self.num_std = num_std
        self.lookback = period

    @property
    def output_names(self) -> list[str]:
        p, s = self.period, self.num_std
        return [f"bb_{p}_{s}_mid", f"bb_{p}_{s}_upper", f"bb_{p}_{s}_lower"]

    def compute(self, bars):
        if bars.height < self.period:
            return self._insufficient()
        window = bars.tail(self.period)["close"]
        mid = float(window.mean())
        std = float(window.std() or 0.0)
        p, s = self.period, self.num_std
        return {
            f"bb_{p}_{s}_mid": mid,
            f"bb_{p}_{s}_upper": mid + s * std,
            f"bb_{p}_{s}_lower": mid - s * std,
        }
