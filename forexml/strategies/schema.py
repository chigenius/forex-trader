"""Strategy definition schema.

A strategy is configuration, not code (brief section 2): every YAML
definition is validated against this schema on load, with a clear,
structured error on failure — never a stack trace surfacing from deep
inside the rule engine.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field, field_validator

StrategyStatus = Literal["proposed", "approved", "active", "retired"]
Author = Literal["human", "agent"]

_VALID_TIMEFRAMES = {"M1", "M5", "M15", "M30", "H1", "H4", "D1"}


class FilterSpec(BaseModel):
    model_config = {"extra": "allow"}
    type: str


class ConditionSpec(BaseModel):
    model_config = {"extra": "allow"}
    type: str


class ExitLegSpec(BaseModel):
    model_config = {"extra": "allow"}
    type: str


class TimeStopSpec(BaseModel):
    bars: int = Field(gt=0)


class ExitSpec(BaseModel):
    stop_loss: ExitLegSpec
    take_profit: ExitLegSpec
    time_stop: TimeStopSpec | None = None


class EntrySpec(BaseModel):
    conditions: list[ConditionSpec] = Field(min_length=1)


class RiskSpec(BaseModel):
    risk_per_trade_pct: float = Field(gt=0, le=10)
    max_concurrent: int = Field(gt=0)


class StrategyDefinition(BaseModel):
    name: str
    symbols: list[str] = Field(min_length=1)
    timeframe: str
    status: StrategyStatus = "proposed"
    author: Author = "human"
    enabled: bool = True
    filters: list[FilterSpec] = Field(default_factory=list)
    entry: EntrySpec
    exit: ExitSpec
    risk: RiskSpec

    @field_validator("timeframe")
    @classmethod
    def _valid_timeframe(cls, v: str) -> str:
        if v not in _VALID_TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {sorted(_VALID_TIMEFRAMES)}, got {v!r}")
        return v

    @property
    def version_hash(self) -> str:
        """Content hash used as `strategy_version` in every signal record
        this definition produces — any edit changes the hash, so a signal
        can always be traced back to the exact rules that generated it.
        """
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]
