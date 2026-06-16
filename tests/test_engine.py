"""Integration tests for the Backtester over a tiny on-disk dataset: the two
strategy entry styles (callbacks and the legacy Strategy subclass), multi-symbol
shared-capital runs, and on_tick wiring."""

import pytest

from pytick import Backtester, DataConfig, BacktestConfig, Strategy
from _util import write_symbol, US_PER_HOUR


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    root = tmp_path_factory.mktemp("npy")
    write_symbol(root, "EURUSD", [
        (0,               [1.1000, 1.1010], [1.1001, 1.1011]),
        (US_PER_HOUR,     [1.1010, 1.1025], [1.1011, 1.1026]),
        (2 * US_PER_HOUR, [1.1025, 1.1040], [1.1026, 1.1041]),
        (3 * US_PER_HOUR, [1.1040, 1.1045], [1.1041, 1.1046]),
    ])
    write_symbol(root, "AUDUSD", [
        (0,               [0.6500, 0.6510], [0.6501, 0.6511]),
        (US_PER_HOUR,     [0.6510, 0.6525], [0.6511, 0.6526]),
        (2 * US_PER_HOUR, [0.6525, 0.6540], [0.6526, 0.6541]),
        (3 * US_PER_HOUR, [0.6540, 0.6545], [0.6541, 0.6546]),
    ])
    return root


def bt(dataset, symbols=("EURUSD", "AUDUSD")):
    return Backtester(DataConfig(data_dir=dataset, symbols=symbols), BacktestConfig())


# Same momentum rule expressed both ways: flat + bullish hour -> buy a bracket.

def momentum(ctx, bars):
    for sym, bar in bars.items():
        if ctx.position(sym):
            continue
        if bar["close"] > bar["open"]:
            c = bar["close"]
            ctx.buy(sym, 1.0, sl=c - 0.0050, tp=c + 0.0020)


class Momentum(Strategy):
    def on_candle(self, hour, bars):
        for sym, bar in bars.items():
            if self.position(sym):
                continue
            if bar["close"] > bar["open"]:
                c = bar["close"]
                self.buy(sym, 1.0, sl=c - 0.0050, tp=c + 0.0020)


def test_callback_and_strategy_equivalence(dataset):
    r_cb = bt(dataset).run(on_candle=momentum)
    r_st = bt(dataset).run(Momentum)
    assert r_cb.total_trades == r_st.total_trades >= 2
    assert r_cb.end_capital == pytest.approx(r_st.end_capital)


def test_multi_symbol_trades_both_on_shared_account(dataset):
    r = bt(dataset).run(on_candle=momentum)
    assert {t["symbol"] for t in r.trades} == {"EURUSD", "AUDUSD"}
    # one synchronized equity curve across both symbols' union of hours
    assert len(r.equity_curve) == 4
    assert r.end_capital != r.initial_capital


class TickCounter(Strategy):
    def __init__(self):
        self.n = 0

    def on_tick(self, symbol, bid, ask):
        self.n += 1


def test_on_tick_subclass_counts_every_tick(dataset):
    s = TickCounter()
    bt(dataset).run(s)
    assert s.n == 16          # 8 EURUSD + 8 AUDUSD ticks


def test_on_tick_callback_counts_every_tick(dataset):
    seen = {"n": 0}

    def on_tick(ctx, symbol, bid, ask):
        seen["n"] += 1

    bt(dataset).run(on_candle=lambda ctx, bars: None, on_tick=on_tick)
    assert seen["n"] == 16


def test_rejects_mixing_styles(dataset):
    with pytest.raises(ValueError):
        bt(dataset).run(Momentum, on_candle=momentum)


def test_rejects_empty_run(dataset):
    with pytest.raises(ValueError):
        bt(dataset).run()
