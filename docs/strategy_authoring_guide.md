# Strategy authoring guide

This guide is for the trader, not the engineer. It assumes no Python
knowledge. A strategy is a YAML file — a plain text file with a specific
structure — that describes when to enter a trade, when to exit it, and
what to avoid trading through. You do not need anyone's help to write or
change one, as long as you use the building blocks described below.

Every strategy file lives in `forexml/strategies/definitions/` and ends
in `.yaml`.

## Checking your work

Before anything else, check that the file is well-formed:

```
forexml validate-strategy forexml/strategies/definitions/my_strategy.yaml
```

This tells you immediately if something is missing or misspelled, with a
plain description of the problem — not a programming error.

## The shape of a strategy file

```yaml
name: london_range_breakout
symbols: [EURUSD, GBPUSD]
timeframe: M15
status: proposed        # proposed | approved | active | retired
author: human            # human | agent
enabled: true

filters:
  - type: session
    value: london
  - type: volatility_regime
    operator: in
    value: [normal, high]
  - type: news_blackout
    minutes_before: 30
    minutes_after: 15

entry:
  conditions:
    - type: price_breaks
      reference: asian_session_high
      confirmation: close
    - type: indicator_comparison
      left: atr_14
      operator: '>'
      right: atr_14_median_20

exit:
  stop_loss:
    type: atr_multiple
    multiple: 1.5
  take_profit:
    type: r_multiple
    multiple: 2.0
  time_stop:
    bars: 32

risk:
  risk_per_trade_pct: 0.5
  max_concurrent: 2
```

### `name`, `symbols`, `timeframe`

- `name` — a short unique identifier, lowercase with underscores.
- `symbols` — which pairs this strategy trades, e.g. `[EURUSD, GBPUSD]`.
- `timeframe` — one of `M1`, `M5`, `M15`, `M30`, `H1`, `H4`, `D1`.

### `status` — the most important field

This controls whether the strategy can actually place trades:

| status | can it backtest? | can it trade real money? |
|---|---|---|
| `proposed` | yes | **no** |
| `approved` | yes | **no** |
| `active` | yes | **yes** |
| `retired` | yes (historical) | **no** |

A new strategy always starts as `proposed`. Backtest it, review the
results, and only change it to `active` yourself, by hand, when you are
satisfied. The system will not let anything else — a script, an
automated process — do this for you. That is deliberate.

### `filters`

Filters are conditions that must ALL be true for a trade to be
considered at all. If any filter fails, the trade is skipped and the
reason is recorded (this is useful later — even skipped opportunities
teach the system something).

Available filter types:

- **`session`** — restrict to a trading session.
  `value`: one of `asian`, `london`, `new_york`.
- **`volatility_regime`** — restrict to a volatility condition.
  `operator: in`, `value: [low, normal, high, extreme]` (any subset).
- **`news_blackout`** — avoid trading around scheduled high-impact news.
  `minutes_before` / `minutes_after` the event.
- **`time_of_day`** — restrict to a plain UTC hour window, with no
  assumption of a named regional session. Use this instead of `session`
  for an instrument with no real trading session — a round-the-clock
  synthetic index, for example.
  `start_hour`, `end_hour` (UTC, `start_hour` may exceed `end_hour` to
  wrap past midnight, e.g. `start_hour: 22, end_hour: 4`).
- **`volatility_spike_guard`** — block entry immediately after an
  abnormally large single bar. Use this instead of `news_blackout` for an
  instrument with no real news calendar to sit out — an outsized bar is
  the same kind of shock a news blackout exists to avoid trading into.
  `atr_key` (default `atr_14`), `multiple` (default `3.0`: blocks when
  the last bar's high-low range exceeds `multiple` times that ATR).

### `entry.conditions`

ALL conditions must be true for a signal to fire. Available types:

- **`price_breaks`** — price breaks above/below a reference level.
  `reference`: `asian_session_high`, `asian_session_low`,
  `london_session_high`, or `london_session_low`.
  `confirmation`: `close` (wait for the bar to close beyond the level)
  or `high`/`low` (touch is enough).
- **`indicator_comparison`** — compare two values.
  `left`, `operator` (`>`, `<`, `>=`, `<=`, `==`), `right`. Either side
  can be a number (e.g. `70`), an indicator name (e.g. `rsi_14`), or a raw
  price field (`open`, `high`, `low`, `close` of the most recent closed bar).

Available indicator names (ask the engineer to add more if you need
something not listed): `sma_<period>`, `ema_<period>`, `atr_<period>`,
`rsi_<period>`, `bb_<period>_<std>_mid/upper/lower`, `pivot`,
`pivot_r1`, `pivot_s1`, `swing_high`, `swing_low`,
`atr_<period>_median_<window>`.

### `exit`

- **`stop_loss`** — `atr_multiple` (multiple of ATR from entry) or
  `fixed_pips`.
- **`take_profit`** — `r_multiple` (multiple of your stop distance) or
  `fixed_pips`.
- **`time_stop`** *(optional)* — close after `bars` bars regardless of
  price, in case neither the stop nor target is ever hit.

### `risk`

- **`risk_per_trade_pct`** — percent of account equity risked per trade.
  This is a ceiling: the system will never let you risk more than the
  framework-wide limit even if you set a higher number here.
- **`max_concurrent`** — how many open positions this strategy may hold
  at once.

## What you cannot do in a YAML file — and why that's fine

You cannot set position sizes directly, disable the daily loss limit, or
make a strategy trade without going through the risk system. Those are
enforced outside your file, on purpose, so a mistake in a strategy
definition can never blow past the account's overall risk limits.

## Testing a change

Run a backtest before trusting any new or edited strategy:

```
forexml backtest --strategy forexml/strategies/definitions/my_strategy.yaml \
  --symbol EURUSD --bars-path path/to/eurusd_history.csv
```

This prints win rate, profit factor, expectancy, drawdown, and — if the
strategy's `status` is not `active` — a reminder that no trade was ever
actually placed, even though the report shows what would have happened.
