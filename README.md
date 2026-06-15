# PyTick

High-performance Forex tick-data backtesting in Python, with a C++ core.

> **Status: pre-1.0.** The strategy interface, the C++ broker simulation and the
> performance metrics are implemented and run end-to-end (`unstable` branch). The
> API is still unstable and not ready for production use.

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
  core (pybind11) does all per-tick work — fills, SL/TP scanning, equity. The
  language boundary is crossed once per hour (~37k times for six years of data),
  not once per tick (275M times). The opt-in `on_tick` callback trades that for
  per-tick granularity.
- **Decide at the close, no look-ahead.** Each hour the broker processes that
  hour's ticks *before* `on_candle` runs, so the strategy always acts on a
  completed candle. Orders it places fill on the next hour's first tick (or the
  exact SL/TP level), never at the current open.

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
pip install -e .
```

Re-run after changing C++ sources.

## Usage

Subclass `Strategy`, override `on_candle`, and hand the class to `run`:

```python
from pathlib import Path
from pytick import Backtester, DataConfig, BacktestConfig, Strategy

class MyStrategy(Strategy):
    def on_candle(self, hour, bars):
        # bars = {"EURUSD": {"open","high","low","close"}, ...}  (bid-only)
        bar = bars.get("EURUSD")
        if bar is None or self.position("EURUSD"):
            return
        if bar["close"] > bar["open"]:                 # bullish hour
            price = bar["close"]
            self.buy("EURUSD", lots=1.0,
                     sl=price - 0.0020,                # absolute prices
                     tp=price + 0.0040)

bt = Backtester(
    DataConfig(data_dir=Path("data/npy"), symbols=("EURUSD", "AUDUSD")),
    BacktestConfig(),
)
result = bt.run(MyStrategy)
result.summary()
```

Or run the bundled example via the package entry point:

```
python -m pytick
```

### Strategy API

Inside `on_candle(hour, bars)` (called once per hour, at the close):

| Call                                  | Effect                                            |
| ------------------------------------- | ------------------------------------------------- |
| `self.buy(sym, lots, sl=None, tp=None)`  | Open a long; fills next tick at the ask        |
| `self.sell(sym, lots, sl=None, tp=None)` | Open a short; fills next tick at the bid        |
| `self.close(sym)`                     | Close every open position on `sym` (market)       |
| `self.position(sym)`                  | List of open positions on `sym` (empty if flat)   |
| `self.equity` / `self.cash`           | Account equity (incl. unrealized) / realized cash |

`sl`/`tp` are absolute prices. `on_tick(self, symbol, bid, ask)` is an opt-in
hook: override it and the broker scans every tick and calls it for each one —
tens of seconds for the full dataset, so use it for short windows and feature
exploration, not production sweeps.

### Broker model (v1)

- **Orders:** market entries with an optional stop-loss / take-profit bracket;
  size in standard lots (`lot_size`, default 100k base-currency units).
- **Fills:** next tick after the order — buy at the ask, sell at the bid;
  SL/TP fill at the exact level. No commission or swap.
- **SL/TP scan:** gated by the hour's price extrema (fast path); a full tick
  scan runs only when both stop and target sit inside the hour's range.
- **P&L:** computed in the quote currency — exact in account USD for
  USD-quoted pairs (EURUSD, AUDUSD, GBP/NZD-USD). USD-base pairs (USDJPY, …)
  would need conversion and are out of v1 scope.
- **Leverage** caps the maximum position notional (`equity × leverage`);
  there is no margin call or liquidation in v1.

### Metrics

`result.summary()` reports end capital, return %, total trades, wins/losses,
win rate, and the Sharpe & Sortino ratios (from daily equity returns,
annualized with `ann_factor`, risk-free `risk_free`).

## Performance

Hourly bid-bar building over the full dataset — 275M ticks / ~37k hours:

| Implementation                       | Time    |
| ------------------------------------ | ------- |
| Pure Python (numpy per hour)         | ~2.5 s  |
| C++ core (scalar loop, pybind11)     | ~1.8 s  |
| C++ core (AVX2-vectorized min/max)   | ~1.35 s |

A full backtest (bars + broker tick scan with an SL/TP strategy holding
positions, two symbols) runs in **~2.3 s warm** / ~27 s cold (the first pass
streams ~2.5 GB through mmap, page-fault bound). Measured on the development
machine.

## Roadmap

### Done

- [x] Dukascopy `.bi5` converter with integrity checks
- [x] mmap-based SoA data loader, global hour axis across symbols
- [x] `make_bar` in the C++ core: bid-only OHLC, AVX2-vectorized, memory-bound
- [x] `Strategy` interface: `on_candle` (per hour, all active symbols) and the
      opt-in per-tick `on_tick` (override-detected)
- [x] C++ broker: tick-precise market + SL/TP fills, extrema-gated SL/TP scan,
      leverage-capped sizing, per-hour equity/position reporting
- [x] Performance metrics: end capital, return, win rate, trade counts,
      Sharpe & Sortino

### Next

- [ ] Limit / stop pending entries
- [ ] Quote-currency conversion for USD-base pairs (USDJPY, …)
- [ ] Commission / swap modelling
- [ ] Prop-firm drawdown stop (worst-case intra-hour equity)
- [ ] Fold bid-bar extrema into the broker scan to drop the separate `make_bar`
      pass

## License

No license yet — all rights reserved until one is chosen.
