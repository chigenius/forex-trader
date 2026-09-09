"""In-process signal bus.

Brief 4.4: "the signal bus must be a defined, versioned event schema —
not a set of in-process function calls between components." The Phase 1
transport is deliberately just an in-process queue; what matters is that
publishers (the rule engine) and subscribers (the signal store, and later
monitoring, an agent, the ML layer) are decoupled through this object
rather than calling each other directly. A new subscriber attaches with
`subscribe()` and starts receiving every future event with zero change to
any producer — that's the property worth paying for now, before several
components get wired together directly and retrofitting it gets
expensive.
"""
from __future__ import annotations

from typing import Callable

from .schema import SignalEvent

Subscriber = Callable[[SignalEvent], None]


class SignalBus:
    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.append(subscriber)

    def publish(self, event: SignalEvent) -> None:
        for subscriber in self._subscribers:
            subscriber(event)
