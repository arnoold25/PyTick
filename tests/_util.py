"""Shared helpers for the test suite (synthetic tick data, no disk needed for
the broker tests; on-disk SoA layout for the engine tests)."""

import numpy as np

US_PER_HOUR = 3_600_000_000
US_PER_DAY = 86_400_000_000


def build_symbol(hours):
    """Assemble (bid, ask, index) from a list of (hour_start_us, bids, asks).

    `index` is the (H, 3) [hour_start_us, start_row, end_row) layout the loader
    and broker expect. Hours must be given in chronological order."""
    bids, asks, index = [], [], []
    row = 0
    for hus, b, a in hours:
        b = np.asarray(b, dtype=float)
        a = np.asarray(a, dtype=float)
        assert b.size == a.size and b.size > 0
        index.append((hus, row, row + b.size))
        bids.append(b)
        asks.append(a)
        row += b.size
    return np.concatenate(bids), np.concatenate(asks), np.array(index, dtype=np.int64)


def write_symbol(root, name, hours):
    """Write a synthetic symbol to disk in the SoA layout DataLoader reads."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    bid, ask, index = build_symbol(hours)
    np.save(d / "bid.npy", bid)
    np.save(d / "ask.npy", ask)
    np.save(d / "index.npy", index)
    return d
