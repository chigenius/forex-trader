"""MetaTrader 5 adapter — live bars and recent history.

The MT5 terminal reports timestamps in the broker's server time, not UTC.
That offset is applied and discarded right here, at the adapter boundary,
so no broker-local timestamp ever reaches the feature engine or the rule
engine. Requires the `MetaTrader5` package and a running terminal
(Windows only) — see the `mt5` extra in pyproject.toml.
"""
from __future__ import annotations

from datetime import datetime

import polars as pl

from .base import BAR_SCHEMA, DataAdapter

_TIMEFRAME_ATTR = {
    "M1": "TIMEFRAME_M1",
    "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}


class MT5Adapter(DataAdapter):
    name = "mt5"

    def __init__(self, server_utc_offset_hours: float = 0.0):
        self.server_utc_offset_hours = server_utc_offset_hours
        try:
            import MetaTrader5 as mt5  # noqa: N814
        except ImportError as exc:  # pragma: no cover - platform dependent
            raise RuntimeError(
                "MetaTrader5 package is not installed. Install the 'mt5' "
                "extra on a Windows host with a running MT5 terminal."
            ) from exc
        self._mt5 = mt5
        self._connected = False

    def connect(self) -> None:
        if not self._mt5.initialize():
            raise RuntimeError(f"MT5 initialize() failed: {self._mt5.last_error()}")
        self._connected = True

    def shutdown(self) -> None:
        if self._connected:
            self._mt5.shutdown()
            self._connected = False

    @staticmethod
    def _point(symbol: str) -> float:
        return 0.01 if "JPY" in symbol.upper() else 0.0001

    def fetch_bars(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> pl.DataFrame:
        if not self._connected:
            raise RuntimeError("call connect() before fetch_bars()")
        mt5 = self._mt5
        tf = getattr(mt5, _TIMEFRAME_ATTR[timeframe])
        rates = mt5.copy_rates_range(symbol, tf, start, end)
        if rates is None or len(rates) == 0:
            return pl.DataFrame(schema=BAR_SCHEMA)

        df = pl.DataFrame(rates)
        server_ts = pl.from_epoch(df["time"], time_unit="s")
        utc_ts = (server_ts - pl.duration(hours=self.server_utc_offset_hours)).dt.replace_time_zone(
            "UTC"
        )
        point = self._point(symbol)
        df = df.with_columns(
            utc_ts.alias("timestamp_utc"),
            pl.lit(symbol).alias("symbol"),
            pl.col("close").alias("bid_close"),
            (pl.col("close") + pl.col("spread") * point).alias("ask_close"),
        ).rename({"tick_volume": "volume"})
        return df.select(list(BAR_SCHEMA.keys())).sort("timestamp_utc")
