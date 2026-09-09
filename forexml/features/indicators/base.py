"""Indicator interface.

Every indicator declares `lookback` — the minimum number of closed bars
it needs — so the feature engine can enforce warm-up automatically. An
indicator given fewer than `lookback` bars must return None for every one
of its outputs rather than a value computed on a too-short window; a
silently wrong early value is worse than an honest gap.
"""
from __future__ import annotations

import abc

import polars as pl


class Indicator(abc.ABC):
    lookback: int

    @property
    @abc.abstractmethod
    def output_names(self) -> list[str]:
        """Feature-vector keys this indicator produces."""

    @abc.abstractmethod
    def compute(self, bars: pl.DataFrame) -> dict[str, float | None]:
        """`bars` are CLOSED bars only, ascending by timestamp_utc, already
        truncated to the point-in-time cutoff by the feature engine —
        this method never sees a bar that wasn't fully formed as of the
        decision timestamp.
        """

    def _insufficient(self) -> dict[str, float | None]:
        return {name: None for name in self.output_names}
