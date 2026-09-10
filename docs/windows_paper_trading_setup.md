# Running `forexml paper-trade` on your own Windows machine

This walks through a local "training period" setup: your Windows PC runs
the framework continuously, reading real prices from your broker's MT5
terminal and simulating fills against them — no real order ever placed,
which is what makes it safe to leave running for days or weeks while you
watch how a strategy actually performs.

## 1. Install Python

Install Python 3.11 or later from [python.org](https://www.python.org/downloads/windows/).
During install, check **"Add python.exe to PATH"**.

Verify in PowerShell:

```
py --version
```

## 2. Get the code and install dependencies

```
git clone <this repo's URL>
cd forex-trader
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -e ".[mt5]"
```

The `mt5` extra pulls in the `MetaTrader5` Python package, which only
works on Windows — this is exactly why the paper-trading loop has to run
here rather than in the Linux Docker image.

## 3. Install and log into MT5

1. Install the MetaTrader 5 terminal from your broker (most brokers link
   a customized installer from their client area).
2. Log in with your account credentials. **Use a demo account for the
   training period** unless you specifically intend to watch live-account
   price action — either works for paper trading, since `forexml` never
   places a real order through this command regardless.
3. In the terminal: **Tools → Options → Expert Advisors** and check
   **"Allow algorithmic trading"**. The `MetaTrader5` Python package talks
   to the terminal the same way an Expert Advisor does, and won't be able
   to pull data without this.
4. Leave the terminal open and logged in for the whole training period —
   `forexml paper-trade` reads through it, not around it.

## 4. Find your broker's exact symbol name

Brokers frequently suffix symbol names (`EURUSD.m`, `EURUSDm`,
`EURUSD.a`, plain `EURUSD`, etc.). In MT5's **Market Watch** panel,
right-click → **Symbols** and find the exact spelling your broker uses.
Pass that exact string as `--symbol` below — a mismatch means MT5 returns
no data, not an error.

## 5. Find your broker's server UTC offset

The framework needs to convert MT5's server timestamps to UTC once, at
the adapter boundary — see the note in `forexml/data/adapters/mt5.py`. In
MT5, check the current server time (bottom-right of any chart, or
**Tools → Options → Server tab**) and compare it to the actual current
UTC time. The difference, in hours, is `--server-utc-offset`. Most
brokers publish this explicitly (e.g. "server time is UTC+2" or
"UTC+3 in winter, UTC+2 in summer with DST") — check their FAQ if unsure,
and re-check twice a year if your broker's server observes DST while UTC
does not.

## 6. Pick a strategy and decide whether it should actually trade

Backtest first if you haven't:

```
forexml backtest --strategy forexml\strategies\definitions\london_range_breakout.yaml ^
  --symbol EURUSD --bars-path path\to\some_history.csv
```

The `status` field in the strategy YAML controls whether paper-trade
actually opens simulated positions:

- `status: proposed` (the default) — every signal is still evaluated and
  logged to the signal store, but nothing is ever opened. Useful for
  watching how a strategy would have signaled against live prices without
  it accumulating any P&L yet.
- `status: active` — signals that clear the risk gate open real simulated
  (paper) positions, and the ledger accrues P&L. This requires you,
  personally, to edit the YAML and change the field — nothing automated
  can do this (see `docs/strategy_authoring_guide.md`).

For an actual training period you'll almost always want `status: active`
so you get a real equity curve to evaluate.

## 7. Run it

```
mkdir data
forexml paper-trade --strategy forexml\strategies\definitions\london_range_breakout.yaml ^
  --symbol EURUSD --store-path data\barstore --signal-store data\signals.duckdb ^
  --positions-store data\open_positions.duckdb --ledger-store data\ledger.duckdb ^
  --server-utc-offset 2
```

This blocks and polls once per bar close (M15 → every 15 minutes,
H1 → every hour, etc.), printing what it does. Leave the window open;
**Ctrl+C** stops it cleanly — everything is already on disk in `.\data`,
so there's nothing to save.

## 8. Keeping it running for the whole training period

- In **Settings → System → Power & sleep**, set sleep to "Never" while
  this is running, or the scheduled polls simply stop firing while the
  machine sleeps (missed polls are not backfilled — they're just gaps).
- You can minimize the console window; you don't need to keep it in
  focus. Closing it (or logging out) does stop the process, though.
- If your PC restarts (Windows Update, power loss), both the MT5 terminal
  and `forexml paper-trade` need to be started again manually. Restarting
  is safe — the loop reloads its open positions and full trade history
  from `data\open_positions.duckdb` and `data\ledger.duckdb` automatically
  (see `forexml/live/positions.py`).

## 9. Monitoring progress during the training period

Run this any time — it reads the same files the live loop is writing to,
so it's safe to run alongside it:

```
forexml report --signal-store data\signals.duckdb --walk-forward
```

For finer-grained inspection (open positions, full trade-by-trade
ledger), the state is plain DuckDB files — open them with the `duckdb`
Python package or the `duckdb` CLI directly:

```
python -c "import duckdb; print(duckdb.connect('data/ledger.duckdb').execute('SELECT * FROM ledger ORDER BY timestamp_utc DESC LIMIT 20').df())"
```

There's no built-in alerting (email/SMS) if something goes wrong — check
in on it periodically, especially in the first few days.
