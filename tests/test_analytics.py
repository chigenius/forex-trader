from __future__ import annotations

from datetime import datetime, timedelta, timezone

import polars as pl

from forexml.analytics import compute_metrics, generate_windows, run_walk_forward
from forexml.analytics.walkforward import render_report


def _closed_trades(pnls: list[float], start: datetime) -> pl.DataFrame:
    rows = [
        (f"t{i}", start + timedelta(days=i), pnl, pnl / 10.0)
        for i, pnl in enumerate(pnls)
    ]
    return pl.DataFrame(rows, schema=["trade_id", "timestamp_utc", "realised_pnl", "realised_r"], orient="row")


def test_metrics_on_empty_trades_are_all_zero():
    metrics = compute_metrics(pl.DataFrame(schema={"realised_pnl": pl.Float64, "realised_r": pl.Float64}))
    assert metrics.trade_count == 0
    assert metrics.net_pnl == 0.0


def test_win_rate_and_profit_factor():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    trades = _closed_trades([100, -50, 100, -50, 100], start)
    metrics = compute_metrics(trades)
    assert metrics.trade_count == 5
    assert metrics.win_rate == 0.6
    assert metrics.net_pnl == 200
    assert metrics.profit_factor == 3.0  # 300 gross profit / 100 gross loss


def test_max_drawdown_is_negative_and_correct_magnitude():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    # equity curve: 100, 50, 0, -50, 50 -> trough at -50 from peak 100 = -150
    trades = _closed_trades([100, -50, -50, -50, 100], start)
    metrics = compute_metrics(trades)
    assert metrics.max_drawdown == -150.0


def test_walk_forward_windows_never_overlap_train_and_test_and_separate_is_vs_oos():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    windows = generate_windows(start, start + timedelta(days=100), train_days=30, test_days=10)
    for train_start, train_end, test_start, test_end in windows:
        assert train_end == test_start  # contiguous, no overlap
        assert test_end > test_start

    trades = _closed_trades(list(range(-50, 50, 2)), start)
    results = run_walk_forward(trades, start, start + timedelta(days=100), train_days=30, test_days=10)
    assert len(results) == len(windows)
    for window in results:
        # in-sample and out-of-sample are always separate objects, never merged
        assert window.in_sample is not window.out_of_sample

    report_text = render_report(results)
    assert "IN-SAMPLE" in report_text
    assert "OUT-OF-SAMPLE" in report_text
