"""Backtest engine — the same code path a paper or live loop would use,
with only the execution adapter and bar source swapped out. This is where
backtest/live parity (brief section 2) actually lives: everything from
"compute the feature vector at this decision timestamp" onward — feature
engine, rule engine, signal bus, risk gate, execution interface — is
identical code regardless of mode.

Signals reach execution only when TWO independent things are both true:
the rule engine says `taken`, and `lifecycle.is_tradeable(strategy.status)`
is true. A `proposed` or `approved` strategy runs through this exact loop
and is fully backtestable, but its signals stop at the "publish to the
signal bus" step — this is the enforcement brief section 4.3 requires,
made structural rather than a matter of caller discipline.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

import polars as pl

from ..execution.base import ExecutionAdapter, Order
from ..execution.simulated import SimulatedExecutionAdapter
from ..features.context.calendar import EconomicCalendar
from ..features.engine import TIMEFRAME_SECONDS, DataFrameBarSource, FeatureEngine
from ..ledger import LedgerEntry, TradeLedger
from ..risk.gate import OpenPosition, PortfolioState, RiskGate, SizingProposal
from ..signals.bus import SignalBus
from ..signals.schema import SignalEvent
from ..signals.store import SignalStore
from ..strategies.engine import StrategyEngine
from ..strategies.lifecycle import is_tradeable
from ..strategies.schema import StrategyDefinition


@dataclass
class OpenTrade:
    trade_id: str
    signal_id: str
    symbol: str
    direction: str
    entry_price: float
    stop_price: float
    target_price: float
    size_units: float
    opened_bar_index: int
    time_stop_bars: int | None
    mae: float = 0.0
    mfe: float = 0.0


@dataclass
class BacktestResult:
    ledger: TradeLedger
    signal_event_count: int
    final_equity: float


class BacktestEngine:
    def __init__(
        self,
        feature_engine: FeatureEngine,
        risk_gate: RiskGate,
        execution: ExecutionAdapter | None = None,
        calendar: EconomicCalendar | None = None,
        initial_equity: float = 10_000.0,
        signal_bus: SignalBus | None = None,
        signal_store: SignalStore | None = None,
    ):
        self.feature_engine = feature_engine
        self.risk_gate = risk_gate
        self.execution = execution or SimulatedExecutionAdapter()
        self.calendar = calendar or EconomicCalendar.empty()
        self.initial_equity = initial_equity
        self.signal_bus = signal_bus or SignalBus()
        self.signal_store = signal_store
        if self.signal_store is not None:
            self.signal_bus.subscribe(self.signal_store.append)
        self.ledger = TradeLedger()

    def run(self, strategy: StrategyDefinition, symbol: str, bars: pl.DataFrame) -> BacktestResult:
        rule_engine = StrategyEngine(strategy)
        bars = bars.sort("timestamp_utc")
        bar_source = DataFrameBarSource(bars)
        timeframe = strategy.timeframe
        bar_seconds = TIMEFRAME_SECONDS[timeframe]

        equity = self.initial_equity
        open_trades: list[OpenTrade] = []
        event_count = 0
        n = bars.height
        timestamps = bars["timestamp_utc"].to_list()

        for i in range(n):
            bar_open_ts = timestamps[i]
            # Decisions happen once bar i has fully closed, i.e. at bar i's
            # open time plus one full interval — never mid-bar.
            decision_ts = bar_open_ts + timedelta(seconds=bar_seconds)
            last_bar = bars.slice(i, 1)
            last_bid = float(last_bar["bid_close"][0])
            last_ask = float(last_bar["ask_close"][0])
            high = float(last_bar["high"][0])
            low = float(last_bar["low"][0])
            close = float(last_bar["close"][0])

            still_open: list[OpenTrade] = []
            for trade in open_trades:
                exit_price = self._check_exit(trade, high, low, close, i)
                if exit_price is None:
                    if trade.direction == "long":
                        trade.mfe = max(trade.mfe, high - trade.entry_price)
                        trade.mae = min(trade.mae, low - trade.entry_price)
                    else:
                        trade.mfe = max(trade.mfe, trade.entry_price - low)
                        trade.mae = min(trade.mae, trade.entry_price - high)
                    still_open.append(trade)
                    continue

                close_order = Order(
                    order_id=str(uuid.uuid4()),
                    symbol=symbol,
                    direction="short" if trade.direction == "long" else "long",
                    size_units=trade.size_units,
                    timestamp_utc=decision_ts,
                    reference_bid=last_bid,
                    reference_ask=last_ask,
                )
                fill = self.execution.submit_market_order(close_order)
                pnl = self._pnl(trade, fill.fill_price) - fill.commission
                risk_amount = abs(trade.entry_price - trade.stop_price) * trade.size_units
                realised_r = pnl / risk_amount if risk_amount > 0 else 0.0
                equity += pnl

                self.ledger.record(
                    LedgerEntry(
                        trade_id=trade.trade_id,
                        signal_id=trade.signal_id,
                        strategy_name=strategy.name,
                        symbol=symbol,
                        direction=trade.direction,
                        event="close",
                        timestamp_utc=decision_ts,
                        price=fill.fill_price,
                        size_units=trade.size_units,
                        commission=fill.commission,
                        realised_pnl=pnl,
                        mae=trade.mae,
                        mfe=trade.mfe,
                        realised_r=realised_r,
                    )
                )
                if self.signal_store is not None:
                    outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "timeout"
                    self.signal_store.resolve_outcome(
                        trade.signal_id, outcome, trade.mae, trade.mfe, realised_r
                    )

            open_trades = still_open

            fv = self.feature_engine.get_feature_vector(symbol, decision_ts, timeframe, bar_source)
            closed_bars = bars.slice(0, i + 1)
            evaluation = rule_engine.evaluate(symbol, fv, closed_bars, self.calendar)
            if evaluation is None:
                continue

            self.signal_bus.publish(SignalEvent.from_rule_evaluation(evaluation))
            event_count += 1

            if evaluation.status != "taken":
                continue
            if not is_tradeable(strategy.status):
                continue
            if len(open_trades) >= strategy.risk.max_concurrent:
                continue

            proposal = SizingProposal(
                symbol=symbol,
                direction=evaluation.direction,
                entry_price=evaluation.intended_entry,
                stop_price=evaluation.stop,
            )
            portfolio = PortfolioState(
                equity=equity,
                open_positions=tuple(OpenPosition(t.symbol, 0.0) for t in open_trades),
                # Daily/weekly realised P&L tracking against the ledger is
                # the caller's responsibility above this loop in a
                # multi-strategy/multi-day driver; a single-run backtest
                # over one strategy has no cross-day state to feed in yet.
                realised_pnl_today_pct=0.0,
                realised_pnl_this_week_pct=0.0,
            )
            decision = self.risk_gate.evaluate(proposal, portfolio)
            if not decision.approved:
                continue

            open_order = Order(
                order_id=str(uuid.uuid4()),
                symbol=symbol,
                direction=evaluation.direction,
                size_units=decision.position_size_units,
                timestamp_utc=decision_ts,
                reference_bid=last_bid,
                reference_ask=last_ask,
                volatility_regime=fv.get("volatility_regime"),
                news_blackout=bool(fv.get("news_blackout_high_impact")),
            )
            fill = self.execution.submit_market_order(open_order)
            trade = OpenTrade(
                trade_id=str(uuid.uuid4()),
                signal_id=evaluation.signal_id,
                symbol=symbol,
                direction=evaluation.direction,
                entry_price=fill.fill_price,
                stop_price=evaluation.stop,
                target_price=evaluation.target,
                size_units=fill.size_units,
                opened_bar_index=i,
                time_stop_bars=strategy.exit.time_stop.bars if strategy.exit.time_stop else None,
            )
            open_trades.append(trade)
            self.ledger.record(
                LedgerEntry(
                    trade_id=trade.trade_id,
                    signal_id=trade.signal_id,
                    strategy_name=strategy.name,
                    symbol=symbol,
                    direction=trade.direction,
                    event="open",
                    timestamp_utc=decision_ts,
                    price=fill.fill_price,
                    size_units=fill.size_units,
                    commission=fill.commission,
                )
            )

        return BacktestResult(ledger=self.ledger, signal_event_count=event_count, final_equity=equity)

    @staticmethod
    def _check_exit(trade: OpenTrade, high: float, low: float, close: float, bar_index: int) -> float | None:
        if trade.direction == "long":
            if low <= trade.stop_price:
                return trade.stop_price
            if high >= trade.target_price:
                return trade.target_price
        else:
            if high >= trade.stop_price:
                return trade.stop_price
            if low <= trade.target_price:
                return trade.target_price
        if trade.time_stop_bars is not None and (bar_index - trade.opened_bar_index) >= trade.time_stop_bars:
            return close  # time stop: exit at the current bar's close
        return None

    @staticmethod
    def _pnl(trade: OpenTrade, exit_price: float) -> float:
        if trade.direction == "long":
            return (exit_price - trade.entry_price) * trade.size_units
        return (trade.entry_price - exit_price) * trade.size_units
