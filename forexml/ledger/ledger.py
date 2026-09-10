"""Immutable trade ledger — every fill, modification and close.

Append-only by construction: there is no update or delete method. A
correction is a new row, never a mutation of history, so the ledger can
always be replayed to reconstruct exactly what happened.

A one-shot backtest is fine with the default in-memory-only ledger — it
lives exactly as long as the run. A continuously-running paper/live loop
is not: a crash between restarts must not silently erase trade history,
only the in-flight open positions get their own restart-safety net
(`forexml.live.positions.OpenPositionStore`). Pass `persist_path` to back
this ledger with the same DuckDB-on-disk pattern used by the signal store
and decision log.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import duckdb
import polars as pl

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS ledger (
    trade_id VARCHAR,
    signal_id VARCHAR,
    strategy_name VARCHAR,
    symbol VARCHAR,
    direction VARCHAR,
    event VARCHAR,
    timestamp_utc TIMESTAMP,
    price DOUBLE,
    size_units DOUBLE,
    commission DOUBLE,
    swap DOUBLE,
    realised_pnl DOUBLE,
    mae DOUBLE,
    mfe DOUBLE,
    realised_r DOUBLE
)
"""

_COLUMNS = [
    "trade_id", "signal_id", "strategy_name", "symbol", "direction", "event",
    "timestamp_utc", "price", "size_units", "commission", "swap",
    "realised_pnl", "mae", "mfe", "realised_r",
]

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
    def __init__(self, persist_path: Path | str | None = None) -> None:
        self._entries: list[LedgerEntry] = []
        self._con: duckdb.DuckDBPyConnection | None = None
        if persist_path is not None:
            path = Path(persist_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._con = duckdb.connect(str(path))
            self._con.execute(_CREATE_TABLE)
            self._load_existing()

    def _load_existing(self) -> None:
        rows = self._con.execute(f"SELECT {', '.join(_COLUMNS)} FROM ledger ORDER BY timestamp_utc").fetchall()
        self._entries = [LedgerEntry(**dict(zip(_COLUMNS, row))) for row in rows]

    def record(self, entry: LedgerEntry) -> None:
        self._entries.append(entry)
        if self._con is not None:
            placeholders = ",".join(["?"] * len(_COLUMNS))
            self._con.execute(
                f"INSERT INTO ledger ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
                [getattr(entry, col) for col in _COLUMNS],
            )

    def close(self) -> None:
        if self._con is not None:
            self._con.close()

    def as_dataframe(self) -> pl.DataFrame:
        if not self._entries:
            return pl.DataFrame(schema=_SCHEMA)
        return pl.DataFrame([asdict(e) for e in self._entries], schema=_SCHEMA)

    def closed_trades(self) -> pl.DataFrame:
        return self.as_dataframe().filter(pl.col("event") == "close")
