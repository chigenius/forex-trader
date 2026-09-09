"""Strategy lifecycle enforcement (brief section 4.3).

`status` is enforced here, not by convention. `proposed` and `approved`
strategies can be backtested and reported on but must never reach the
execution layer. Only a recorded HUMAN action may transition a strategy
to `active` — no automated process, agent included, may perform that
transition. That single rule is what later lets an agent generate and
iterate on `proposed` strategies (brief section 11, Level 1) without ever
being able to make one tradeable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from .schema import StrategyStatus

_ALLOWED_TRANSITIONS: dict[StrategyStatus, set[StrategyStatus]] = {
    "proposed": {"approved", "retired"},
    "approved": {"active", "retired", "proposed"},
    "active": {"retired"},
    "retired": set(),
}

TRADEABLE_STATUSES: set[StrategyStatus] = {"active"}


class LifecycleError(Exception):
    pass


@dataclass(frozen=True)
class LifecycleTransition:
    strategy_name: str
    from_status: StrategyStatus
    to_status: StrategyStatus
    actor: str
    actor_kind: Literal["human", "agent"]
    timestamp_utc: datetime
    reason: str


class LifecycleAuditTrail:
    """Append-only record of every status transition. Nothing here is
    mutable after the fact — `record()` is the only way in, and it either
    appends a transition or raises.
    """

    def __init__(self) -> None:
        self._transitions: list[LifecycleTransition] = []

    def record(
        self,
        strategy_name: str,
        from_status: StrategyStatus,
        to_status: StrategyStatus,
        actor: str,
        actor_kind: Literal["human", "agent"],
        reason: str,
    ) -> LifecycleTransition:
        if to_status not in _ALLOWED_TRANSITIONS.get(from_status, set()):
            raise LifecycleError(
                f"illegal transition {from_status!r} -> {to_status!r} for {strategy_name!r}"
            )
        if to_status == "active" and actor_kind != "human":
            raise LifecycleError(
                "only a human actor may transition a strategy to 'active' "
                f"(attempted by actor_kind={actor_kind!r} actor={actor!r})"
            )
        transition = LifecycleTransition(
            strategy_name=strategy_name,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            actor_kind=actor_kind,
            timestamp_utc=datetime.now(timezone.utc),
            reason=reason,
        )
        self._transitions.append(transition)
        return transition

    def history(self, strategy_name: str | None = None) -> list[LifecycleTransition]:
        if strategy_name is None:
            return list(self._transitions)
        return [t for t in self._transitions if t.strategy_name == strategy_name]


def is_tradeable(status: StrategyStatus) -> bool:
    return status in TRADEABLE_STATUSES
