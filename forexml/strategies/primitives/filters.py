from __future__ import annotations

from .base import EvalContext, Filter, register_filter


@register_filter("session")
class SessionFilter(Filter):
    def evaluate(self, ctx: EvalContext) -> tuple[bool, str | None]:
        session = self.spec["value"]
        ok = bool(ctx.features.get(f"session_{session}"))
        return ok, (None if ok else f"session filter: not in {session!r} session")


@register_filter("volatility_regime")
class VolatilityRegimeFilter(Filter):
    def evaluate(self, ctx: EvalContext) -> tuple[bool, str | None]:
        operator = self.spec.get("operator", "in")
        allowed = self.spec["value"]
        current = ctx.features.get("volatility_regime")
        if operator == "in":
            ok = current in allowed
        elif operator == "==":
            ok = current == allowed
        else:
            raise ValueError(f"unsupported operator for volatility_regime filter: {operator!r}")
        return ok, (None if ok else f"volatility_regime filter: {current!r} not in {allowed!r}")


@register_filter("news_blackout")
class NewsBlackoutFilter(Filter):
    def evaluate(self, ctx: EvalContext) -> tuple[bool, str | None]:
        minutes_before = self.spec.get("minutes_before", 30)
        minutes_after = self.spec.get("minutes_after", 15)
        blocked = ctx.calendar.is_blackout(
            ctx.symbol, ctx.timestamp, minutes_before, minutes_after, min_impact="high"
        )
        return (not blocked), ("news blackout active" if blocked else None)
