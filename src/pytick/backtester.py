import time
import numpy as np

from .config import DataConfig, BacktestConfig
from .data_loader import DataLoader
from .strategy import Strategy
from .metrics import compute_metrics, BacktestResult
from ._core import make_bar, Broker  # type: ignore  # C++ extension (pybind11)


class Backtester:
    """
    Drives the hourly event loop over all configured symbols.

    Per hour the engine (1) builds each active symbol's bid bar, (2) hands the
    hour to the C++ broker, which scans that hour's ticks for fills and SL/TP
    exits and marks equity to market, then (3) calls the strategy's `on_candle`
    at the close. Orders placed there fill on the next hour's first tick, so
    decisions are always taken at the close, never with look-ahead.
    """

    def __init__(self, data_config: DataConfig, backtest_config: BacktestConfig) -> None:
        self.data_config = data_config
        self.backtest_config = backtest_config

        self.loader = DataLoader(data_config)
        idx = self.loader.index
        self.symbols = list(data_config.symbols)

        # global hour axis: sorted union of every symbol's hours
        self.hours = np.unique(
            np.concatenate([idx[s][:, 0] for s in idx])
        ).tolist()

        # per symbol: hour -> (start_row, end_row) into the tick columns
        self.spans = {
            s: {h: (a, b) for h, a, b in idx[s].tolist()}
            for s in idx
        }

    def _build_broker(self) -> Broker:
        cfg = self.backtest_config
        broker = Broker(cfg.initial_capital, cfg.lot_size, float(cfg.leverage))
        for sym in self.symbols:
            d = self.loader.data[sym]
            broker.add_symbol(sym, d["bid"], d["ask"], self.loader.index[sym])
        return broker

    def run(self, strategy) -> BacktestResult:
        if isinstance(strategy, type):
            strategy = strategy()

        broker = self._build_broker()
        strategy._bind(broker)

        # opt-in: wire the per-tick callback only if the subclass overrides it
        if type(strategy).on_tick is not Strategy.on_tick:
            broker.set_tick_callback(strategy.on_tick)

        bid = {s: self.loader.data[s]["bid"] for s in self.symbols}
        spans = self.spans
        on_candle = strategy.on_candle
        process_hour = broker.process_hour

        equity_curve: list = []
        trades: list = []

        t0 = time.perf_counter()
        for h in self.hours:
            # 1. bid bars for every active symbol (before the tick process)
            bars = {}
            for sym in self.symbols:
                se = spans[sym].get(h)
                if se is not None:
                    a, b = se
                    bars[sym] = make_bar(bid[sym][a:b])

            # 2. C++ tick iterator: fills, SL/TP, equity (returns hour report)
            rep = process_hour(h)
            equity_curve.append((h, rep["equity"]))
            if rep["closed"]:
                trades.extend(rep["closed"])

            # 3. strategy decides at the close
            on_candle(h, bars)
        elapsed = time.perf_counter() - t0

        print(f"  [ OK ]  Backtest  {len(self.hours):>13,} hours  ->  {elapsed:.3f}s")
        return compute_metrics(self.backtest_config, equity_curve, trades)
