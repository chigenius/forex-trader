"""Ingest-time validation.

Every rejected bar is recorded in the returned report — never silently
dropped. A caller that ignores the report has made an active choice to do
so; the framework itself always tells you what it quarantined and why.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import polars as pl

_EXPECTED_SECONDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
}


@dataclass(frozen=True)
class ValidationIssue:
    kind: str
    timestamp: Any
    detail: str


@dataclass
class ValidationReport:
    symbol: str
    total_bars: int
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.issues

    def issues_of(self, kind: str) -> list[ValidationIssue]:
        return [i for i in self.issues if i.kind == kind]

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for issue in self.issues:
            counts[issue.kind] = counts.get(issue.kind, 0) + 1
        return counts


def validate_bars(
    df: pl.DataFrame,
    symbol: str,
    timeframe: str,
    spike_std: float = 8.0,
    gap_tolerance_bars: float = 5.0,
) -> tuple[pl.DataFrame, ValidationReport]:
    """Quarantine bad rows and report every gap, duplicate, bad-spread and
    price-spike found. Returns (clean_df, report); `clean_df` never
    contains a quarantined row.
    """
    issues: list[ValidationIssue] = []
    df = df.sort("timestamp_utc")

    dup_mask = df["timestamp_utc"].is_duplicated()
    for ts in df.filter(dup_mask)["timestamp_utc"]:
        issues.append(ValidationIssue("duplicate_timestamp", ts, "duplicate bar timestamp"))
    df = df.unique(subset=["timestamp_utc"], keep="first").sort("timestamp_utc")

    bad_spread = (pl.col("ask_close") - pl.col("bid_close")) <= 0
    bad_spread_df = df.filter(bad_spread)
    for ts in bad_spread_df["timestamp_utc"]:
        issues.append(ValidationIssue("nonpositive_spread", ts, "ask <= bid"))
    df = df.filter(~bad_spread)

    if df.height > 2:
        returns = df["close"].pct_change().fill_null(0.0)
        std = returns.std()
        if std and std > 0:
            spike_mask = returns.abs() > (spike_std * std)
            spike_df = df.filter(spike_mask)
            for ts in spike_df["timestamp_utc"]:
                issues.append(
                    ValidationIssue("price_spike", ts, f"return exceeds {spike_std} std devs")
                )
            df = df.filter(~spike_mask)

    expected = _EXPECTED_SECONDS.get(timeframe)
    if expected and df.height > 1:
        gaps = df["timestamp_utc"].diff().dt.total_seconds().fill_null(0.0)
        gap_mask = gaps > (expected * gap_tolerance_bars)
        gap_df = df.filter(gap_mask)
        for ts, gap in zip(gap_df["timestamp_utc"], gaps.filter(gap_mask)):
            issues.append(
                ValidationIssue(
                    "gap", ts, f"{gap:.0f}s gap exceeds {gap_tolerance_bars}x expected interval"
                )
            )

    return df, ValidationReport(symbol=symbol, total_bars=df.height, issues=issues)
