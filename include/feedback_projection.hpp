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
struct ProjectionOptions {
    bool persistent_blocks = false; // Opt-in: frozen round10 behavior remains available.
    unsigned timeout_ms = 50;
};
struct ProjectionResult {
    GlobalResult query;
    std::vector<unsigned> variables;
    std::vector<std::vector<Z>> forbidden;
    unsigned blocks_before = 0, blocks_after = 0;
    double block_seconds = 0;
    bool block_added = false, internal_duplicate = false;
    std::string stop_reason = "none";
    std::string condition_json(const Formula &) const;
};
class ProjectionGuidedExplorer {
    Formula &f;
    z3::solver solver;
    ProjectionOptions options;
    std::set<std::string> blocked_keys;
    bool duplicate_stop = false;

  public:
    std::function<std::optional<GlobalResult>(unsigned)> check_override;
    static constexpr unsigned block_limit = 256;
    ProjectionGuidedExplorer(Formula &, unsigned seed, ProjectionOptions = {});
    unsigned blocked_models() const { return blocked_keys.size(); }
    bool available() const {
        return !options.persistent_blocks || (blocked_keys.size() < block_limit && !duplicate_stop);
    }
    std::string stop_reason() const {
        return duplicate_stop ? "projection_duplicate_guard" : available() ? "none" : "projection_block_cap";
    }
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
    double quota = 0, spent = 0, stage_cap = .05, minimum_stage = .02;
    explicit ProjectionExtraBudget(double session_seconds, double cap = .05, double minimum = .02)
        : quota(session_seconds * .1), stage_cap(cap), minimum_stage(minimum) {}
    double allowance(double remaining) const;
    void charge(double s) { spent += s; }
};
} // namespace lia
