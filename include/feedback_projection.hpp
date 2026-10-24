#pragma once
#include "residual_coverage.hpp"
#include <deque>
#include <memory>
namespace lia {
struct ProjectionScore {
    unsigned variable = 0, related_nodes = 0, stable_transitions = 0;
    uint64_t gap_weight = 0, score = 0;
    bool truncated = false;
};
struct ProjectionSelection {
    std::vector<unsigned> variables;
    std::vector<ProjectionScore> assessed;
    uint64_t missing_bits = 0, unobserved_nodes = 0;
    unsigned dependency_operations = 0;
    bool complete = true;
    std::string reason = "selected";
    double seconds = 0;
    std::string json(const Formula &) const;
};
class FeedbackProjection {
    Formula &f;
    struct Dependencies {
        std::vector<int> nodes;
        bool built = false, truncated = false;
    };
    std::vector<Dependencies> related;
    std::vector<unsigned> order;
    size_t cursor = 0;
    unsigned operations = 0;
    std::mt19937_64 rng;

  public:
    static constexpr unsigned variables_per_action = 32, node_cap = 128, per_variable_cap = 512,
                              total_cap = 200000;
    FeedbackProjection(Formula &, unsigned seed);
    ProjectionSelection select(const Coverage &, const std::deque<Assignment> &, Budget &);
};
struct ProjectionResult {
    GlobalResult query;
    std::vector<unsigned> variables;
    std::vector<std::vector<Z>> forbidden;
    std::string condition_json(const Formula &) const;
};
class ProjectionGuidedExplorer {
    Formula &f;
    z3::solver solver;

  public:
    std::function<std::optional<GlobalResult>(unsigned)> check_override;
    ProjectionGuidedExplorer(Formula &, unsigned seed);
    ProjectionResult query(const std::vector<unsigned> &, const std::deque<Assignment> &, Budget &, Stats &);
};
struct ProjectionTrigger {
    bool first = true;
    unsigned plain_opportunities = 4, consecutive_zero = 0;
    std::string observe(const std::optional<ResidualCandidate> &);
    void attempted() {
        first = false;
        plain_opportunities = 0;
    }
};
// Tie retains ordinary candidate. No formal state is mutated here.
bool projection_better(const ResidualCandidate &, const ResidualCandidate &);
struct ProjectionExtraBudget {
    double quota = 0, spent = 0;
    explicit ProjectionExtraBudget(double session_seconds) : quota(session_seconds * .1) {}
    double allowance(double remaining) const;
    void charge(double s) { spent += s; }
};
} // namespace lia
