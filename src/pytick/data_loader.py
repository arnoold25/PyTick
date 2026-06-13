import numpy as np
from pathlib import Path

from .config import DataConfig

class DataLoader:
    """
    Loads each symbol's SoA tick columns as read-only mmap arrays plus the
    hour index (read eagerly - it is small and accessed constantly).
    """

    def __init__(self, config: DataConfig) -> None:
        self.data:      dict[str, dict[str, np.ndarray]]    = {}
        self.index:     dict[str, np.ndarray]               = {}
        self.config = config
        self._load()

    def _load(self) -> None:
        cols  = self.config.active_columns()
        root = Path(self.config.data_dir)
        for sym in self.config.symbols:
            self._load_symbol(sym, root, cols)

    def _load_symbol(self, sym: str, root: Path, cols: tuple[str, ...]) -> None:
        sym_dir = root / sym

        # guards
        if not sym_dir.is_dir(): 
            raise NotADirectoryError(f"Not a valid directory: {sym_dir}")
        required = [f"{c}.npy" for c in cols] + ["index.npy"]
        missing = [f for f in required if not (sym_dir / f).exists()]
        if missing:
            raise FileNotFoundError(f"Missing files in {sym_dir}: {missing}")

        # load
        self.index[sym] = np.load(sym_dir / "index.npy")
        self.data[sym]  = {c: np.load(sym_dir / f"{c}.npy", mmap_mode="r") for c in cols}

        # integrity check
        if len(self.index[sym]) == 0:
            raise ValueError("Corrupted files")
        if any(len(self.data[sym][c]) != self.index[sym][-1, 2] for c in cols):
            raise ValueError("Corrupted files")