#include "../include/pytick/bar_builder.hpp"

py::dict make_bar(py::array_t<double> bid) {
    auto r = bid.unchecked<1>();
    int n   = r.shape(0);

    double open  = r(0);
    double close = r(n - 1);
    double high  = r(0), low = r(0);

    for (int i = 1; i < n; i++) {
        if (r(i) > high) high = r(i);
        if (r(i) < low)  low  = r(i);
    }

    py::dict bar;
    bar["open"]  = open;
    bar["high"]  = high;
    bar["low"]   = low;
    bar["close"] = close;

    return bar;
}