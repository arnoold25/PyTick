from dataclasses import dataclass
from pathlib import Path

_ALL_COLUMNS = ("ts", "bid", "ask", "bid_vol", "ask_vol")

@dataclass(frozen=True)
class DataConfig:
    """Selects symbols and which tick columns get mmap'ed by the DataLoader."""

    data_dir:   Path
    symbols:    tuple[str, ...]

    # column toggles - a disabled column's .npy is never read from disk
    ts:         bool = False
    bid:        bool = True
    ask:        bool = True
    bid_vol:    bool = False
    ask_vol:    bool = False

    def __post_init__(self):
        if not self.symbols:
            raise ValueError("Symbols empty - give at least one pair!")
        if not (self.bid and self.ask):
            raise ValueError("bid and ask need to be turned on!")

    def active_columns(self) -> tuple[str, ...]:
        return tuple(c for c in _ALL_COLUMNS if getattr(self, c))

@dataclass(frozen=True)
class BacktestConfig:
    """Account and broker parameters for the simulation."""

    initial_capital:    float = 100_000
    leverage:           int = 30

    # broker
    lot_size:           float = 100_000     # base-currency units per standard lot

    # performance metrics
    risk_free:          float = 0.0         # annual risk-free rate (Sharpe/Sortino)
    ann_factor:         int = 252           # periods/year to annualize daily returns
