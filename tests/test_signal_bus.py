"""Brief acceptance criterion (section 8): "A new consumer can subscribe
to the signal bus and receive events without modifying any existing
producer."
"""
from __future__ import annotations

from datetime import datetime, timezone

from forexml.signals import SignalBus, SignalEvent


def _event(signal_id="s1") -> SignalEvent:
    return SignalEvent(
        signal_id=signal_id,
        timestamp_utc=datetime(2024, 1, 1, tzinfo=timezone.utc),
        strategy_name="test_strategy",
        strategy_version="abc123",
        symbol="EURUSD",
        direction="long",
        status="taken",
        features={"sma_20": 1.1},
    )


def test_a_single_publish_reaches_multiple_independent_subscribers():
    bus = SignalBus()
    received_a: list[SignalEvent] = []
    received_b: list[SignalEvent] = []

    bus.subscribe(received_a.append)
    event = _event()
    bus.publish(event)
    assert received_a == [event]
    assert received_b == []  # not yet subscribed

    # A brand-new consumer subscribes later — no change to the producer or
    # to the first subscriber was required.
    bus.subscribe(received_b.append)
    event2 = _event(signal_id="s2")
    bus.publish(event2)

    assert received_a == [event, event2]
    assert received_b == [event2]


def test_schema_version_is_present_on_every_event():
    event = _event()
    assert event.schema_version == 1
