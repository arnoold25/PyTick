cmake_minimum_required(VERSION 3.26)
project(pytick LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(Python COMPONENTS Interpreter Development.Module REQUIRED)
execute_process(
    COMMAND "${Python_EXECUTABLE}" -m pybind11 --cmakedir
    OUTPUT_VARIABLE pybind11_DIR
    OUTPUT_STRIP_TRAILING_WHITESPACE
)
set(PYBIND11_FINDPYTHON ON)
find_package(pybind11 CONFIG REQUIRED)

pybind11_add_module(_core
    cpp/bindings.cpp
    cpp/src/bar_builder.cpp
)

target_include_directories(_core
    PRIVATE cpp/include
)

target_compile_options(_core PRIVATE
    $<$<CXX_COMPILER_ID:MSVC>:/O2;/arch:AVX2;/fp:fast>
)

install(TARGETS _core LIBRARY DESTINATION pytick)