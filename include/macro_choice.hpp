#pragma once
#include "residual_coverage.hpp"
namespace lia {
struct ChoiceAlternative {
    int node;
    std::map<int, Z> assignments;
    uint64_t visits = 0;
    bool impossible = false;
};
struct ChoiceGroup {
    int node;
    std::vector<ChoiceAlternative> alternatives;
    std::set<int> variables, arithmetic_nodes, equality_variables;
    bool exclusive = false;
    std::set<std::string> visited_modes;
};
class ChoiceCatalog {
    Formula &f;

  public:
    std::vector<ChoiceGroup> groups;
    bool complete = true;
    unsigned operations = 0, skipped = 0, scanned_nodes = 0;
    double seconds = 0;
    static constexpr unsigned operation_cap = 200000, node_cap = 50000, group_cap = 512, alternative_cap = 16;
    explicit ChoiceCatalog(Formula &, Budget &);
    std::vector<unsigned> satisfied(const ChoiceGroup &, const Assignment &) const;
    std::string signature(const Assignment &) const;
    void commit(const Assignment &);
    std::string json() const;
};
} // namespace lia
namespace lia {
struct BroadResult {
    std::optional<ResidualCandidate> best;
    std::vector<std::string> queries, assessments;
    unsigned calls = 0, macro_calls = 0, fallback_calls = 0, duplicates = 0, late = 0;
    double full_seconds = 0, first_positive_seconds = -1;
};
class MacroChoiceGenerator {
    Formula &f;
    z3::solver solver;
    std::mt19937_64 rng;
    SolveBudgetState adaptive;

  public:
    ChoiceCatalog &catalog;
    // Synthetic regression seam only; production always checks the full F.
    std::function<std::optional<GlobalResult>(unsigned)> query_override;
    unsigned calls_seen = 0;
    MacroChoiceGenerator(Formula &, ChoiceCatalog &, unsigned);
    GlobalResult query_choices(const std::vector<std::pair<unsigned, unsigned>> &, Budget &, Stats &);
    BroadResult generate(bool structured, Global &, const OnlineCoverage &, const Assignment &, Budget &,
                         Stats &, const std::function<double(const ResidualCandidate &)> &,
                         unsigned max_calls = 2);
};
BroadResult plain_broad(Global &, const OnlineCoverage &, const Assignment &, Budget &, Stats &,
                        const std::function<double(const ResidualCandidate &)> &, unsigned max_calls = 2);
} // namespace lia
