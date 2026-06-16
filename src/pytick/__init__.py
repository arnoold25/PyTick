"""PyTick - Forex tick-data backtesting in Python with a C++ core."""

from .config import DataConfig, BacktestConfig
from .data_loader import DataLoader
from .strategy import Strategy, Context
from .metrics import BacktestResult
from .backtester import Backtester, make_bar  # type: ignore

__all__ = [
    "DataConfig",
    "BacktestConfig",
    "DataLoader",
    "Strategy",
    "Context",
    "BacktestResult",
    "Backtester",
    "make_bar",
]
__version__ = "0.3.0"
