"""Common execution interface — two implementations behind it (simulated,
live MT5) so nothing upstream needs to know which one is running. That
uniformity is what backtest/live parity (brief section 2) requires at the
execution boundary: the same `Order` shape and the same
`submit_market_order()` call, whichever adapter is wired in.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Order:
    order_id: str
    symbol: str
    direction: str  # "long" | "short" — direction of this fill
    size_units: float
    timestamp_utc: datetime
    # Populated by the backtest/paper loop from the current bar; a live
    # adapter ignores these and asks the broker for its own quote instead.
    reference_bid: float | None = None
    reference_ask: float | None = None
    volatility_regime: str | None = None
    news_blackout: bool = False


@dataclass(frozen=True)
class Fill:
    order_id: str
    symbol: str
    direction: str
    fill_price: float
    size_units: float
    timestamp_utc: datetime
    commission: float
    slippage_pips: float


class ExecutionAdapter(abc.ABC):
    @abc.abstractmethod
    def submit_market_order(self, order: Order) -> Fill:
        """Execute `order` and return the resulting fill. Must never fill
        at the bare signal price — always through this adapter's own cost
        model (simulated) or the broker's actual mechanics (live)."""
