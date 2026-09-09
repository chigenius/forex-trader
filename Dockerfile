# forexml runs as a CLI, not a server: this image packages `forexml`
# (ingest / validate-strategy / backtest / report) for research and
# paper-mode use. There is no continuously-running live-trading loop in
# Phase 1 yet — execution/mt5.py exists and is disabled by default, but
# nothing here schedules it. See README.md "Deployment" before pointing
# this at a real account.
#
# The MetaTrader5 package only talks to a Windows MT5 terminal, so this
# Linux image cannot place live orders on its own — it is for the
# data/backtest/analytics side of the pipeline. A live deployment needs a
# Windows host (or Wine) running MT5 alongside whatever eventually calls
# execution/mt5.py.
FROM python:3.11-slim

# duckdb/polars/pyarrow ship manylinux wheels for this base image, so no
# compiler toolchain is needed here.
WORKDIR /app

COPY pyproject.toml ./
COPY forexml/ ./forexml/

RUN pip install --no-cache-dir .

# Bar store, signal store and decision log are all plain files addressed
# by CLI flags (--store-path/--bars-path/--signal-store) — mount a volume
# at /data and point those flags under it so state survives container
# restarts and rebuilds; nothing is read from an env var for this.
RUN mkdir -p /data

# Live trading requires FOREXML_LIVE_TRADING=1 (see execution/mt5.py) —
# deliberately left unset here so no image ever places a real order
# unless an operator opts in explicitly at run time.

ENTRYPOINT ["forexml"]
CMD ["--help"]
