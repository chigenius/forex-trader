"""HistData.com adapter — fallback source for gaps in Dukascopy coverage.

HistData has no stable automated bulk-download API: each file download is
gated by a per-request confirmation token meant for a browser, not a
script. Rather than reverse-engineer that gate (fragile and against the
spirit of the site's terms), this adapter ingests the ASCII/CSV exports a
human downloads manually from histdata.com and drops into a local
directory — "ASCII M1 Data" export format:
`SYMBOL,YYYYMMDD HHMMSS,open,high,low,close,volume` (semicolon-delimited,
no header).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import polars as pl

from ..resample import bars_to_bars
from .base import BAR_SCHEMA, DataAdapter


class HistDataAdapter(DataAdapter):
    name = "histdata"

    def __init__(self, export_dir: Path | str):
        self.export_dir = Path(export_dir)

    def _locate(self, symbol: str) -> Path:
        matches = sorted(self.export_dir.glob(f"*{symbol.upper()}*.csv"))
        if not matches:
            raise FileNotFoundError(
                f"no HistData export found for {symbol} under {self.export_dir}"
            )
        return matches[0]

    def fetch_bars(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> pl.DataFrame:
        path = self._locate(symbol)
        raw = pl.read_csv(
            path,
            has_header=False,
            new_columns=["timestamp_str", "open", "high", "low", "close", "volume"],
            separator=";",
        )
        df = raw.with_columns(
            pl.col("timestamp_str")
            .str.strptime(pl.Datetime("us"), "%Y%m%d %H%M%S")
            .dt.replace_time_zone("UTC")
            .alias("timestamp_utc"),
            pl.lit(symbol).alias("symbol"),
        ).drop("timestamp_str")

        df = df.filter(
            (pl.col("timestamp_utc") >= start) & (pl.col("timestamp_utc") < end)
        )
        df = df.with_columns(
            pl.col("close").alias("bid_close"),
            pl.col("close").alias("ask_close"),
        )

        if timeframe != "M1":
            df = bars_to_bars(df, timeframe)

        return df.select(list(BAR_SCHEMA.keys())).sort("timestamp_utc")
