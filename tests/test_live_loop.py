"""Tests for the live/paper trading loop (forexml.live.loop).

Driven through `LocalFileAdapter` rather than MT5 — the loop is
deliberately written against the generic `DataAdapter` interface so this
exact code path can be validated without a Windows MT5 terminal, per the
same backtest/live parity principle the feature engine relies on.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import polars as pl

from forexml.data.adapters.local_file import LocalFileAdapter
from forexml.data.store import BarStore
from forexml.features import FeatureEngine
from forexml.features.indicators import AverageTrueRange, RollingATRMedian
from forexml.ledger import TradeLedger
from forexml.live import LiveTradingLoop, OpenPositionStore
from forexml.risk import RiskGate, RiskLimits
from forexml.signals import SignalStore
from forexml.strategies import load_strategy


def _write_bars_csv(path, n_days=6, seed=11):
    rng = random.Random(seed)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = []
    price = 1.1000
    for i in range(n_days * 96):  # M15 bars
        ts = start + timedelta(minutes=15 * i)
        hour = ts.hour
        magnitude = 0.0003 if 8 <= hour < 16 else 0.00008
        price += rng.choice([-1, 1]) * magnitude * rng.random()
        high = price + abs(rng.gauss(0, 0.0004))
        low = price - abs(rng.gauss(0, 0.0004))
        rows.append((ts, "EURUSD", price, high, low, price, 100.0, price - 0.00006, price + 0.00006))
    df = pl.DataFrame(
        rows,
        schema=["timestamp_utc", "symbol", "open", "high", "low", "close", "volume", "bid_close", "ask_close"],
        orient="row",
    )
    df.write_csv(path)
    return df


def _make_loop(tmp_path, bars_csv, strategy, execution=None):
    bar_store = BarStore(tmp_path / "barstore")
    position_store = OpenPositionStore(tmp_path / "positions.duckdb")
    signal_store = SignalStore(tmp_path / "signals.duckdb")
    ledger = TradeLedger(persist_path=tmp_path / "ledger.duckdb")
    feature_engine = FeatureEngine([AverageTrueRange(14), RollingATRMedian(14, 20)])
    limits = RiskLimits(
        max_risk_per_trade_pct=0.5,
        max_concurrent_positions=2,
        max_total_risk_pct=1.5,
        correlated_exposure_cap_pct=1.0,
        daily_loss_limit_pct=3.0,
        weekly_loss_limit_pct=6.0,
    )
    gate = RiskGate(limits)
    adapter = LocalFileAdapter(bars_csv)
    loop = LiveTradingLoop(
        strategy=strategy,
        symbol="EURUSD",
        bar_adapter=adapter,
        bar_store=bar_store,
        feature_engine=feature_engine,
        risk_gate=gate,
        ledger=ledger,
        position_store=position_store,
        signal_store=signal_store,
        execution=execution,
    )
    return loop, bar_store, position_store, signal_store, ledger


def test_polling_every_bar_close_produces_trades_and_logs_signals(tmp_path):
    bars_csv = tmp_path / "bars.csv"
    bars = _write_bars_csv(bars_csv)
    strategy = load_strategy("forexml/strategies/definitions/london_range_breakout.yaml").model_copy(
        update={"status": "active"}
    )

    loop, bar_store, position_store, signal_store, ledger = _make_loop(tmp_path, bars_csv, strategy)

    for ts in bars["timestamp_utc"].to_list():
        loop.poll(now=ts + timedelta(minutes=15))

    all_signals = signal_store.read_all()
    assert all_signals.height > 0
    assert set(all_signals["status"].unique().to_list()) <= {"taken", "rejected"}

    ledger_df = ledger.as_dataframe()
    opens = ledger_df.filter(pl.col("event") == "open")
    closes = ledger_df.filter(pl.col("event") == "close")
    assert opens.height > 0
    # every close corresponds to a prior open, and there can be at most
    # one still-open trade sitting unclosed at the end of the run
    assert opens.height - closes.height == len(loop.open_positions)

    remaining_in_store = position_store.load_all()
    assert remaining_in_store.height == len(loop.open_positions)

    signal_store.close()
    ledger.close()
    position_store.close()


def test_restart_reloads_open_positions_from_disk(tmp_path):
    bars_csv = tmp_path / "bars.csv"
    bars = _write_bars_csv(bars_csv, n_days=6, seed=3)
    strategy = load_strategy("forexml/strategies/definitions/london_range_breakout.yaml").model_copy(
        update={"status": "active"}
    )

    loop, bar_store, position_store, signal_store, ledger = _make_loop(tmp_path, bars_csv, strategy)
    for ts in bars["timestamp_utc"].to_list():
        loop.poll(now=ts + timedelta(minutes=15))

    open_before = {p.trade_id: p for p in loop.open_positions}
    ledger_rows_before = ledger.as_dataframe().height
    signal_store.close()
    ledger.close()
    position_store.close()

    # Simulate a process restart: brand-new objects, same on-disk paths.
    position_store2 = OpenPositionStore(tmp_path / "positions.duckdb")
    ledger2 = TradeLedger(persist_path=tmp_path / "ledger.duckdb")
    signal_store2 = SignalStore(tmp_path / "signals.duckdb")
    feature_engine = FeatureEngine([AverageTrueRange(14), RollingATRMedian(14, 20)])
    limits = RiskLimits(
        max_risk_per_trade_pct=0.5, max_concurrent_positions=2, max_total_risk_pct=1.5,
        correlated_exposure_cap_pct=1.0, daily_loss_limit_pct=3.0, weekly_loss_limit_pct=6.0,
    )
    gate = RiskGate(limits)
    adapter = LocalFileAdapter(bars_csv)
    loop2 = LiveTradingLoop(
        strategy=strategy, symbol="EURUSD", bar_adapter=adapter, bar_store=bar_store,
        feature_engine=feature_engine, risk_gate=gate, ledger=ledger2,
        position_store=position_store2, signal_store=signal_store2,
    )

    assert {p.trade_id for p in loop2.open_positions} == set(open_before.keys())
    assert ledger2.as_dataframe().height == ledger_rows_before  # trade history survived the restart too

    signal_store2.close()
    ledger2.close()
    position_store2.close()


def test_proposed_strategy_logs_but_never_opens_a_paper_position(tmp_path):
    bars_csv = tmp_path / "bars.csv"
    bars = _write_bars_csv(bars_csv, n_days=6, seed=5)
    strategy = load_strategy("forexml/strategies/definitions/london_range_breakout.yaml")
    assert strategy.status == "proposed"

    loop, *_stores = _make_loop(tmp_path, bars_csv, strategy)
    for ts in bars["timestamp_utc"].to_list():
        loop.poll(now=ts + timedelta(minutes=15))

    assert loop.open_positions == []
    assert loop.ledger.as_dataframe().height == 0
    assert loop.equity == 10_000.0


class _FakeBrokerExecutionAdapter:
    """Stands in for MT5ExecutionAdapter: assigns a unique ticket on every
    opening order and asserts every closing order references a ticket
    that is actually still open — exactly the invariant the real
    MT5ExecutionAdapter fix (see execution/mt5.py) exists to preserve.
    """

    def __init__(self):
        self._next_ticket = 1
        self.open_tickets: set[str] = set()
        self.close_calls: list[str] = []

    def submit_market_order(self, order):
        from forexml.execution.base import Fill

        if order.closes_position_id is None:
            ticket = str(self._next_ticket)
            self._next_ticket += 1
            self.open_tickets.add(ticket)
            broker_position_id = ticket
        else:
            assert order.closes_position_id in self.open_tickets, (
                f"close referenced ticket {order.closes_position_id!r} which isn't open: "
                f"{self.open_tickets!r}"
            )
            self.open_tickets.discard(order.closes_position_id)
            self.close_calls.append(order.closes_position_id)
            broker_position_id = order.closes_position_id

        price = order.reference_ask if order.direction == "long" else order.reference_bid
        return Fill(
            order_id=order.order_id,
            symbol=order.symbol,
            direction=order.direction,
            fill_price=price,
            size_units=order.size_units,
            timestamp_utc=order.timestamp_utc,
            commission=0.0,
            slippage_pips=0.0,
            broker_position_id=broker_position_id,
        )


def test_close_orders_always_reference_the_ticket_opened_for_that_trade(tmp_path):
    """The bug this guards against: a close order that doesn't carry the
    right broker position id can end up opening an unrelated position
    instead of closing the intended one. `_FakeBrokerExecutionAdapter`
    raises immediately if that ever happens.
    """
    bars_csv = tmp_path / "bars.csv"
    bars = _write_bars_csv(bars_csv, n_days=6, seed=11)
    strategy = load_strategy("forexml/strategies/definitions/london_range_breakout.yaml").model_copy(
        update={"status": "active"}
    )

    broker = _FakeBrokerExecutionAdapter()
    loop, *_stores = _make_loop(tmp_path, bars_csv, strategy, execution=broker)
    for ts in bars["timestamp_utc"].to_list():
        loop.poll(now=ts + timedelta(minutes=15))

    assert len(broker.close_calls) > 0  # the scenario actually exercised a close
    assert {p.broker_position_id for p in loop.open_positions} == broker.open_tickets
