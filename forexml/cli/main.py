"""Command-line interface (brief deliverable 8): ingest data, validate a
strategy, run a backtest, produce a report. This is the trader-facing
surface — see docs/strategy_authoring_guide.md for the non-engineer view.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import click
import polars as pl
import yaml

from ..analytics import compute_metrics, render_report, run_walk_forward
from ..backtest import BacktestEngine
from ..data.adapters import DukascopyAdapter, HistDataAdapter, LocalFileAdapter
from ..data.store import BarStore
from ..data.validation import validate_bars
from ..features import FeatureEngine
from ..features.indicators import (
    AverageTrueRange,
    BollingerBands,
    PivotLevels,
    RelativeStrengthIndex,
    RollingATRMedian,
    SimpleMovingAverage,
    SwingStructure,
)
from ..risk import RiskGate, RiskLimits
from ..signals import SignalStore
from ..strategies import load_strategy
from ..strategies.lifecycle import is_tradeable

_WIDE_RANGE = (datetime(1970, 1, 1, tzinfo=timezone.utc), datetime(2100, 1, 1, tzinfo=timezone.utc))

DEFAULT_INDICATORS = [
    SimpleMovingAverage(20),
    AverageTrueRange(14),
    RollingATRMedian(14, 20),
    RelativeStrengthIndex(14),
    BollingerBands(20, 2.0),
    PivotLevels(),
    SwingStructure(),
]


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@click.group()
def cli():
    """forexml — Phase 1 forex strategy research & execution framework."""


@cli.command("ingest")
@click.option("--source", type=click.Choice(["dukascopy", "histdata", "local"]), required=True)
@click.option("--symbol", required=True)
@click.option("--timeframe", required=True)
@click.option("--start", required=True, help="ISO date/time, e.g. 2015-01-01")
@click.option("--end", required=True)
@click.option("--store-path", required=True, type=click.Path())
@click.option(
    "--input-path", type=click.Path(exists=True),
    help="required for source=histdata (export dir) and source=local (CSV/Parquet file)",
)
def ingest(source, symbol, timeframe, start, end, store_path, input_path):
    """Fetch, validate and store bars for SYMBOL over [start, end)."""
    start_dt, end_dt = _parse_dt(start), _parse_dt(end)

    if source == "dukascopy":
        adapter = DukascopyAdapter()
    elif source == "histdata":
        if not input_path:
            raise click.UsageError("--input-path is required for source=histdata")
        adapter = HistDataAdapter(input_path)
    else:
        if not input_path:
            raise click.UsageError("--input-path is required for source=local")
        adapter = LocalFileAdapter(input_path)

    click.echo(f"fetching {symbol} {timeframe} from {source} [{start_dt} .. {end_dt})")
    raw = adapter.fetch_bars(symbol, timeframe, start_dt, end_dt)
    if raw.is_empty():
        click.echo("no bars returned", err=True)
        sys.exit(1)

    clean, validation = validate_bars(raw, symbol, timeframe)
    click.echo(f"validated: {clean.height}/{raw.height} bars kept; issues: {validation.summary()}")

    store = BarStore(store_path)
    store.write(clean, symbol, timeframe)
    click.echo(f"stored under {store_path}/{symbol.upper()}/{timeframe}/")


@cli.command("validate-strategy")
@click.argument("path", type=click.Path(exists=True))
def validate_strategy(path):
    """Validate a strategy YAML against the schema."""
    try:
        strategy = load_strategy(path)
    except Exception as exc:
        click.echo(f"INVALID: {exc}", err=True)
        sys.exit(1)
    click.echo(f"OK: {strategy.name} (version {strategy.version_hash}, status={strategy.status})")
    click.echo(f"  symbols={strategy.symbols} timeframe={strategy.timeframe}")
    click.echo(f"  tradeable={is_tradeable(strategy.status)}")


@cli.command("backtest")
@click.option("--strategy", "strategy_path", required=True, type=click.Path(exists=True))
@click.option("--symbol", required=True)
@click.option("--bars-path", required=True, type=click.Path(exists=True), help="CSV/Parquet bar file")
@click.option("--signal-store", "signal_store_path", type=click.Path(), default="signals.duckdb")
@click.option("--initial-equity", type=float, default=10_000.0)
@click.option(
    "--risk-config", type=click.Path(exists=True), default=None,
    help="YAML with max_risk_per_trade_pct, max_concurrent_positions, max_total_risk_pct, "
    "correlated_exposure_cap_pct, daily_loss_limit_pct, weekly_loss_limit_pct",
)
def backtest(strategy_path, symbol, bars_path, signal_store_path, initial_equity, risk_config):
    """Run a full backtest for one strategy/symbol and print performance metrics."""
    strategy = load_strategy(strategy_path)
    bars = LocalFileAdapter(bars_path).fetch_bars(symbol, strategy.timeframe, *_WIDE_RANGE)
    if bars.is_empty():
        raise click.UsageError(f"no bars for {symbol} found in {bars_path}")

    if risk_config:
        with open(risk_config) as fh:
            limits = RiskLimits.from_dict(yaml.safe_load(fh))
    else:
        limits = RiskLimits(
            max_risk_per_trade_pct=strategy.risk.risk_per_trade_pct,
            max_concurrent_positions=strategy.risk.max_concurrent,
            max_total_risk_pct=strategy.risk.risk_per_trade_pct * strategy.risk.max_concurrent,
            correlated_exposure_cap_pct=strategy.risk.risk_per_trade_pct * strategy.risk.max_concurrent,
            daily_loss_limit_pct=3.0,
            weekly_loss_limit_pct=6.0,
        )

    feature_engine = FeatureEngine(DEFAULT_INDICATORS)
    gate = RiskGate(limits)
    store = SignalStore(signal_store_path)

    engine = BacktestEngine(feature_engine, gate, initial_equity=initial_equity, signal_store=store)
    result = engine.run(strategy, symbol, bars)

    closed = result.ledger.closed_trades()
    metrics = compute_metrics(closed)
    click.echo(f"strategy={strategy.name} status={strategy.status} tradeable={is_tradeable(strategy.status)}")
    click.echo(f"signal events: {result.signal_event_count}  closed trades: {metrics.trade_count}")
    click.echo(f"final equity: {result.final_equity:.2f}  net P&L: {metrics.net_pnl:.2f}")
    click.echo(
        f"win_rate={metrics.win_rate:.1%} profit_factor={metrics.profit_factor:.2f} "
        f"expectancy_r={metrics.expectancy_r:.3f}"
    )
    click.echo(
        f"sharpe={metrics.sharpe:.2f} sortino={metrics.sortino:.2f} "
        f"max_drawdown={metrics.max_drawdown:.2f} ({metrics.max_drawdown_duration_trades} trades)"
    )
    if not is_tradeable(strategy.status):
        click.echo(
            f"note: status={strategy.status!r} is not tradeable — these trades are "
            "backtest-only and were never routed past the risk gate to a real order"
        )
    store.close()


@cli.command("report")
@click.option("--signal-store", "signal_store_path", required=True, type=click.Path(exists=True))
@click.option("--walk-forward", is_flag=True, help="also render a walk-forward in/out-of-sample report")
@click.option("--train-days", type=int, default=180)
@click.option("--test-days", type=int, default=30)
@click.option("--out", type=click.Path(), default=None, help="write the report text here")
def report(signal_store_path, walk_forward, train_days, test_days, out):
    """Produce an analytics report from a signal store."""
    store = SignalStore(signal_store_path)
    df = store.read_all()
    store.close()

    taken = df.filter(pl.col("status") == "taken")
    rejected = df.filter(pl.col("status") == "rejected")
    lines = [
        "# Signal store report",
        f"total signals: {df.height}",
        f"taken: {taken.height}  rejected: {rejected.height}",
    ]
    if "strategy_name" in df.columns and df.height:
        by_strategy = df.group_by("strategy_name").agg(pl.len().alias("count"))
        lines.append(str(by_strategy))

    if walk_forward and taken.height:
        resolved = taken.filter(pl.col("outcome") != "open")
        if resolved.height:
            # The signal store carries realised_r (in R-multiples), not a
            # currency P&L — use it directly as the metrics' "pnl" column
            # so profit factor / win rate / expectancy read in R terms.
            closed = resolved.with_columns(pl.col("realised_r").alias("realised_pnl"))
            start = df["timestamp_utc"].min()
            end = df["timestamp_utc"].max()
            windows = run_walk_forward(closed, start, end, train_days, test_days)
            lines.append("")
            lines.append(render_report(windows))

    text = "\n".join(lines)
    click.echo(text)
    if out:
        Path(out).write_text(text)
        click.echo(f"written to {out}")


if __name__ == "__main__":
    cli()
