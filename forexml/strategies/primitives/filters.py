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


@register_filter("time_of_day")
class TimeOfDayFilter(Filter):
    """A UTC hour window with no assumption of a named regional session —
    for instruments with no real trading session (round-the-clock
    synthetic indices) where a trader still wants to bound *when* a
    strategy is allowed to fire, e.g. to hours they can personally
    monitor it or an empirically favorable window. `start_hour` >
    `end_hour` wraps past midnight (e.g. 22 -> 4).
    """

    def evaluate(self, ctx: EvalContext) -> tuple[bool, str | None]:
        start_hour = self.spec["start_hour"]
        end_hour = self.spec["end_hour"]
        hour = ctx.timestamp.hour
        if start_hour <= end_hour:
            ok = start_hour <= hour < end_hour
        else:
            ok = hour >= start_hour or hour < end_hour
        reason = None if ok else f"time_of_day filter: hour {hour} UTC not in [{start_hour}, {end_hour})"
        return ok, reason


@register_filter("volatility_spike_guard")
class VolatilitySpikeGuardFilter(Filter):
    """Blocks entry immediately after an abnormally large single bar —
    the synthetic-index analog of a news blackout. There is no
    macroeconomic calendar to sit out on a synthetic instrument, but an
    outsized bar is the same kind of shock a news blackout exists to
    avoid trading into on a currency pair; this grounds the guard in the
    price action itself instead of a calendar that has no meaning here.
    """

    def evaluate(self, ctx: EvalContext) -> tuple[bool, str | None]:
        atr_key = self.spec.get("atr_key", "atr_14")
        multiple = self.spec.get("multiple", 3.0)
        atr = ctx.features.get(atr_key)
        if atr is None or not atr or ctx.bars.is_empty():
            return True, None  # nothing to compare against — don't block
        last_bar = ctx.bars.tail(1)
        bar_range = float(last_bar["high"][0]) - float(last_bar["low"][0])
        spiked = bar_range > multiple * atr
        reason = (
            f"volatility_spike_guard: last bar range {bar_range:.5f} exceeds "
            f"{multiple}x {atr_key} ({atr:.5f})"
            if spiked
            else None
        )
        return (not spiked), reason
