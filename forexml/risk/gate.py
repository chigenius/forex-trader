"""Risk & sizing — the non-bypassable hard gate (brief section 4.5).

Every order reaches execution through `RiskGate.evaluate()`. This class
deliberately exposes no interface for anything upstream to modify its
limits, disable a check, or override a veto: limits are a frozen
dataclass loaded once at construction, and `RiskGate` itself refuses any
attribute assignment after `__init__` completes — there is no setter
method anywhere in this file, public or private. A pluggable sizer may
*propose* a risk percentage; the gate still clamps it to the configured
maximum before anything downstream ever sees it. This is enforced by the
module's structure, not by policy or code review, because it should be
built as if an untrusted caller is on the other side of it — brief
section 11.2 is explicit that no agent may ever touch this gate, at any
level of autonomy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class RiskGateImmutableError(Exception):
    pass


@dataclass(frozen=True)
class RiskLimits:
    """Loaded once from configuration at process startup. A frozen
    dataclass: any attempt to assign to a field raises
    `dataclasses.FrozenInstanceError`. Changing a limit means editing
    configuration and restarting the process — never a runtime call.
    """

    max_risk_per_trade_pct: float
    max_concurrent_positions: int
    max_total_risk_pct: float
    correlated_exposure_cap_pct: float
    daily_loss_limit_pct: float
    weekly_loss_limit_pct: float
    correlated_groups: tuple[tuple[str, ...], ...] = ()

    @classmethod
    def from_dict(cls, data: dict) -> "RiskLimits":
        groups = tuple(tuple(g) for g in data.get("correlated_groups", []))
        return cls(
            max_risk_per_trade_pct=data["max_risk_per_trade_pct"],
            max_concurrent_positions=data["max_concurrent_positions"],
            max_total_risk_pct=data["max_total_risk_pct"],
            correlated_exposure_cap_pct=data["correlated_exposure_cap_pct"],
            daily_loss_limit_pct=data["daily_loss_limit_pct"],
            weekly_loss_limit_pct=data["weekly_loss_limit_pct"],
            correlated_groups=groups,
        )


@dataclass(frozen=True)
class OpenPosition:
    symbol: str
    risk_pct: float


@dataclass(frozen=True)
class PortfolioState:
    """A snapshot the gate reads but never mutates or stores."""

    equity: float
    open_positions: tuple[OpenPosition, ...]
    realised_pnl_today_pct: float
    realised_pnl_this_week_pct: float


@dataclass(frozen=True)
class SizingProposal:
    symbol: str
    direction: str
    entry_price: float
    stop_price: float


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str | None
    risk_pct: float | None = None
    position_size_units: float | None = None


class Sizer(Protocol):
    """A pluggable sizer proposes a risk percentage. Phase 3 substitutes a
    model-driven sizer here without touching anything upstream — but
    whatever it proposes still passes through the gate's clamp below.
    """

    def propose_risk_pct(self, proposal: SizingProposal, limits: RiskLimits) -> float: ...


class FixedFractionalSizer:
    def propose_risk_pct(self, proposal: SizingProposal, limits: RiskLimits) -> float:
        return limits.max_risk_per_trade_pct


class RiskGate:
    """The hard gate. Construct once at startup; nothing about it changes
    for the rest of the process's life except the `PortfolioState`
    snapshot passed into each `evaluate()` call.
    """

    __slots__ = ("_limits", "_sizer")

    def __init__(self, limits: RiskLimits, sizer: Sizer | None = None):
        object.__setattr__(self, "_limits", limits)
        object.__setattr__(self, "_sizer", sizer or FixedFractionalSizer())

    def __setattr__(self, key, value):
        raise RiskGateImmutableError(
            "RiskGate is immutable after construction: there is no supported way to "
            "change its limits, disable a check, or otherwise reconfigure it at runtime"
        )

    def __delattr__(self, key):
        raise RiskGateImmutableError("RiskGate attributes cannot be deleted")

    def evaluate(self, proposal: SizingProposal, portfolio: PortfolioState) -> RiskDecision:
        limits = self._limits

        if portfolio.realised_pnl_today_pct <= -abs(limits.daily_loss_limit_pct):
            return RiskDecision(False, "daily loss limit breached — new entries disabled")
        if portfolio.realised_pnl_this_week_pct <= -abs(limits.weekly_loss_limit_pct):
            return RiskDecision(False, "weekly loss limit breached — new entries disabled")

        if len(portfolio.open_positions) >= limits.max_concurrent_positions:
            return RiskDecision(False, "max concurrent positions reached")

        proposed_pct = self._sizer.propose_risk_pct(proposal, limits)
        # The clamp: whatever the sizer proposed, it can never exceed the
        # hard limit. This line is what makes a model-driven sizer safe.
        risk_pct = min(proposed_pct, limits.max_risk_per_trade_pct)
        if risk_pct <= 0:
            return RiskDecision(False, "proposed risk is zero or negative")

        total_risk = sum(p.risk_pct for p in portfolio.open_positions) + risk_pct
        if total_risk > limits.max_total_risk_pct:
            return RiskDecision(
                False,
                f"total book risk {total_risk:.2f}% would exceed cap "
                f"{limits.max_total_risk_pct:.2f}%",
            )

        for group in limits.correlated_groups:
            if proposal.symbol in group:
                correlated_risk = risk_pct + sum(
                    p.risk_pct for p in portfolio.open_positions if p.symbol in group
                )
                if correlated_risk > limits.correlated_exposure_cap_pct:
                    return RiskDecision(
                        False,
                        f"correlated exposure {correlated_risk:.2f}% would exceed cap "
                        f"{limits.correlated_exposure_cap_pct:.2f}% for group {group}",
                    )
                break

        stop_distance = abs(proposal.entry_price - proposal.stop_price)
        if stop_distance <= 0:
            return RiskDecision(False, "stop distance is zero — cannot size position")

        risk_amount = portfolio.equity * (risk_pct / 100.0)
        position_size_units = risk_amount / stop_distance

        return RiskDecision(True, None, risk_pct=risk_pct, position_size_units=position_size_units)
