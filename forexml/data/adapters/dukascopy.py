"""Dukascopy adapter — bulk historical tick data, the primary Phase 1 source.

Dukascopy publishes free historical tick data as hourly `.bi5` files:
LZMA-compressed, fixed-width 20-byte binary records of
`(ms_offset: uint32 BE, ask: uint32 BE, bid: uint32 BE, ask_vol: float32 BE, bid_vol: float32 BE)`.
Prices are integers scaled by the instrument's point value (3 decimals
for JPY crosses, 5 for everything else).

An hour with no published file (weekend, holiday, or simply no ticks) is
not an ingestion error — it is skipped. Gap detection on the resulting
bars is the validation layer's job (see `forexml.data.validation`), which
can distinguish a real gap from an expected quiet period.
"""
from __future__ import annotations

import lzma
import struct
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import polars as pl

from ..resample import ticks_to_bars
from .base import DataAdapter

_RECORD = struct.Struct(">IIIff")
_BASE_URL = "https://datafeed.dukascopy.com/datafeed"

TICK_SCHEMA = ["timestamp_utc", "bid", "ask", "bid_volume", "ask_volume"]


def _point_value(symbol: str) -> float:
    return 1000.0 if "JPY" in symbol.upper() else 100000.0


class DukascopyAdapter(DataAdapter):
    name = "dukascopy"

    def __init__(self, opener: urllib.request.OpenerDirector | None = None):
        self._opener = opener or urllib.request.build_opener()

    def _hour_url(self, symbol: str, hour: datetime) -> str:
        # Dukascopy's month path segment is zero-indexed (January == "00").
        return (
            f"{_BASE_URL}/{symbol.upper()}/{hour.year:04d}/{hour.month - 1:02d}/"
            f"{hour.day:02d}/{hour.hour:02d}h_ticks.bi5"
        )

    def decode_bi5(self, compressed: bytes, hour: datetime, symbol: str) -> list[tuple]:
        """Decompress and unpack one hour's tick file into raw rows."""
        if not compressed:
            return []
        raw = lzma.decompress(compressed)
        point = _point_value(symbol)
        rows = []
        for offset in range(0, len(raw) - _RECORD.size + 1, _RECORD.size):
            ms, ask_i, bid_i, ask_vol, bid_vol = _RECORD.unpack_from(raw, offset)
            ts = hour + timedelta(milliseconds=ms)
            rows.append((ts, bid_i / point, ask_i / point, bid_vol, ask_vol))
        return rows

    def fetch_ticks(self, symbol: str, start: datetime, end: datetime) -> pl.DataFrame:
        rows: list[tuple] = []
        hour = start.replace(minute=0, second=0, microsecond=0)
        while hour < end:
            try:
                with self._opener.open(self._hour_url(symbol, hour), timeout=30) as resp:
                    compressed = resp.read()
            except (urllib.error.URLError, TimeoutError, OSError):
                hour += timedelta(hours=1)
                continue
            for ts, bid, ask, bid_vol, ask_vol in self.decode_bi5(compressed, hour, symbol):
                if start <= ts < end:
                    rows.append((ts, bid, ask, bid_vol, ask_vol))
            hour += timedelta(hours=1)
        if not rows:
            return pl.DataFrame(schema={"timestamp_utc": pl.Datetime("us", "UTC"), **{c: pl.Float64 for c in TICK_SCHEMA[1:]}})
        return pl.DataFrame(rows, schema=TICK_SCHEMA, orient="row").with_columns(
            pl.col("timestamp_utc").dt.replace_time_zone("UTC")
        )

    def fetch_bars(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> pl.DataFrame:
        ticks = self.fetch_ticks(symbol, start, end)
        if ticks.is_empty():
            return ticks
        bars = ticks_to_bars(ticks, timeframe)
        return bars.with_columns(pl.lit(symbol).alias("symbol"))
