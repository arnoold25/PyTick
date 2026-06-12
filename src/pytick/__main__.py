from pathlib import Path

from .config import DataConfig, BacktestConfig
from .backtester import Backtester

Backtester(
    DataConfig(
        data_dir=Path("data", "npy"),
        symbols=("EURUSD", "AUDUSD"),
    ),
    BacktestConfig(),
).run()
