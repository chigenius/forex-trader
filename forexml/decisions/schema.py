"""Decision log schema (brief section 4.8).

In Phase 1 there are no non-deterministic decisions to record — every
outcome is fully determined by rules and inputs. This exists anyway so
the ML and agent layers have somewhere to write from day one, and so the
discipline of recording every non-deterministic call is established
before there is any pressure to skip it. Without this, an agent's
behaviour cannot be audited, debugged, or improved: you get results with
no way to reconstruct why.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class DecisionRecord(BaseModel):
    decision_id: str
    timestamp_utc: datetime
    component: str
    model_id: str | None = None
    model_version: str | None = None
    inputs: dict[str, Any]
    prompt: str | None = None
    raw_output: str | None = None
    parsed_action: dict[str, Any] | None = None
    outcome_ref: str | None = None
