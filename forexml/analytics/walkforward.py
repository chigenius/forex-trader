"""Walk-forward harness: rolling train/test windows, with in-sample and
out-of-sample results always reported separately (brief section 4.7,
acceptance criterion in section 8) — never blended into a single figure
that would hide overfitting.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import polars as pl

from .metrics import PerformanceMetrics, compute_metrics


@dataclass
class WalkForwardWindow:
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    in_sample: PerformanceMetrics
    out_of_sample: PerformanceMetrics


def generate_windows(
    start: datetime, end: datetime, train_days: int, test_days: int
) -> list[tuple[datetime, datetime, datetime, datetime]]:
    windows = []
    cursor = start
    while True:
        train_start = cursor
        train_end = train_start + timedelta(days=train_days)
        test_start = train_end
        test_end = test_start + timedelta(days=test_days)
        if test_end > end:
            break
        windows.append((train_start, train_end, test_start, test_end))
        cursor = test_start
    return windows


def run_walk_forward(
    closed_trades: pl.DataFrame,
    start: datetime,
    end: datetime,
    train_days: int = 180,
    test_days: int = 30,
) -> list[WalkForwardWindow]:
    results = []
    for train_start, train_end, test_start, test_end in generate_windows(start, end, train_days, test_days):
        train_trades = closed_trades.filter(
            (pl.col("timestamp_utc") >= train_start) & (pl.col("timestamp_utc") < train_end)
        )
        test_trades = closed_trades.filter(
            (pl.col("timestamp_utc") >= test_start) & (pl.col("timestamp_utc") < test_end)
        )
        results.append(
            WalkForwardWindow(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                in_sample=compute_metrics(train_trades),
                out_of_sample=compute_metrics(test_trades),
            )
        )
    return results


def render_report(windows: list[WalkForwardWindow]) -> str:
    lines = ["# Walk-forward report", ""]
    for i, w in enumerate(windows, start=1):
        lines.append(
            f"## Window {i}: train {w.train_start.date()}–{w.train_end.date()}, "
            f"test {w.test_start.date()}–{w.test_end.date()}"
        )
        lines.append(
            f"- IN-SAMPLE     : trades={w.in_sample.trade_count} net_pnl={w.in_sample.net_pnl:.2f} "
            f"win_rate={w.in_sample.win_rate:.1%} expectancy_r={w.in_sample.expectancy_r:.3f}"
        )
        lines.append(
            f"- OUT-OF-SAMPLE : trades={w.out_of_sample.trade_count} net_pnl={w.out_of_sample.net_pnl:.2f} "
            f"win_rate={w.out_of_sample.win_rate:.1%} expectancy_r={w.out_of_sample.expectancy_r:.3f}"
        )
        lines.append("")
    return "\n".join(lines)
