"""Signal event schema — the versioned contract between the rule engine
and every consumer of the signal bus (the signal store today; monitoring,
an agent, and the ML layer later). `schema_version` lets a future
consumer detect an old payload shape and handle it explicitly instead of
guessing.

This is also, verbatim, the schema in brief section 4.4 — the contract
between Phase 1 and the Phase 3 ML work.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

SCHEMA_VERSION = 1

SignalStatus = Literal["taken", "rejected"]
SignalOutcome = Literal["win", "loss", "timeout", "open"]


class SignalEvent(BaseModel):
    schema_version: int = SCHEMA_VERSION
    signal_id: str
    timestamp_utc: datetime
    strategy_name: str
    strategy_version: str
    symbol: str
    direction: str | None
    status: SignalStatus
    rejection_reason: str | None = None
    features: dict[str, Any]
    intended_entry: float | None = None
    stop: float | None = None
    target: float | None = None
    outcome: SignalOutcome | None = None
    mae: float | None = None
    mfe: float | None = None
    realised_r: float | None = None

    @classmethod
    def from_rule_evaluation(cls, evaluation) -> "SignalEvent":
        return cls(
            signal_id=evaluation.signal_id,
            timestamp_utc=evaluation.timestamp_utc,
            strategy_name=evaluation.strategy_name,
            strategy_version=evaluation.strategy_version,
            symbol=evaluation.symbol,
            direction=evaluation.direction,
            status=evaluation.status,
            rejection_reason=evaluation.rejection_reason,
            features=evaluation.features,
            intended_entry=evaluation.intended_entry,
            stop=evaluation.stop,
            target=evaluation.target,
            outcome="open" if evaluation.status == "taken" else None,
        )
