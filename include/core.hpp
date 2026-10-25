#pragma once
#include "math.hpp"
#include <chrono>
#include <csignal>
#include <functional>
#include <map>
#include <set>
#include <sstream>
#include <unordered_map>
#include <z3++.h>

namespace lia {
using Clock = std::chrono::steady_clock;
using Assignment = std::vector<Z>;
extern volatile std::sig_atomic_t interrupted;
std::string quote(const std::string &s);
struct Budget {
    Clock::time_point start = Clock::now();
    double seconds = 900;
    const Budget *parent = nullptr;
    const char *label = "global";
    double elapsed() const { return std::chrono::duration<double>(Clock::now() - start).count(); }
    double total_elapsed() const { return parent ? parent->total_elapsed() : elapsed(); }
    double left() const {
        double remaining = std::max(0.0, seconds - elapsed());
        return parent ? std::min(remaining, parent->left()) : remaining;
    }
    bool global_done() const { return parent ? parent->global_done() : done(); }
    bool done() const { return interrupted || left() <= 0; }
    unsigned milliseconds(unsigned cap) const {
        return std::max(1U, std::min(cap, static_cast<unsigned>(std::min(left() * 1000, 4294967294.0))));
    }
};
struct Stats {
    std::map<std::string, double> times;
    // Parent action costs are deliberately separate from nested stage costs.
    std::map<std::string, double> action_times;
    double refinement_seconds = 0;
    std::map<std::string, unsigned long> counts;
    std::string json() const;
};
struct Timer {
    Stats &stats;
    std::string name;
    Clock::time_point t = Clock::now();
    Timer(Stats &s, std::string n) : stats(s), name(std::move(n)) {}
    ~Timer() { stats.times[name] += std::chrono::duration<double>(Clock::now() - t).count(); }
};
struct Node {
    z3::expr expr;
    std::vector<int> children;
    std::vector<std::pair<int, int>> parents;
    int variable = -1;
    bool boolean;
    Z numeral;
    explicit Node(z3::expr e) : expr(e), boolean(e.is_bool()) {}
};
struct Observation {
    std::vector<Z> values;
    std::vector<unsigned char> visited;
    std::vector<int> events;
};
struct State {
    uint64_t zero = 0, one = 0;
};
struct Gain {
    unsigned long covered = 0, observed = 0;
};
struct AcceptResult {
    bool emitted = false;
    uint64_t new_coverage = 0, new_observations = 0;
};
struct ProgressFeedback {
    uint64_t coverage = 0, observations = 0;
    void add(const AcceptResult &r) {
        if (r.emitted) {
            coverage += r.new_coverage;
            observations += r.new_observations;
        }
    }
    unsigned next_stalled(unsigned previous) const { return coverage ? 0 : previous + 1; }
};
enum class SolveStatus {
    Sat,
    Unsat,
    Timeout,
    Cancelled,
    ResourceExhausted,
    Unknown,
    NoTarget,
    BudgetExhausted
};
std::string status_name(SolveStatus status);
struct QueryExitEvidence {
    std::string raw_reason;
    unsigned actual_timeout_ms = 0;
    double check_seconds = 0;
    bool external_interrupt = false, session_deadline_reached = false, action_deadline_reached = false,
         resource_limit_enabled = false;
};
struct QueryExitDecision {
    SolveStatus code = SolveStatus::Unknown;
    std::string classification = "unknown";
    bool inferred = false, allow_upgrade = false, stop = false;
};
QueryExitDecision interpret_query_exit(const std::string &raw_status, const QueryExitEvidence &);
struct QueryGrant {
    unsigned requested_ms = 0, granted_ms = 0;
    std::string clip_reason = "none";
    bool full() const { return requested_ms && requested_ms == granted_ms; }
};
QueryGrant grant_query(unsigned requested, double session_left, double action_left, double reserve = 0);
struct BroadBudgetPool {
    double quota = 0, spent = 0;
    explicit BroadBudgetPool(double q) : quota(q) {}
    double available(double session_left) const;
    void charge(double seconds) { spent += seconds; }
};
struct SolveBudgetState {
    static constexpr unsigned levels[4] = {50, 200, 1000, 2000};
    bool fixed_levels = false;
    unsigned retry_level = 0;
    unsigned requested_timeout_ms = 50, max_timeout_ms = 2000;
    unsigned consecutive_timeouts = 0, consecutive_unknowns = 0, consecutive_fast_successes = 0;
    double cumulative_seconds = 0, cooldown_until = 0;
    void observe(SolveStatus status, double elapsed, double now, unsigned actual_ms);
    void observe(const QueryExitDecision &, double elapsed, const QueryGrant &);
};
class Formula {
  public:
    // Every AST-owning member must be destroyed BEFORE its Z3 context.
    z3::context ctx;

  private:
    std::unordered_map<unsigned, int> index;
    std::unordered_map<int, z3::expr> reach_cache;
    int add(z3::expr e);

  public:
    z3::expr_vector assertions;
    z3::expr root;
    std::vector<Node> nodes;
    std::vector<int> variables;
    bool metric_compatible = true, legacy_compatible = true;
    std::string metric_reason, legacy_reason;
    explicit Formula(const std::string &file);
    Observation evaluate(const Assignment &m, bool trace = false) const;
    Assignment complete(z3::model model) const;
    bool validate(const Assignment &m) const;
    z3::model as_model(const Assignment &m) const;
    z3::expr value(int var, const Z &v) const;
    std::string key(const Assignment &m) const;
    std::string model_json(const Assignment &m) const;
    z3::expr reach(int node);
    unsigned long total_bits() const;
};
class Coverage {
    const Formula &f;

  public:
    std::vector<State> states;
    explicit Coverage(const Formula &formula) : f(formula), states(formula.nodes.size()) {}
    Gain gain(const Observation &o) const;
    Gain accept(const Observation &o);
    unsigned long covered() const;
    std::string json(const Observation *o = nullptr) const;
};
struct Target {
    int node;
    unsigned bit;
    bool desired;
    auto operator<=>(const Target &) const = default;
    std::string key() const {
        return std::to_string(node) + ":" + std::to_string(bit) + ":" + std::to_string(desired);
    }
};
std::vector<Target> active_targets(const Formula &f, const Coverage &cov, std::mt19937_64 &rng, size_t cap);
struct Affine {
    std::map<int, Z> a;
    Z c = 0;
    void add(const Affine &other, const Z &scale = 1);
    Z at(const Assignment &m) const;
    Z dot(const Assignment &d) const;
};
struct Region {
    Formula &f;
    const Assignment &model;
    const Observation &values;
    std::vector<Affine> eq, le; // row == 0, row <= 0
    std::map<int, bool> boolean;
    std::map<int, Affine> affine_cache;
    std::map<int, bool> required;
    bool supported = true;
    std::string reason;
    Budget &budget;
    Region(Formula &f, const Assignment &m, const Observation &o, Budget &b);
    Affine linear(int n, bool permit_conditions = true);
    void require(int n, bool truth);
    bool contains(const Assignment &m) const;
    bool kernel(const Assignment &d) const;
    Interval interval(const Assignment &m, const Assignment &d) const;
    z3::expr expression() const;
    std::string signature() const;
};
std::vector<Assignment> directions(const Region &r, std::mt19937_64 &rng, Stats &stats, size_t cap = 16);
struct ConflictCache {
    std::vector<std::set<int>> cores;
    bool blocked(const std::set<int> &pack) const;
    void insert(std::set<int> core);
};
struct GlobalResult {
    std::optional<Assignment> model;
    std::string status, detail;
    std::vector<int> core;
    SolveStatus code = SolveStatus::Unknown;
    unsigned actual_timeout_ms = 0, resource_limit = 0;
    unsigned requested_timeout_ms = 0, retry_level_before = 0, retry_level_after = 0;
    std::string raw_status = "not_checked", budget_clip_reason = "none";
    QueryExitEvidence exit_evidence;
    QueryExitDecision exit_decision;
    double check_seconds = 0, constraint_seconds = 0, completion_seconds = 0, validation_seconds = 0;
    double session_remaining_before_check = 0, action_remaining_before_check = 0;
    bool check_started = false;
    std::string evidence_json() const;
    double elapsed_seconds = 0;
    std::vector<Target> attempted;
    std::vector<Target> core_targets;
    std::vector<unsigned> fixed_core;
    bool exclusion_in_core = false, global_target_proof = false;
    // Separate threshold-query evidence; never a fixed-conjunction core.
    unsigned requested_threshold = 0, selected_activations = 0;
    uint64_t coverage_version = 0;
    bool target_encoding_complete = true, threshold_proof = false;
};
struct ThresholdConclusion {
    std::vector<Target> window;
    unsigned threshold;
    uint64_t coverage_version;
};
// One immutable anchor per action. Labels are registered lazily in a single
// Global instance; releasing a variable removes its assumption, not F.
// Dormant fresh implications are unconstrained in subsequent actions.
struct GlobalRestrictions {
    const Assignment anchor;
    std::set<unsigned> free_variables;
    std::vector<Assignment> excluded;
    std::map<unsigned, z3::expr> fixed_labels;
    std::vector<z3::expr> exclusion_labels;
    const void *owner = nullptr;
    explicit GlobalRestrictions(Assignment seed) : anchor(std::move(seed)) {}
};
class Global {
    Formula &f;
    z3::solver solver;
    z3::solver exploration;
    std::map<Target, int> ids;
    std::vector<z3::expr> activations;
    std::map<unsigned, int> activation_ids;
    std::vector<Target> registered_targets;
    size_t registered_quotients = 0;
    std::string last_assumptions;
    int ensure(Target t);
    unsigned seed, resource_limit;
    std::set<std::string> blocked_model_keys;
    void block_model(const Assignment &model, Stats &stats);

  public:
    ConflictCache conflicts;
    std::vector<ThresholdConclusion> threshold_conclusions;
    double registered_definition_seconds = 0;
    std::optional<Assignment> invalid_exploration_model;
    std::string exploration_query_smt2() { return exploration.to_smt2(); }
    SolveBudgetState target_budget, exploration_budget;
    unsigned pack_size = 4;
    bool use_cache = true;
    // Regression seam: production leaves this empty and always calls Z3.
    std::function<std::optional<GlobalResult>(unsigned)> exploration_check;
    std::function<GlobalResult(const std::vector<Target> &, Budget &, Stats &)> repair_check;
    std::function<void(const std::string &)> trace;
    explicit Global(Formula &f, unsigned seed, unsigned resource_limit = 0);
    GlobalResult query(const std::vector<Target> &targets, Budget &budget, Stats &stats,
                       unsigned allocation_ms = 0xffffffffU, bool adapt_pack = true,
                       GlobalRestrictions *restrictions = nullptr);
    GlobalResult query_at_least(const std::vector<Target> &window, unsigned threshold,
                                uint64_t coverage_version, Budget &budget, Stats &stats);
    GlobalResult repair(const std::vector<Target> &targets, Budget &budget, Stats &stats,
                        const std::function<Gain(const Assignment &)> &score,
                        const std::function<void(const GlobalResult &)> &feedback, unsigned max_calls = 4,
                        unsigned max_expansions = 2);
    GlobalResult explore(const Assignment &current, Budget &budget, Stats &stats,
                         unsigned allocation_ms = 0xffffffffU, bool bypass_soft_cooldown = false);
    size_t blocked_models() const { return blocked_model_keys.size(); }
    bool unreachable(Target target) const {
        auto it = ids.find(target);
        return it != ids.end() && conflicts.blocked({it->second});
    }
    void rebuild_exploration(Stats &stats);
    void validate_conflicts(const Assignment &model);
    std::string query_smt2() {
        return solver.to_smt2() + "\n(check-sat-assuming (" + last_assumptions + "))\n";
    }
};
} // namespace lia
