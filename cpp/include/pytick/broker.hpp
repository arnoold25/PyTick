#pragma once
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/numpy.h>
#include <pybind11/functional.h>

#include <string>
#include <vector>
#include <unordered_map>
#include <cstdint>

namespace py = pybind11;

// A standing position. dir = +1 long / -1 short. sl/tp are absolute prices,
// NaN when unset. Size is in lots; notional units = lots * lot_size.
struct Position {
    int     sym;
    int     dir;
    double  lots;
    double  entry_price;
    double  sl;
    double  tp;
    int64_t entry_hour;
};

// A queued order from on_candle/on_tick, filled on the next tick of its symbol.
// kind: 0 = open long, 1 = open short, 2 = close all on symbol.
struct Order {
    int    sym;
    int    kind;
    double lots;
    double sl;
    double tp;
};

// Per-symbol tick data plus an hour -> [start, end) row map. Holds the numpy
// arrays to keep the mmap buffers alive behind the raw pointers.
struct SymData {
    const double* bid = nullptr;
    const double* ask = nullptr;
    py::ssize_t   n   = 0;
    std::unordered_map<int64_t, std::pair<py::ssize_t, py::ssize_t>> spans;
    double last_bid;          // last seen close, for mark-to-market on idle hours
    double last_ask;
    py::array bid_arr, ask_arr, index_arr;
};

// Tick-precise broker simulation. Python drives the hourly loop and calls
// process_hour once per hour; everything per-tick (fills, SL/TP, equity) runs
// here in C++. Orders submitted from the strategy fill on the next tick.
class Broker {
public:
    Broker(double initial_capital, double lot_size, double leverage);

    void add_symbol(const std::string& name,
                    py::array_t<double> bid,
                    py::array_t<double> ask,
                    py::array_t<int64_t> index);

    // order submission (from the Python strategy)
    void submit_buy(const std::string& sym, double lots, double sl, double tp);
    void submit_sell(const std::string& sym, double lots, double sl, double tp);
    void submit_close(const std::string& sym);

    // opt-in per-tick callback (Strategy.on_tick)
    void set_tick_callback(py::function cb);

    // advance one hour: fill pending orders, resolve SL/TP, mark to market.
    // returns {equity, cash, opened, closed, current}.
    py::dict process_hour(int64_t hour);

    double   equity() const { return equity_; }
    double   cash()   const { return cash_; }
    py::list positions(const std::string& sym) const;

private:
    struct Ext { bool has; double bhi, blo, ahi, alo; py::ssize_t a, b; };

    int  sym_id(const std::string& name) const;

    void fill_pending(int64_t hour, py::list& opened, py::list& closed);
    void resolve_levels(int64_t hour, py::list& closed);          // fast path
    void scan_symbol_ticks(int sym, int64_t hour, py::list& closed); // callback path
    void close_symbol(int sym, double fill_bid, double fill_ask,
                      int64_t hour, py::list& closed);
    void update_last_prices(int64_t hour);
    void mark_to_market();

    bool   hit_level(const Position& p, const Ext& e, double& exit_px) const;
    double first_touch(const Position& p, py::ssize_t a, py::ssize_t b) const;
    bool   tick_hit(const Position& p, double bid, double ask, double& exit_px) const;
    void   record_close(const Position& p, double exit_px, int64_t hour, py::list& closed);

    py::dict pos_to_dict(const Position& p) const;
    py::dict trade_dict(const Position& p, double exit_px, int64_t exit_hour, double pnl) const;

    double initial_capital_;
    double lot_size_;
    double leverage_;
    double cash_;
    double equity_;

    std::vector<std::string>             sym_names_;
    std::unordered_map<std::string, int> name_to_id_;
    std::vector<SymData>                 syms_;

    std::vector<Order>    pending_;
    std::vector<Position> open_;

    py::function tick_cb_;
    bool         has_tick_cb_ = false;
};
