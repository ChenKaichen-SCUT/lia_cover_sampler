#pragma once
#include "residual_coverage.hpp"
namespace lia {
struct AlternativeAttemptState {
    uint64_t attempts = 0, sat_count = 0, timeout_like_count = 0, unknown_count = 0;
    double cumulative_seconds = 0;
    unsigned retry_level = 0, completed_levels = 0, clipped_attempts = 0;
    uint64_t last_attempt_order = 0, eligible_after_order = 0;
    bool retired = false;
    std::string json() const;
};
struct ChoiceAlternative {
    int node;
    std::map<int, Z> assignments;
    uint64_t visits = 0;
    bool impossible = false;
    AlternativeAttemptState attempt;
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
    uint64_t attempt_order = 0;
    unsigned new_since_retry = 0;

  public:
    ChoiceCatalog &catalog;
    // Synthetic regression seam only; production always checks the full F.
    std::function<std::optional<GlobalResult>(unsigned)> query_override;
    unsigned calls_seen = 0;
    std::function<std::optional<GlobalResult>(unsigned)> timed_query_override;
    bool allow_combo = false, fair_retry = true;
    std::string selected_queue;
    std::optional<std::pair<unsigned, unsigned>> select(const Assignment &);
    void fallback_opportunity() { ++attempt_order; }
    MacroChoiceGenerator(Formula &, ChoiceCatalog &, unsigned);
    GlobalResult query_choices(const std::vector<std::pair<unsigned, unsigned>> &, Budget &, Stats &,
                               unsigned requested_ms = 0);
    BroadResult generate(bool structured, Global &, const OnlineCoverage &, const Assignment &, Budget &,
                         Stats &, const std::function<double(const ResidualCandidate &)> &,
                         unsigned max_calls = 2);
};
BroadResult plain_broad(Global &, const OnlineCoverage &, const Assignment &, Budget &, Stats &,
                        const std::function<double(const ResidualCandidate &)> &, unsigned max_calls = 2);
} // namespace lia
