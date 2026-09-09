"""Decision log writer.

Append-only, backed by the same DuckDB-on-disk pattern as the signal
store (brief section 4.8 wants every non-deterministic decision
reconstructable after the fact, which means durable storage now rather
than added later under pressure).
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import polars as pl

from .schema import DecisionRecord

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS decisions (
    decision_id VARCHAR PRIMARY KEY,
    timestamp_utc TIMESTAMP,
    component VARCHAR,
    model_id VARCHAR,
    model_version VARCHAR,
    inputs VARCHAR,
    prompt VARCHAR,
    raw_output VARCHAR,
    parsed_action VARCHAR,
    outcome_ref VARCHAR
)
"""


class DecisionLog:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(self.path))
        self._con.execute(_CREATE_TABLE)

    def record(self, decision: DecisionRecord) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                decision.decision_id,
                decision.timestamp_utc,
                decision.component,
                decision.model_id,
                decision.model_version,
                json.dumps(decision.inputs, default=str),
                decision.prompt,
                decision.raw_output,
                json.dumps(decision.parsed_action, default=str) if decision.parsed_action else None,
                decision.outcome_ref,
            ],
        )

    def read_all(self) -> pl.DataFrame:
        return self._con.execute("SELECT * FROM decisions ORDER BY timestamp_utc").pl()

    def close(self) -> None:
        self._con.close()
