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
    """Account, broker and risk parameters for the simulation.

    The account is denominated in USD. Cost and risk features below all default
    to off/zero, so a default config reproduces the plain v0.2 broker exactly.
    """

    initial_capital:    float = 100_000
    leverage:           int = 30

    # broker
    lot_size:           float = 100_000     # base-currency units per standard lot

    # costs (account currency = USD)
    commission_per_lot: float = 0.0         # charged per lot on each side (open + close)
    swap_long:          float = 0.0         # per lot per night held long  (+ credit / - cost)
    swap_short:         float = 0.0         # per lot per night held short
    swap_hour:          int = 22            # UTC hour at which swap is charged
    triple_swap_weekday: int = 2            # weekday charged 3x (Mon=0..Sun=6); -1 disables

    # prop-firm style risk stop (0 / off by default)
    max_drawdown_pct:   float = 0.0         # halt+flatten when equity drops this % below base
    dd_trailing:        bool = True         # base = running peak (trailing) vs initial (static)
    daily_loss_limit:   float = 0.0         # halt when a UTC day's loss exceeds this (USD); 0 off

    # performance metrics
    risk_free:          float = 0.0         # annual risk-free rate (Sharpe/Sortino)
    ann_factor:         int = 252           # periods/year to annualize daily returns
