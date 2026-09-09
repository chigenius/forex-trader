"""Session and day-of-week tagging.

Session boundaries are plain UTC hour ranges — a deliberate Phase 1
simplification that ignores broker/exchange DST shifts (see brief
section 9, open decision 2 on timeframes/infra). They are configurable
per deployment via the `sessions` constructor argument.
"""
from __future__ import annotations

from datetime import datetime

DEFAULT_SESSIONS: dict[str, tuple[int, int]] = {
    "asian": (0, 8),
    "london": (8, 16),
    "new_york": (13, 21),
}


class SessionClassifier:
    def __init__(self, sessions: dict[str, tuple[int, int]] | None = None):
        self.sessions = sessions or DEFAULT_SESSIONS

    def active_sessions(self, timestamp: datetime) -> list[str]:
        hour = timestamp.hour
        return [name for name, (start, end) in self.sessions.items() if start <= hour < end]

    def tag(self, timestamp: datetime) -> dict:
        active = self.active_sessions(timestamp)
        tags: dict = {f"session_{name}": (name in active) for name in self.sessions}
        tags["session_overlap"] = len(active) > 1
        tags["day_of_week"] = timestamp.strftime("%A").lower()
        tags["active_sessions"] = ",".join(active) if active else "none"
        return tags
