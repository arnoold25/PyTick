#pragma once
#include <pybind11/pybind11.h>
#include <pybind11/pytypes.h>
#include <pybind11/stl.h>
#include <pybind11/numpy.h>

namespace py = pybind11;

// Builds an OHLC bar (dict) from one hour of bid prices.
py::dict make_bar(py::array_t<double> bid);