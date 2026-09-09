"""Simulated execution — used by the backtester and paper mode.

Costs modelled pessimistically (brief section 2): spread comes from the
bar's own bid/ask where available, slippage is configurable and gets
worse in high volatility or around news, and commission and swap are
first-class inputs rather than an afterthought. A fill never happens at
the bare signal (close) price.
"""
from __future__ import annotations

from dataclasses import dataclass

from .base import ExecutionAdapter, Fill, Order


@dataclass(frozen=True)
class CostModel:
    base_slippage_pips: float = 0.2
    high_volatility_slippage_pips: float = 1.0
    news_slippage_pips: float = 3.0
    commission_per_lot: float = 7.0  # round-turn, per 100,000-unit lot
    swap_long_per_lot_per_day: float = -2.5
    swap_short_per_lot_per_day: float = 0.5

    def pip_size(self, symbol: str) -> float:
        return 0.01 if "JPY" in symbol.upper() else 0.0001

    def slippage_pips(self, volatility_regime: str | None, news_blackout: bool) -> float:
        if news_blackout:
            return self.news_slippage_pips
        if volatility_regime in ("high", "extreme"):
            return self.high_volatility_slippage_pips
        return self.base_slippage_pips

    def swap_for(self, direction: str, size_units: float, days_held: int) -> float:
        lots = size_units / 100_000
        per_day = self.swap_long_per_lot_per_day if direction == "long" else self.swap_short_per_lot_per_day
        return per_day * lots * days_held


class SimulatedExecutionAdapter(ExecutionAdapter):
    def __init__(self, cost_model: CostModel | None = None):
        self.cost_model = cost_model or CostModel()

    def submit_market_order(self, order: Order) -> Fill:
        if order.reference_bid is None or order.reference_ask is None:
            raise ValueError(
                "SimulatedExecutionAdapter requires order.reference_bid/reference_ask — "
                "the backtest/paper loop must supply the current bar's quote"
            )
        pip = self.cost_model.pip_size(order.symbol)
        slippage_pips = self.cost_model.slippage_pips(order.volatility_regime, order.news_blackout)
        slippage = slippage_pips * pip

        if order.direction == "long":
            fill_price = order.reference_ask + slippage
        else:
            fill_price = order.reference_bid - slippage

        lots = order.size_units / 100_000
        commission = self.cost_model.commission_per_lot * lots

        return Fill(
            order_id=order.order_id,
            symbol=order.symbol,
            direction=order.direction,
            fill_price=fill_price,
            size_units=order.size_units,
            timestamp_utc=order.timestamp_utc,
            commission=commission,
            slippage_pips=slippage_pips,
        )
