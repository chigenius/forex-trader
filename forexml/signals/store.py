"""Signal store — persists every signal (taken and rejected) as the
future ML training set (brief section 4.4). Outcomes are populated by a
separate resolution pass once a trade closes, so records need real
in-place UPDATE support — this is why the store is backed by DuckDB's own
on-disk format rather than plain Parquet, which has no update story.
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import polars as pl

from .schema import SignalEvent

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS signals (
    schema_version INTEGER,
    signal_id VARCHAR PRIMARY KEY,
    timestamp_utc TIMESTAMP,
    strategy_name VARCHAR,
    strategy_version VARCHAR,
    symbol VARCHAR,
    direction VARCHAR,
    status VARCHAR,
    rejection_reason VARCHAR,
    features VARCHAR,
    intended_entry DOUBLE,
    stop DOUBLE,
    target DOUBLE,
    outcome VARCHAR,
    mae DOUBLE,
    mfe DOUBLE,
    realised_r DOUBLE
)
"""

_COLUMNS = [
    "schema_version", "signal_id", "timestamp_utc", "strategy_name", "strategy_version",
    "symbol", "direction", "status", "rejection_reason", "features",
    "intended_entry", "stop", "target", "outcome", "mae", "mfe", "realised_r",
]


class SignalStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(self.path))
        self._con.execute(_CREATE_TABLE)

    def append(self, event: SignalEvent) -> None:
        placeholders = ",".join(["?"] * len(_COLUMNS))
        self._con.execute(
            f"INSERT OR REPLACE INTO signals VALUES ({placeholders})",
            [
                event.schema_version,
                event.signal_id,
                event.timestamp_utc,
                event.strategy_name,
                event.strategy_version,
                event.symbol,
                event.direction,
                event.status,
                event.rejection_reason,
                json.dumps(event.features, default=str),
                event.intended_entry,
                event.stop,
                event.target,
                event.outcome,
                event.mae,
                event.mfe,
                event.realised_r,
            ],
        )

    def resolve_outcome(
        self, signal_id: str, outcome: str, mae: float, mfe: float, realised_r: float
    ) -> None:
        self._con.execute(
            "UPDATE signals SET outcome = ?, mae = ?, mfe = ?, realised_r = ? WHERE signal_id = ?",
            [outcome, mae, mfe, realised_r, signal_id],
        )

    def read_all(self) -> pl.DataFrame:
        return self._con.execute("SELECT * FROM signals ORDER BY timestamp_utc").pl()

    def read_open(self) -> pl.DataFrame:
        return self._con.execute(
            "SELECT * FROM signals WHERE status = 'taken' AND outcome = 'open' "
            "ORDER BY timestamp_utc"
        ).pl()

    def close(self) -> None:
        self._con.close()
