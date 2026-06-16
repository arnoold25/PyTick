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
// commission holds the cost paid so far (open side); swap accrues nightly.
// was_pending marks a limit/stop fill (its SL/TP is left for the next hour, so
// the mid-hour fill is never matched against pre-fill ticks of the same hour).
struct Position {
    int     sym;
    int     dir;
    double  lots;
    double  entry_price;
    double  sl;
    double  tp;
    int64_t entry_hour;
    double  commission;
    double  swap;
    bool    was_pending;
};

// A queued order from on_candle/on_tick, filled on its symbol's next ticks.
//   kind:       0 = open long, 1 = open short, 2 = close all on symbol
//   entry_type: 0 = market (first tick), 1 = limit, 2 = stop
//   trigger:    limit/stop price (NaN for market)
struct Order {
    int    sym;
    int    kind;
    int    entry_type;
    double lots;
    double sl;
    double tp;
    double trigger;
};

// Per-symbol tick data plus an hour -> [start, end) row map. Holds the numpy
// arrays to keep the mmap buffers alive behind the raw pointers.
//   quote_conv: 0 = USD-quoted (P&L already USD), 1 = USD-base (divide by price)
struct SymData {
    const double* bid = nullptr;
    const double* ask = nullptr;
    py::ssize_t   n   = 0;
    int           quote_conv = 0;
    std::unordered_map<int64_t, std::pair<py::ssize_t, py::ssize_t>> spans;
    double last_bid;          // last seen close, for mark-to-market on idle hours
    double last_ask;
    py::array bid_arr, ask_arr, index_arr;
};

// Tick-precise broker simulation. Python drives the hourly loop and calls
// process_hour once per hour; everything per-tick (bar extrema, fills, SL/TP,
// equity, risk) runs here in C++. Orders submitted from the strategy fill on
// the next tick(s). Account currency is USD.
class Broker {
public:
    Broker(double initial_capital, double lot_size, double leverage,
           double commission_per_lot = 0.0,
           double swap_long = 0.0, double swap_short = 0.0,
           int swap_hour = 22, int triple_swap_weekday = 2,
           double max_drawdown_pct = 0.0, bool dd_trailing = true,
           double daily_loss_limit = 0.0);

    void add_symbol(const std::string& name,
                    py::array_t<double> bid,
                    py::array_t<double> ask,
                    py::array_t<int64_t> index);

    // order submission (from the Python strategy). trigger is NaN for market,
    // else the limit/stop price; entry_type selects market/limit/stop.
    void submit_buy(const std::string& sym, double lots, double sl, double tp,
                    int entry_type, double trigger);
    void submit_sell(const std::string& sym, double lots, double sl, double tp,
                     int entry_type, double trigger);
    void submit_close(const std::string& sym);

    // opt-in per-tick callback (Strategy.on_tick)
    void set_tick_callback(py::function cb);

    // advance one hour: build bars, fill pending orders, resolve SL/TP, charge
    // financing, mark to market, enforce the risk stop.
    // returns {equity, cash, opened, closed, current, bars, halted, halt_reason}.
    py::dict process_hour(int64_t hour);

    double   equity() const { return equity_; }
    double   cash()   const { return cash_; }
    bool     halted() const { return halted_; }
    py::list positions(const std::string& sym) const;

private:
    // Per-symbol summary of one hour: bid OHLC always; ask extrema only when a
    // short / pending sell / buy-limit-stop on that symbol needs them.
    struct HourStat {
        bool        active  = false;
        bool        has_ask = false;
        double      open = 0, close = 0;
        double      bhi = 0, blo = 0, ahi = 0, alo = 0;
        py::ssize_t a = 0, b = 0;
    };

    int  sym_id(const std::string& name) const;

    std::vector<HourStat> compute_hour_stats(int64_t hour);
    void charge_swap(int64_t hour);
    void fill_pending(int64_t hour, const std::vector<HourStat>& st,
                      py::list& opened, py::list& closed);
    bool check_trigger(const Order& o, const HourStat& s, double& fill_px) const;
    void resolve_levels(int64_t hour, const std::vector<HourStat>& st, py::list& closed);
    void scan_symbol_ticks(int sym, int64_t hour, py::list& closed);
    void close_symbol(int sym, double fill_bid, double fill_ask,
                      int64_t hour, py::list& closed);
    void mark_to_market();

    double worst_case_equity(const std::vector<HourStat>& st) const;
    void   check_risk(int64_t hour, const std::vector<HourStat>& st, py::list& closed);
    void   flatten_all(const std::vector<HourStat>& st, int64_t hour,
                       py::list& closed, bool use_adverse);

    bool   hit_level(const Position& p, const HourStat& s, double& exit_px) const;
    double first_touch(const Position& p, py::ssize_t a, py::ssize_t b) const;
    bool   tick_hit(const Position& p, double bid, double ask, double& exit_px) const;
    void   record_close(const Position& p, double exit_px, int64_t hour, py::list& closed);
    double to_usd(int sym, double pnl_quote, double price) const;

    py::dict pos_to_dict(const Position& p) const;
    py::dict trade_dict(const Position& p, double exit_px, int64_t exit_hour,
                        double gross, double commission, double swap, double net) const;

    double initial_capital_;
    double lot_size_;
    double leverage_;
    double cash_;
    double equity_;

    // costs / risk
    double commission_per_lot_;
    double swap_long_, swap_short_;
    int    swap_hour_, triple_swap_weekday_;
    double max_drawdown_pct_;
    bool   dd_trailing_;
    double daily_loss_limit_;

    // risk-stop bookkeeping
    double  peak_equity_;
    double  day_start_equity_;
    int64_t cur_day_       = INT64_MIN;
    int64_t last_swap_day_ = INT64_MIN;
    bool    halted_        = false;
    std::string halt_reason_;

    std::vector<std::string>             sym_names_;
    std::unordered_map<std::string, int> name_to_id_;
    std::vector<SymData>                 syms_;

    std::vector<Order>    pending_;
    std::vector<Position> open_;

    py::function tick_cb_;
    bool         has_tick_cb_ = false;
};
