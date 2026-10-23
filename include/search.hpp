#pragma once
#include "core.hpp"
#include "line.hpp"
#include <unordered_set>

namespace lia {
struct SearchOptions {
    bool quotas = true, fair = true, matching = true, polish = true;
    bool line = false, repair = false, random = false;
    bool streaming = true, directional_targets = true;
    unsigned direction_cap = 20, candidate_cap = 24, attempt_cap = 256, polish_steps = 6;
    double polish_seconds = .06;
    LineLimits line_limits;
};
struct DirectionSystem {
    size_t width = 0;
    std::vector<Affine> equations;
    std::set<int> booleans;
    std::string scope = "safe_region";
    bool kernel(const Assignment &d) const;
    std::string signature() const;
};
struct Direction {
    Assignment delta;
    std::string source, scope, equation_signature;
};
std::vector<Direction> quota_directions(const DirectionSystem &system, const std::vector<Assignment> &history,
    const Assignment &base, Budget &budget, std::mt19937_64 &rng, Stats &stats, unsigned cap, bool quotas = true,
    const std::function<bool(const Direction &)> &on_direction = {});
struct TargetRecord {
    bool witnessed = false, globally_unreachable = false;
    unsigned timeouts = 0;
    double solve_seconds = 0, cooldown_until = 0;
    uint64_t incidental_gain = 0;
};
class TargetManager {
    Formula &f;
    size_t cursor = 0;
    std::vector<std::optional<std::vector<int>>> dependencies;
    bool dependency(int node, Budget &budget);
  public:
    std::map<Target, TargetRecord> records;
    std::vector<std::vector<int>> variable_observers;
    std::function<bool(Target)> globally_unreachable;
    std::string formula_metric_identity;
    explicit TargetManager(Formula &formula);
    bool eligible(Target target, const Coverage &coverage, double now);
    std::vector<Target> select(const Coverage &coverage, std::mt19937_64 &rng, size_t cap,
                              Budget &budget, const Assignment *direction = nullptr);
    std::vector<Target> related(const Coverage &coverage, const Assignment &direction,
        std::mt19937_64 &rng, size_t cap, Budget &budget, Stats &stats,
        const std::function<std::optional<Z>(int)> &alpha);
    void feedback(const std::vector<Target> &targets, const GlobalResult &result,
                  uint64_t gain, double now);
    size_t rotation_cursor() const { return cursor; }
};
struct CandidateLimits {
    unsigned directions = 0xffffffffU, candidates = 0xffffffffU, attempts = 0xffffffffU;
};
struct CandidateBatch {
    std::optional<Assignment> model;
    Gain gain;
    std::string status = "no_candidate", direction_source;
    uint64_t attempts = 0, scored = 0, duplicates = 0;
    std::vector<unsigned> direction_attempts;
    double first_valid_seconds = -1, first_positive_seconds = -1;
};
struct PolishResult {
    Assignment model;
    Gain before, after;
    uint64_t retained = 0, added = 0, lost = 0, moves = 0;
    double seconds = 0;
    std::vector<std::string> modules;
};
std::vector<uint64_t> new_coverage_bits(const Formula &f, const Coverage &coverage, const Observation &model);
std::string assignment_id(const Formula &f, const Assignment &model);
class Search {
    Formula &f;
    std::set<std::string> action_scored;
    std::unique_ptr<AffineSyntax> syntax;
    std::optional<StructuralEquations> structural;
  public:
    TargetManager targets;
    SearchOptions options;
    std::string last_failure;
    Assignment last_candidate;
    bool line_supported = true;
    // Injectable deadline/failure boundary used by deterministic anytime regression tests.
    std::function<void(unsigned)> direction_boundary;
    explicit Search(Formula &formula) : f(formula), targets(formula) {}
    void begin_action() { action_scored.clear(); }
    CandidateBatch safe(const Assignment &seed, const Coverage &coverage, Budget &budget,
        std::mt19937_64 &rng, Stats &stats, const std::vector<Assignment> &history,
        const std::function<bool(const std::string &)> &already_emitted, CandidateLimits limits = {});
    CandidateBatch line(const Assignment &seed, const Coverage &coverage, Budget &budget,
        std::mt19937_64 &rng, Stats &stats, const std::vector<Assignment> &history,
        const std::function<bool(const std::string &)> &already_emitted, CandidateLimits limits = {});
    CandidateBatch generate(bool line_mode, const Assignment &seed, const Coverage &coverage, Budget &budget,
        std::mt19937_64 &rng, Stats &stats, const std::vector<Assignment> &history,
        const std::function<bool(const std::string &)> &already_emitted, CandidateLimits limits);
    CandidateBatch safe_eager(const Assignment &seed, const Coverage &coverage, Budget &budget,
        std::mt19937_64 &rng, Stats &stats, const std::vector<Assignment> &history,
        const std::function<bool(const std::string &)> &already_emitted, CandidateLimits limits);
    CandidateBatch line_eager(const Assignment &seed, const Coverage &coverage, Budget &budget,
        std::mt19937_64 &rng, Stats &stats, const std::vector<Assignment> &history,
        const std::function<bool(const std::string &)> &already_emitted, CandidateLimits limits);
    PolishResult polish_candidate(const Assignment &seed, const Coverage &committed,
        Budget &budget, std::mt19937_64 &rng, Stats &stats,
        const std::function<bool(const std::string &)> &already_emitted);
};
} // namespace lia
