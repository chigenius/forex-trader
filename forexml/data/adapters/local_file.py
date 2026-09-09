"""Generic local CSV/Parquet adapter.

Used for synthetic/test data and for any manually-prepared bar file that
doesn't come from a supported vendor. Also the natural target format for
Dukascopy/HistData/MT5 ingestion once staged to disk before validation.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import polars as pl

from .base import BAR_SCHEMA, DataAdapter


class LocalFileAdapter(DataAdapter):
    name = "local_file"

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def fetch_bars(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> pl.DataFrame:
        if self.path.suffix == ".parquet":
            df = pl.read_parquet(self.path)
        else:
            df = pl.read_csv(self.path, try_parse_dates=True)

        if df["timestamp_utc"].dtype == pl.Utf8:
            df = df.with_columns(pl.col("timestamp_utc").str.to_datetime())
        if df.schema["timestamp_utc"].time_zone is None:
            df = df.with_columns(pl.col("timestamp_utc").dt.replace_time_zone("UTC"))

        if "symbol" in df.columns:
            df = df.filter(pl.col("symbol") == symbol)
        else:
            df = df.with_columns(pl.lit(symbol).alias("symbol"))

        df = df.filter(
            (pl.col("timestamp_utc") >= start) & (pl.col("timestamp_utc") < end)
        )

        if "bid_close" not in df.columns:
            df = df.with_columns(pl.col("close").alias("bid_close"))
        if "ask_close" not in df.columns:
            df = df.with_columns(pl.col("close").alias("ask_close"))
        if "volume" not in df.columns:
            df = df.with_columns(pl.lit(0.0).alias("volume"))

        return df.select(list(BAR_SCHEMA.keys())).sort("timestamp_utc")
