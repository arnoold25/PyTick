#include "pytick/broker.hpp"

#include <cmath>
#include <utility>

static constexpr int64_t US_PER_HOUR = 3'600'000'000LL;
static constexpr int64_t US_PER_DAY  = 86'400'000'000LL;

Broker::Broker(double initial_capital, double lot_size, double leverage,
               double commission_per_lot,
               double swap_long, double swap_short,
               int swap_hour, int triple_swap_weekday,
               double max_drawdown_pct, bool dd_trailing,
               double daily_loss_limit)
    : initial_capital_(initial_capital),
      lot_size_(lot_size),
      leverage_(leverage),
      cash_(initial_capital),
      equity_(initial_capital),
      commission_per_lot_(commission_per_lot),
      swap_long_(swap_long),
      swap_short_(swap_short),
      swap_hour_(swap_hour),
      triple_swap_weekday_(triple_swap_weekday),
      max_drawdown_pct_(max_drawdown_pct),
      dd_trailing_(dd_trailing),
      daily_loss_limit_(daily_loss_limit),
      peak_equity_(initial_capital),
      day_start_equity_(initial_capital) {}

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

    // P&L is computed in the quote currency. USD-quoted pairs (xxxUSD) are
    // already in account USD; USD-base pairs (USDxxx) are converted by dividing
    // by the pair's own price. Crosses fall back to no conversion (documented).
    sd.quote_conv = 0;
    if (name.size() >= 6) {
        if (name.compare(3, 3, "USD") == 0)      sd.quote_conv = 0;  // xxxUSD
        else if (name.compare(0, 3, "USD") == 0) sd.quote_conv = 1;  // USDxxx
    }

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

void Broker::submit_buy(const std::string& sym, double lots, double sl, double tp,
                        int entry_type, double trigger) {
    pending_.push_back({sym_id(sym), 0, entry_type, lots, sl, tp, trigger});
}

void Broker::submit_sell(const std::string& sym, double lots, double sl, double tp,
                         int entry_type, double trigger) {
    pending_.push_back({sym_id(sym), 1, entry_type, lots, sl, tp, trigger});
}

void Broker::submit_close(const std::string& sym) {
    pending_.push_back({sym_id(sym), 2, 0, 0.0, std::nan(""), std::nan(""), std::nan("")});
}

void Broker::set_tick_callback(py::function cb) {
    tick_cb_     = std::move(cb);
    has_tick_cb_ = true;
}

double Broker::to_usd(int sym, double pnl_quote, double price) const {
    // USD-base pair: P&L is in the quote currency; divide by the pair price to
    // land in USD. price is the exit (realized) or mark (unrealized) price.
    if (syms_[sym].quote_conv == 1) return pnl_quote / price;
    return pnl_quote;
}

// --- per-hour pipeline -----------------------------------------------------

py::dict Broker::process_hour(int64_t hour) {
    // Day rollover: snapshot equity for the daily-loss check before this hour's P&L.
    int64_t day = hour / US_PER_DAY;
    if (day != cur_day_) {
        cur_day_ = day;
        day_start_equity_ = equity_;
    }

    std::vector<HourStat> st = compute_hour_stats(hour);

    py::list opened, closed;

    charge_swap(hour);                 // financing on positions held into the rollover

    if (!halted_) {
        fill_pending(hour, st, opened, closed);

        if (has_tick_cb_) {
            // opt-in: full per-tick scan over every active symbol (expensive)
            for (int s = 0; s < static_cast<int>(syms_.size()); ++s)
                scan_symbol_ticks(s, hour, closed);
        } else {
            resolve_levels(hour, st, closed);
        }
    }

    mark_to_market();
    check_risk(hour, st, closed);      // may flatten + halt
    mark_to_market();

    py::dict bars;
    for (int s = 0; s < static_cast<int>(syms_.size()); ++s) {
        if (!st[s].active) continue;
        py::dict b;
        b["open"]  = st[s].open;
        b["high"]  = st[s].bhi;
        b["low"]   = st[s].blo;
        b["close"] = st[s].close;
        bars[py::cast(sym_names_[s])] = b;
    }

    py::list current;
    for (const Position& p : open_)
        current.append(pos_to_dict(p));

    py::dict rep;
    rep["equity"]      = equity_;
    rep["cash"]        = cash_;
    rep["opened"]      = opened;
    rep["closed"]      = closed;
    rep["current"]     = current;
    rep["bars"]        = bars;
    rep["halted"]      = halted_;
    rep["halt_reason"] = halt_reason_.empty() ? py::object(py::none())
                                              : py::object(py::cast(halt_reason_));
    return rep;
}

// Single fused pass per active symbol: bid OHLC always; ask extrema only when
// some short / pending sell / buy-limit-stop on the symbol will need them. This
// is the one scan over the hour's ticks (it replaces the old separate make_bar
// pass and the broker's old extrema rescan).
std::vector<Broker::HourStat> Broker::compute_hour_stats(int64_t hour) {
    const int n = static_cast<int>(syms_.size());

    std::vector<char> need_ask(n, 0);
    for (const Position& p : open_)
        if (p.dir < 0) need_ask[p.sym] = 1;            // short exits at ask
    for (const Order& o : pending_) {
        if (o.kind == 1) need_ask[o.sym] = 1;          // sell -> short -> ask
        else if (o.kind == 0 && o.entry_type != 0) need_ask[o.sym] = 1;  // buy limit/stop triggers on ask
    }

    std::vector<HourStat> st(n);
    for (int s = 0; s < n; ++s) {
        SymData& sd = syms_[s];
        auto it = sd.spans.find(hour);
        if (it == sd.spans.end()) continue;            // idle this hour
        py::ssize_t a = it->second.first, b = it->second.second;

        HourStat& h = st[s];
        h.active = true;
        h.a = a; h.b = b;
        h.open = sd.bid[a];
        h.close = sd.bid[b - 1];

        double bhi = sd.bid[a], blo = sd.bid[a];
        if (need_ask[s]) {
            double ahi = sd.ask[a], alo = sd.ask[a];
            for (py::ssize_t i = a; i < b; ++i) {
                double bd = sd.bid[i], ak = sd.ask[i];
                if (bd > bhi) bhi = bd;
                if (bd < blo) blo = bd;
                if (ak > ahi) ahi = ak;
                if (ak < alo) alo = ak;
            }
            h.ahi = ahi; h.alo = alo; h.has_ask = true;
        } else {
            for (py::ssize_t i = a; i < b; ++i) {
                double bd = sd.bid[i];
                if (bd > bhi) bhi = bd;
                if (bd < blo) blo = bd;
            }
        }
        h.bhi = bhi; h.blo = blo;

        sd.last_bid = sd.bid[b - 1];
        sd.last_ask = sd.ask[b - 1];                   // cheap single read, keeps mark valid
    }
    return st;
}

void Broker::charge_swap(int64_t hour) {
    if (swap_long_ == 0.0 && swap_short_ == 0.0) return;
    if (static_cast<int>((hour / US_PER_HOUR) % 24) != swap_hour_) return;
    int64_t day = hour / US_PER_DAY;
    if (day == last_swap_day_) return;                 // once per day
    last_swap_day_ = day;

    int wd = static_cast<int>((day + 3) % 7);          // epoch (1970-01-01) was Thursday
    double mult = (wd == triple_swap_weekday_) ? 3.0 : 1.0;
    for (Position& p : open_) {
        double s = p.lots * (p.dir > 0 ? swap_long_ : swap_short_) * mult;
        p.swap += s;
        cash_  += s;
    }
}

void Broker::fill_pending(int64_t hour, const std::vector<HourStat>& st,
                          py::list& opened, py::list& closed) {
    std::vector<Order> still;
    for (const Order& o : pending_) {
        const HourStat& s = st[o.sym];
        if (!s.active) {                   // symbol idle this hour: keep waiting
            still.push_back(o);
            continue;
        }
        const SymData& sd = syms_[o.sym];
        double fb = sd.bid[s.a];
        double fa = sd.ask[s.a];

        if (o.kind == 2) {                 // close all on symbol, at market
            close_symbol(o.sym, fb, fa, hour, closed);
            continue;
        }

        double price;
        bool   pending_fill;
        if (o.entry_type == 0) {           // market: first tick
            price = (o.kind == 0) ? fa : fb;
            pending_fill = false;
        } else {                           // limit / stop
            if (!check_trigger(o, s, price)) {
                still.push_back(o);        // not triggered this hour: carry over
                continue;
            }
            pending_fill = true;
        }

        // notional in USD (USD-base pairs carry their notional in the quote ccy)
        double notional = to_usd(o.sym, o.lots * lot_size_ * price, price);
        if (notional > equity_ * leverage_)                 // exceeds leverage: drop
            continue;

        double comm = o.lots * commission_per_lot_;
        cash_ -= comm;
        Position p{o.sym, (o.kind == 0 ? +1 : -1), o.lots, price,
                   o.sl, o.tp, hour, comm, 0.0, pending_fill};
        open_.push_back(p);
        opened.append(pos_to_dict(p));
    }
    pending_.swap(still);
}

// Pending entry triggers, gated by the hour's extrema; fills at the trigger.
//   buy  limit: ask falls to/below trigger     buy  stop: ask rises to/above
//   sell limit: bid rises to/above trigger      sell stop: bid falls to/below
bool Broker::check_trigger(const Order& o, const HourStat& s, double& fill_px) const {
    if (o.kind == 0) {                     // buy, watches ask
        if (!s.has_ask) return false;      // ask extrema not gathered -> cannot trigger
        bool hit = (o.entry_type == 1) ? (s.alo <= o.trigger)   // limit
                                       : (s.ahi >= o.trigger);  // stop
        if (hit) { fill_px = o.trigger; return true; }
    } else {                               // sell, watches bid
        bool hit = (o.entry_type == 1) ? (s.bhi >= o.trigger)   // limit
                                       : (s.blo <= o.trigger);  // stop
        if (hit) { fill_px = o.trigger; return true; }
    }
    return false;
}

// Fast path: gate SL/TP by the cached hour extrema; full tick scan only when
// both levels sit inside the range (order is then ambiguous). Positions just
// filled by a limit/stop this hour are left until next hour (their fill was
// mid-hour, so this hour's extrema include pre-fill ticks).
void Broker::resolve_levels(int64_t hour, const std::vector<HourStat>& st, py::list& closed) {
    std::vector<Position> survivors;
    survivors.reserve(open_.size());
    for (Position& p : open_) {
        bool has_level = !std::isnan(p.sl) || !std::isnan(p.tp);
        bool skip = p.was_pending && p.entry_hour == hour;
        double exit_px;
        if (has_level && !skip && st[p.sym].active && hit_level(p, st[p.sym], exit_px))
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
            bool skip = p.was_pending && p.entry_hour == hour;
            if (p.sym == sym && !skip && tick_hit(p, bd, ak, exit_px)) {
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

void Broker::mark_to_market() {
    double unreal = 0.0;
    for (const Position& p : open_) {
        const SymData& sd = syms_[p.sym];
        double px = (p.dir > 0) ? sd.last_bid : sd.last_ask;   // exit side
        if (!std::isnan(px))
            unreal += to_usd(p.sym, p.dir * (px - p.entry_price) * p.lots * lot_size_, px);
    }
    equity_ = cash_ + unreal;
}

// --- risk stop -------------------------------------------------------------

// Equity if every open position were marked at this hour's adverse extreme
// (long -> hour low bid, short -> hour high ask): the worst the account touched
// intra-hour. Idle symbols fall back to their last price.
double Broker::worst_case_equity(const std::vector<HourStat>& st) const {
    double unreal = 0.0;
    for (const Position& p : open_) {
        const SymData& sd = syms_[p.sym];
        const HourStat& s = st[p.sym];
        double adverse;
        if (s.active && (p.dir > 0 || s.has_ask))
            adverse = (p.dir > 0) ? s.blo : s.ahi;
        else
            adverse = (p.dir > 0) ? sd.last_bid : sd.last_ask;
        if (!std::isnan(adverse))
            unreal += to_usd(p.sym, p.dir * (adverse - p.entry_price) * p.lots * lot_size_, adverse);
    }
    return cash_ + unreal;
}

void Broker::check_risk(int64_t hour, const std::vector<HourStat>& st, py::list& closed) {
    if (halted_) return;
    if (equity_ > peak_equity_) peak_equity_ = equity_;

    bool breach = false;
    bool adverse = false;
    if (max_drawdown_pct_ > 0.0) {
        double base  = dd_trailing_ ? peak_equity_ : initial_capital_;
        double limit = base * (1.0 - max_drawdown_pct_ / 100.0);
        if (worst_case_equity(st) <= limit) {
            breach = true; adverse = true; halt_reason_ = "max_drawdown";
        }
    }
    if (!breach && daily_loss_limit_ > 0.0 &&
        equity_ <= day_start_equity_ - daily_loss_limit_) {
        breach = true; halt_reason_ = "daily_loss";
    }

    if (breach) {
        flatten_all(st, hour, closed, adverse);   // adverse level for DD, mark for daily-loss
        halted_ = true;
    }
}

void Broker::flatten_all(const std::vector<HourStat>& st, int64_t hour,
                         py::list& closed, bool use_adverse) {
    for (Position& p : open_) {
        const SymData& sd = syms_[p.sym];
        const HourStat& s = st[p.sym];
        double px;
        if (use_adverse && s.active && (p.dir > 0 || s.has_ask))
            px = (p.dir > 0) ? s.blo : s.ahi;
        else
            px = (p.dir > 0) ? sd.last_bid : sd.last_ask;
        record_close(p, px, hour, closed);
    }
    open_.clear();
}

// --- SL/TP helpers ---------------------------------------------------------

bool Broker::hit_level(const Position& p, const HourStat& s, double& exit_px) const {
    bool sl_set = !std::isnan(p.sl), tp_set = !std::isnan(p.tp);
    bool sl_hit, tp_hit;
    if (p.dir > 0) {                       // long: exit at bid
        sl_hit = sl_set && s.blo <= p.sl;
        tp_hit = tp_set && s.bhi >= p.tp;
    } else {                               // short: exit at ask
        sl_hit = sl_set && s.ahi >= p.sl;
        tp_hit = tp_set && s.alo <= p.tp;
    }
    if (!sl_hit && !tp_hit) return false;
    if (sl_hit && !tp_hit) { exit_px = p.sl; return true; }
    if (tp_hit && !sl_hit) { exit_px = p.tp; return true; }
    exit_px = first_touch(p, s.a, s.b);    // both in range: which came first?
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
    double close_comm = p.lots * commission_per_lot_;
    cash_ -= close_comm;
    double gross = to_usd(p.sym, p.dir * (exit_px - p.entry_price) * p.lots * lot_size_, exit_px);
    cash_ += gross;
    double commission = p.commission + close_comm;
    double net = gross - commission + p.swap;
    closed.append(trade_dict(p, exit_px, hour, gross, commission, p.swap, net));
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

py::dict Broker::trade_dict(const Position& p, double exit_px, int64_t exit_hour,
                            double gross, double commission, double swap, double net) const {
    py::dict d = pos_to_dict(p);
    d["exit_price"] = exit_px;
    d["exit_hour"]  = exit_hour;
    d["gross_pnl"]  = gross;
    d["commission"] = commission;
    d["swap"]       = swap;
    d["pnl"]        = net;
    return d;
}

py::list Broker::positions(const std::string& name) const {
    int s = sym_id(name);
    py::list out;
    for (const Position& p : open_)
        if (p.sym == s) out.append(pos_to_dict(p));
    return out;
}
