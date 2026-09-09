"""Brief section 4.3: status transitions are enforced by the engine, and
only a recorded human action may activate a strategy — never an
automated process, agent included.
"""
from __future__ import annotations

import pytest

from forexml.strategies.lifecycle import LifecycleAuditTrail, LifecycleError, is_tradeable


@pytest.fixture
def trail():
    return LifecycleAuditTrail()


def test_agent_cannot_activate_a_strategy(trail):
    with pytest.raises(LifecycleError):
        trail.record("s1", "approved", "active", actor="research-agent", actor_kind="agent", reason="looks good")


def test_agent_cannot_activate_directly_from_proposed(trail):
    with pytest.raises(LifecycleError):
        trail.record("s1", "proposed", "active", actor="research-agent", actor_kind="agent", reason="skip review")


def test_human_can_activate_an_approved_strategy(trail):
    transition = trail.record("s1", "approved", "active", actor="trader", actor_kind="human", reason="go live")
    assert transition.to_status == "active"
    assert trail.history("s1")[-1] is transition


def test_illegal_transition_is_rejected_even_for_a_human(trail):
    with pytest.raises(LifecycleError):
        trail.record("s1", "retired", "active", actor="trader", actor_kind="human", reason="revive")


def test_only_active_status_is_tradeable():
    assert is_tradeable("active")
    assert not is_tradeable("proposed")
    assert not is_tradeable("approved")
    assert not is_tradeable("retired")


def test_audit_trail_is_append_only_history(trail):
    trail.record("s1", "proposed", "approved", actor="trader", actor_kind="human", reason="reviewed")
    trail.record("s1", "approved", "active", actor="trader", actor_kind="human", reason="go live")
    history = trail.history("s1")
    assert [t.to_status for t in history] == ["approved", "active"]
