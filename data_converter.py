import lzma
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

US_PER_HOUR = 3_600 * 1_000_000  # microseconds per hour
_BI5_DTYPE = np.dtype([
    ("ms",      ">u4"),  # milliseconds from the start of the hour
    ("ask",     ">u4"),  # point-scaled integer price
    ("bid",     ">u4"),  # point-scaled integer price
    ("ask_vol", ">f4"),  # ask volume (millions of base currency)
    ("bid_vol", ">f4"),  # bid volume (millions of base currency)
])
POINT_DIVISOR: dict[str, float] = {
    "EURUSD": 100_000, "GBPUSD": 100_000, "AUDUSD": 100_000,
    "USDCAD": 100_000, "USDCHF": 100_000, "NZDUSD": 100_000,
    "USDJPY": 1_000, "EURJPY": 1_000, "GBPJPY": 1_000,
    "XAUUSD": 1_000,
}
_COLUMNS = ("ts", "bid", "ask", "bid_vol", "ask_vol")

class Bi5Converter:
    """
    Converts a directory of LZMA-compressed Dukascopy .bi5 files into a
    Struct-of-Arrays layout: one .npy per column plus a three-column hour index.

    Both stages are usable independently:
      * parse_file(path)  -> decode a single .bi5 into native numpy arrays
      * build()           -> decode all files, concatenate, validate, and write
    """

    def __init__(
        self,
        symbol: str,
        src_dir: str | Path,
        out_dir: str | Path = "data/npy",
        divisor: float | None = None,
    ) -> None:
        self.symbol = symbol
        self.src_dir = Path(src_dir)
        self.out_dir = Path(out_dir) / symbol

        if divisor is not None:
            self.divisor = float(divisor)
        elif symbol in POINT_DIVISOR:
            self.divisor = float(POINT_DIVISOR[symbol])
        else:
            raise ValueError(
                f"No price divisor known for {symbol!r}. Pass divisor=... explicitly "
                f"(a wrong divisor silently corrupts every price)."
            )

    # --- single-file stage -------------------------------------------------

    @staticmethod
    def hour_start_us(filename: str) -> int:
        """
        Absolute start of the file's hour, in microseconds since epoch.

        Ticktorial naming scheme: SYMBOL_YYYY-MM-DD_HH(.bi5). The .bi5 content
        only stores ms offsets within the hour, so the absolute hour must be
        derived from the filename. Override this method for a different naming scheme.
        """
        parts = Path(filename).stem.split("_")
        dt = datetime.strptime(f"{parts[-2]}_{parts[-1]}", "%Y-%m-%d_%H")
        dt = dt.replace(tzinfo=timezone.utc)             # Dukascopy timestamps are UTC
        return int(dt.timestamp()) * 1_000_000           # exact: full hour

    def parse_file(self, path: str | Path):
        """
        Decodes a .bi5 file into (ts, bid, ask, bid_vol, ask_vol) as native
        numpy arrays. Returns None for empty hours (weekend / market closed) and
        for unreadable files. ts is absolute int64 microseconds.
        """
        path = Path(path)
        try:
            with lzma.open(path) as f:
                raw = f.read()
        except (lzma.LZMAError, EOFError):
            print(f"  [SKIP]  corrupt ..... {path.name}")
            return None

        if not raw:                                      # valid but empty hour
            return None
        if len(raw) % _BI5_DTYPE.itemsize:               # truncated download
            print(f"  [SKIP]  incomplete .. {path.name}")
            return None

        rec = np.frombuffer(raw, dtype=_BI5_DTYPE)
        base = self.hour_start_us(path.name)

        ts = base + rec["ms"].astype(np.int64) * 1_000   # ms offset -> absolute µs
        bid = rec["bid"].astype(np.float64) / self.divisor
        ask = rec["ask"].astype(np.float64) / self.divisor
        bid_vol = rec["bid_vol"].astype(np.float64)
        ask_vol = rec["ask_vol"].astype(np.float64)
        return ts, bid, ask, bid_vol, ask_vol

    # --- full dataset stage ------------------------------------------------

    def build(self) -> int:
        """
        Decodes every .bi5 in src_dir, concatenates chronologically, validates,
        and writes the SoA columns plus the hour index to out_dir.
        Returns the total tick count.
        """
        files = sorted(self.src_dir.iterdir())           # SYMBOL_YYYY-MM-DD_HH sorts chronologically
        if not files:
            raise FileNotFoundError(f"No files found in {self.src_dir}")

        cols: list[list[np.ndarray]] = [[] for _ in _COLUMNS]
        for path in files:
            parsed = self.parse_file(path)
            if parsed is None:
                continue
            for bucket, arr in zip(cols, parsed):
                bucket.append(arr)

        if not cols[0]:
            raise RuntimeError(f"No usable ticks found for {self.symbol}")

        # NOTE: chunks and result briefly coexist in RAM
        # (~6-12 GB for 150M ticks x5). For tighter memory: sum lengths in a
        # first pass, then write into np.lib.format.open_memmap arrays.
        ts, bid, ask, bid_vol, ask_vol = (np.concatenate(c) for c in cols)

        # --- correctness guards: cheaper than a silent bug ---
        assert np.all(np.diff(ts) >= 0), "Timestamps not monotonic - check file order"
        n = len(ts)
        assert all(len(a) == n for a in (bid, ask, bid_vol, ask_vol)), "Column length mismatch"
        assert (ask >= bid).mean() > 0.99, "ask/bid are likely swapped"

        self.out_dir.mkdir(parents=True, exist_ok=True)
        np.save(self.out_dir / "ts.npy", ts)
        np.save(self.out_dir / "bid.npy", bid)
        np.save(self.out_dir / "ask.npy", ask)
        np.save(self.out_dir / "bid_vol.npy", bid_vol)
        np.save(self.out_dir / "ask_vol.npy", ask_vol)
        np.save(self.out_dir / "index.npy", self._build_index(ts))

        print(f"  [ OK ]  {self.symbol:<8}  {n:>13,} ticks  ->  {self.out_dir}")
        return n

    @staticmethod
    def _build_index(ts: np.ndarray) -> np.ndarray:
        """
        Three-column hour index [hour_start_us, start_row, end_row) built from
        the final ts array. Rows are half-open [start, end). Hours without ticks
        have no row. Requires sorted ts (guaranteed by build()).
        """
        hours = ts // US_PER_HOUR                        # integer hour bucket per tick
        change = np.flatnonzero(np.diff(hours)) + 1      # first row of each new hour
        starts = np.concatenate(([0], change))
        ends = np.concatenate((change, [len(ts)]))
        hour_ts = hours[starts] * US_PER_HOUR            # back to absolute µs
        return np.column_stack([hour_ts, starts, ends]).astype(np.int64)

if __name__ == "__main__":
    print("  --- Bi5 Converter " + "-" * 40)
    symbols = [j.name for j in Path("data", "raw").iterdir()]
    for sym in symbols:
        Bi5Converter(sym, Path("data", "raw", sym), divisor=POINT_DIVISOR[sym]).build()
    print("  " + "-" * 58)
