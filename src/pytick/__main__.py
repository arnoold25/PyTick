"""Package entry point: python -m pytick

Runs a tiny example strategy end-to-end so the full pipeline is exercised, then
writes a plot of the result. The callback style below needs no subclassing;
a pytick.Strategy subclass works too (see the README).
"""

from pathlib import Path

from pytick import Backtester, DataConfig, BacktestConfig


def example(ctx, bars):
    """Toy momentum: when flat and the hour closed up, buy EURUSD with a fixed
    20/40-pip bracket. Demonstrates on_candle + stop/target orders, nothing more."""
    bar = bars.get("EURUSD")
    if bar is None or ctx.position("EURUSD"):
        return
    if bar["close"] > bar["open"]:                  # bullish hour
        price = bar["close"]
        ctx.buy("EURUSD", lots=1.0,
                sl=price - 0.0020,                  # 20-pip stop
                tp=price + 0.0040)                  # 40-pip target


bt = Backtester(
    DataConfig(
        data_dir=Path("data", "npy"),
        symbols=("EURUSD", "AUDUSD"),
    ),
    BacktestConfig(),
)

result = bt.run(on_candle=example)
result.summary()
result.plot(save="backtest.png", show=False)
print("  [ OK ]  Plot      saved -> backtest.png")
