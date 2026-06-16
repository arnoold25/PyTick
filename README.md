# PyTick

High-performance Forex tick-data backtesting in Python, with a C++ core.

> **Status: pre-1.0.** The strategy interface, the C++ broker simulation,
> performance metrics and plotting are implemented and run end-to-end
> (`unstable` branch). Multi-symbol backtests share one synchronized account.
> The API is settling but not yet frozen.

## Design

PyTick is built around a few hard rules:

- **Tick data, not bars.** Backtests run on raw Dukascopy tick data (bid/ask
  plus volumes). Fills and SL/TP are resolved on the raw ticks. Bars are a
  bid-only OHLC view, built once per hour for the strategy to inspect — they are
  never used for fills.
- **Struct-of-Arrays storage.** Each symbol is stored as one `.npy` file per
  column plus an hour index. All price arrays are accessed via `mmap` — only
  the columns a backtest actually uses are ever read from disk.
- **Python drives, C++ executes.** A Python loop iterates over hours; the C++
  core (pybind11) does all per-tick work — bar extrema, fills, SL/TP scanning,
  financing, equity. The language boundary is crossed once per hour (~37k times
  for six years of data), not once per tick (275M times). The opt-in `on_tick`
  callback trades that for per-tick granularity.
- **Decide at the close, no look-ahead.** Each hour the broker processes that
  hour's ticks *before* the strategy runs, so it always acts on a completed
  candle. Orders it places fill on the next hour's first tick (or the exact
  SL/TP/trigger level), never at the current open.

## Data pipeline

```
Dukascopy .bi5 → Bi5Converter → one .npy per column + hour index → DataLoader (mmap) → Backtester
```

On-disk layout per symbol (`<data_dir>/<SYMBOL>/`):

| File                      | dtype                | Content                                              |
| ------------------------- | -------------------- | ---------------------------------------------------- |
| `ts.npy`                  | int64                | Timestamp, microseconds since epoch (UTC)            |
| `bid.npy` / `ask.npy`     | float64              | Prices                                               |
| `bid_vol.npy` / `ask_vol.npy` | float64          | Volumes (millions of base currency)                  |
| `index.npy`               | int64, shape (H, 3)  | `[hour_start_us, start_row, end_row)` — half-open; hours without ticks have no row |

Source files are expected as `SYMBOL_YYYY-MM-DD_HH.bi5`. Override
`Bi5Converter.hour_start_us` for a different naming scheme.

## Installation (development)

Requires Python ≥ 3.11 and a C++17 compiler (developed against MSVC on
Windows). The C++ extension is built automatically via scikit-build-core/CMake.

```
pip install -e .            # core + plotting (numpy, matplotlib)
pip install -e .[dev]       # + pytest
```

After changing C++ sources, rebuild. Once the build tools are in your
environment (`pip install scikit-build-core cmake ninja pybind11`) the fast path
is:

```
pip install -e . --no-build-isolation
```

## Usage

Write an `on_candle(ctx, bars)` callback and hand it to `run`:

```python
from pathlib import Path
from pytick import Backtester, DataConfig, BacktestConfig

def on_candle(ctx, bars):
    # bars = {"EURUSD": {"open","high","low","close"}, ...}  (bid-only)
    bar = bars.get("EURUSD")
    if bar is None or ctx.position("EURUSD"):
        return
    if bar["close"] > bar["open"]:                 # bullish hour
        price = bar["close"]
        ctx.buy("EURUSD", lots=1.0,
                sl=price - 0.0020,                 # absolute prices
                tp=price + 0.0040)

bt = Backtester(
    DataConfig(data_dir=Path("data/npy"), symbols=("EURUSD", "AUDUSD")),
    BacktestConfig(),
)
result = bt.run(on_candle=on_candle)
result.summary()
result.plot()
```

Strategy state lives in your own object — pass a bound method, no base class
needed:

```python
class Momentum:
    def __init__(self, fast=10):
        self.fast = fast
        self.history = []
    def on_candle(self, ctx, bars):
        ...

m = Momentum()
bt.run(on_candle=m.on_candle, on_tick=m.on_tick)   # on_tick optional
```

The legacy `Strategy` subclass still works — subclass it, override `on_candle`
(and optionally `on_tick`), and pass the class to `run`:

```python
from pytick import Strategy

class MyStrategy(Strategy):
    def on_candle(self, hour, bars):
        if not self.position("EURUSD") and bars["EURUSD"]["close"] > bars["EURUSD"]["open"]:
            self.buy("EURUSD", lots=1.0)

bt.run(MyStrategy)
```

Or run the bundled example via the package entry point:

```
python -m pytick
```

### Strategy API

The `ctx` handed to `on_candle(ctx, bars)` (and the `self` of a `Strategy`)
expose the same order API:

| Call                                              | Effect                                            |
| ------------------------------------------------- | ------------------------------------------------- |
| `buy(sym, lots, sl=None, tp=None, limit=None, stop=None)`  | Open a long; market by default, or a `limit`/`stop` pending entry |
| `sell(sym, lots, sl=None, tp=None, limit=None, stop=None)` | Open a short                             |
| `close(sym)`                                      | Close every open position on `sym` (market)       |
| `position(sym)`                                   | List of open positions on `sym` (empty if flat)   |
| `equity` / `cash`                                 | Account equity (incl. unrealized) / realized cash |

`sl`/`tp`/`limit`/`stop` are absolute prices. A market order fills at the next
tick (buy@ask, sell@bid); a `limit`/`stop` order fills at its trigger once the
hour's range reaches it, and carries over until then.

`on_tick(ctx, symbol, bid, ask)` (or `on_tick(self, symbol, bid, ask)` on a
`Strategy`) is an opt-in hook: provide it and the broker scans every tick and
calls it for each one — tens of seconds for the full dataset, so use it for
short windows and feature exploration, not production sweeps. On a `Strategy`
it is wired only when the subclass actually overrides it.

### Broker model

- **Account:** one shared USD account across all symbols; the hour axis is the
  union of every symbol's hours, so capital and time are synchronized.
- **Entries:** market entries and `limit`/`stop` pending entries, with an
  optional stop-loss / take-profit bracket; size in standard lots (`lot_size`,
  default 100k base-currency units).
- **Fills:** market — next tick (buy@ask, sell@bid); limit/stop — at the trigger
  price; SL/TP — at the exact level. SL/TP uses an extrema-gated fast path; a
  full tick scan runs only when both stop and target lie inside the hour's range.
- **Quote-currency conversion:** P&L is exact in account USD for USD-quoted
  pairs (EURUSD, …) and for USD-base pairs (USDJPY, USDCAD, USDCHF), the latter
  converted by the pair's own price. Crosses fall back to no conversion.
- **Costs:** optional `commission_per_lot` (charged each side) and per-night
  `swap_long`/`swap_short` (charged at `swap_hour` UTC, triple on
  `triple_swap_weekday`). Closed trades report `gross_pnl`, `commission`, `swap`
  and net `pnl`.
- **Leverage** caps position notional (in USD) at `equity × leverage`.
- **Prop-firm drawdown stop:** set `max_drawdown_pct` (trailing from the equity
  peak, or static from initial capital with `dd_trailing=False`) and/or a
  `daily_loss_limit`. The stop is checked against the **worst-case intra-hour
  equity** (open positions marked at the hour's adverse extreme); on a breach
  the account flattens and halts.

All cost/risk parameters live on `BacktestConfig` and default to off/zero, so a
default config reproduces the plain broker exactly.

### Metrics

`result.summary()` reports end capital, return %, CAGR, max drawdown, exposure,
trade counts, win rate, average win/loss, profit factor, expectancy, and the
Sharpe & Sortino ratios (from daily equity returns, annualized with
`ann_factor`, risk-free `risk_free`). The raw `equity_curve` and `trades` are
kept on the result for custom analysis.

### Visualization

`result.plot(save=None, show=True)` draws a matplotlib dashboard — equity curve
with drawdown shading, an underwater drawdown plot, per-trade and cumulative
P&L, and the per-trade return distribution. `save="run.png"` writes a PNG;
`show=False` skips the window.

## Performance

Hourly bid-bar building over the full dataset — 275M ticks / ~37k hours:

| Implementation                       | Time    |
| ------------------------------------ | ------- |
| Pure Python (numpy per hour)         | ~2.5 s  |
| C++ core (scalar loop, pybind11)     | ~1.8 s  |
| C++ core (AVX2-vectorized min/max)   | ~1.35 s |

A full backtest (bars + broker tick scan with an SL/TP strategy holding
positions, two symbols) runs in **~1.4 s warm** / ~26 s cold (the first pass
streams ~2.5 GB through mmap, page-fault bound). The bar build is now folded
into the broker's single per-hour pass — bid is scanned once instead of twice,
and the per-symbol `make_bar` boundary crossings are gone — down from the ~2.3 s
warm of the separate-pass design. Measured on the development machine.

## Roadmap

### Done

- [x] Dukascopy `.bi5` converter with integrity checks
- [x] mmap-based SoA data loader, global hour axis across symbols
- [x] Bar building folded into the C++ broker's single per-hour pass
      (bid-only OHLC, AVX2 `make_bar` still exported for ad-hoc slicing)
- [x] Strategy API: `on_candle`/`on_tick` callbacks with a `Context`, plus the
      legacy `Strategy` subclass
- [x] C++ broker: market + limit/stop entries, tick-precise SL/TP fills,
      extrema-gated scan, leverage-capped sizing, shared USD account
- [x] Quote-currency conversion for USD-base pairs (USDJPY, …)
- [x] Commission / swap modelling
- [x] Prop-firm drawdown stop (worst-case intra-hour equity)
- [x] Metrics: return, CAGR, max drawdown, exposure, win rate, profit factor,
      expectancy, Sharpe & Sortino
- [x] matplotlib visualization (`result.plot()`)
- [x] pytest suite (synthetic broker + engine tests)

### Next

- [ ] Cross-pair conversion via a third series (EURJPY, …)
- [ ] Partial closes / position scaling and trailing stops
- [ ] Pending-order expiry (GTC → GTD/day orders)
- [ ] Parameter sweeps / vectorized multi-run driver

## License

No license yet — all rights reserved until one is chosen.
