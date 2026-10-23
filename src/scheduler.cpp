#include "scheduler.hpp"
#include <algorithm>

namespace lia {
std::string action_name(ActionKind kind) {
    static const char *names[] = {"local_safe", "local_line", "target", "explore"};
    return names[static_cast<unsigned>(kind)];
}
std::optional<ActionSelection> Scheduler::choose(double now, double remaining,
    const std::array<bool, 4> &enabled, unsigned target_ms, unsigned exploration_ms) {
    no_action_reason.clear();
    if (remaining <= 0) { no_action_reason="budget_exhausted"; return {}; }
    std::vector<unsigned> available;
    std::vector<unsigned> cooled;
    for (unsigned i = 0; i < 4; ++i)
        if (enabled[i] && now >= meters[i].hard_unavailable_until) {
            if (now >= meters[i].cooldown_until) available.push_back(i);
            else cooled.push_back(i);
        }
    bool bypass = available.empty() && !cooled.empty();
    if (bypass) {
        ++all_actions_soft_cooled;
        if (work_conserving) available=cooled;
        else { no_action_reason="legacy_soft_cooldown"; return {}; }
    }
    if (available.empty()) {
        ++hard_unavailable_events; no_action_reason="all_actions_hard_unavailable"; return {};
    }
    auto slice = [&](unsigned i) {
        // Permit the solver's own retry state to grow; preparation shares this budget.
        double requested = i == 2 ? target_ms * .001 : i == 3 ? exploration_ms * .001 : .15;
        return std::min(remaining, std::max(.05, requested * 1.25 + .025));
    };
    unsigned best = *std::max_element(available.begin(), available.end(), [&](unsigned a, unsigned b) {
        auto priority=[&](unsigned i) {
            return meters[i].rate()/(1+std::max(0.0,meters[i].cooldown_until-now));
        };
        return priority(a) < priority(b);
    });
    unsigned oldest = *std::min_element(available.begin(), available.end(), [&](unsigned a, unsigned b) {
        // Obtain a diverse global seed before spending early output slots on small
        // local moves. This is a cold-start tie break, not a periodic call quota.
        static const unsigned priority[] = {2,3,1,0};
        if (meters[a].last_attempt==meters[b].last_attempt) return priority[a]<priority[b];
        return meters[a].last_attempt < meters[b].last_attempt;
    });
    const double credit = .25 + probe_fraction * total_seconds - probe_seconds;
    bool no_exploit = meters[best].rate() == 0;
    bool probe = oldest != best && credit >= slice(oldest) &&
                 (meters[oldest].calls == 0 || now - meters[oldest].last_attempt >= .25);
    // When every measured action has zero reward, bounded attempts remain necessary;
    // this is search under uncertainty, not a declaration of optimal coverage.
    unsigned selected = no_exploit ? oldest : probe ? oldest : best;
    if (bypass) ++soft_cooldown_bypasses;
    if (meters[selected].failures>=3) ++actions_selected_after_cooldown;
    return ActionSelection{static_cast<ActionKind>(selected), slice(selected), probe && !no_exploit,
                           bypass, bypass ? "all_soft_cooled_bounded_probe" : "reward_cost"};
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
        // Distinct valid fillers are search progress, although they are never
        // coverage progress. Do not throttle their throughput like failed calls.
        m.cooldown_until = now + std::min(result.emitted_samples ? .2 : 2.0, .02 * (m.failures - 2));
    }
}
void Scheduler::refresh(double now) {
    for (auto &m : meters)
        m.cooldown_until = std::min(m.cooldown_until, now + .05);
}
} // namespace lia
