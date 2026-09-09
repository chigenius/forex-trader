"""ATR-percentile volatility regime classification — sufficient for
Phase 1 per the brief. The percentile is computed strictly from bars
already closed as of the point-in-time cutoff the feature engine already
applied; this module trusts that truncation and does no filtering of its
own on wall-clock time.
"""
from __future__ import annotations

import numpy as np
import polars as pl

_BAND_UPPER_BOUNDS = [(25.0, "low"), (75.0, "normal"), (95.0, "high")]  # else "extreme"


def classify_regime(
    closed: pl.DataFrame, period: int = 14, history: int = 100
) -> dict:
    min_bars = period + 2
    if closed.height < min_bars:
        return {"volatility_regime": None, "atr_percentile": None}

    high = closed["high"].to_numpy()
    low = closed["low"].to_numpy()
    close = closed["close"].to_numpy()
    prev_close = close[:-1]
    true_range = np.maximum.reduce(
        [
            high[1:] - low[1:],
            np.abs(high[1:] - prev_close),
            np.abs(low[1:] - prev_close),
        ]
    )
    if len(true_range) < period:
        return {"volatility_regime": None, "atr_percentile": None}

    atr_series = np.convolve(true_range, np.ones(period) / period, mode="valid")
    if len(atr_series) < 2:
        return {"volatility_regime": None, "atr_percentile": None}

    current = atr_series[-1]
    trailing = atr_series[:-1][-history:]
    if len(trailing) < 5:
        return {"volatility_regime": None, "atr_percentile": None}

    percentile = float((trailing < current).sum()) / len(trailing) * 100.0

    band = "extreme"
    for upper, name in _BAND_UPPER_BOUNDS:
        if percentile <= upper:
            band = name
            break

    return {"volatility_regime": band, "atr_percentile": percentile}
