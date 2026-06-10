from dataclasses import dataclass, field
from pathlib import Path

_ALL_COLUMNS = ("ts", "bid", "ask", "bid_vol", "ask_vol")

@dataclass(frozen=True)
class DataConfig:
    data_dir:   Path
    symbols:    tuple[str, ...]

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