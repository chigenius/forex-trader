"""Paper/live trading loop — the scheduled counterpart to the backtest
engine.

Same pipeline as `forexml.backtest.BacktestEngine` — feature engine ->
rule engine -> signal bus -> risk gate -> execution -> ledger — and the
same `StrategyEngine` and `FeatureEngine.get_feature_vector()` used by a
backtest. This class only supplies *when* to run it (once per bar close,
via a scheduler) and *where* bars come from (any `DataAdapter`, MT5 in
production). That is what backtest/live parity (brief section 2) buys in
practice: nothing about the decision logic below differs from a backtest
loop — it is deliberately structured to accept any `DataAdapter`
precisely so this same loop can be driven by `LocalFileAdapter` for
testing (see tests/test_live_loop.py) before ever pointing it at a real
MT5 terminal.

The execution adapter is pluggable (any `ExecutionAdapter`): the default
is `SimulatedExecutionAdapter`, so "paper trading" means simulated fills
against real, live prices with nothing ever reaching the broker. Passing
an `MT5ExecutionAdapter` instead makes this loop place real orders — that
adapter's own two independent guards (a constructor flag plus
`FOREXML_LIVE_TRADING=1`) still gate every call, so wiring it in here is
not by itself enough to trade for real; see `cli.main.paper_trade` for
where that decision is actually made explicit.

Closing a position round-trips whatever `Fill.broker_position_id` the
opening fill returned, so a live adapter can target the correct broker
position rather than guessing from direction alone (simulated fills
leave this `None` throughout — there's no broker position to target).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..data.adapters.base import DataAdapter
from ..data.store import BarStore
from ..data.validation import validate_bars
from ..execution.base import ExecutionAdapter, Order
from ..execution.simulated import SimulatedExecutionAdapter
from ..features.context.calendar import EconomicCalendar
from ..features.engine import TIMEFRAME_SECONDS, FeatureEngine, StoreBarSource
from ..ledger import LedgerEntry, TradeLedger
from ..risk.gate import OpenPosition, PortfolioState, RiskGate, SizingProposal
from ..signals.bus import SignalBus
from ..signals.schema import SignalEvent
from ..signals.store import SignalStore
from ..strategies.engine import StrategyEngine
from ..strategies.lifecycle import is_tradeable
from ..strategies.schema import StrategyDefinition
from .positions import OpenPositionStore

log = logging.getLogger(__name__)


@dataclass
class LivePosition:
    trade_id: str
    signal_id: str
    strategy_name: str
    symbol: str
    direction: str
    entry_price: float
    stop_price: float
    target_price: float
    size_units: float
    opened_at: datetime
    time_stop_at: datetime | None
    mae: float = 0.0
    mfe: float = 0.0
    broker_position_id: str | None = None


class LiveTradingLoop:
    """Call `poll()` once per bar close (see `cli.main.paper_trade` for the
    real scheduler). Not thread-safe — run one instance per
    (strategy, symbol) pair.
    """

    def __init__(
        self,
        strategy: StrategyDefinition,
        symbol: str,
        bar_adapter: DataAdapter,
        bar_store: BarStore,
        feature_engine: FeatureEngine,
        risk_gate: RiskGate,
        ledger: TradeLedger,
        position_store: OpenPositionStore,
        signal_bus: SignalBus | None = None,
        signal_store: SignalStore | None = None,
        execution: ExecutionAdapter | None = None,
        calendar: EconomicCalendar | None = None,
        initial_equity: float = 10_000.0,
        history_window_bars: int = 500,
    ):
        if not is_tradeable(strategy.status):
            log.warning(
                "strategy %r has status=%r (not tradeable) — signals will be logged "
                "as taken/rejected but no paper position will ever open",
                strategy.name,
                strategy.status,
            )
        self.strategy = strategy
        self.symbol = symbol
        self.bar_adapter = bar_adapter
        self.bar_store = bar_store
        self.feature_engine = feature_engine
        self.risk_gate = risk_gate
        self.ledger = ledger
        self.position_store = position_store
        self.signal_bus = signal_bus or SignalBus()
        self.signal_store = signal_store
        if self.signal_store is not None:
            self.signal_bus.subscribe(self.signal_store.append)
        self.execution = execution or SimulatedExecutionAdapter()
        self.calendar = calendar or EconomicCalendar.empty()
        self.equity = initial_equity
        self.history_window_bars = history_window_bars
        self.rule_engine = StrategyEngine(strategy)
        self.bar_seconds = TIMEFRAME_SECONDS[strategy.timeframe]
        self.open_positions: list[LivePosition] = self._reload_open_positions()

    def _reload_open_positions(self) -> list[LivePosition]:
        df = self.position_store.load_all()
        positions = [LivePosition(**row) for row in df.iter_rows(named=True)]
        if positions:
            log.info("reloaded %d open position(s) from disk", len(positions))
        return positions

    def poll(self, now: datetime | None = None) -> None:
        """Fetch the latest bars, ingest them, and run one full pipeline
        pass — identical logic to one iteration of
        `BacktestEngine.run`'s per-bar body. Call this once per bar close.
        """
        now = now or datetime.now(timezone.utc)
        decision_ts = now.replace(microsecond=0)
        window = timedelta(seconds=self.bar_seconds * (self.history_window_bars + 5))

        raw = self.bar_adapter.fetch_bars(self.symbol, self.strategy.timeframe, decision_ts - window, decision_ts)
        if raw.is_empty():
            log.warning("no bars returned for %s %s", self.symbol, self.strategy.timeframe)
            return
        clean, report = validate_bars(raw, self.symbol, self.strategy.timeframe)
        if not report.is_clean:
            log.warning("ingest validation issues: %s", report.summary())
        self.bar_store.write(clean, self.symbol, self.strategy.timeframe)

        closed_bars = self.bar_store.read(self.symbol, self.strategy.timeframe, decision_ts - window, decision_ts)
        if closed_bars.is_empty():
            log.warning("no closed bar available yet for %s at %s", self.symbol, decision_ts)
            return
        last_bar = closed_bars.tail(1)
        last_bid = float(last_bar["bid_close"][0])
        last_ask = float(last_bar["ask_close"][0])
        high = float(last_bar["high"][0])
        low = float(last_bar["low"][0])
        close = float(last_bar["close"][0])

        self._manage_open_positions(decision_ts, high, low, close, last_bid, last_ask)

        bar_source = StoreBarSource(self.bar_store)
        fv = self.feature_engine.get_feature_vector(self.symbol, decision_ts, self.strategy.timeframe, bar_source)
        evaluation = self.rule_engine.evaluate(self.symbol, fv, closed_bars, self.calendar)
        if evaluation is None:
            return

        self.signal_bus.publish(SignalEvent.from_rule_evaluation(evaluation))

        if evaluation.status != "taken":
            return
        if not is_tradeable(self.strategy.status):
            log.info("signal %s taken but strategy status=%r is not tradeable", evaluation.signal_id, self.strategy.status)
            return
        if len(self.open_positions) >= self.strategy.risk.max_concurrent:
            return

        proposal = SizingProposal(
            symbol=self.symbol,
            direction=evaluation.direction,
            entry_price=evaluation.intended_entry,
            stop_price=evaluation.stop,
        )
        portfolio = PortfolioState(
            equity=self.equity,
            open_positions=tuple(OpenPosition(p.symbol, 0.0) for p in self.open_positions),
            # Same simplification as the backtest engine: daily/weekly
            # realised P&L tracking against the ledger is left to a
            # wrapper that has visibility across strategies/days.
            realised_pnl_today_pct=0.0,
            realised_pnl_this_week_pct=0.0,
        )
        decision = self.risk_gate.evaluate(proposal, portfolio)
        if not decision.approved:
            log.info("risk gate rejected signal %s: %s", evaluation.signal_id, decision.reason)
            return

        order = Order(
            order_id=str(uuid.uuid4()),
            symbol=self.symbol,
            direction=evaluation.direction,
            size_units=decision.position_size_units,
            timestamp_utc=decision_ts,
            reference_bid=last_bid,
            reference_ask=last_ask,
            volatility_regime=fv.get("volatility_regime"),
            news_blackout=bool(fv.get("news_blackout_high_impact")),
        )
        fill = self.execution.submit_market_order(order)
        time_stop_at = (
            decision_ts + timedelta(seconds=self.bar_seconds * self.strategy.exit.time_stop.bars)
            if self.strategy.exit.time_stop
            else None
        )
        position = LivePosition(
            trade_id=str(uuid.uuid4()),
            signal_id=evaluation.signal_id,
            strategy_name=self.strategy.name,
            symbol=self.symbol,
            direction=evaluation.direction,
            entry_price=fill.fill_price,
            stop_price=evaluation.stop,
            target_price=evaluation.target,
            size_units=fill.size_units,
            opened_at=decision_ts,
            time_stop_at=time_stop_at,
            broker_position_id=fill.broker_position_id,
        )
        self.open_positions.append(position)
        self.position_store.save(position)
        self.ledger.record(
            LedgerEntry(
                trade_id=position.trade_id,
                signal_id=position.signal_id,
                strategy_name=self.strategy.name,
                symbol=self.symbol,
                direction=position.direction,
                event="open",
                timestamp_utc=decision_ts,
                price=fill.fill_price,
                size_units=fill.size_units,
                commission=fill.commission,
            )
        )
        log.info("opened paper position %s %s @ %.5f", position.direction, self.symbol, fill.fill_price)

    def _manage_open_positions(self, decision_ts, high, low, close, last_bid, last_ask) -> None:
        still_open: list[LivePosition] = []
        for position in self.open_positions:
            exit_price = self._check_exit(position, high, low, close, decision_ts)
            if exit_price is None:
                if position.direction == "long":
                    position.mfe = max(position.mfe, high - position.entry_price)
                    position.mae = min(position.mae, low - position.entry_price)
                else:
                    position.mfe = max(position.mfe, position.entry_price - low)
                    position.mae = min(position.mae, position.entry_price - high)
                self.position_store.save(position)
                still_open.append(position)
                continue

            close_order = Order(
                order_id=str(uuid.uuid4()),
                symbol=self.symbol,
                direction="short" if position.direction == "long" else "long",
                size_units=position.size_units,
                timestamp_utc=decision_ts,
                reference_bid=last_bid,
                reference_ask=last_ask,
                closes_position_id=position.broker_position_id,
            )
            fill = self.execution.submit_market_order(close_order)
            pnl = self._pnl(position, fill.fill_price) - fill.commission
            risk_amount = abs(position.entry_price - position.stop_price) * position.size_units
            realised_r = pnl / risk_amount if risk_amount > 0 else 0.0
            self.equity += pnl

            self.ledger.record(
                LedgerEntry(
                    trade_id=position.trade_id,
                    signal_id=position.signal_id,
                    strategy_name=self.strategy.name,
                    symbol=self.symbol,
                    direction=position.direction,
                    event="close",
                    timestamp_utc=decision_ts,
                    price=fill.fill_price,
                    size_units=position.size_units,
                    commission=fill.commission,
                    realised_pnl=pnl,
                    mae=position.mae,
                    mfe=position.mfe,
                    realised_r=realised_r,
                )
            )
            if self.signal_store is not None:
                outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "timeout"
                self.signal_store.resolve_outcome(position.signal_id, outcome, position.mae, position.mfe, realised_r)
            self.position_store.delete(position.trade_id)
            log.info(
                "closed paper position %s %s @ %.5f pnl=%.2f", position.direction, self.symbol, fill.fill_price, pnl
            )

        self.open_positions = still_open

    @staticmethod
    def _check_exit(position: LivePosition, high: float, low: float, close: float, now: datetime) -> float | None:
        if position.direction == "long":
            if low <= position.stop_price:
                return position.stop_price
            if high >= position.target_price:
                return position.target_price
        else:
            if high >= position.stop_price:
                return position.stop_price
            if low <= position.target_price:
                return position.target_price
        if position.time_stop_at is not None and now >= position.time_stop_at:
            return close
        return None

    @staticmethod
    def _pnl(position: LivePosition, exit_price: float) -> float:
        if position.direction == "long":
            return (exit_price - position.entry_price) * position.size_units
        return (position.entry_price - exit_price) * position.size_units
