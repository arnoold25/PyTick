"""Package entry point: python -m pytick

Runs a tiny example strategy end-to-end so the full pipeline is exercised.
Swap `Example` for your own pytick.Strategy subclass.
"""

from pathlib import Path

from pytick import Backtester, DataConfig, BacktestConfig, Strategy


class Example(Strategy):
    """Toy momentum: when flat and the hour closed up, buy EURUSD with a fixed
    bracket. Demonstrates on_candle + stop/target orders, nothing more."""

    def on_candle(self, hour, bars):
        bar = bars.get("EURUSD")
        if bar is None or self.position("EURUSD"):
            return
        if bar["close"] > bar["open"]:              # bullish hour
            price = bar["close"]
            self.buy("EURUSD", lots=1.0,
                     sl=price - 0.0020,             # 20-pip stop
                     tp=price + 0.0040)             # 40-pip target


bt = Backtester(
    DataConfig(
        data_dir=Path("data", "npy"),
        symbols=("EURUSD", "AUDUSD"),
    ),
    BacktestConfig(),
)

result = bt.run(Example)
result.summary()
