import numpy as np
from dataclasses import dataclass

from .config import BacktestConfig

_US_PER_DAY = 86_400_000_000


@dataclass
class BacktestResult:
    """Summary statistics of a finished backtest. `summary()` prints them."""

    initial_capital: float
    end_capital:     float
    return_pct:      float
    total_trades:    int
    total_wins:      int
    total_losses:    int
    win_rate:        float
    sharpe:          float
    sortino:         float

    def summary(self) -> None:
        print("  +-- Backtest Result -------------------------")
        print(f"  |  End Capital     {self.end_capital:>16,.2f}")
        print(f"  |  Return          {self.return_pct:>15.2f} %")
        print(f"  |  Total Trades    {self.total_trades:>16,}")
        print(f"  |  Wins / Losses   {self.total_wins:>7,} / {self.total_losses:<8,}")
        print(f"  |  Win Rate        {self.win_rate:>15.2f} %")
        print(f"  |  Sharpe          {self.sharpe:>16.2f}")
        print(f"  |  Sortino         {self.sortino:>16.2f}")
        print("  +--------------------------------------------")


def compute_metrics(config: BacktestConfig, equity_curve: list, trades: list) -> BacktestResult:
    """Build a `BacktestResult` from the hourly equity curve (list of
    `(hour_us, equity)`) and the closed-trade list (dicts with a `pnl` key)."""
    initial = config.initial_capital
    end = equity_curve[-1][1] if equity_curve else initial
    return_pct = (end - initial) / initial * 100.0 if initial else 0.0

    pnls = np.array([t["pnl"] for t in trades], dtype=float)
    total = int(pnls.size)
    wins = int((pnls > 0).sum())
    losses = int((pnls < 0).sum())
    win_rate = wins / total * 100.0 if total else 0.0

    sharpe, sortino = _risk_ratios(equity_curve, config.risk_free, config.ann_factor)

    return BacktestResult(
        initial_capital=initial,
        end_capital=end,
        return_pct=return_pct,
        total_trades=total,
        total_wins=wins,
        total_losses=losses,
        win_rate=win_rate,
        sharpe=sharpe,
        sortino=sortino,
    )


def _risk_ratios(equity_curve: list, risk_free: float, ann_factor: int):
    """Sharpe and Sortino from daily equity returns (rf annualized, target 0)."""
    if len(equity_curve) < 2:
        return 0.0, 0.0

    hours = np.fromiter((h for h, _ in equity_curve), dtype=np.int64, count=len(equity_curve))
    eq = np.fromiter((e for _, e in equity_curve), dtype=float, count=len(equity_curve))

    # collapse to the last equity of each UTC day, then daily returns
    days = hours // _US_PER_DAY
    last = np.append(np.nonzero(np.diff(days))[0], len(days) - 1)
    daily_eq = eq[last]
    if daily_eq.size < 2:
        return 0.0, 0.0

    rets = np.diff(daily_eq) / daily_eq[:-1]
    excess = rets - risk_free / ann_factor
    ann = np.sqrt(ann_factor)

    sd = excess.std(ddof=1)
    sharpe = float(excess.mean() / sd * ann) if sd > 0 else 0.0

    neg = np.minimum(excess, 0.0)
    downside = np.sqrt((neg ** 2).mean())          # downside deviation, target 0
    sortino = float(excess.mean() / downside * ann) if downside > 0 else 0.0

    return sharpe, sortino
