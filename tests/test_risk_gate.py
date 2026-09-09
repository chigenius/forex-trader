"""Acceptance criterion (brief section 8): "An automated test attempts to
raise a risk limit at runtime and to route an order around the risk
gate; both attempts fail."
"""
from __future__ import annotations

import dataclasses

import pytest

from forexml.risk import (
    OpenPosition,
    PortfolioState,
    RiskGate,
    RiskGateImmutableError,
    RiskLimits,
    SizingProposal,
)


@pytest.fixture
def limits():
    return RiskLimits(
        max_risk_per_trade_pct=0.5,
        max_concurrent_positions=2,
        max_total_risk_pct=1.0,
        correlated_exposure_cap_pct=0.8,
        daily_loss_limit_pct=3.0,
        weekly_loss_limit_pct=6.0,
        correlated_groups=(("EURUSD", "GBPUSD"),),
    )


@pytest.fixture
def gate(limits):
    return RiskGate(limits)


@pytest.fixture
def flat_portfolio():
    return PortfolioState(equity=10_000, open_positions=(), realised_pnl_today_pct=0.0, realised_pnl_this_week_pct=0.0)


def test_cannot_raise_a_limit_on_the_gate_at_runtime(gate, limits):
    with pytest.raises(RiskGateImmutableError):
        gate._limits = dataclasses.replace(limits, max_risk_per_trade_pct=1000.0)
    with pytest.raises(RiskGateImmutableError):
        gate.some_new_attr = "bypass"


def test_cannot_mutate_the_frozen_limits_object_directly(limits):
    with pytest.raises(dataclasses.FrozenInstanceError):
        limits.max_risk_per_trade_pct = 1000.0


def test_gate_has_no_public_setter_or_disable_method(gate):
    public_attrs = [a for a in dir(gate) if not a.startswith("_")]
    assert public_attrs == ["evaluate"]


def test_greedy_sizer_proposal_is_clamped_not_bypassed(limits, flat_portfolio):
    class GreedySizer:
        def propose_risk_pct(self, proposal, limits):
            return 999.0  # tries to "route around" the configured limit

    gate = RiskGate(limits, sizer=GreedySizer())
    proposal = SizingProposal("EURUSD", "long", entry_price=1.1000, stop_price=1.0950)
    decision = gate.evaluate(proposal, flat_portfolio)
    assert decision.approved
    assert decision.risk_pct == limits.max_risk_per_trade_pct


def test_daily_loss_limit_vetoes_new_entries(gate, flat_portfolio):
    breached = dataclasses.replace(flat_portfolio, realised_pnl_today_pct=-5.0)
    proposal = SizingProposal("EURUSD", "long", entry_price=1.1000, stop_price=1.0950)
    decision = gate.evaluate(proposal, breached)
    assert not decision.approved
    assert "daily loss limit" in decision.reason


def test_max_concurrent_positions_vetoes_new_entries(gate, flat_portfolio):
    loaded = dataclasses.replace(
        flat_portfolio,
        open_positions=(OpenPosition("EURUSD", 0.3), OpenPosition("USDJPY", 0.3)),
    )
    proposal = SizingProposal("GBPUSD", "long", entry_price=1.25, stop_price=1.24)
    decision = gate.evaluate(proposal, loaded)
    assert not decision.approved
    assert "concurrent" in decision.reason


def test_correlated_exposure_cap_vetoes_new_entries(gate, flat_portfolio):
    loaded = dataclasses.replace(flat_portfolio, open_positions=(OpenPosition("GBPUSD", 0.5),))
    proposal = SizingProposal("EURUSD", "long", entry_price=1.1000, stop_price=1.0950)
    decision = gate.evaluate(proposal, loaded)
    assert not decision.approved
    assert "correlated exposure" in decision.reason


def test_zero_stop_distance_is_rejected(gate, flat_portfolio):
    proposal = SizingProposal("EURUSD", "long", entry_price=1.1000, stop_price=1.1000)
    decision = gate.evaluate(proposal, flat_portfolio)
    assert not decision.approved
    assert "stop distance" in decision.reason
