from pathlib import Path
import numpy as np
import time

from config import DataConfig, BacktestConfig
from data_loader import DataLoader

class Backtester:
    def __init__(self, data_config: DataConfig, backtest_config: BacktestConfig) -> None:
        self.data_config = data_config
        self.backtest_config = backtest_config

        self.loader = DataLoader(data_config)
        idx = self.loader.index
        self.hours = np.unique(
            np.concatenate([idx[s][:, 0] for s in idx])
        ).tolist()
        self.spans = {
            s: {h: (a, b) for h, a, b in idx[s].tolist()}
            for s in idx
        }

    def _make_bar(self, cols, start, end):
        bid = cols["bid"][start:end]
        return {"open": bid[0], "high": bid.max(), "low": bid.min(), "close": bid[-1]}

    def _iter_hour(self):
        data = self.loader.data
        for h in self.hours:
            bars = {}
            for sym, span in self.spans.items():
                se = span.get(h)
                if se is None:
                    continue
                start, end = se
                bars[sym] = self._make_bar(data[sym], start, end)
            yield h, bars

    def run(self):
        t0 = time.perf_counter()
        for h, bars in self._iter_hour():
            pass
        print(f"Time:   {time.perf_counter() - t0:.3f}s")

Backtester(
    DataConfig(
        data_dir=Path("data", "npy"),
        symbols=("EURUSD", "AUDUSD"),
    ),
    BacktestConfig(),
).run()