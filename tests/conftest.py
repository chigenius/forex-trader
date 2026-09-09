from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import polars as pl
import pytest

BAR_COLUMNS = ["timestamp_utc", "open", "high", "low", "close", "volume", "bid_close", "ask_close"]


def make_bars(
    n: int,
    start: datetime | None = None,
    timeframe_minutes: int = 15,
    seed: int = 42,
    base_price: float = 1.1000,
    trend_hours: tuple[int, int] | None = (8, 16),
) -> pl.DataFrame:
    """Deterministic synthetic OHLCV bars: mild directional drift during
    `trend_hours` (simulating a session effect), noise otherwise."""
    rng = random.Random(seed)
    start = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
    price = base_price
    rows = []
    for i in range(n):
        ts = start + timedelta(minutes=timeframe_minutes * i)
        in_trend = trend_hours and trend_hours[0] <= ts.hour < trend_hours[1]
        magnitude = 0.0003 if in_trend else 0.00008
        price += rng.choice([-1, 1]) * magnitude * rng.random()
        high = price + abs(rng.gauss(0, 0.0004))
        low = price - abs(rng.gauss(0, 0.0004))
        rows.append((ts, price, high, low, price, 100.0, price - 0.00006, price + 0.00006))
    return pl.DataFrame(rows, schema=BAR_COLUMNS, orient="row")


@pytest.fixture
def synthetic_bars() -> pl.DataFrame:
    return make_bars(500)
