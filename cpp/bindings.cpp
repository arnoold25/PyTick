#include <pybind11/pybind11.h>
#include <pybind11/pytypes.h>
#include <pybind11/stl.h>
#include <pybind11/numpy.h>
#include <pybind11/functional.h>

#include "pytick/bar_builder.hpp"
#include "pytick/broker.hpp"

namespace py = pybind11;

PYBIND11_MODULE(_core, m) {
    m.doc() = "PyTick C++ Core";

    m.def("make_bar", &make_bar,
        py::arg("bid")
    );

    py::class_<Broker>(m, "Broker")
        .def(py::init<double, double, double>(),
            py::arg("initial_capital"), py::arg("lot_size"), py::arg("leverage"))
        .def("add_symbol", &Broker::add_symbol,
            py::arg("name"), py::arg("bid"), py::arg("ask"), py::arg("index"))
        .def("submit_buy", &Broker::submit_buy,
            py::arg("sym"), py::arg("lots"), py::arg("sl"), py::arg("tp"))
        .def("submit_sell", &Broker::submit_sell,
            py::arg("sym"), py::arg("lots"), py::arg("sl"), py::arg("tp"))
        .def("submit_close", &Broker::submit_close, py::arg("sym"))
        .def("set_tick_callback", &Broker::set_tick_callback, py::arg("cb"))
        .def("process_hour", &Broker::process_hour, py::arg("hour"))
        .def("equity", &Broker::equity)
        .def("cash", &Broker::cash)
        .def("positions", &Broker::positions, py::arg("sym"));
}
