#include "pytick/bar_builder.hpp"

#include <immintrin.h>   // AVX2 intrinsics
#include <algorithm>

// Hot path: bid arrives as a zero-copy, contiguous view into the mmap'ed
// column. Single pass, AVX2-vectorized min/max over 4 independent accumulator
// lanes (16 doubles / iteration). The independent lanes break the loop-carried
// dependency so the loop runs at throughput, not latency -> memory-bound.
// Assumes a contiguous float64 array (always true for a 1-D mmap slice here).
py::dict make_bar(py::array_t<double> bid) {
    const double* p = bid.data();
    const py::ssize_t n = bid.shape(0);

    // user-driven slices can be empty (e.g. a time range with no ticks); the
    // internal hour loop could not, since the index only holds non-empty hours
    if (n == 0)
        throw py::value_error("make_bar: empty array (no ticks in the given range)");

    const double open  = p[0];
    const double close = p[n - 1];

    __m256d mx0 = _mm256_set1_pd(p[0]), mn0 = mx0;
    __m256d mx1 = mx0, mn1 = mx0;
    __m256d mx2 = mx0, mn2 = mx0;
    __m256d mx3 = mx0, mn3 = mx0;

    py::ssize_t i = 0;
    for (; i + 16 <= n; i += 16) {
        __m256d v0 = _mm256_loadu_pd(p + i);
        __m256d v1 = _mm256_loadu_pd(p + i + 4);
        __m256d v2 = _mm256_loadu_pd(p + i + 8);
        __m256d v3 = _mm256_loadu_pd(p + i + 12);
        mx0 = _mm256_max_pd(mx0, v0); mn0 = _mm256_min_pd(mn0, v0);
        mx1 = _mm256_max_pd(mx1, v1); mn1 = _mm256_min_pd(mn1, v1);
        mx2 = _mm256_max_pd(mx2, v2); mn2 = _mm256_min_pd(mn2, v2);
        mx3 = _mm256_max_pd(mx3, v3); mn3 = _mm256_min_pd(mn3, v3);
    }

    // fold the 4 lanes into one vector, then horizontally to scalars
    __m256d vmax = _mm256_max_pd(_mm256_max_pd(mx0, mx1), _mm256_max_pd(mx2, mx3));
    __m256d vmin = _mm256_min_pd(_mm256_min_pd(mn0, mn1), _mm256_min_pd(mn2, mn3));

    alignas(32) double hb[4], lb[4];
    _mm256_store_pd(hb, vmax);
    _mm256_store_pd(lb, vmin);
    double high = std::max({hb[0], hb[1], hb[2], hb[3]});
    double low  = std::min({lb[0], lb[1], lb[2], lb[3]});

    // scalar tail (< 16 remaining; also covers n < 16, since i starts at 0)
    for (; i < n; ++i) {
        high = std::max(high, p[i]);
        low  = std::min(low,  p[i]);
    }

    py::dict bar;
    bar["open"]  = open;
    bar["high"]  = high;
    bar["low"]   = low;
    bar["close"] = close;

    return bar;
}
