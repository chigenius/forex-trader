"""Immutable trade ledger — every fill, modification and close.

Append-only by construction: there is no update or delete method. A
correction is a new row, never a mutation of history, so the ledger can
always be replayed to reconstruct exactly what happened.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

import polars as pl

_SCHEMA = {
    "trade_id": pl.Utf8,
    "signal_id": pl.Utf8,
    "strategy_name": pl.Utf8,
    "symbol": pl.Utf8,
    "direction": pl.Utf8,
    "event": pl.Utf8,
    "timestamp_utc": pl.Datetime("us", "UTC"),
    "price": pl.Float64,
    "size_units": pl.Float64,
    "commission": pl.Float64,
    "swap": pl.Float64,
    "realised_pnl": pl.Float64,
    "mae": pl.Float64,
    "mfe": pl.Float64,
    "realised_r": pl.Float64,
}


@dataclass(frozen=True)
class LedgerEntry:
    trade_id: str
    signal_id: str | None
    strategy_name: str
    symbol: str
    direction: str
    event: str  # "open" | "modify" | "close"
    timestamp_utc: datetime
    price: float
    size_units: float
    commission: float = 0.0
    swap: float = 0.0
    realised_pnl: float | None = None
    mae: float | None = None
    mfe: float | None = None
    realised_r: float | None = None


class TradeLedger:
    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []

    def record(self, entry: LedgerEntry) -> None:
        self._entries.append(entry)

    def as_dataframe(self) -> pl.DataFrame:
        if not self._entries:
            return pl.DataFrame(schema=_SCHEMA)
        return pl.DataFrame([asdict(e) for e in self._entries], schema=_SCHEMA)

    def closed_trades(self) -> pl.DataFrame:
        return self.as_dataframe().filter(pl.col("event") == "close")
