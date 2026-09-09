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
