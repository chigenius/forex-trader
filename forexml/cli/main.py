"""Command-line interface (brief deliverable 8): ingest data, validate a
strategy, run a backtest, produce a report, or run a live paper-trading
loop. This is the trader-facing surface — see
docs/strategy_authoring_guide.md for the non-engineer view.
"""
from __future__ import annotations

import logging
import os
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
from ..ledger import TradeLedger
from ..live import LiveTradingLoop, OpenPositionStore
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


def _risk_limits(strategy, risk_config_path) -> RiskLimits:
    if risk_config_path:
        with open(risk_config_path) as fh:
            return RiskLimits.from_dict(yaml.safe_load(fh))
    return RiskLimits(
        max_risk_per_trade_pct=strategy.risk.risk_per_trade_pct,
        max_concurrent_positions=strategy.risk.max_concurrent,
        max_total_risk_pct=strategy.risk.risk_per_trade_pct * strategy.risk.max_concurrent,
        correlated_exposure_cap_pct=strategy.risk.risk_per_trade_pct * strategy.risk.max_concurrent,
        daily_loss_limit_pct=3.0,
        weekly_loss_limit_pct=6.0,
    )


_CRON_FIELDS_BY_TIMEFRAME = {
    "M1": {"minute": "*"},
    "M5": {"minute": "*/5"},
    "M15": {"minute": "0,15,30,45"},
    "M30": {"minute": "0,30"},
    "H1": {"minute": 0},
    "H4": {"hour": "0,4,8,12,16,20", "minute": 0},
    "D1": {"hour": 0, "minute": 0},
}


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

    limits = _risk_limits(strategy, risk_config)
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


@cli.command("paper-trade")
@click.option("--strategy", "strategy_path", required=True, type=click.Path(exists=True))
@click.option("--symbol", required=True)
@click.option("--store-path", required=True, type=click.Path(), help="bar store directory")
@click.option("--signal-store", "signal_store_path", type=click.Path(), default="signals.duckdb")
@click.option("--positions-store", "positions_store_path", type=click.Path(), default="open_positions.duckdb")
@click.option("--ledger-store", "ledger_store_path", type=click.Path(), default="ledger.duckdb")
@click.option("--initial-equity", type=float, default=10_000.0)
@click.option(
    "--risk-config", type=click.Path(exists=True), default=None,
    help="YAML with max_risk_per_trade_pct, max_concurrent_positions, max_total_risk_pct, "
    "correlated_exposure_cap_pct, daily_loss_limit_pct, weekly_loss_limit_pct",
)
@click.option(
    "--server-utc-offset", type=float, default=0.0,
    help="your MT5 broker's server time minus UTC, in hours (check the terminal's clock vs. UTC)",
)
@click.option(
    "--poll-delay-seconds", type=int, default=5,
    help="wait this long after each bar-close boundary before polling, so the broker has time to publish it",
)
@click.option(
    "--execution", "execution_kind", type=click.Choice(["simulated", "mt5"]), default="simulated",
    help="'simulated' (default) never sends an order anywhere. 'mt5' places REAL orders through your "
    "MT5 terminal — on whatever account is logged in there, demo or not — and additionally requires "
    "--enable-live-orders and the FOREXML_LIVE_TRADING=1 environment variable.",
)
@click.option(
    "--enable-live-orders", "enable_live_orders", is_flag=True, default=False,
    help="required (together with FOREXML_LIVE_TRADING=1) to use --execution mt5. Refused otherwise.",
)
@click.option("--magic-number", type=int, default=20240101, help="MT5 magic number tagging this bot's orders")
def paper_trade(
    strategy_path, symbol, store_path, signal_store_path, positions_store_path, ledger_store_path,
    initial_equity, risk_config, server_utc_offset, poll_delay_seconds, execution_kind, enable_live_orders,
    magic_number,
):
    """Run a continuous trading loop against a live MT5 terminal.

    By default (--execution simulated) fills are always simulated: this
    never places a real order, regardless of strategy status or
    FOREXML_LIVE_TRADING. Runs until interrupted (Ctrl+C); state
    (bar store, signal store, open positions, ledger) all persists to
    disk so it survives a restart.

    --execution mt5 places REAL orders on whatever account your MT5
    terminal is logged into — a demo account included: the guards below
    protect against sending an order by accident, not against risking
    real money specifically, so they behave identically either way.
    """
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as exc:
        raise click.ClickException("apscheduler is required for paper-trade: pip install apscheduler") from exc

    from ..data.adapters.mt5 import MT5Adapter

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    strategy = load_strategy(strategy_path)
    if not is_tradeable(strategy.status):
        click.echo(
            f"warning: strategy status={strategy.status!r} — every signal will be logged "
            "to the signal store but no position will ever open. Change `status` to "
            "`active` yourself in the YAML once you're satisfied with backtest results.",
            err=True,
        )
    if strategy.timeframe not in _CRON_FIELDS_BY_TIMEFRAME:
        raise click.UsageError(f"unsupported timeframe for paper-trade: {strategy.timeframe!r}")

    if execution_kind == "mt5":
        if not enable_live_orders:
            raise click.UsageError(
                "--execution mt5 requires --enable-live-orders as well — this is deliberately not the "
                "default so real orders are never one flag away by accident"
            )
        if os.environ.get("FOREXML_LIVE_TRADING") != "1":
            raise click.UsageError(
                "--execution mt5 also requires the FOREXML_LIVE_TRADING=1 environment variable "
                "(a second, independent guard — see forexml/execution/mt5.py)"
            )
        from ..execution.mt5 import MT5ExecutionAdapter

        execution = MT5ExecutionAdapter(enable_live_trading=True, magic_number=magic_number)
        click.echo(
            "*** --execution mt5: REAL orders will be sent to whatever account your MT5 terminal "
            "is logged into. Confirm that is the account you intend before signals start firing. ***"
        )
    else:
        execution = None  # LiveTradingLoop defaults to SimulatedExecutionAdapter

    mt5_adapter = MT5Adapter(server_utc_offset_hours=server_utc_offset)
    mt5_adapter.connect()

    limits = _risk_limits(strategy, risk_config)
    bar_store = BarStore(store_path)
    signal_store = SignalStore(signal_store_path)
    position_store = OpenPositionStore(positions_store_path)
    ledger = TradeLedger(persist_path=ledger_store_path)
    feature_engine = FeatureEngine(DEFAULT_INDICATORS)
    gate = RiskGate(limits)

    loop = LiveTradingLoop(
        strategy=strategy,
        symbol=symbol,
        bar_adapter=mt5_adapter,
        bar_store=bar_store,
        feature_engine=feature_engine,
        risk_gate=gate,
        ledger=ledger,
        position_store=position_store,
        signal_store=signal_store,
        execution=execution,
        initial_equity=initial_equity,
    )

    scheduler = BlockingScheduler(timezone="UTC")
    trigger = CronTrigger(second=poll_delay_seconds, timezone="UTC", **_CRON_FIELDS_BY_TIMEFRAME[strategy.timeframe])
    scheduler.add_job(loop.poll, trigger=trigger, id="paper-trade-poll")

    click.echo(
        f"paper-trading {strategy.name!r} on {symbol} ({strategy.timeframe}), "
        f"{len(loop.open_positions)} open position(s) reloaded — Ctrl+C to stop"
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        scheduler.shutdown(wait=False)
        mt5_adapter.shutdown()
        signal_store.close()
        ledger.close()
        position_store.close()
        click.echo(f"stopped. final paper equity: {loop.equity:.2f}")


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
