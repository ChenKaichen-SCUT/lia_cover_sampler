#include "core.hpp"
#include <algorithm>
#include <cmath>
#include <iomanip>

namespace lia {
std::string status_name(SolveStatus status) {
    switch (status) {
    case SolveStatus::Sat:
        return "sat";
    case SolveStatus::Unsat:
        return "unsat";
    case SolveStatus::Timeout:
        return "timeout";
    case SolveStatus::Cancelled:
        return "cancelled";
    case SolveStatus::ResourceExhausted:
        return "resource_exhausted";
    case SolveStatus::NoTarget:
        return "no_target";
    case SolveStatus::BudgetExhausted:
        return "budget_exhausted";
    default:
        return "unknown";
    }
}
void SolveBudgetState::observe(SolveStatus status, double elapsed, double now, unsigned actual_ms) {
    if (fixed_levels) {
        QueryExitDecision d;
        d.code = status;
        d.allow_upgrade = status == SolveStatus::Timeout;
        observe(d, elapsed, QueryGrant{requested_timeout_ms, actual_ms, "legacy_caller"});
        return;
    }
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

QueryExitDecision interpret_query_exit(const std::string &raw, const QueryExitEvidence &e) {
    if (raw == "sat")
        return {SolveStatus::Sat, "sat", false, false, false};
    if (raw == "unsat")
        return {SolveStatus::Unsat, "unsat", false, false, false};
    if (e.external_interrupt)
        return {SolveStatus::Cancelled, "external_interrupt", false, false, true};
    if (e.session_deadline_reached)
        return {SolveStatus::BudgetExhausted, "session_deadline", false, false, true};
    if (e.raw_reason.find("resource") != std::string::npos ||
        e.raw_reason.find("rlimit") != std::string::npos || e.raw_reason.find("memory") != std::string::npos)
        return {SolveStatus::ResourceExhausted, "resource_exhausted", false, false, false};
    if (e.raw_reason.find("timeout") != std::string::npos)
        return {SolveStatus::Timeout, "explicit_local_timeout", false, true, false};
    if (e.actual_timeout_ms && e.check_seconds * 1000 >= .8 * e.actual_timeout_ms &&
        (e.raw_reason == "canceled" || e.raw_reason == "cancelled"))
        return {SolveStatus::Timeout, "suspected_local_timeout", true, true, false};
    return {SolveStatus::Unknown, "unresolved_unknown", false, false, false};
}
QueryGrant grant_query(unsigned requested, double session_left, double action_left, double reserve) {
    QueryGrant g;
    g.requested_ms = requested;
    double left = std::max(0., std::min(session_left, action_left) - reserve);
    g.granted_ms = std::min(requested, static_cast<unsigned>(std::min(4294967294., std::floor(left * 1000))));
    if (!g.full())
        g.clip_reason = session_left <= action_left ? "session_remaining_after_reserve"
                                                    : "action_remaining_after_reserve";
    return g;
}
double BroadBudgetPool::available(double session_left) const {
    return std::max(0., std::min({2.075, quota - spent, session_left - .025}));
}
void SolveBudgetState::observe(const QueryExitDecision &d, double elapsed, const QueryGrant &g) {
    cumulative_seconds += elapsed;
    if (d.allow_upgrade && g.full() && retry_level < 3)
        ++retry_level;
    requested_timeout_ms = std::min(max_timeout_ms, levels[retry_level]);
    if (d.code == SolveStatus::Timeout)
        ++consecutive_timeouts;
    if (d.code == SolveStatus::Unknown)
        ++consecutive_unknowns;
}
std::string GlobalResult::evidence_json() const {
    std::ostringstream o;
    o << std::setprecision(17) << "{\"raw_status\":" << quote(raw_status)
      << ",\"raw_reason\":" << quote(detail)
      << ",\"control_classification\":" << quote(exit_decision.classification)
      << ",\"inferred\":" << (exit_decision.inferred ? "true" : "false")
      << ",\"allow_upgrade\":" << (exit_decision.allow_upgrade ? "true" : "false")
      << ",\"stop\":" << (exit_decision.stop ? "true" : "false")
      << ",\"check_started\":" << (check_started ? "true" : "false")
      << ",\"requested_timeout_ms\":" << requested_timeout_ms
      << ",\"granted_timeout_ms\":" << actual_timeout_ms << ",\"actual_check_seconds\":" << check_seconds
      << ",\"constraint_seconds\":" << constraint_seconds << ",\"completion_seconds\":" << completion_seconds
      << ",\"validation_seconds\":" << validation_seconds
      << ",\"budget_clip_reason\":" << quote(budget_clip_reason)
      << ",\"retry_level_before\":" << retry_level_before << ",\"retry_level_after\":" << retry_level_after
      << ",\"session_remaining_before_check\":" << session_remaining_before_check
      << ",\"action_remaining_before_check\":" << action_remaining_before_check
      << ",\"external_interrupt\":" << (exit_evidence.external_interrupt ? "true" : "false")
      << ",\"session_deadline_reached\":" << (exit_evidence.session_deadline_reached ? "true" : "false")
      << ",\"action_deadline_reached\":" << (exit_evidence.action_deadline_reached ? "true" : "false")
      << ",\"resource_limit_enabled\":" << (exit_evidence.resource_limit_enabled ? "true" : "false") << '}';
    return o.str();
}
} // namespace lia
