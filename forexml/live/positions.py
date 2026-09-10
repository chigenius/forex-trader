"""Open position store — the live loop's restart safety net.

Unlike the trade ledger (immutable, append-only history of everything
that happened) this table holds only currently-open positions and is
mutated in place: a position is written on open, updated as MAE/MFE
accrue, and deleted on close. If the process restarts mid-trade, the loop
reloads its open positions from here instead of forgetting about them —
without this, a crash during a live paper-trading run would silently
drop whatever was open at the time.
"""
from __future__ import annotations

from pathlib import Path

import polars as pl
import duckdb

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS open_positions (
    trade_id VARCHAR PRIMARY KEY,
    signal_id VARCHAR,
    strategy_name VARCHAR,
    symbol VARCHAR,
    direction VARCHAR,
    entry_price DOUBLE,
    stop_price DOUBLE,
    target_price DOUBLE,
    size_units DOUBLE,
    opened_at TIMESTAMP,
    time_stop_at TIMESTAMP,
    mae DOUBLE,
    mfe DOUBLE
)
"""

_COLUMNS = [
    "trade_id", "signal_id", "strategy_name", "symbol", "direction",
    "entry_price", "stop_price", "target_price", "size_units",
    "opened_at", "time_stop_at", "mae", "mfe",
]


class OpenPositionStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(self.path))
        self._con.execute(_CREATE_TABLE)

    def save(self, position) -> None:
        placeholders = ",".join(["?"] * len(_COLUMNS))
        self._con.execute(
            f"INSERT OR REPLACE INTO open_positions VALUES ({placeholders})",
            [getattr(position, col) for col in _COLUMNS],
        )

    def delete(self, trade_id: str) -> None:
        self._con.execute("DELETE FROM open_positions WHERE trade_id = ?", [trade_id])

    def load_all(self) -> pl.DataFrame:
        return self._con.execute("SELECT * FROM open_positions").pl()

    def close(self) -> None:
        self._con.close()
