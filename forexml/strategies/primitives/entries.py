from __future__ import annotations

from datetime import timedelta

import polars as pl

from ...features.context.sessions import DEFAULT_SESSIONS
from .base import Condition, EvalContext, register_condition

_REFERENCE_SESSIONS = {
    "asian_session_high": ("asian", "high"),
    "asian_session_low": ("asian", "low"),
    "london_session_high": ("london", "high"),
    "london_session_low": ("london", "low"),
}

_PRICE_FIELDS = {"open", "high", "low", "close"}

_OPERATORS = {
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
}


def _session_extreme(bars: pl.DataFrame, timestamp, session_name: str, kind: str) -> float | None:
    """Extreme (high/low) of the most recently *completed* occurrence of
    `session_name` as of `timestamp`. If that session hasn't ended yet
    today, falls back to yesterday's occurrence — never a still-forming
    one, which would be a look-ahead leak for a breakout strategy."""
    start_hour, end_hour = DEFAULT_SESSIONS[session_name]
    day = timestamp.date() if timestamp.hour >= end_hour else timestamp.date() - timedelta(days=1)
    session_bars = bars.filter(
        (pl.col("timestamp_utc").dt.date() == day)
        & (pl.col("timestamp_utc").dt.hour() >= start_hour)
        & (pl.col("timestamp_utc").dt.hour() < end_hour)
    )
    if session_bars.is_empty():
        return None
    return float(session_bars["high"].max() if kind == "high" else session_bars["low"].min())


@register_condition("price_breaks")
class PriceBreaksCondition(Condition):
    def evaluate(self, ctx: EvalContext) -> tuple[bool, str | None]:
        reference = self.spec["reference"]
        confirmation = self.spec.get("confirmation", "close")
        if reference not in _REFERENCE_SESSIONS:
            raise ValueError(f"unknown price_breaks reference: {reference!r}")
        session_name, kind = _REFERENCE_SESSIONS[reference]
        level = _session_extreme(ctx.bars, ctx.timestamp, session_name, kind)
        if level is None or ctx.bars.is_empty():
            return False, f"price_breaks: no data available for reference {reference!r}"

        price = float(ctx.bars[confirmation][-1])
        breaks_up = kind == "high"
        met = price > level if breaks_up else price < level
        reason = None if met else f"price_breaks: {confirmation}={price} did not break {reference}={level}"
        return met, reason

    @property
    def implied_direction(self) -> str:
        _, kind = _REFERENCE_SESSIONS[self.spec["reference"]]
        return "long" if kind == "high" else "short"


@register_condition("indicator_comparison")
class IndicatorComparisonCondition(Condition):
    def evaluate(self, ctx: EvalContext) -> tuple[bool, str | None]:
        left = self._resolve(self.spec["left"], ctx)
        right = self._resolve(self.spec["right"], ctx)
        op = self.spec["operator"]
        if left is None or right is None:
            return False, (
                f"indicator_comparison: missing value "
                f"({self.spec['left']}={left}, {self.spec['right']}={right})"
            )
        met = _OPERATORS[op](left, right)
        reason = None if met else f"indicator_comparison: {self.spec['left']}={left} !{op} {self.spec['right']}={right}"
        return met, reason

    @staticmethod
    def _resolve(token, ctx: EvalContext):
        if isinstance(token, (int, float)):
            return token
        if token in ctx.features:
            return ctx.features[token]
        if token in _PRICE_FIELDS and not ctx.bars.is_empty():
            return float(ctx.bars[token][-1])
        raise ValueError(
            f"unresolvable indicator_comparison token {token!r}: not a literal, not a raw "
            f"price field {sorted(_PRICE_FIELDS)}, and not present in the feature vector — "
            "register a matching indicator with the feature engine, or fix the strategy YAML"
        )
