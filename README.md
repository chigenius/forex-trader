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

**Paper trading against live MT5 data.** `forexml paper-trade` (see
`forexml/live/`) runs a continuous, scheduled loop: fetch the latest
closed bar from MT5, run it through the same feature engine / rule
engine / risk gate as a backtest, and simulate the fill — never a real
order. State (bar store, signal store, open positions, ledger) persists
to disk, so stopping and restarting the process picks up exactly where
it left off. For a training period on your own machine, see
[`docs/windows_paper_trading_setup.md`](docs/windows_paper_trading_setup.md).

**Placing real orders.** `forexml paper-trade --execution mt5
--enable-live-orders` (with `FOREXML_LIVE_TRADING=1` set in the
environment — two independent guards, neither optional) routes the same
loop's decisions through `MT5ExecutionAdapter` instead of simulating the
fill. This sends real orders to whatever account your MT5 terminal is
logged into, demo or real — the guards protect against sending an order
by accident, not against risking real money specifically. See
"Placing real orders on your demo account" in
[`docs/windows_paper_trading_setup.md`](docs/windows_paper_trading_setup.md)
before using it. Known gaps: there's no reconciliation between forexml's
local ledger and the broker's actual position state (manually closing a
position in the MT5 terminal will desync them until the framework's own
stop/target/time-stop fires and finds nothing there to close), and order
volume isn't rounded to the symbol's broker-defined volume step — a
rejected order surfaces as a loud error, never a silent mis-fill, but
neither is handled gracefully yet.

**A dedicated deployment host** for round-the-clock real trading isn't
built as a managed deployment yet — that's a separate step from having
the code path work locally. When that time comes, the brief's
recommendation (and the right one for this codebase) is a single small
VPS running this container, co-located near the broker's servers for
latency — not a cluster, not autoscaling. The `MetaTrader5` Python
package only talks to a running MT5 terminal, which is Windows-only, so
that box needs to be Windows (or MT5 under Wine) — this Docker image is
Linux and only covers the data/backtest/analytics side.

## Documentation

- [`docs/strategy_authoring_guide.md`](docs/strategy_authoring_guide.md) —
  written for the trader, not the engineer.
- [`docs/windows_paper_trading_setup.md`](docs/windows_paper_trading_setup.md) —
  running `forexml paper-trade` against MT5 on your own Windows machine.
- `forexml/risk/example_limits.yaml` — example risk gate configuration.

## Layout

See `forexml/` for the package: `data/`, `features/`, `strategies/`,
`signals/`, `risk/`, `execution/`, `ledger/`, `analytics/`, `decisions/`,
`backtest/`, `live/` (the paper-trading loop), `ml/` (scaffolded, empty in
Phase 1), `cli/`. `tests/` holds the pytest + Hypothesis suite, including
the point-in-time look-ahead tests and the backtest/live parity test
called out in the brief's acceptance criteria.
