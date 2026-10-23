#include "scheduler.hpp"
#include <algorithm>

namespace lia {
std::string action_name(ActionKind kind) {
    static const char *names[] = {"local_safe", "local_line", "target", "explore"};
    return names[static_cast<unsigned>(kind)];
}
std::optional<ActionSelection> Scheduler::choose(double now, double remaining,
    const std::array<bool, 4> &enabled, unsigned target_ms, unsigned exploration_ms) {
    if (remaining <= 0) return {};
    std::vector<unsigned> available;
    for (unsigned i = 0; i < 4; ++i)
        if (enabled[i] && now >= meters[i].cooldown_until) available.push_back(i);
    if (available.empty()) return {};
    auto slice = [&](unsigned i) {
        // Permit the solver's own retry state to grow; preparation shares this budget.
        double requested = i == 2 ? target_ms * .001 : i == 3 ? exploration_ms * .001 : .15;
        return std::min(remaining, std::max(.05, requested * 1.25 + .025));
    };
    unsigned best = *std::max_element(available.begin(), available.end(), [&](unsigned a, unsigned b) {
        return meters[a].rate() < meters[b].rate();
    });
    unsigned oldest = *std::min_element(available.begin(), available.end(), [&](unsigned a, unsigned b) {
        return meters[a].last_attempt < meters[b].last_attempt;
    });
    const double credit = .25 + probe_fraction * total_seconds - probe_seconds;
    bool no_exploit = meters[best].rate() == 0;
    bool probe = oldest != best && credit >= slice(oldest) &&
                 (meters[oldest].calls == 0 || now - meters[oldest].last_attempt >= .25);
    // When every measured action has zero reward, bounded attempts remain necessary;
    // this is search under uncertainty, not a declaration of optimal coverage.
    unsigned selected = no_exploit ? oldest : probe ? oldest : best;
    return ActionSelection{static_cast<ActionKind>(selected), slice(selected), probe && !no_exploit};
}
void Scheduler::feedback(const ActionSelection &selection, const ActionResult &result, double now) {
    auto &m = meters[static_cast<unsigned>(result.kind)];
    const double cost = std::max(1e-6, result.elapsed_seconds);
    // Smooth totals before division, never instantaneous gain/time ratios.
    m.smooth_gain = .8 * m.smooth_gain + .2 * result.emitted_new_coverage;
    m.smooth_cost = .8 * m.smooth_cost + .2 * cost;
    m.smooth_samples = .8 * m.smooth_samples + .2 * result.emitted_samples;
    m.total_seconds += cost;
    m.last_attempt = now;
    ++m.calls;
    m.gain += result.emitted_new_coverage;
    m.samples += result.emitted_samples;
    total_seconds += cost;
    if (selection.probe) probe_seconds += cost;
    if (result.emitted_new_coverage) {
        m.failures = 0;
        m.cooldown_until = now;
    } else if (++m.failures >= 3) {
        m.cooldown_until = now + std::min(2.0, .02 * (m.failures - 2));
    }
}
void Scheduler::refresh(double now) {
    for (auto &m : meters)
        m.cooldown_until = std::min(m.cooldown_until, now + .05);
}
} // namespace lia
