"""Brief acceptance criterion (section 8): "A strategy with status
`proposed` backtests successfully and is refused by the execution
layer."
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from conftest import make_bars

from forexml.backtest import BacktestEngine
from forexml.features import FeatureEngine
from forexml.features.indicators import AverageTrueRange, RollingATRMedian
from forexml.risk import RiskGate, RiskLimits
from forexml.strategies import load_strategy


@pytest.fixture
def strategy_path():
    return "forexml/strategies/definitions/london_range_breakout.yaml"


@pytest.fixture
def risk_gate():
    limits = RiskLimits(
        max_risk_per_trade_pct=0.5,
        max_concurrent_positions=2,
        max_total_risk_pct=1.0,
        correlated_exposure_cap_pct=1.0,
        daily_loss_limit_pct=3.0,
        weekly_loss_limit_pct=6.0,
    )
    return RiskGate(limits)


def test_proposed_strategy_backtests_but_never_reaches_execution(strategy_path, risk_gate):
    strategy = load_strategy(strategy_path)
    assert strategy.status == "proposed"

    bars = make_bars(1000, timeframe_minutes=15)
    engine = FeatureEngine([AverageTrueRange(14), RollingATRMedian(14, 20)])
    bt = BacktestEngine(engine, risk_gate)

    result = bt.run(strategy, "EURUSD", bars)  # must not raise

    assert result.signal_event_count > 0  # the rule engine did evaluate and log signals
    assert result.ledger.closed_trades().height == 0  # but nothing was ever opened or closed
    assert result.final_equity == bt.initial_equity  # equity is untouched


def test_active_strategy_can_actually_trade(strategy_path, risk_gate):
    strategy = load_strategy(strategy_path).model_copy(update={"status": "active"})
    bars = make_bars(1000, timeframe_minutes=15)
    engine = FeatureEngine([AverageTrueRange(14), RollingATRMedian(14, 20)])
    bt = BacktestEngine(engine, risk_gate)

    result = bt.run(strategy, "EURUSD", bars)

    total_taken_trades = result.ledger.as_dataframe().filter(
        __import__("polars").col("event") == "open"
    ).height
    assert total_taken_trades > 0


def test_bad_strategy_yaml_is_rejected_with_a_clear_schema_error(tmp_path):
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text(
        """
name: broken
symbols: []
timeframe: M15
entry:
  conditions: []
exit:
  stop_loss: {type: atr_multiple, multiple: 1.5}
  take_profit: {type: r_multiple, multiple: 2.0}
risk:
  risk_per_trade_pct: 0.5
  max_concurrent: 2
"""
    )
    with pytest.raises(ValidationError):
        load_strategy(bad_yaml)
