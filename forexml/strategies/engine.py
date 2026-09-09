"""Rule engine — evaluates one strategy definition against a feature
vector and emits a signal.

Critical behaviour (brief section 4.3): a near-miss — conditions almost
met, or met but blocked by a filter — is still emitted, flagged
`rejected` with a reason. Rejected signals are training data, not noise.
To keep bar-by-bar evaluation from flooding the signal store with
"nothing happened" rows, only two situations produce a `RuleEvaluation`
at all: every entry condition is met (whether or not filters then block
it), or at least `near_miss_threshold` of them are met. Anything further
from a signal than that returns `None` and is not logged — that
threshold, not silent condition failure, is the one judgment call in this
module and should be tuned per strategy if the resulting log volume or
its usefulness as near-miss training data is wrong for a given case.

Status gating (brief section 4.3) is a caller responsibility: this engine
evaluates `proposed`/`approved` strategies exactly like `active` ones (so
they can be backtested), and does not itself decide execution
eligibility. Whatever drives this engine — the backtester or the live
loop — must check `lifecycle.is_tradeable(strategy.status)` before ever
routing a `taken` evaluation past the signal bus into risk/execution.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

import polars as pl

from ..features.context.calendar import EconomicCalendar
from ..features.engine import FeatureVector
from .primitives.base import EvalContext, build_condition, build_exit, build_filter
from .schema import StrategyDefinition


@dataclass
class RuleEvaluation:
    signal_id: str
    timestamp_utc: datetime
    strategy_name: str
    strategy_version: str
    symbol: str
    direction: str | None
    status: str  # "taken" | "rejected"
    rejection_reason: str | None
    features: dict = field(default_factory=dict)
    intended_entry: float | None = None
    stop: float | None = None
    target: float | None = None


class StrategyEngine:
    def __init__(self, definition: StrategyDefinition, near_miss_threshold: float = 0.5):
        self.definition = definition
        self.near_miss_threshold = near_miss_threshold
        self._filters = [build_filter(f.model_dump()) for f in definition.filters]
        self._conditions = [build_condition(c.model_dump()) for c in definition.entry.conditions]
        self._stop_rule = build_exit(definition.exit.stop_loss.model_dump(), "stop_loss")
        self._target_rule = build_exit(definition.exit.take_profit.model_dump(), "take_profit")

    def evaluate(
        self,
        symbol: str,
        feature_vector: FeatureVector,
        bars: pl.DataFrame,
        calendar: EconomicCalendar,
    ) -> RuleEvaluation | None:
        ctx = EvalContext(
            symbol=symbol,
            timestamp=feature_vector.timestamp_utc,
            timeframe=feature_vector.timeframe,
            features=feature_vector.values,
            bars=bars,
            calendar=calendar,
        )

        condition_results = [c.evaluate(ctx) for c in self._conditions]
        met_count = sum(1 for met, _ in condition_results if met)
        total = len(condition_results)
        all_met = total > 0 and met_count == total
        near_miss = (not all_met) and total > 0 and (met_count / total) >= self.near_miss_threshold

        if not all_met and not near_miss:
            return None

        direction = self._infer_direction()

        if not all_met:
            reasons = "; ".join(r for met, r in condition_results if not met and r)
            return self._build(ctx, direction, "rejected", f"near-miss entry: {reasons}")

        filter_results = [f.evaluate(ctx) for f in self._filters]
        first_failure = next((reason for ok, reason in filter_results if not ok), None)
        if first_failure is not None:
            return self._build(ctx, direction, "rejected", first_failure)

        if bars.is_empty():
            return self._build(ctx, direction, "rejected", "no bar data available for entry price")

        entry_price = float(bars["close"][-1])
        stop_price = self._stop_rule.compute(entry_price, direction, ctx)
        target_price = self._target_rule.compute(entry_price, direction, ctx, stop_price=stop_price)

        evaluation = self._build(ctx, direction, "taken", None)
        evaluation.intended_entry = entry_price
        evaluation.stop = stop_price
        evaluation.target = target_price
        return evaluation

    def _infer_direction(self) -> str:
        for condition in self._conditions:
            implied = getattr(condition, "implied_direction", None)
            if implied:
                return implied
        return "long"

    def _build(self, ctx: EvalContext, direction, status, reason) -> RuleEvaluation:
        return RuleEvaluation(
            signal_id=str(uuid.uuid4()),
            timestamp_utc=ctx.timestamp,
            strategy_name=self.definition.name,
            strategy_version=self.definition.version_hash,
            symbol=ctx.symbol,
            direction=direction,
            status=status,
            rejection_reason=reason,
            features=dict(ctx.features),
        )
