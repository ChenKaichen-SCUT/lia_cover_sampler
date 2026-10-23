#include "core.hpp"
#include <algorithm>

namespace lia {
std::string status_name(SolveStatus status) {
    switch (status) {
    case SolveStatus::Sat: return "sat";
    case SolveStatus::Unsat: return "unsat";
    case SolveStatus::Timeout: return "timeout";
    case SolveStatus::Cancelled: return "cancelled";
    case SolveStatus::ResourceExhausted: return "resource_exhausted";
    case SolveStatus::NoTarget: return "no_target";
    case SolveStatus::BudgetExhausted: return "budget_exhausted";
    default: return "unknown";
    }
}
void SolveBudgetState::observe(SolveStatus status, double elapsed, double now, unsigned actual_ms) {
    cumulative_seconds += elapsed;
    if (status == SolveStatus::Timeout) {
        ++consecutive_timeouts;
        consecutive_fast_successes = 0;
        requested_timeout_ms = std::min(max_timeout_ms, requested_timeout_ms * 2);
        if (consecutive_timeouts >= 4)
            cooldown_until = now + std::min(1.0, .05 * consecutive_timeouts);
    } else if (status == SolveStatus::Unknown) {
        ++consecutive_unknowns;
        consecutive_fast_successes = 0;
        cooldown_until = now + .05 * std::min(20U, consecutive_unknowns);
    } else if (status == SolveStatus::ResourceExhausted) {
        // A larger timeout does not replenish a Z3 resource limit.
        consecutive_fast_successes = 0;
        cooldown_until = now + 1;
    } else if (status == SolveStatus::Sat) {
        consecutive_timeouts = consecutive_unknowns = 0;
        cooldown_until = 0;
        consecutive_fast_successes = elapsed * 1000 < actual_ms * .25 ? consecutive_fast_successes + 1 : 0;
        if (consecutive_fast_successes >= 8) {
            requested_timeout_ms = std::max(50U, requested_timeout_ms * 3 / 4);
            consecutive_fast_successes = 0;
        }
    }
}
} // namespace lia
