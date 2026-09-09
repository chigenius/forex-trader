"""The point-in-time feature engine.

Hard requirement (brief section 4.2): a single function that, given a
symbol and timestamp, returns the complete feature vector as it would
have been known at that moment — used identically by the backtester and
the live loop. This is what makes backtest/live parity (brief section 2)
possible for anything downstream: if features differ between modes, every
guarantee built on top of them is void.

Look-ahead is made *structurally* difficult, not just discouraged: the
engine truncates whatever bars its `BarSource` hands it to those already
fully closed as of `timestamp`, regardless of what the source returns.
A backtest implementation that (for performance) slices from an
in-memory full-history DataFrame is therefore still protected even if it
carelessly hands the engine bars beyond the cutoff — the truncation on
line ~90 below is unconditional.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

import polars as pl

from .context.calendar import EconomicCalendar
from .context.regime import classify_regime
from .context.sessions import SessionClassifier
from .indicators.base import Indicator

TIMEFRAME_SECONDS: dict[str, int] = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
}


class BarSource(Protocol):
    def get_bars(
        self, symbol: str, timeframe: str, end_exclusive: datetime, min_bars: int
    ) -> pl.DataFrame:
        """Return up to `min_bars` most recent bars, ascending by
        timestamp_utc. Implementations MAY return bars at or after
        `end_exclusive` (e.g. a backtest slicing from full in-memory
        history); `FeatureEngine` truncates regardless.
        """
        ...


FeatureValue = float | str | bool | int | None


@dataclass
class FeatureVector:
    symbol: str
    timestamp_utc: datetime
    timeframe: str
    values: dict[str, FeatureValue]

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "timeframe": self.timeframe,
            **self.values,
        }

    def get(self, key: str, default: FeatureValue = None) -> FeatureValue:
        return self.values.get(key, default)


class FeatureEngine:
    def __init__(
        self,
        indicators: list[Indicator],
        calendar: EconomicCalendar | None = None,
        sessions: SessionClassifier | None = None,
        regime_period: int = 14,
        regime_history: int = 100,
    ):
        self.indicators = indicators
        self.calendar = calendar or EconomicCalendar.empty()
        self.sessions = sessions or SessionClassifier()
        self.regime_period = regime_period
        self.regime_history = regime_history
        self._max_lookback = max((ind.lookback for ind in indicators), default=1)

    def get_feature_vector(
        self,
        symbol: str,
        timestamp: datetime,
        timeframe: str,
        bar_source: BarSource,
        extra_lookback_bars: int = 300,
    ) -> FeatureVector:
        """THE single point-in-time entry point. Backtest and live callers
        must both go through this function and no other path to compute
        features — that is the parity guarantee.
        """
        if timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware (UTC)")

        bar_seconds = TIMEFRAME_SECONDS[timeframe]
        needed = self._max_lookback + max(self.regime_period + 2, 1) + extra_lookback_bars
        raw = bar_source.get_bars(symbol, timeframe, timestamp, needed)

        # Structural point-in-time guard. A bar is usable only once fully
        # closed: its open time + one full interval must be <= timestamp.
        # This excludes both the still-forming current bar and anything
        # the bar source erroneously returned from the future.
        cutoff = timestamp - timedelta(seconds=bar_seconds)
        closed = raw.filter(pl.col("timestamp_utc") <= cutoff).sort("timestamp_utc")
        if closed.height > needed:
            closed = closed.tail(needed)

        values: dict[str, FeatureValue] = {}
        for indicator in self.indicators:
            values.update(indicator.compute(closed))

        values["bars_available"] = closed.height
        values["last_closed_bar_ts"] = closed["timestamp_utc"][-1] if closed.height else None

        values.update(self.sessions.tag(timestamp))
        values.update(classify_regime(closed, self.regime_period, self.regime_history))
        values.update(self.calendar.tag(symbol, timestamp))

        return FeatureVector(
            symbol=symbol, timestamp_utc=timestamp, timeframe=timeframe, values=values
        )


class StoreBarSource:
    """Adapts a `forexml.data.store.BarStore` to the `BarSource` protocol."""

    def __init__(self, store):
        self.store = store

    def get_bars(self, symbol, timeframe, end_exclusive, min_bars):
        bar_seconds = TIMEFRAME_SECONDS[timeframe]
        start = end_exclusive - timedelta(seconds=bar_seconds * min_bars * 2)
        return self.store.read(symbol, timeframe, start, end_exclusive)


class DataFrameBarSource:
    """Adapts a single in-memory full-history DataFrame — the common case
    in a backtest, where the whole symbol history is loaded once and
    sliced repeatedly. Deliberately does NOT pre-filter on `end_exclusive`
    beyond a cheap head-room slice, so `FeatureEngine`'s own truncation is
    what is actually being exercised and tested.
    """

    def __init__(self, bars: pl.DataFrame):
        self.bars = bars.sort("timestamp_utc")

    def get_bars(self, symbol, timeframe, end_exclusive, min_bars):
        return self.bars
