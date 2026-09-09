"""Tick-to-bar and bar-to-bar aggregation.

Performed in-framework (rather than trusting each vendor's own bars) so
bar boundaries are identical no matter which source produced the
underlying data. Bars are left-labeled and left-closed: a bar's timestamp
is its OPEN time, and the bar is only "complete" once the next bar's open
time has arrived. That is what lets the feature engine's point-in-time
guard treat "the current incomplete bar" as a well-defined thing to
exclude.
"""
from __future__ import annotations

import polars as pl

TIMEFRAME_TO_POLARS = {
    "M1": "1m",
    "M5": "5m",
    "M15": "15m",
    "M30": "30m",
    "H1": "1h",
    "H4": "4h",
    "D1": "1d",
}


def _every(timeframe: str) -> str:
    if timeframe not in TIMEFRAME_TO_POLARS:
        raise ValueError(f"unknown timeframe: {timeframe!r}")
    return TIMEFRAME_TO_POLARS[timeframe]


def ticks_to_bars(ticks: pl.DataFrame, timeframe: str) -> pl.DataFrame:
    """Aggregate raw bid/ask ticks (columns: timestamp_utc, bid, ask, ...)
    into OHLCV bars keyed on trade mid-price, retaining last bid/ask for
    the cost model.
    """
    ticks = ticks.sort("timestamp_utc").with_columns(
        ((pl.col("bid") + pl.col("ask")) / 2).alias("mid")
    )
    return ticks.group_by_dynamic(
        "timestamp_utc", every=_every(timeframe), closed="left", label="left"
    ).agg(
        pl.col("mid").first().alias("open"),
        pl.col("mid").max().alias("high"),
        pl.col("mid").min().alias("low"),
        pl.col("mid").last().alias("close"),
        pl.len().alias("volume"),
        pl.col("bid").last().alias("bid_close"),
        pl.col("ask").last().alias("ask_close"),
    )


def bars_to_bars(bars: pl.DataFrame, timeframe: str) -> pl.DataFrame:
    """Re-aggregate finer OHLC bars into a coarser timeframe."""
    keep_symbol = "symbol" in bars.columns
    agg = [
        pl.col("open").first().alias("open"),
        pl.col("high").max().alias("high"),
        pl.col("low").min().alias("low"),
        pl.col("close").last().alias("close"),
        pl.col("volume").sum().alias("volume"),
        pl.col("bid_close").last().alias("bid_close"),
        pl.col("ask_close").last().alias("ask_close"),
    ]
    if keep_symbol:
        agg.append(pl.col("symbol").first().alias("symbol"))
    return bars.sort("timestamp_utc").group_by_dynamic(
        "timestamp_utc", every=_every(timeframe), closed="left", label="left"
    ).agg(agg)
