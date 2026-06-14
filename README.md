# PyTick

High-performance Forex tick-data backtesting in Python, with a C++ core.

> **Status: early development.** The API is unstable and the broker simulation
> is not implemented yet. Not ready for production use.

## Design

PyTick is built around a few hard rules:

- **Tick data, not bars.** Backtests run on raw Dukascopy tick data (bid/ask
  plus volumes). Bars are derived views built on demand for user inspection
  (wicks, closes) — they are not part of the hot loop and not intended for
  strategy calculations.
- **Struct-of-Arrays storage.** Each symbol is stored as one `.npy` file per
  column plus an hour index. All price arrays are accessed via `mmap` — only
  the columns a backtest actually uses are ever read from disk.
- **Python drives, C++ executes.** A Python loop iterates over hours and calls
  into a C++ core (pybind11) for all per-tick work: tick scanning, broker
  simulation. The language boundary is crossed once per symbol-hour (~37k times
  for six years of data) by default. Per-tick Python callbacks (`on_tick`) are
  available as an opt-in that trades performance for granularity.
- **Tick-precise fills.** Orders fill at the exact tick price found by the C++
  scanner (or the exact limit price for limit orders), not at the next bar's
  open.

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
Windows).

```
pip install -e .
```

The C++ extension is built automatically via scikit-build-core/CMake — no
manual CMake invocation needed. Re-run the command after changing C++ sources.

## Usage (current state)

```python
from pathlib import Path
from pytick.data_converter import Bi5Converter
from pytick import Backtester, DataConfig, BacktestConfig

# One-time: convert raw .bi5 files into the SoA layout
Bi5Converter("EURUSD", src_dir="data/raw/EURUSD", out_dir="data/npy").build()

# Run — currently iterates hours and builds bars; no strategy hook yet
bt = Backtester(
    DataConfig(data_dir=Path("data/npy"), symbols=("EURUSD",)),
    BacktestConfig(),
)
bt.run()
```

Alternatively, run the package entry point (configured in `src/pytick/__main__.py`):

```
python -m pytick
```

## Performance

Hourly bar building over the full dataset — 275M ticks / ~37k hours:

| Implementation                       | Time    |
| ------------------------------------ | ------- |
| Pure Python (numpy per hour)         | ~2.5 s  |
| C++ core (scalar loop, pybind11)     | ~1.8 s  |
| C++ core (AVX2-vectorized min/max)   | ~1.35 s |

Measured end-to-end (mmap'ed columns, warm cache) on the development machine.
The vectorized loop is memory-bound (streaming ~2.5 GB of bid data through
mmap), so the gain over the scalar loop is modest — the per-tick work is no
longer the bottleneck.

## Roadmap

### Done

- [x] Dukascopy `.bi5` converter with integrity checks
- [x] mmap-based SoA data loader, global hour axis across symbols
- [x] `make_bar` in the C++ core (pybind11): bid-only OHLC, AVX2-vectorized
      min/max, memory-bound at ~1.35 s for 275 M ticks

### On-demand bar API

`make_bar(prices)` is the public on-demand bar primitive: the user slices any
price column themselves (`make_bar(data[sym]["bid"][a:b])`) and inspects the
resulting OHLC — arbitrary windows, not just hour-aligned. It is re-exported via
`pytick.backtester` (a re-export of the C++ symbol, not a wrapper — zero call
overhead), so `from pytick.backtester import make_bar` needs no `_core` import.

- [x] Empty-input guard in `make_bar` — user-driven slices can be empty (a time
      range with no ticks), unlike the internal hour loop where the index
      guaranteed non-empty spans
- [x] Remove `make_bar` from the hourly hot loop — bars are for user inspection
      only, not strategy calculations, so they must not be computed eagerly
- [ ] Hand the strategy callback the price arrays plus a way to map hour/time →
      row range (expose `spans`, or a small `rows_for_hour` helper), so user-side
      slicing stays ergonomic. Time-based slicing needs the `ts` column, which is
      off by default in `DataConfig`

### Strategy interface

- [ ] `on_candle(hour, bars)` — called once per hour after the C++ core
      processes that hour's ticks; the default `bars` dict is empty (bars are
      on-demand); entry point for all hourly strategy logic
- [ ] `on_tick(tick)` — opt-in, expensive; enabled only when the subclass
      overrides it (detected at init via `type(strategy).on_tick is not
      Base.on_tick`). C++ calls back into Python from inside the fill-simulation
      loop so that tick decisions and fills are interleaved in a single pass
      (a two-pass model would be causally broken). Expected cost: ~30–60 s for
      275 M ticks even with an empty body — suitable for short windows,
      development, and feature exploration, not full-dataset production runs.

### Broker simulation (C++)

- [ ] Tick-precise fills: C++ scanner finds the exact fill tick per order
- [ ] TP/SL ambiguity resolution: fast path via bar extrema, slow path via
      full tick scan when high/low are ambiguous
- [ ] Prop-firm drawdown stop: worst-case hourly equity check
- [ ] The fill-simulation loop doubles as the `on_tick` dispatch loop when
      per-tick callbacks are active — one pass, no duplicate iteration

### Performance metrics

- [ ] Daily equity snapshots
- [ ] Sharpe / Sortino ratio from equity curve

## License

No license yet — all rights reserved until one is chosen.