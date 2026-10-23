#pragma once
#include "core.hpp"

namespace lia {
enum class LineStatus { Exact, Unsupported, BudgetExceeded };
std::string line_status_name(LineStatus status);
struct LineDomain {
    std::vector<Interval> intervals;
    LineDomain() = default;
    explicit LineDomain(std::vector<Interval> intervals);
    static LineDomain all();
    static LineDomain point(const Z &z);
    bool contains(const Z &z) const;
    LineDomain unite(const LineDomain &other) const;
    LineDomain intersect(const LineDomain &other) const;
    LineDomain complement() const;
    LineDomain without(const Z &z) const;
    std::string json() const;
};
struct LineLimits {
    size_t intervals = 512, dag_nodes = 50000, affine_terms = 200000;
    unsigned integer_bits = 4096;
};
struct LineResult {
    LineStatus status = LineStatus::Unsupported;
    LineDomain domain;
    std::string reason;
};
class LineFailure : public std::runtime_error {
  public:
    LineStatus status;
    LineFailure(LineStatus status, const std::string &reason) : std::runtime_error(reason), status(status) {}
};
class AffineSyntax {
    Formula &f;
    std::map<int, Affine> cache;
    size_t terms = 0;
  public:
    LineLimits limits;
    explicit AffineSyntax(Formula &formula, LineLimits limits = {}) : f(formula), limits(limits) {}
    const Affine &linear(int node, Budget &budget);
};
struct StructuralEquations {
    std::vector<Affine> rows;
    bool complete = true;
    std::string reason;
};
StructuralEquations structural_equalities(Formula &f, AffineSyntax &syntax, Budget &budget);
class LineAnalyzer {
    Formula &f;
    const Assignment &base, &direction;
    AffineSyntax &syntax;
    Budget &budget;
    std::vector<std::optional<LineDomain>> truth_cache, reach_cache;
    size_t processed = 0;
    void tick();
    LineDomain bounded(LineDomain domain);
    LineDomain truth(int node);
    LineDomain reach(int node);
  public:
    LineAnalyzer(Formula &formula, const Assignment &base, const Assignment &direction,
                 AffineSyntax &syntax, Budget &budget);
    LineResult feasible();
    LineResult observable(int node);
    LineResult boolean_target(Target target);
    std::pair<Z, Z> numeric(int node);
};
std::optional<Z> line_bit_witness(const LineDomain &domain, const Z &alpha, const Z &beta,
                                 unsigned bit_index, bool desired, std::mt19937_64 &rng,
                                 bool random = false);
} // namespace lia
