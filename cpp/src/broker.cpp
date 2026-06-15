#include "pytick/broker.hpp"

#include <cmath>
#include <utility>

Broker::Broker(double initial_capital, double lot_size, double leverage)
    : initial_capital_(initial_capital),
      lot_size_(lot_size),
      leverage_(leverage),
      cash_(initial_capital),
      equity_(initial_capital) {}

void Broker::add_symbol(const std::string& name,
                        py::array_t<double> bid,
                        py::array_t<double> ask,
                        py::array_t<int64_t> index) {
    int id = static_cast<int>(syms_.size());
    name_to_id_[name] = id;
    sym_names_.push_back(name);

    SymData sd;
    sd.bid_arr   = bid;                 // keep the mmap buffers alive
    sd.ask_arr   = ask;
    sd.index_arr = index;
    sd.bid       = bid.data();
    sd.ask       = ask.data();
    sd.n         = bid.shape(0);
    sd.last_bid  = std::nan("");
    sd.last_ask  = std::nan("");

    // index is (H, 3): [hour_start_us, start_row, end_row)
    auto idx = index.unchecked<2>();
    for (py::ssize_t r = 0; r < index.shape(0); ++r)
        sd.spans[idx(r, 0)] = {static_cast<py::ssize_t>(idx(r, 1)),
                               static_cast<py::ssize_t>(idx(r, 2))};

    syms_.push_back(std::move(sd));
}

int Broker::sym_id(const std::string& name) const {
    auto it = name_to_id_.find(name);
    if (it == name_to_id_.end())
        throw py::value_error("unknown symbol: " + name);
    return it->second;
}

void Broker::submit_buy(const std::string& sym, double lots, double sl, double tp) {
    pending_.push_back({sym_id(sym), 0, lots, sl, tp});
}

void Broker::submit_sell(const std::string& sym, double lots, double sl, double tp) {
    pending_.push_back({sym_id(sym), 1, lots, sl, tp});
}

void Broker::submit_close(const std::string& sym) {
    pending_.push_back({sym_id(sym), 2, 0.0, std::nan(""), std::nan("")});
}

void Broker::set_tick_callback(py::function cb) {
    tick_cb_     = std::move(cb);
    has_tick_cb_ = true;
}

// --- per-hour pipeline -----------------------------------------------------

py::dict Broker::process_hour(int64_t hour) {
    py::list opened, closed;

    fill_pending(hour, opened, closed);

    if (has_tick_cb_) {
        // opt-in: full per-tick scan over every active symbol (expensive)
        for (int s = 0; s < static_cast<int>(syms_.size()); ++s)
            scan_symbol_ticks(s, hour, closed);
    } else {
        // fast path: only symbols carrying SL/TP positions need a scan
        resolve_levels(hour, closed);
    }

    update_last_prices(hour);
    mark_to_market();

    py::list current;
    for (const Position& p : open_)
        current.append(pos_to_dict(p));

    py::dict rep;
    rep["equity"]  = equity_;
    rep["cash"]    = cash_;
    rep["opened"]  = opened;
    rep["closed"]  = closed;
    rep["current"] = current;
    return rep;
}

void Broker::fill_pending(int64_t hour, py::list& opened, py::list& closed) {
    std::vector<Order> still;
    for (const Order& o : pending_) {
        SymData& sd = syms_[o.sym];
        auto it = sd.spans.find(hour);
        if (it == sd.spans.end()) {        // symbol idle this hour: keep waiting
            still.push_back(o);
            continue;
        }
        py::ssize_t start = it->second.first;
        double fb = sd.bid[start];
        double fa = sd.ask[start];

        if (o.kind == 2) {                 // close all on symbol, at market
            close_symbol(o.sym, fb, fa, hour, closed);
            continue;
        }

        double price    = (o.kind == 0) ? fa : fb;          // buy@ask, sell@bid
        double notional = o.lots * lot_size_ * price;
        if (notional > equity_ * leverage_)                 // exceeds leverage: drop
            continue;

        Position p{o.sym, (o.kind == 0 ? +1 : -1), o.lots, price, o.sl, o.tp, hour};
        open_.push_back(p);
        opened.append(pos_to_dict(p));
    }
    pending_.swap(still);
}

// Fast path: gate SL/TP by the hour's price extrema; only fall back to a full
// tick scan when both levels sit inside the range (order is then ambiguous).
void Broker::resolve_levels(int64_t hour, py::list& closed) {
    std::vector<Ext> ext(syms_.size(), Ext{false, 0, 0, 0, 0, 0, 0});

    for (const Position& p : open_) {
        if (std::isnan(p.sl) && std::isnan(p.tp)) continue;
        Ext& e = ext[p.sym];
        if (e.has) continue;
        SymData& sd = syms_[p.sym];
        auto it = sd.spans.find(hour);
        if (it == sd.spans.end()) continue;            // idle: nothing to resolve
        py::ssize_t a = it->second.first, b = it->second.second;
        double bhi = sd.bid[a], blo = sd.bid[a], ahi = sd.ask[a], alo = sd.ask[a];
        for (py::ssize_t i = a; i < b; ++i) {
            double bd = sd.bid[i], ak = sd.ask[i];
            if (bd > bhi) bhi = bd;
            if (bd < blo) blo = bd;
            if (ak > ahi) ahi = ak;
            if (ak < alo) alo = ak;
        }
        e = {true, bhi, blo, ahi, alo, a, b};
    }

    std::vector<Position> survivors;
    survivors.reserve(open_.size());
    for (Position& p : open_) {
        double exit_px;
        bool has_level = !std::isnan(p.sl) || !std::isnan(p.tp);
        if (has_level && ext[p.sym].has && hit_level(p, ext[p.sym], exit_px))
            record_close(p, exit_px, hour, closed);
        else
            survivors.push_back(p);
    }
    open_.swap(survivors);
}

// Opt-in path: notify on_tick for every tick and resolve SL/TP tick-exactly.
void Broker::scan_symbol_ticks(int sym, int64_t hour, py::list& closed) {
    SymData& sd = syms_[sym];
    auto it = sd.spans.find(hour);
    if (it == sd.spans.end()) return;
    py::ssize_t a = it->second.first, b = it->second.second;
    const std::string& name = sym_names_[sym];

    for (py::ssize_t i = a; i < b; ++i) {
        double bd = sd.bid[i], ak = sd.ask[i];
        tick_cb_(name, bd, ak);
        for (std::size_t k = 0; k < open_.size();) {
            Position& p = open_[k];
            double exit_px;
            if (p.sym == sym && tick_hit(p, bd, ak, exit_px)) {
                record_close(p, exit_px, hour, closed);
                open_[k] = open_.back();        // swap-erase (order irrelevant)
                open_.pop_back();
            } else {
                ++k;
            }
        }
    }
}

void Broker::close_symbol(int sym, double fill_bid, double fill_ask,
                          int64_t hour, py::list& closed) {
    std::vector<Position> survivors;
    survivors.reserve(open_.size());
    for (Position& p : open_) {
        if (p.sym != sym) { survivors.push_back(p); continue; }
        double exit_px = (p.dir > 0) ? fill_bid : fill_ask;   // long@bid, short@ask
        record_close(p, exit_px, hour, closed);
    }
    open_.swap(survivors);
}

void Broker::update_last_prices(int64_t hour) {
    for (SymData& sd : syms_) {
        auto it = sd.spans.find(hour);
        if (it == sd.spans.end()) continue;
        py::ssize_t b = it->second.second;
        sd.last_bid = sd.bid[b - 1];
        sd.last_ask = sd.ask[b - 1];
    }
}

void Broker::mark_to_market() {
    double unreal = 0.0;
    for (const Position& p : open_) {
        const SymData& sd = syms_[p.sym];
        double px = (p.dir > 0) ? sd.last_bid : sd.last_ask;   // exit side
        if (!std::isnan(px))
            unreal += p.dir * (px - p.entry_price) * p.lots * lot_size_;
    }
    equity_ = cash_ + unreal;
}

// --- SL/TP helpers ---------------------------------------------------------

bool Broker::hit_level(const Position& p, const Ext& e, double& exit_px) const {
    bool sl_set = !std::isnan(p.sl), tp_set = !std::isnan(p.tp);
    bool sl_hit, tp_hit;
    if (p.dir > 0) {                       // long: exit at bid
        sl_hit = sl_set && e.blo <= p.sl;
        tp_hit = tp_set && e.bhi >= p.tp;
    } else {                               // short: exit at ask
        sl_hit = sl_set && e.ahi >= p.sl;
        tp_hit = tp_set && e.alo <= p.tp;
    }
    if (!sl_hit && !tp_hit) return false;
    if (sl_hit && !tp_hit) { exit_px = p.sl; return true; }
    if (tp_hit && !sl_hit) { exit_px = p.tp; return true; }
    exit_px = first_touch(p, e.a, e.b);    // both in range: which came first?
    return true;
}

double Broker::first_touch(const Position& p, py::ssize_t a, py::ssize_t b) const {
    const SymData& sd = syms_[p.sym];
    if (p.dir > 0) {
        for (py::ssize_t i = a; i < b; ++i) {
            double bd = sd.bid[i];
            if (!std::isnan(p.sl) && bd <= p.sl) return p.sl;   // SL wins ties
            if (!std::isnan(p.tp) && bd >= p.tp) return p.tp;
        }
    } else {
        for (py::ssize_t i = a; i < b; ++i) {
            double ak = sd.ask[i];
            if (!std::isnan(p.sl) && ak >= p.sl) return p.sl;
            if (!std::isnan(p.tp) && ak <= p.tp) return p.tp;
        }
    }
    return p.sl;                           // unreachable when extrema agree
}

bool Broker::tick_hit(const Position& p, double bid, double ask, double& exit_px) const {
    if (p.dir > 0) {
        if (!std::isnan(p.sl) && bid <= p.sl) { exit_px = p.sl; return true; }
        if (!std::isnan(p.tp) && bid >= p.tp) { exit_px = p.tp; return true; }
    } else {
        if (!std::isnan(p.sl) && ask >= p.sl) { exit_px = p.sl; return true; }
        if (!std::isnan(p.tp) && ask <= p.tp) { exit_px = p.tp; return true; }
    }
    return false;
}

void Broker::record_close(const Position& p, double exit_px, int64_t hour, py::list& closed) {
    double pnl = p.dir * (exit_px - p.entry_price) * p.lots * lot_size_;
    cash_ += pnl;
    closed.append(trade_dict(p, exit_px, hour, pnl));
}

// --- dict conversion -------------------------------------------------------

py::dict Broker::pos_to_dict(const Position& p) const {
    py::dict d;
    d["symbol"]      = sym_names_[p.sym];
    d["side"]        = (p.dir > 0) ? "long" : "short";
    d["lots"]        = p.lots;
    d["entry_price"] = p.entry_price;
    d["entry_hour"]  = p.entry_hour;
    if (std::isnan(p.sl)) d["sl"] = py::none(); else d["sl"] = p.sl;
    if (std::isnan(p.tp)) d["tp"] = py::none(); else d["tp"] = p.tp;
    return d;
}

py::dict Broker::trade_dict(const Position& p, double exit_px,
                            int64_t exit_hour, double pnl) const {
    py::dict d = pos_to_dict(p);
    d["exit_price"] = exit_px;
    d["exit_hour"]  = exit_hour;
    d["pnl"]        = pnl;
    return d;
}

py::list Broker::positions(const std::string& name) const {
    int s = sym_id(name);
    py::list out;
    for (const Position& p : open_)
        if (p.sym == s) out.append(pos_to_dict(p));
    return out;
}
