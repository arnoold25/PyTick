# PyTick

High-performance Forex tick-data backtesting in Python, with a C++ core.

> **Status: early development.** The API is unstable and the broker simulation
> is not implemented yet. Not ready for production use.

## Design

PyTick is built around a few hard rules:

- **Tick data, not bars.** Backtests run on raw Dukascopy tick data (bid/ask
  plus volumes). Hourly bars are derived views, computed on the fly.
- **Struct-of-Arrays storage.** Each symbol is stored as one `.npy` file per
  column plus an hour index. All price arrays are accessed via `mmap` — only
  the columns a backtest actually uses are ever read from disk.
- **Python drives, C++ executes.** A Python loop iterates over hours and calls
  into a C++ core (pybind11) for all per-tick work: bar aggregation, tick
  scanning, broker simulation. C++ never calls back into Python, so the
  language boundary is crossed once per hour (~37k times for six years of
  data) instead of once per tick (hundreds of millions of times).
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

## Roadmap

- [x] Dukascopy `.bi5` converter with integrity checks
- [x] mmap-based SoA data loader, global hour axis across symbols
- [x] Hourly bar generator (Python placeholder)
- [ ] Bid/ask OHLC bars in the C++ core
- [ ] Broker simulation in C++: tick-precise fills, TP/SL ambiguity resolution
      (fast path via bar extrema, slow path via tick scan), prop-firm drawdown
      stop on worst-case hourly equity
- [ ] Performance metrics (Sharpe/Sortino from daily equity snapshots)
- [ ] Strategy interface

## License

No license yet — all rights reserved until one is chosen.