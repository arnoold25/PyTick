# Development Log

Newest entries first. Benchmark reference dataset: **275M ticks / ~37k hours**
(EURUSD + AUDUSD, six years of Dukascopy tick data).

## 2026-06-15

Project completed on the `unstable` branch: strategy interface, C++ broker
simulation and performance metrics. Version bumped `0.1.2` → `0.2.0`.

- **LOC (tracked): 1,381** — 1,067 code (545 Python + 522 C++), 275 docs
  (README 170 + Log 105), 39 build/config. Code-line growth by commit:
  163 → 223 → 280 → 283 → 299 →
  329 → 397 → 398 (on-demand make_bar) → **1,067** (broker + strategy + metrics).
  The C++ jumped 87 → 522 (the broker is 416 of it: `broker.cpp` 304 +
  `broker.hpp` 112).
- **Strategy interface.** New `Strategy` base class: subclass it, override
  `on_candle(hour, bars)` and hand the class to `bt.run(Strategy)`. `on_candle`
  fires once per hour with `{symbol: bid OHLC}` for every active symbol; the
  order helpers (`buy/sell/close/position`, `equity/cash`) forward into the C++
  broker. `on_tick(symbol, bid, ask)` is opt-in — wired only when the subclass
  overrides it (`type(s).on_tick is not Strategy.on_tick`); when active the
  broker scans every tick and calls back into Python (the expensive path).
- **C++ broker** (`broker.hpp/.cpp`). Holds account state + zero-copy bid/ask
  views + an hour→row map per symbol. `process_hour(hour)` fills pending orders
  at the hour's first tick (buy@ask, sell@bid), resolves SL/TP, marks equity to
  market, and returns `{equity, cash, opened, closed, current}`. SL/TP uses an
  **extrema-gated fast path** (one O(n) min/max pass; full tick scan only when
  both stop and target lie inside the hour's range — ambiguous order). Leverage
  caps position notional at `equity × leverage`; no commission/swap/margin-call
  in v1. P&L exact in USD for USD-quoted pairs.
- **make_bar back in the loop.** Reverted the on-demand step: bars are computed
  automatically per hour (bid-only OHLC) *before* the tick process and passed to
  `on_candle`. The public re-export stays for ad-hoc slicing. Decision: the
  ~1 s extra is dwarfed by user-side compute, and it keeps the API simple.
- **Engine ordering** = the causality guarantee, per hour H: build bars →
  `broker.process_hour(H)` → `on_candle(H, bars)`. Orders placed at H's close
  fill in H+1 (tick-precise), never at the open.
- **Metrics** (`metrics.py`). `BacktestResult` with end capital, return %,
  total trades / wins / losses, win rate, Sharpe & Sortino (daily equity
  returns, annualized `√ann_factor`, risk-free `risk_free`). `BacktestConfig`
  gained `lot_size`, `risk_free`, `ann_factor`.
- **Verification.** Synthetic broker tests pass exactly: long+TP (+430),
  short+SL (−920), leverage guard (oversized order rejected), `on_tick` fires
  once per tick + override detection. Full run on EURUSD/AUDUSD: **~2.3 s warm**
  (26.6 s cold, page-fault bound), 36,702 hours; the toy example strategy posts
  3,419 trades / 33% win rate / −18% return (a naive 20:40 bracket — expected).

## 2026-06-13

- **LOC (tracked): 656** — 398 code (311 Python + 87 C++), 215 docs,
  43 build/config. Code-line growth by commit: 163 (initial pipeline) → 223
  (loader) → 280 (iterator) → 283 (C++ bridge) → 299 (bug fixes) → 329
  (make_bar) → 397 (vectorized) → 398 (guard + hot-loop cleanup).
- `make_bar` guarded against empty input (`ValueError` on `n == 0`). It is now
  the public on-demand bar primitive: the user slices any price column and calls
  it directly (`make_bar(data[sym]["bid"][a:b])`), so empty slices are reachable,
  unlike the internal hour loop. Exposed as a re-export (zero call overhead, no
  wrapper); the earlier `bar(symbol, hour)` accessor idea was dropped in favor of
  user-side slicing for arbitrary (not only hour-aligned) windows.
- `make_bar` vectorized: AVX2 min/max over 4 independent accumulator lanes
  (16 doubles/iteration), raw contiguous pointer instead of the strided
  accessor. Build flags added: `/arch:AVX2 /fp:fast`.
- **Benchmark: ~1.35 s** (from ~1.8 s scalar; empty C++ call is ~0.3 s, so the
  per-tick loop dropped ~1.5 s → ~1.05 s). The modest gain confirms the loop is
  now memory-bound (streaming ~2.5 GB of bid data through mmap), not
  compute-bound — further loop micro-optimization is not worthwhile.
- C++ bar builder wired into the hourly loop: `_iter_hour` slices the mmap'ed
  bid column (zero-copy view) and passes it straight to `_core.make_bar`.
- **Benchmark: ~1.8 s** for the full dataset (vs. ~2.5 s pure Python, ~28% faster).
- Comments and docstrings homogenized in English across Python and C++ sources;
  terminal output unified under the `[ OK ] / [SKIP]` format.
- README updated (performance section, roadmap, `python -m pytick` entry point);
  this log added.

## 2026-06-12

- Package restructured as an installable library (`pip install -e .`):
  relative imports throughout, `__init__.py` as the single aggregation point —
  no more circular imports.
- `__main__.py` added as entry point (`python -m pytick`); removes the
  `runpy`/`sys.modules` RuntimeWarning that `-m pytick.backtester` produced.
- CMake fixes: `find_package(pybind11 CONFIG REQUIRED)` was missing entirely;
  `PYBIND11_FINDPYTHON ON` set (legacy `FindPythonLibsNew` fails on
  Python 3.14); `Development` → `Development.Module`.
- Bug fixes in loader/iterator.
- **Benchmark (pure Python `_make_bar`): ~2.5 s** for the full dataset.

## 2026-06-11

- Python iterator skeleton: `Backtester` with global hour axis
  (union over all symbols) and per-symbol `(start, end)` spans per hour.
- C++ bridge added: pybind11 + scikit-build-core, `pyproject.toml`,
  `_core` extension module.

## 2026-06-10

- `DataLoader` added: read-only mmap access to the SoA columns,
  guards for missing files and index/column length mismatch.

## 2026-06-09

- Initial commit: data pipeline. `Bi5Converter` decodes LZMA-compressed
  Dukascopy `.bi5` files into one `.npy` per column plus a three-column
  hour index `[hour_start_us, start_row, end_row)`, with integrity checks
  (monotonic timestamps, column lengths, ask ≥ bid).
