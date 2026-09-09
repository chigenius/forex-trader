"""Price history storage: Parquet partitioned by symbol/timeframe/year,
queried through DuckDB.

Multi-year backtests read large sequential column scans — exactly what a
columnar layout is for. A row-oriented database would make the common
query (all M1 bars for EURUSD across a decade) unnecessarily slow.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import duckdb
import polars as pl

from .adapters.base import BAR_SCHEMA


class BarStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _partition_path(self, symbol: str, timeframe: str, year: int) -> Path:
        return self.root / symbol.upper() / timeframe / f"{year}.parquet"

    def write(self, df: pl.DataFrame, symbol: str, timeframe: str) -> None:
        """Merge bars into yearly partitions, de-duplicated by timestamp
        (last write wins for a given timestamp)."""
        if df.is_empty():
            return
        by_year = df.with_columns(
            pl.col("timestamp_utc").dt.year().alias("_year")
        ).partition_by("_year", as_dict=True)

        for key, year_df in by_year.items():
            year = key[0] if isinstance(key, tuple) else key
            path = self._partition_path(symbol, timeframe, year)
            path.parent.mkdir(parents=True, exist_ok=True)
            year_df = year_df.drop("_year")
            if path.exists():
                existing = pl.read_parquet(path)
                merged = (
                    pl.concat([existing, year_df], how="diagonal_relaxed")
                    .unique(subset=["timestamp_utc"], keep="last")
                    .sort("timestamp_utc")
                )
            else:
                merged = year_df.sort("timestamp_utc")
            merged.write_parquet(path)

    def read(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> pl.DataFrame:
        directory = self.root / symbol.upper() / timeframe
        if not directory.exists() or not any(directory.glob("*.parquet")):
            return pl.DataFrame(schema=BAR_SCHEMA)
        glob = str(directory / "*.parquet")
        con = duckdb.connect()
        try:
            arrow = con.execute(
                "SELECT * FROM read_parquet(?) "
                "WHERE timestamp_utc >= ? AND timestamp_utc < ? "
                "ORDER BY timestamp_utc",
                [glob, start, end],
            ).arrow()
        finally:
            con.close()
        df = pl.from_arrow(arrow)
        # DuckDB round-trips timezone-aware timestamps under a different
        # (but equivalent) zone name ("Etc/UTC" vs "UTC"), which polars
        # treats as a distinct dtype for comparison purposes. Normalize so
        # a store-backed source is interchangeable with any other.
        return df.with_columns(pl.col("timestamp_utc").dt.convert_time_zone("UTC"))

    def coverage(self, symbol: str, timeframe: str) -> tuple[datetime, datetime] | None:
        directory = self.root / symbol.upper() / timeframe
        if not directory.exists() or not any(directory.glob("*.parquet")):
            return None
        con = duckdb.connect()
        try:
            row = con.execute(
                "SELECT min(timestamp_utc), max(timestamp_utc) FROM read_parquet(?)",
                [str(directory / "*.parquet")],
            ).fetchone()
        finally:
            con.close()
        return (row[0], row[1]) if row else None
