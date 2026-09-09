"""Common interface every data adapter implements.

All adapters return bars in this exact schema so tick-to-bar aggregation,
validation and storage are source-agnostic — the feature engine and
backtester never know or care whether a bar came from Dukascopy, HistData
or a live MT5 feed.
"""
from __future__ import annotations

import abc
from datetime import datetime

import polars as pl

BAR_SCHEMA: dict[str, pl.DataType] = {
    "timestamp_utc": pl.Datetime("us", "UTC"),
    "symbol": pl.Utf8,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "bid_close": pl.Float64,
    "ask_close": pl.Float64,
}


class DataAdapter(abc.ABC):
    """Source of historical or live OHLCV bars, in UTC.

    Broker/vendor server time must be converted to UTC at the adapter
    boundary; it must never propagate inward.
    """

    name: str

    @abc.abstractmethod
    def fetch_bars(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> pl.DataFrame:
        """Return bars for `symbol` at `timeframe` over [start, end), UTC.

        Must conform to BAR_SCHEMA. An adapter with no data for the range
        returns an empty frame with that schema, never raises.
        """
        raise NotImplementedError
