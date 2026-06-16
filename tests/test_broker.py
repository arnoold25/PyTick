"""Unit tests for the C++ broker, driven directly with synthetic in-memory
tick arrays (fast, no disk). Covers fills, SL/TP, limit/stop entries, quote
conversion, commission/swap, the per-tick callback and the prop-firm stop."""

import math

import numpy as np
import pytest

from pytick._core import Broker, make_bar
from _util import build_symbol, US_PER_HOUR


def make_broker(symbols, **cfg):
    """symbols: {name: hours-list}. Account: 100k, 100k lot, 30x leverage."""
    b = Broker(100_000.0, 100_000.0, 30.0, **cfg)
    for name, hours in symbols.items():
        bid, ask, idx = build_symbol(hours)
        b.add_symbol(name, bid, ask, idx)
    return b


def test_long_take_profit_exact_pnl():
    b = make_broker({"EURUSD": [(0, [1.0998, 1.1041], [1.1000, 1.1043])]})
    b.submit_buy("EURUSD", 1.0, math.nan, 1.1040, 0, math.nan)   # market long, TP 1.1040
    rep = b.process_hour(0)

    assert len(rep["closed"]) == 1
    tr = rep["closed"][0]
    assert tr["side"] == "long"
    assert tr["exit_price"] == pytest.approx(1.1040)
    assert tr["pnl"] == pytest.approx(400.0)          # (1.1040-1.1000)*100000
    assert b.cash() == pytest.approx(100_400.0)
    assert b.positions("EURUSD") == []


def test_short_stop_loss_exact_pnl():
    b = make_broker({"EURUSD": [(0, [1.1000, 1.0959], [1.1002, 1.1061])]})
    b.submit_sell("EURUSD", 1.0, 1.1060, math.nan, 0, math.nan)  # short, SL above entry
    rep = b.process_hour(0)

    tr = rep["closed"][0]
    assert tr["side"] == "short"
    assert tr["exit_price"] == pytest.approx(1.1060)
    assert tr["pnl"] == pytest.approx(-600.0)          # -(1.1060-1.1000)*100000
    assert b.cash() == pytest.approx(99_400.0)


def test_leverage_guard_rejects_oversized_order():
    b = make_broker({"EURUSD": [(0, [1.0999], [1.1000])]})
    b.submit_buy("EURUSD", 50.0, math.nan, math.nan, 0, math.nan)  # 5.5M notional > 3M cap
    rep = b.process_hour(0)

    assert rep["opened"] == []
    assert b.positions("EURUSD") == []


def test_on_tick_fires_once_per_tick():
    b = make_broker({"EURUSD": [(0, [1.0, 1.0, 1.0], [1.1, 1.1, 1.1])]})
    calls = []
    b.set_tick_callback(lambda name, bid, ask: calls.append((name, bid, ask)))
    b.process_hour(0)
    assert len(calls) == 3


def test_bars_match_make_bar():
    """Phase B fold: bars from the broker == standalone make_bar over the slice."""
    bid = [1.10, 1.12, 1.09, 1.11, 1.105]
    ask = [1.101] * 5
    b = make_broker({"EURUSD": [(0, bid, ask)]})
    rep = b.process_hour(0)
    assert rep["bars"]["EURUSD"] == make_bar(np.asarray(bid, dtype=float))


def test_buy_limit_triggers_and_fills_at_limit():
    # ask dips to 1.0949 <= limit 1.0950 -> fills at the limit price
    b = make_broker({"EURUSD": [(0, [1.0951, 1.0947], [1.0953, 1.0949])]})
    b.submit_buy("EURUSD", 1.0, math.nan, math.nan, 1, 1.0950)   # entry_type 1 = limit
    b.process_hour(0)

    pos = b.positions("EURUSD")
    assert len(pos) == 1
    assert pos[0]["entry_price"] == pytest.approx(1.0950)


def test_limit_not_triggered_carries_over():
    b = make_broker({"EURUSD": [(0, [1.0990], [1.0992]),
                                 (US_PER_HOUR, [1.0948], [1.0950])]})
    b.submit_buy("EURUSD", 1.0, math.nan, math.nan, 1, 1.0950)
    b.process_hour(0)
    assert b.positions("EURUSD") == []          # ask 1.0992 never reached 1.0950
    b.process_hour(US_PER_HOUR)
    assert len(b.positions("EURUSD")) == 1      # ask 1.0950 triggers next hour


def test_buy_stop_triggers_on_breakout():
    # ask rises to >= stop 1.1050 -> fills at the stop price
    b = make_broker({"EURUSD": [(0, [1.1048, 1.1051], [1.1050, 1.1053])]})
    b.submit_buy("EURUSD", 1.0, math.nan, math.nan, 2, 1.1050)   # entry_type 2 = stop
    b.process_hour(0)

    pos = b.positions("EURUSD")
    assert len(pos) == 1
    assert pos[0]["entry_price"] == pytest.approx(1.1050)


def test_usd_base_pnl_converted_to_usd():
    # USDJPY is USD-base: P&L is in JPY and must be divided by the exit price.
    b = make_broker({"USDJPY": [(0, [149.99, 151.01], [150.00, 151.03])]})
    b.submit_buy("USDJPY", 1.0, math.nan, 151.00, 0, math.nan)   # long, TP 151.00
    rep = b.process_hour(0)

    tr = rep["closed"][0]
    assert tr["exit_price"] == pytest.approx(151.00)
    # gross 100,000 JPY = (151-150)*100000  ->  /151 USD
    assert tr["pnl"] == pytest.approx(100_000.0 / 151.00, rel=1e-9)


def test_commission_charged_on_both_sides():
    b = make_broker({"EURUSD": [(0, [1.0998, 1.1041], [1.1000, 1.1043])]},
                    commission_per_lot=3.5)
    b.submit_buy("EURUSD", 2.0, math.nan, 1.1040, 0, math.nan)
    rep = b.process_hour(0)

    tr = rep["closed"][0]
    assert tr["gross_pnl"] == pytest.approx(800.0)         # (1.1040-1.1000)*2*100000
    assert tr["commission"] == pytest.approx(14.0)         # 2 lots * 3.5 * 2 sides
    assert tr["pnl"] == pytest.approx(786.0)
    assert b.cash() == pytest.approx(100_786.0)


def test_swap_charged_overnight():
    # Day 0 (1970-01-01, a Thursday -> not the triple-swap weekday). Open at
    # 20:00, hold through the 22:00 rollover, TP fills the same rollover hour.
    h_open = 20 * US_PER_HOUR
    h_swap = 22 * US_PER_HOUR
    b = make_broker(
        {"EURUSD": [(h_open, [1.0999], [1.1000]),
                    (h_swap, [1.1000, 1.1041], [1.1002, 1.1043])]},
        swap_long=-2.0,
    )
    b.submit_buy("EURUSD", 1.0, math.nan, 1.1040, 0, math.nan)
    b.process_hour(h_open)            # fill long, TP not yet hit
    rep = b.process_hour(h_swap)      # swap charged, then TP at 1.1040

    tr = rep["closed"][0]
    assert tr["swap"] == pytest.approx(-2.0)
    assert tr["pnl"] == pytest.approx(398.0)               # 400 gross - 2 swap
    assert b.cash() == pytest.approx(100_398.0)


def test_max_drawdown_halts_and_flattens():
    h_open = 0
    h_dd = US_PER_HOUR
    b = make_broker(
        {"EURUSD": [(h_open, [1.0999], [1.1000]),
                    (h_dd, [1.0990, 1.0890, 1.0950], [1.0992, 1.0892, 1.0952])]},
        max_drawdown_pct=10.0, dd_trailing=False,         # static 10% from 100k -> 90k floor
    )
    b.submit_buy("EURUSD", 10.0, math.nan, math.nan, 0, math.nan)
    b.process_hour(h_open)
    rep = b.process_hour(h_dd)

    assert rep["halted"] is True
    assert rep["halt_reason"] == "max_drawdown"
    assert b.halted() is True
    assert b.positions("EURUSD") == []
    tr = rep["closed"][-1]
    assert tr["exit_price"] == pytest.approx(1.0890)       # flattened at the adverse low
    assert b.cash() == pytest.approx(89_000.0)             # -11,000 = (1.0890-1.1000)*10*100000


def test_halted_account_rejects_new_orders():
    b = make_broker(
        {"EURUSD": [(0, [1.0999], [1.1000]),
                    (US_PER_HOUR, [1.0890], [1.0892]),
                    (2 * US_PER_HOUR, [1.0900], [1.0902])]},
        max_drawdown_pct=10.0, dd_trailing=False,
    )
    b.submit_buy("EURUSD", 10.0, math.nan, math.nan, 0, math.nan)
    b.process_hour(0)
    b.process_hour(US_PER_HOUR)                # breach -> halt
    assert b.halted() is True

    b.submit_buy("EURUSD", 1.0, math.nan, math.nan, 0, math.nan)
    rep = b.process_hour(2 * US_PER_HOUR)
    assert rep["opened"] == []                 # no trading after a halt
    assert b.positions("EURUSD") == []
