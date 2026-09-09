"""Performance metrics: net P&L, expectancy in R, win rate, profit
factor, Sharpe/Sortino, max drawdown and duration, average MAE/MFE — and
attribution breakdowns by strategy, symbol, or any other closed-trade
column (session, volatility regime, day of week). This is how the trader
finds out which conditions their strategies actually work in.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl


@dataclass
class PerformanceMetrics:
    trade_count: int
    net_pnl: float
    win_rate: float
    profit_factor: float
    expectancy_r: float
    sharpe: float
    sortino: float
    max_drawdown: float
    max_drawdown_duration_trades: int
    avg_mae: float | None
    avg_mfe: float | None


def compute_metrics(closed_trades: pl.DataFrame, periods_per_year: float = 252.0) -> PerformanceMetrics:
    if closed_trades.is_empty():
        return PerformanceMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, None, None)

    pnl = closed_trades["realised_pnl"].fill_null(0.0).to_numpy()
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]

    win_rate = float(len(wins) / len(pnl)) if len(pnl) else 0.0
    gross_profit = float(wins.sum())
    gross_loss = float(-losses.sum())
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = float("inf") if gross_profit > 0 else 0.0

    if "realised_r" in closed_trades.columns:
        r = closed_trades["realised_r"].drop_nulls().to_numpy()
    else:
        r = np.array([])
    expectancy_r = float(r.mean()) if len(r) else 0.0

    mean_pnl = float(pnl.mean()) if len(pnl) else 0.0
    std_pnl = float(pnl.std(ddof=1)) if len(pnl) > 1 else 0.0
    sharpe = (mean_pnl / std_pnl * np.sqrt(periods_per_year)) if std_pnl > 0 else 0.0

    downside = pnl[pnl < 0]
    downside_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    sortino = (mean_pnl / downside_std * np.sqrt(periods_per_year)) if downside_std > 0 else 0.0

    equity_curve = np.cumsum(pnl)
    running_max = np.maximum.accumulate(equity_curve) if len(equity_curve) else np.array([0.0])
    drawdown = equity_curve - running_max
    max_dd = float(drawdown.min()) if len(drawdown) else 0.0

    duration = 0
    max_duration = 0
    for d in drawdown:
        duration = duration + 1 if d < 0 else 0
        max_duration = max(max_duration, duration)

    avg_mae = (
        float(closed_trades["mae"].drop_nulls().mean())
        if "mae" in closed_trades.columns and closed_trades["mae"].drop_nulls().len() > 0
        else None
    )
    avg_mfe = (
        float(closed_trades["mfe"].drop_nulls().mean())
        if "mfe" in closed_trades.columns and closed_trades["mfe"].drop_nulls().len() > 0
        else None
    )

    return PerformanceMetrics(
        trade_count=closed_trades.height,
        net_pnl=float(pnl.sum()),
        win_rate=win_rate,
        profit_factor=profit_factor,
        expectancy_r=expectancy_r,
        sharpe=float(sharpe),
        sortino=float(sortino),
        max_drawdown=max_dd,
        max_drawdown_duration_trades=max_duration,
        avg_mae=avg_mae,
        avg_mfe=avg_mfe,
    )


def attribution(closed_trades: pl.DataFrame, by: str) -> pl.DataFrame:
    if closed_trades.is_empty() or by not in closed_trades.columns:
        return pl.DataFrame()
    return (
        closed_trades.group_by(by)
        .agg(
            pl.len().alias("trade_count"),
            pl.col("realised_pnl").sum().alias("net_pnl"),
            (pl.col("realised_pnl") > 0).mean().alias("win_rate"),
            pl.col("realised_r").mean().alias("avg_r"),
        )
        .sort("net_pnl", descending=True)
    )
