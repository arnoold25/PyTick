#include <pybind11/pybind11.h>
#include <pybind11/pytypes.h>
#include <pybind11/stl.h>
#include <pybind11/numpy.h>
#include <pybind11/functional.h>

#include <cmath>

#include "pytick/bar_builder.hpp"
#include "pytick/broker.hpp"

namespace py = pybind11;

PYBIND11_MODULE(_core, m) {
    m.doc() = "PyTick C++ Core";

    m.def("make_bar", &make_bar,
        py::arg("bid")
    );

    py::class_<Broker>(m, "Broker")
        .def(py::init<double, double, double,
                      double, double, double, int, int, double, bool, double>(),
            py::arg("initial_capital"), py::arg("lot_size"), py::arg("leverage"),
            py::arg("commission_per_lot") = 0.0,
            py::arg("swap_long") = 0.0, py::arg("swap_short") = 0.0,
            py::arg("swap_hour") = 22, py::arg("triple_swap_weekday") = 2,
            py::arg("max_drawdown_pct") = 0.0, py::arg("dd_trailing") = true,
            py::arg("daily_loss_limit") = 0.0)
        .def("add_symbol", &Broker::add_symbol,
            py::arg("name"), py::arg("bid"), py::arg("ask"), py::arg("index"))
        .def("submit_buy", &Broker::submit_buy,
            py::arg("sym"), py::arg("lots"), py::arg("sl"), py::arg("tp"),
            py::arg("entry_type") = 0, py::arg("trigger") = std::nan(""))
        .def("submit_sell", &Broker::submit_sell,
            py::arg("sym"), py::arg("lots"), py::arg("sl"), py::arg("tp"),
            py::arg("entry_type") = 0, py::arg("trigger") = std::nan(""))
        .def("submit_close", &Broker::submit_close, py::arg("sym"))
        .def("set_tick_callback", &Broker::set_tick_callback, py::arg("cb"))
        .def("process_hour", &Broker::process_hour, py::arg("hour"))
        .def("equity", &Broker::equity)
        .def("cash", &Broker::cash)
        .def("halted", &Broker::halted)
        .def("positions", &Broker::positions, py::arg("sym"));
}
