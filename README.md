# forex-trader

Phase 1 foundation layer of the forex strategy research & execution
framework described in the engineering brief: data ingestion → point-in-time
feature engine → configuration-driven rule engine → versioned signal bus →
non-bypassable risk gate → simulated/live execution → immutable ledger →
analytics with a walk-forward harness. No machine learning is built here;
the signal store this produces is the Phase 3 training set.

## Setup

```
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Running the tests

```
.venv/bin/pytest
```

## CLI

```
forexml validate-strategy forexml/strategies/definitions/london_range_breakout.yaml
forexml ingest --source local --symbol EURUSD --timeframe M15 \
  --start 2024-01-01 --end 2024-04-01 --store-path ./barstore --input-path bars.csv
forexml backtest --strategy forexml/strategies/definitions/london_range_breakout.yaml \
  --symbol EURUSD --bars-path bars.csv
forexml report --signal-store signals.duckdb --walk-forward
```

## Deployment

`forexml` is a CLI, not a server — there is nothing to "start" in
production the way a web app has a server process. Two different needs:

**Research / backtesting.** No deployment: install locally (or in the
Docker image below) and run `forexml backtest ...` on your own machine or
in CI. State lives in plain files (Parquet bar store, a DuckDB signal
store) — no database server to run.

**Docker.** Build and run one-off CLI commands with state persisted on a
mounted volume:

```
docker compose build
docker compose run --rm forexml validate-strategy \
  forexml/strategies/definitions/london_range_breakout.yaml
docker compose run --rm forexml backtest \
  --strategy forexml/strategies/definitions/london_range_breakout.yaml \
  --symbol EURUSD --bars-path /data/bars/eurusd_m15.csv \
  --signal-store /data/signals.duckdb
```

Everything under `./data` on the host is mounted at `/data` in the
container and survives rebuilds — point `--store-path` / `--bars-path` /
`--signal-store` there.

**Paper/live trading.** The brief's recommendation (and the right one for
this codebase) is a single small VPS running this container, co-located
near the broker's servers for latency — not a cluster, not autoscaling.
Two things to know before doing this:

- Phase 1 built the MT5 execution adapter (`execution/mt5.py`) but not a
  continuously-scheduled loop that calls it — there is currently nothing
  to deploy that trades on a timer. That loop is future work.
- The `MetaTrader5` Python package only talks to a running MT5 terminal,
  which is Windows-only. This Docker image is Linux and can run the
  data/backtest/analytics side of the pipeline, but a live deployment
  needs a Windows host (or MT5 under Wine) for the broker connection.
- `FOREXML_LIVE_TRADING=1` is the guard that lets `execution/mt5.py`
  place a real order (see `docker-compose.yml`) — leave it unset
  everywhere except the one box you actually intend to trade live from.

## Documentation

- [`docs/strategy_authoring_guide.md`](docs/strategy_authoring_guide.md) —
  written for the trader, not the engineer.
- `forexml/risk/example_limits.yaml` — example risk gate configuration.

## Layout

See `forexml/` for the package: `data/`, `features/`, `strategies/`,
`signals/`, `risk/`, `execution/`, `ledger/`, `analytics/`, `decisions/`,
`backtest/`, `ml/` (scaffolded, empty in Phase 1), `cli/`. `tests/` holds
the pytest + Hypothesis suite, including the point-in-time look-ahead
tests and the backtest/live parity test called out in the brief's
acceptance criteria.
