#pragma once
#include "core.hpp"
#include <array>

namespace lia {
enum class ActionKind { LocalSafe, LocalLine, TargetSolve, Explore };
std::string action_name(ActionKind kind);
struct ActionResult {
    ActionKind kind = ActionKind::LocalSafe;
    double elapsed_seconds = 0;
    uint64_t emitted_new_coverage = 0, emitted_new_observations = 0, emitted_samples = 0;
    uint64_t generated_candidates = 0, unique_valid_candidates = 0;
    std::string status = "no_gain";
};
struct ActionMeter {
    double smooth_gain = 0, smooth_cost = 0, smooth_samples = 0;
    double total_seconds = 0, last_attempt = -1, cooldown_until = 0;
    uint64_t calls = 0, gain = 0, samples = 0;
    unsigned failures = 0;
    double rate() const { return smooth_cost > 0 ? smooth_gain / smooth_cost : 0; }
    double quality() const { return smooth_samples > 0 ? smooth_gain / smooth_samples : 0; }
};
struct ActionSelection {
    ActionKind kind;
    double seconds;
    bool probe = false;
};
class Scheduler {
  public:
    std::array<ActionMeter, 4> meters;
    double total_seconds = 0, probe_seconds = 0, probe_fraction = .15;
    std::optional<ActionSelection> choose(double now, double remaining,
        const std::array<bool, 4> &enabled, unsigned target_ms, unsigned exploration_ms);
    void feedback(const ActionSelection &selection, const ActionResult &result, double now);
    void refresh(double now);
};
} // namespace lia
