#pragma once
#include "core.hpp"

namespace lia {
struct ResidualWindow {
    uint64_t coverage_version = 0;
    std::vector<Target> targets;
    unsigned probes = 0;
    bool complete = true;
};
class ResidualTargetWindow {
    Formula &f;
    std::mt19937_64 rng;
    size_t cursor = 0;
  public:
    static constexpr unsigned capacity = 8;
    ResidualTargetWindow(Formula &formula, unsigned seed) : f(formula), rng(seed) {}
    ResidualWindow select(const Coverage &coverage, uint64_t version, const Global &global, Budget &budget);
    static bool missing_opposite(const Coverage &coverage, Target t);
};
struct ResidualCandidate {
    Assignment model;
    Observation observation;
    Gain gain;
    uint64_t coverage_version = 0;
    unsigned window_hits = 0, first_observations = 0, query = 0;
    std::string origin;
    double checkpoint_completed = -1;
};
// Owns only formally committed state. Query code sees a const Coverage reference.
class OnlineCoverage {
    Formula &f;
    Coverage coverage;
    std::set<std::string> emitted;
    uint64_t version_ = 0, outputs_ = 0;
    unsigned long initial_covered_ = 0;
    bool initialized = false;
  public:
    explicit OnlineCoverage(Formula &formula) : f(formula), coverage(formula) {}
    bool restore(const std::vector<Assignment> &prefix, const Assignment &seed);
    const Coverage &state() const { return coverage; }
    const std::set<std::string> &keys() const { return emitted; }
    uint64_t version() const { return version_; }
    uint64_t outputs() const { return outputs_; }
    unsigned long initial_covered() const { return initial_covered_; }
    bool duplicate(const Assignment &model) const;
    std::optional<ResidualCandidate> prepare(const Assignment &model, const ResidualWindow &window,
                                             const std::string &origin, unsigned query) const;
    // finished is the actual model-flush timestamp in the same clock as deadline.
    // No state change on a late, duplicate, stale or failed transaction.
    bool commit(const ResidualCandidate &candidate, double finished, double deadline);
};
struct ResidualOptions {
    unsigned target_calls = 3;
    double reserve_fraction = .2;
};
struct ResidualRound {
    ResidualWindow window;
    std::optional<ResidualCandidate> best;
    std::vector<std::string> queries;
    std::vector<std::string> assessments;
    std::string terminal = "no_target";
    unsigned target_calls = 0, exploration_calls = 0, duplicates = 0, late = 0;
    double reserve_seconds = 0, full_seconds = 0;
};
class ResidualGenerator {
    Formula &f;
  public:
    Global global;
    ResidualTargetWindow windows;
    ResidualOptions options;
    // Test seam: production leaves it empty. Queries otherwise use Global.
    std::function<std::optional<GlobalResult>(unsigned, const ResidualWindow &, unsigned)> threshold_override;
    std::function<ResidualWindow(const OnlineCoverage &)> window_override;
    std::function<void(const std::string &)> boundary;
    ResidualGenerator(Formula &formula, unsigned seed) : f(formula), global(formula,seed), windows(formula,seed) {}
    ResidualRound generate(bool cardinality, const OnlineCoverage &online, const Assignment &current,
                           Budget &round, Stats &stats,
                           const std::function<double(const ResidualCandidate &)> &checkpoint);
};
std::string residual_targets_json(const std::vector<Target> &targets);
std::vector<Assignment> read_residual_values(const Formula &f, const std::string &path);
} // namespace lia
