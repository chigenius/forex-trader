"""Swing high/low structure via fractal confirmation.

A candidate bar at index i is a confirmed swing high only once `confirm`
closed bars *after* it are also available and all sit below it — which is
precisely why this needs a point-in-time framework: the swing at bar i is
not knowable until `confirm` bars later, and a careless implementation
would tag it retroactively using data that didn't exist yet at bar i.
Here it is only ever reported once genuinely confirmed within the closed
window already truncated by the feature engine.
"""
from __future__ import annotations

from .base import Indicator


class SwingStructure(Indicator):
    def __init__(self, confirm: int = 3, window: int = 50):
        self.confirm = confirm
        self.window = window
        self.lookback = confirm * 2 + 1

    @property
    def output_names(self) -> list[str]:
        return ["swing_high", "swing_low"]

    def compute(self, bars):
        if bars.height < self.lookback:
            return self._insufficient()
        recent = bars.tail(self.window) if bars.height > self.window else bars
        highs = recent["high"].to_list()
        lows = recent["low"].to_list()
        n = len(highs)
        c = self.confirm

        swing_high = None
        swing_low = None
        # Scan from the most recent confirmable candidate backwards so we
        # report the latest confirmed swing point.
        for i in range(n - c - 1, c - 1, -1):
            if swing_high is None:
                window_h = highs[i - c : i + c + 1]
                if highs[i] == max(window_h) and window_h.count(highs[i]) == 1:
                    swing_high = highs[i]
            if swing_low is None:
                window_l = lows[i - c : i + c + 1]
                if lows[i] == min(window_l) and window_l.count(lows[i]) == 1:
                    swing_low = lows[i]
            if swing_high is not None and swing_low is not None:
                break

        return {"swing_high": swing_high, "swing_low": swing_low}
