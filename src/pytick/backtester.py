import time
import numpy as np

from .config import DataConfig, BacktestConfig
from .data_loader import DataLoader
from .strategy import Strategy, Context
from .metrics import compute_metrics, BacktestResult
from ._core import make_bar, Broker  # type: ignore  # C++ extension (pybind11)


class Backtester:
    """
    Drives the hourly event loop over all configured symbols against one shared
    USD account (capital and the time axis are synchronized across symbols).

    Per hour the C++ broker (1) builds each active symbol's bid bar and resolves
    fills / SL-TP / financing in a single pass over that hour's ticks, then the
    engine (2) calls the strategy at the close. Orders placed there fill on the
    next hour's first tick (or the exact level), so decisions are always taken
    at the close, never with look-ahead.

    Two strategy styles (both supported):
        bt.run(MyStrategy)                       # legacy Strategy subclass
        bt.run(on_candle=fn, on_tick=fn2)        # callbacks: fn(ctx, bars)
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

    def _build_broker(self) -> Broker:
        cfg = self.backtest_config
        broker = Broker(
            cfg.initial_capital, cfg.lot_size, float(cfg.leverage),
            cfg.commission_per_lot, cfg.swap_long, cfg.swap_short,
            cfg.swap_hour, cfg.triple_swap_weekday,
            cfg.max_drawdown_pct, cfg.dd_trailing, cfg.daily_loss_limit,
        )
        for sym in self.symbols:
            d = self.loader.data[sym]
            broker.add_symbol(sym, d["bid"], d["ask"], self.loader.index[sym])
        return broker

    def run(self, strategy=None, *, on_candle=None, on_tick=None) -> BacktestResult:
        if strategy is not None and (on_candle is not None or on_tick is not None):
            raise ValueError("pass either a Strategy or on_candle/on_tick callbacks, not both")
        if strategy is None and on_candle is None and on_tick is None:
            raise ValueError("nothing to run: give a Strategy or an on_candle/on_tick callback")

        broker = self._build_broker()
        ctx, dispatch = self._wire(broker, strategy, on_candle, on_tick)

        process_hour = broker.process_hour
        equity_curve: list = []
        trades: list = []
        halted = False

        t0 = time.perf_counter()
        for h in self.hours:
            ctx.hour = h
            rep = process_hour(h)
            equity_curve.append((h, rep["equity"]))
            if rep["closed"]:
                trades.extend(rep["closed"])
            if rep["halted"]:                      # account stopped out: trading is over
                halted = True
                break
            dispatch(h, rep["bars"])
        elapsed = time.perf_counter() - t0

        tag = "  (HALTED)" if halted else ""
        print(f"  [ OK ]  Backtest  {len(equity_curve):>13,} hours  ->  {elapsed:.3f}s{tag}")
        return compute_metrics(self.backtest_config, equity_curve, trades, halted)

    def _wire(self, broker, strategy, on_candle, on_tick):
        """Normalize either entry style into (ctx, dispatch(h, bars))."""
        if strategy is not None:
            if isinstance(strategy, type):
                strategy = strategy()
            strategy._bind(broker)
            if type(strategy).on_tick is not Strategy.on_tick:
                broker.set_tick_callback(strategy.on_tick)
            return strategy._ctx, (lambda h, bars: strategy.on_candle(h, bars))

        ctx = Context(broker)
        if on_tick is not None:
            broker.set_tick_callback(lambda name, bid, ask: on_tick(ctx, name, bid, ask))
        dispatch = (lambda h, bars: on_candle(ctx, bars)) if on_candle is not None \
            else (lambda h, bars: None)
        return ctx, dispatch
