import numpy as np
import time

from .config import DataConfig, BacktestConfig
from .data_loader import DataLoader
from ._core import make_bar  # type: ignore  # C++ extension (pybind11)

class Backtester:
    """
    Drives the hourly event loop over all configured symbols.

    Python iterates over the global hour axis and delegates per-tick work
    (later: the broker simulation) to the C++ core, so the language boundary
    is crossed once per symbol-hour instead of once per tick.
    """

    def __init__(self, data_config: DataConfig, backtest_config: BacktestConfig) -> None:
        self.data_config = data_config
        self.backtest_config = backtest_config

        self.loader = DataLoader(data_config)
        idx = self.loader.index

        # global hour axis: sorted union of every symbol's hours
        self.hours = np.unique(
            np.concatenate([idx[s][:, 0] for s in idx])
        ).tolist()

        # per symbol: hour -> (start_row, end_row) into the tick columns
        self.spans = {
            s: {h: (a, b) for h, a, b in idx[s].tolist()}
            for s in idx
        }

    def _iter_hour(self):
        """Yields (hour_us, bars) per hour, where bars maps each symbol that has
        ticks this hour to an empty slot — filled on demand by the strategy."""
        for h in self.hours:
            bars = {sym: {} for sym, span in self.spans.items() if h in span}
            yield h, bars

    def run(self):
        t0 = time.perf_counter()
        for h, bars in self._iter_hour():
            pass                                     # strategy hook goes here
        elapsed = time.perf_counter() - t0
        print(f"  [ OK ]  Backtest  {len(self.hours):>13,} hours  ->  {elapsed:.3f}s")