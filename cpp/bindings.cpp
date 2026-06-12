#include <pybind11/pybind11.h>
#include <pybind11/pytypes.h>
#include <pybind11/stl.h>
#include <pybind11/numpy.h>


namespace py = pybind11;

PYBIND11_MODULE(_core, m) {
    m.doc() = "PyTick C++ Core";
}