"""Package entry point: python -m pytick"""

from pathlib import Path

from .config import DataConfig, BacktestConfig
from .backtester import Backtester

bt = Backtester(
    DataConfig(
        data_dir=Path("data", "npy"),
        symbols=("EURUSD", "AUDUSD"),
    ),
    BacktestConfig(),
)

bt.run()