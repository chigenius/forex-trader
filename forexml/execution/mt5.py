"""Live MetaTrader 5 execution.

Built but DISABLED BY DEFAULT (brief section 4.6): nothing in Phase 1
should be capable of placing a real order by accident. Two independent
guards must both be satisfied before any broker call is made — a
constructor flag (`enable_live_trading=True`) AND an environment variable
(`FOREXML_LIVE_TRADING=1`). `_check_guards()` runs, and can raise, before
the MetaTrader5 package is even imported. Neither guard knows or cares
whether the connected account is a demo — they protect against placing
an order by accident, not against risking real money specifically, so
the exact same guards apply identically to a demo or a funded account.

Closing a position requires telling MT5 *which* position: a plain
opposite-direction market order is not guaranteed to net against the
original position (depends on the account's netting/hedging mode) and
can instead open an unrelated second position. `Order.closes_position_id`
(the ticket returned as `Fill.broker_position_id` when the position was
opened) is threaded into the request's `position` field for exactly this
reason — see `forexml.live.loop.LiveTradingLoop`, which is the caller
responsible for round-tripping that id.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from .base import ExecutionAdapter, Fill, Order

_ENV_GUARD = "FOREXML_LIVE_TRADING"


class LiveTradingDisabledError(Exception):
    pass


class MT5ExecutionAdapter(ExecutionAdapter):
    def __init__(self, enable_live_trading: bool = False, magic_number: int = 20240101):
        self.enable_live_trading = enable_live_trading
        self.magic_number = magic_number
        self._mt5 = None

    def _check_guards(self) -> None:
        if not self.enable_live_trading:
            raise LiveTradingDisabledError(
                "live trading is disabled: construct MT5ExecutionAdapter(enable_live_trading=True) "
                "explicitly to enable it"
            )
        if os.environ.get(_ENV_GUARD) != "1":
            raise LiveTradingDisabledError(
                f"live trading requires the {_ENV_GUARD}=1 environment guard, which is not set"
            )

    def _mt5_module(self):
        if self._mt5 is None:
            import MetaTrader5 as mt5  # noqa: N814

            if not mt5.initialize():
                raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")
            self._mt5 = mt5
        return self._mt5

    def submit_market_order(self, order: Order) -> Fill:
        self._check_guards()
        mt5 = self._mt5_module()

        order_type = mt5.ORDER_TYPE_BUY if order.direction == "long" else mt5.ORDER_TYPE_SELL
        tick = mt5.symbol_info_tick(order.symbol)
        if tick is None:
            raise RuntimeError(f"no tick data available for {order.symbol}")
        price = tick.ask if order.direction == "long" else tick.bid

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": order.symbol,
            "volume": order.size_units / 100_000,
            "type": order_type,
            "price": price,
            "magic": self.magic_number,
            "comment": order.order_id,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        if order.closes_position_id is not None:
            # Targets a specific existing position rather than opening a
            # new one — see the module docstring for why this matters.
            request["position"] = int(order.closes_position_id)

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"MT5 order_send failed: {result}")

        return Fill(
            order_id=order.order_id,
            symbol=order.symbol,
            direction=order.direction,
            fill_price=result.price,
            size_units=order.size_units,
            timestamp_utc=datetime.now(timezone.utc),
            commission=0.0,  # reconciled from the broker statement, not known at fill time
            slippage_pips=0.0,
            # For a new position this IS the position ticket (MT5 sets the
            # position id equal to the opening order's ticket for a plain
            # market execution); for a close it's the closing deal's own
            # order ticket, which the caller has no further use for.
            broker_position_id=str(result.order),
        )

    def reconcile(self) -> list[dict]:
        """Broker-side open positions, for the caller to diff against its
        own ledger every cycle (brief 4.6: "reconciliation of local state
        against broker state on every cycle")."""
        mt5 = self._mt5_module()
        positions = mt5.positions_get()
        return [p._asdict() for p in positions] if positions else []
