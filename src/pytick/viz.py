"""Matplotlib visualization of a BacktestResult. Imported lazily by
`BacktestResult.plot()` so matplotlib is only required when you actually plot.
"""

from datetime import datetime, timezone

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

_US = 1_000_000


def _times(equity_curve):
    return [datetime.fromtimestamp(h / _US, tz=timezone.utc) for h, _ in equity_curve]


def plot_result(result, save: str | None = None, show: bool = True):
    """2x2 dashboard: equity curve, drawdown, per-trade & cumulative P&L,
    and the per-trade return distribution."""
    ec = result.equity_curve
    if len(ec) < 2:
        raise ValueError("not enough equity data to plot")

    t = _times(ec)
    eq = np.fromiter((e for _, e in ec), dtype=float, count=len(ec))
    peak = np.maximum.accumulate(eq)
    dd = np.where(peak > 0, (eq - peak) / peak * 100.0, 0.0)
    pnls = np.array([tr["pnl"] for tr in result.trades], dtype=float)

    fig, ax = plt.subplots(2, 2, figsize=(13, 8))
    fig.suptitle(
        f"PyTick  |  return {result.return_pct:+.1f}%   "
        f"max DD {result.max_drawdown_pct:.1f}%   "
        f"Sharpe {result.sharpe:.2f}   trades {result.total_trades:,}"
        + ("   [HALTED]" if result.halted else ""),
        fontsize=12,
    )

    # --- equity curve with peak + underwater shading ---
    a = ax[0, 0]
    a.plot(t, eq, color="#1f77b4", lw=1.2, label="equity")
    a.plot(t, peak, color="#999999", lw=0.8, ls="--", label="peak")
    a.fill_between(t, eq, peak, where=eq < peak, color="#d62728", alpha=0.15)
    a.set_title("Equity")
    a.legend(loc="upper left", fontsize=8)
    a.grid(alpha=0.3)

    # --- drawdown underwater ---
    a = ax[0, 1]
    a.fill_between(t, dd, 0.0, color="#d62728", alpha=0.4)
    a.set_title(f"Drawdown (max {result.max_drawdown_pct:.1f}%)")
    a.set_ylabel("%")
    a.grid(alpha=0.3)

    # --- per-trade P&L (bars) + cumulative (line) ---
    a = ax[1, 0]
    if pnls.size:
        idx = np.arange(pnls.size)
        a.bar(idx, pnls, width=1.0,
              color=np.where(pnls >= 0, "#2ca02c", "#d62728"))
        a2 = a.twinx()
        a2.plot(idx, np.cumsum(pnls), color="#1f77b4", lw=1.2)
        a2.set_ylabel("cumulative", color="#1f77b4")
    a.axhline(0, color="#333333", lw=0.6)
    a.set_title("Trade P&L")
    a.set_xlabel("trade #")
    a.grid(alpha=0.3)

    # --- per-trade return distribution ---
    a = ax[1, 1]
    if pnls.size:
        bins = int(min(60, max(10, pnls.size // 20)))
        a.hist(pnls, bins=bins, color="#1f77b4", alpha=0.8)
        a.axvline(0, color="#333333", lw=0.8)
    a.set_title("Return distribution (per trade)")
    a.set_xlabel("P&L")
    a.grid(alpha=0.3)

    for a in ax[0]:
        a.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate()
    fig.tight_layout(rect=(0, 0, 1, 0.97))

    if save:
        fig.savefig(save, dpi=110)
    if show:
        plt.show()
    return fig
