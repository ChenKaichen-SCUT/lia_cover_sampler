#pragma once
#include <gmpxx.h>
#include <optional>
#include <random>
#include <stdexcept>
#include <vector>

namespace lia {
using Z = mpz_class;
using Q = mpq_class;
inline Z floor_div(const Z &a, const Z &b) {
    if (b == 0)
        throw std::invalid_argument("division by zero");
    Z r;
    mpz_fdiv_q(r.get_mpz_t(), a.get_mpz_t(), b.get_mpz_t());
    return r;
}
inline Z ceil_div(const Z &a, const Z &b) {
    if (b == 0)
        throw std::invalid_argument("division by zero");
    Z r;
    mpz_cdiv_q(r.get_mpz_t(), a.get_mpz_t(), b.get_mpz_t());
    return r;
}
inline Z mod(const Z &a, const Z &b) { return a - b * floor_div(a, b); }
inline Z gcd(const Z &a, const Z &b) {
    Z r;
    mpz_gcd(r.get_mpz_t(), a.get_mpz_t(), b.get_mpz_t());
    return r;
}
inline Z lcm(const Z &a, const Z &b) {
    Z r;
    mpz_lcm(r.get_mpz_t(), a.get_mpz_t(), b.get_mpz_t());
    return r;
}
inline Z pow2(unsigned b) { return Z(1) << b; }
inline unsigned bit(const Z &v, unsigned j) { return mpz_tstbit(v.get_mpz_t(), j); }
// Sum floor((a*i+b)/m), 0 <= i < n. Euclidean reduction, O(log m).
inline Z floor_sum(Z n, Z m, Z a, Z b) {
    if (n < 0 || m <= 0)
        throw std::invalid_argument("floor_sum domain");
    Z result = 0;
    while (true) {
        Z qa = floor_div(a, m), qb = floor_div(b, m);
        result += qa * n * (n - 1) / 2 + qb * n;
        a -= qa * m;
        b -= qb * m;
        Z y = a * n + b;
        if (y < m)
            break;
        n = y / m;
        b = y % m;
        std::swap(m, a);
    }
    return result;
}
inline Z count_bit_hits(const Z &alpha, const Z &beta, unsigned j, bool desired, const Z &L, const Z &U) {
    if (j > 63)
        throw std::invalid_argument("bit outside 0..63");
    if (U < L)
        return 0;
    Z n = U - L + 1, m = pow2(j + 1), b = beta + alpha * L;
    Z ones = floor_sum(n, m, alpha, Z(b + pow2(j))) - floor_sum(n, m, alpha, b);
    return desired ? ones : Z(n - ones);
}
inline std::optional<Z> find_bit_hit(const Z &a, const Z &b, unsigned j, bool desired, Z L, Z U, Z rank = 0) {
    Z count = count_bit_hits(a, b, j, desired, L, U);
    if (rank < 0 || rank >= count)
        return {};
    const Z start = L;
    while (L < U) {
        Z mid = floor_div(Z(L + U), 2);
        if (count_bit_hits(a, b, j, desired, start, mid) > rank)
            U = mid;
        else
            L = mid + 1;
    }
    return L;
}
inline Z random_below(const Z &bound, std::mt19937_64 &rng) {
    if (bound <= 0)
        throw std::invalid_argument("random bound");
    size_t bits = mpz_sizeinbase(bound.get_mpz_t(), 2);
    Z v;
    do {
        v = 0;
        for (size_t i = 0; i < bits; i += 64) {
            v <<= 64;
            v += Z(static_cast<unsigned long>(rng()));
        }
        v &= pow2(bits) - 1;
    } while (v >= bound);
    return v;
}
struct Interval {
    std::optional<Z> lo, hi;
    bool empty = false;
    void constrain(const Z &q, const Z &r) {
        if (q == 0) {
            if (r < 0)
                empty = true;
        } else if (q > 0) {
            Z u = floor_div(r, q);
            if (!hi || u < *hi)
                hi = u;
        } else {
            Z l = ceil_div(r, q);
            if (!lo || l > *lo)
                lo = l;
        }
        if (lo && hi && *lo > *hi)
            empty = true;
    }
    bool contains(const Z &v) const { return !empty && (!lo || v >= *lo) && (!hi || v <= *hi); }
    std::pair<Z, Z> finite(const Z &a, unsigned j) const {
        if (empty)
            throw std::logic_error("empty interval");
        if (lo && hi)
            return {*lo, *hi};
        Z period = a == 0 ? Z(1) : Z(pow2(j + 1) / gcd(a, pow2(j + 1)));
        if (lo)
            return {*lo, Z(*lo + period - 1)};
        if (hi)
            return {Z(*hi - period + 1), *hi};
        return {Z(-period / 2), Z(-period / 2 + period - 1)};
    }
};
} // namespace lia
