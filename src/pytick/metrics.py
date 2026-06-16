import math
import numpy as np
from dataclasses import dataclass, field

from .config import BacktestConfig

_US_PER_DAY = 86_400_000_000
_US_PER_HOUR = 3_600_000_000
_US_PER_YEAR = int(365.25 * _US_PER_DAY)


@dataclass
class BacktestResult:
    """Summary statistics of a finished backtest. `summary()` prints them;
    `plot()` draws the equity curve and trade analytics (matplotlib).

    The raw `equity_curve` (list of `(hour_us, equity)`) and `trades` (closed-
    trade dicts) are kept on the result for custom analysis and plotting."""

    initial_capital: float
    end_capital:     float
    return_pct:      float
    cagr_pct:        float
    total_trades:    int
    total_wins:      int
    total_losses:    int
    win_rate:        float
    avg_win:         float
    avg_loss:        float
    profit_factor:   float
    expectancy:      float
    max_drawdown_pct: float
    exposure_pct:    float
    sharpe:          float
    sortino:         float
    halted:          bool = False

    equity_curve: list = field(default_factory=list, repr=False)
    trades:       list = field(default_factory=list, repr=False)

    def summary(self) -> None:
        pf = "   inf" if math.isinf(self.profit_factor) else f"{self.profit_factor:.2f}"
        print("  +-- Backtest Result -------------------------")
        print(f"  |  End Capital     {self.end_capital:>16,.2f}")
        print(f"  |  Return          {self.return_pct:>15.2f} %")
        print(f"  |  CAGR            {self.cagr_pct:>15.2f} %")
        print(f"  |  Max Drawdown    {self.max_drawdown_pct:>15.2f} %")
        print(f"  |  Exposure        {self.exposure_pct:>15.2f} %")
        print(f"  |  Total Trades    {self.total_trades:>16,}")
        print(f"  |  Wins / Losses   {self.total_wins:>7,} / {self.total_losses:<8,}")
        print(f"  |  Win Rate        {self.win_rate:>15.2f} %")
        print(f"  |  Avg Win / Loss  {self.avg_win:>8,.2f} / {self.avg_loss:<,.2f}")
        print(f"  |  Profit Factor   {pf:>16}")
        print(f"  |  Expectancy      {self.expectancy:>16,.2f}")
        print(f"  |  Sharpe          {self.sharpe:>16.2f}")
        print(f"  |  Sortino         {self.sortino:>16.2f}")
        if self.halted:
            print("  |  *** account halted (risk stop) ***")
        print("  +--------------------------------------------")

    def plot(self, save: str | None = None, show: bool = True):
        """Equity curve, drawdown, per-trade & cumulative P&L, return
        histogram (matplotlib). `save` writes a PNG; `show` opens a window."""
        try:
            from .viz import plot_result
        except ImportError as e:
            raise ImportError(
                "plotting requires matplotlib - install it with "
                "`pip install matplotlib` (or `pip install -e .[viz]`)."
            ) from e
        return plot_result(self, save=save, show=show)


def compute_metrics(config: BacktestConfig, equity_curve: list, trades: list,
                    halted: bool = False) -> BacktestResult:
    """Build a `BacktestResult` from the hourly equity curve (list of
    `(hour_us, equity)`) and the closed-trade list (dicts with a `pnl` key)."""
    initial = config.initial_capital
    end = equity_curve[-1][1] if equity_curve else initial
    return_pct = (end - initial) / initial * 100.0 if initial else 0.0

    pnls = np.array([t["pnl"] for t in trades], dtype=float)
    total = int(pnls.size)
    wins_mask = pnls > 0
    loss_mask = pnls < 0
    wins = int(wins_mask.sum())
    losses = int(loss_mask.sum())
    win_rate = wins / total * 100.0 if total else 0.0

    avg_win = float(pnls[wins_mask].mean()) if wins else 0.0
    avg_loss = float(pnls[loss_mask].mean()) if losses else 0.0
    gross_profit = float(pnls[wins_mask].sum())
    gross_loss = -float(pnls[loss_mask].sum())
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = math.inf if gross_profit > 0 else 0.0
    expectancy = float(pnls.mean()) if total else 0.0

    max_dd = _max_drawdown(equity_curve)
    cagr = _cagr(equity_curve, initial, end)
    exposure = _exposure(trades, equity_curve)
    sharpe, sortino = _risk_ratios(equity_curve, config.risk_free, config.ann_factor)

    return BacktestResult(
        initial_capital=initial,
        end_capital=end,
        return_pct=return_pct,
        cagr_pct=cagr,
        total_trades=total,
        total_wins=wins,
        total_losses=losses,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        expectancy=expectancy,
        max_drawdown_pct=max_dd,
        exposure_pct=exposure,
        sharpe=sharpe,
        sortino=sortino,
        halted=halted,
        equity_curve=equity_curve,
        trades=trades,
    )


def _max_drawdown(equity_curve: list) -> float:
    """Largest peak-to-trough equity decline, as a positive percent."""
    if len(equity_curve) < 2:
        return 0.0
    eq = np.fromiter((e for _, e in equity_curve), dtype=float, count=len(equity_curve))
    peak = np.maximum.accumulate(eq)
    dd = np.where(peak > 0, (eq - peak) / peak, 0.0)
    return float(-dd.min() * 100.0)


def _cagr(equity_curve: list, initial: float, end: float) -> float:
    """Compound annual growth rate (%), from the curve's time span."""
    if len(equity_curve) < 2 or initial <= 0 or end <= 0:
        return 0.0
    span_us = equity_curve[-1][0] - equity_curve[0][0]
    years = span_us / _US_PER_YEAR
    if years <= 0:
        return 0.0
    return float(((end / initial) ** (1.0 / years) - 1.0) * 100.0)


def _exposure(trades: list, equity_curve: list) -> float:
    """Percent of the backtest's wall-clock span with at least one open
    position (overlapping trades counted once)."""
    if not trades or len(equity_curve) < 2:
        return 0.0
    span = equity_curve[-1][0] - equity_curve[0][0]
    if span <= 0:
        return 0.0
    # count the holding hour inclusively so same-hour round-trips still register
    iv = sorted((t["entry_hour"], t["exit_hour"] + _US_PER_HOUR) for t in trades)
    covered = 0
    cs, ce = iv[0]
    for s, e in iv[1:]:
        if s > ce:
            covered += ce - cs
            cs, ce = s, e
        else:
            ce = max(ce, e)
    covered += ce - cs
    return min(covered / span * 100.0, 100.0)


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
