"""Economic calendar / news blackout tagging.

Phase 1 ships without a live scheduled-events feed — that provider choice
is an open decision (brief section 9.4). `EconomicCalendar.empty()` never
blacks anything out, so a strategy's `news_blackout` filter is a no-op
until a real feed is wired in. When one is: point-in-time correctness for
news means storing each event's original publication timestamp precisely
and never backfilling or correcting it after the fact (brief 11.1, Level
2) — a revised timestamp would let the backtester "know" about an event
before it actually happened.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

_IMPACT_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class EconomicEvent:
    timestamp_utc: datetime
    currency: str
    impact: str  # low | medium | high
    name: str


class EconomicCalendar:
    def __init__(self, events: list[EconomicEvent]):
        self._events = sorted(events, key=lambda e: e.timestamp_utc)

    @classmethod
    def empty(cls) -> "EconomicCalendar":
        return cls([])

    def events_for(self, currency: str) -> list[EconomicEvent]:
        return [e for e in self._events if e.currency == currency]

    def is_blackout(
        self,
        symbol: str,
        timestamp: datetime,
        minutes_before: int = 30,
        minutes_after: int = 15,
        min_impact: str = "high",
    ) -> bool:
        currencies = {symbol[:3].upper(), symbol[3:6].upper()}
        threshold = _IMPACT_RANK.get(min_impact, 2)
        window_start = timestamp - timedelta(minutes=minutes_after)
        window_end = timestamp + timedelta(minutes=minutes_before)
        for event in self._events:
            if event.currency not in currencies:
                continue
            if _IMPACT_RANK.get(event.impact, 0) < threshold:
                continue
            if window_start <= event.timestamp_utc <= window_end:
                return True
        return False

    def tag(self, symbol: str, timestamp: datetime) -> dict:
        return {"news_blackout_high_impact": self.is_blackout(symbol, timestamp, 30, 15, "high")}
