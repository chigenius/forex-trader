from __future__ import annotations

from .base import EvalContext, ExitRule, register_exit


@register_exit("stop_loss:atr_multiple")
class AtrMultipleStop(ExitRule):
    def compute(self, entry_price, direction, ctx: EvalContext, stop_price=None) -> float:
        atr_key = self.spec.get("atr_key", "atr_14")
        atr = ctx.features.get(atr_key)
        if atr is None:
            raise ValueError(f"atr_multiple stop: feature {atr_key!r} unavailable in feature vector")
        distance = self.spec["multiple"] * atr
        return entry_price - distance if direction == "long" else entry_price + distance


@register_exit("stop_loss:fixed_pips")
class FixedPipsStop(ExitRule):
    def compute(self, entry_price, direction, ctx: EvalContext, stop_price=None) -> float:
        pip = 0.01 if "JPY" in ctx.symbol.upper() else 0.0001
        distance = self.spec["pips"] * pip
        return entry_price - distance if direction == "long" else entry_price + distance


@register_exit("take_profit:r_multiple")
class RMultipleTarget(ExitRule):
    def compute(self, entry_price, direction, ctx: EvalContext, stop_price=None) -> float:
        if stop_price is None:
            raise ValueError("r_multiple target requires the already-computed stop price")
        r = abs(entry_price - stop_price)
        distance = self.spec["multiple"] * r
        return entry_price + distance if direction == "long" else entry_price - distance


@register_exit("take_profit:fixed_pips")
class FixedPipsTarget(ExitRule):
    def compute(self, entry_price, direction, ctx: EvalContext, stop_price=None) -> float:
        pip = 0.01 if "JPY" in ctx.symbol.upper() else 0.0001
        distance = self.spec["pips"] * pip
        return entry_price + distance if direction == "long" else entry_price - distance
