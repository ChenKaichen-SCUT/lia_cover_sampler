#include "feedback_projection.hpp"
#include <bit>
#include <iomanip>
#include <numeric>
namespace lia {
FeedbackProjection::FeedbackProjection(Formula &formula, unsigned seed)
    : f(formula), related(f.variables.size()), order(f.variables.size()), rng(seed ^ 0xd1b54a32d192ed03ULL) {
    std::iota(order.begin(), order.end(), 0);
    std::shuffle(order.begin(), order.end(), rng);
}
ProjectionSelection FeedbackProjection::select(const Coverage &coverage,
                                               const std::deque<Assignment> &history, Budget &b) {
    ProjectionSelection out;
    auto start = Clock::now();
    unsigned before = operations;
    auto finish = [&]() {
        out.seconds = std::chrono::duration<double>(Clock::now() - start).count();
        out.dependency_operations = operations - before;
        return out;
    };
    for (unsigned n = 0; n < f.nodes.size(); ++n) {
        if (b.done()) {
            out.complete = false;
            out.reason = "analysis_budget";
            return finish();
        }
        auto s = coverage.states[n];
        uint64_t mask = f.nodes[n].boolean ? 1 : ~uint64_t(0);
        out.missing_bits += std::popcount((s.zero ^ s.one) & mask);
        out.unobserved_nodes += !(s.zero | s.one);
    }
    if (!out.missing_bits || order.empty() || history.empty()) {
        out.reason = "no_gap_or_history";
        return finish();
    }
    for (unsigned i = 0; i < std::min(variables_per_action, unsigned(order.size())) && !b.done(); ++i) {
        unsigned v = order[cursor++ % order.size()];
        auto &cache = related[v];
        if (!cache.built) {
            if (operations >= total_cap) {
                out.complete = false;
                out.reason = "dependency_cap";
                return finish();
            }
            std::vector<int> todo{f.variables[v]};
            std::set<int> seen;
            unsigned local = 0;
            while (!todo.empty() && seen.size() < node_cap && local < per_variable_cap &&
                   operations < total_cap && !b.done()) {
                int n = todo.back();
                todo.pop_back();
                ++local;
                ++operations;
                if (!seen.insert(n).second)
                    continue;
                for (auto [p, child] : f.nodes[n].parents) {
                    (void)child;
                    ++local;
                    ++operations;
                    if (local >= per_variable_cap || operations >= total_cap)
                        break;
                    todo.push_back(p);
                }
            }
            if (b.done()) {
                out.complete = false;
                out.reason = "analysis_budget";
                return finish();
            }
            cache.nodes.assign(seen.begin(), seen.end());
            cache.truncated = !todo.empty() || local >= per_variable_cap;
            cache.built = true;
        }
        ProjectionScore score;
        score.variable = v;
        score.related_nodes = cache.nodes.size();
        score.truncated = cache.truncated;
        for (int n : cache.nodes) {
            auto s = coverage.states[n];
            score.gap_weight += std::popcount((s.zero ^ s.one) & (f.nodes[n].boolean ? 1 : ~uint64_t(0)));
        }
        for (unsigned h = 1; h < history.size(); ++h)
            score.stable_transitions += history[h - 1].at(v) == history[h].at(v);
        score.score = score.gap_weight * (1 + score.stable_transitions);
        out.assessed.push_back(score);
    }
    if (b.done()) {
        out.complete = false;
        out.reason = "analysis_budget";
        return finish();
    }
    std::stable_sort(out.assessed.begin(), out.assessed.end(),
                     [](auto &a, auto &c) { return a.score > c.score; });
    std::vector<unsigned> pool;
    for (auto &s : out.assessed)
        if (s.score && pool.size() < 8)
            pool.push_back(s.variable);
    std::shuffle(pool.begin(), pool.end(), rng);
    if (pool.size() > 3)
        pool.resize(3);
    out.variables = pool;
    if (pool.empty())
        out.reason = "no_scored_variable";
    return finish();
}
std::string ProjectionSelection::json(const Formula &f) const {
    std::ostringstream o;
    o << std::setprecision(17) << "{\"complete\":" << (complete ? "true" : "false")
      << ",\"reason\":" << quote(reason) << ",\"seconds\":" << seconds << ",\"missing_bits\":" << missing_bits
      << ",\"unobserved_nodes\":" << unobserved_nodes
      << ",\"dependency_operations\":" << dependency_operations << ",\"variables\":[";
    for (unsigned i = 0; i < variables.size(); ++i) {
        if (i)
            o << ',';
        o << variables[i];
    }
    o << "],\"assessed\":[";
    for (unsigned i = 0; i < assessed.size(); ++i) {
        if (i)
            o << ',';
        auto &s = assessed[i];
        o << "{\"variable\":" << s.variable
          << ",\"name\":" << quote(f.nodes[f.variables[s.variable]].expr.decl().name().str())
          << ",\"related_nodes\":" << s.related_nodes << ",\"truncated\":" << (s.truncated ? "true" : "false")
          << ",\"gap_weight\":" << s.gap_weight << ",\"stable_transitions\":" << s.stable_transitions
          << ",\"score\":" << s.score << '}';
    }
    return o.str() + "]}";
}
ProjectionGuidedExplorer::ProjectionGuidedExplorer(Formula &formula, unsigned seed, ProjectionOptions policy)
    : f(formula), solver(f.ctx, z3::solver::simple()), options(policy) {
    if (!options.timeout_ms || options.timeout_ms > 200)
        throw std::invalid_argument("projection timeout cap");
    solver.add(f.root);
    solver.set("random_seed", seed);
}
ProjectionResult ProjectionGuidedExplorer::query(const std::vector<unsigned> &vars,
                                                 const std::deque<Assignment> &history, Budget &b,
                                                 Stats &stats) {
    ProjectionResult out;
    out.variables = vars;
    auto &r = out.query;
    out.blocks_before = out.blocks_after = blocked_keys.size();
    auto start = Clock::now();
    if (!available()) {
        out.stop_reason = stop_reason();
        r.code = SolveStatus::NoTarget;
        r.status = out.stop_reason;
        return out;
    }
    if (vars.empty() || vars.size() > 3 || history.empty() || history.size() > 4)
        throw std::invalid_argument("projection bounds");
    std::set<unsigned> unique(vars.begin(), vars.end());
    if (unique.size() != vars.size())
        throw std::invalid_argument("projection variable duplicate");
    if (b.done()) {
        r.code = interrupted ? SolveStatus::Cancelled : SolveStatus::BudgetExhausted;
        r.status = status_name(r.code);
        return out;
    }
    solver.push();
    try {
        auto construction = Clock::now();
        std::set<std::vector<Z>> tuples;
        for (auto &m : history) {
            if (m.size() != f.variables.size())
                throw std::invalid_argument("incomplete history");
            std::vector<Z> tuple;
            for (auto v : vars) {
                if (v >= f.variables.size())
                    throw std::invalid_argument("projection index");
                tuple.push_back(m[v]);
            }
            tuples.insert(tuple);
        }
        for (auto &tuple : tuples) {
            z3::expr_vector diff(f.ctx);
            for (unsigned j = 0; j < vars.size(); ++j) {
                unsigned v = vars[j];
                diff.push_back(f.nodes[f.variables[v]].expr != f.value(v, tuple[j]));
            }
            solver.add(z3::mk_or(diff));
            out.forbidden.push_back(tuple);
        }
        r.constraint_seconds = std::chrono::duration<double>(Clock::now() - construction).count();
        const Budget *root = &b;
        while (root->parent)
            root = root->parent;
        r.action_remaining_before_check = b.left();
        r.session_remaining_before_check = root->left();
        auto g = grant_query(options.timeout_ms, root->left(), b.left());
        r.requested_timeout_ms = options.timeout_ms;
        r.actual_timeout_ms = g.granted_ms;
        r.budget_clip_reason = g.clip_reason;
        if (!g.granted_ms || b.done()) {
            r.code = interrupted ? SolveStatus::Cancelled : SolveStatus::BudgetExhausted;
            r.status = status_name(r.code);
            solver.pop();
            r.elapsed_seconds = std::chrono::duration<double>(Clock::now() - start).count();
            return out;
        }
        solver.set("timeout", g.granted_ms);
        r.check_started = true;
        auto mock = check_override ? check_override(g.granted_ms) : std::optional<GlobalResult>{};
        if (mock) {
            r.model = mock->model;
            r.detail = mock->detail;
            r.raw_status = mock->raw_status;
            r.check_seconds = mock->check_seconds;
        } else {
            auto t = Clock::now();
            auto result = solver.check();
            r.check_seconds = std::chrono::duration<double>(Clock::now() - t).count();
            r.raw_status = result == z3::sat ? "sat" : result == z3::unsat ? "unsat" : "unknown";
            if (result == z3::sat) {
                auto c = Clock::now();
                r.model = f.complete(solver.get_model());
                r.completion_seconds = std::chrono::duration<double>(Clock::now() - c).count();
            } else if (result == z3::unknown)
                r.detail = solver.reason_unknown();
        }
        r.exit_evidence = {r.detail,      g.granted_ms, r.check_seconds, bool(interrupted), root->left() <= 0,
                           b.left() <= 0, false};
        r.exit_decision = interpret_query_exit(r.raw_status, r.exit_evidence);
        r.code = r.exit_decision.code;
        r.status = status_name(r.code);
        if (r.model) {
            auto t = Clock::now();
            if (!f.validate(*r.model))
                throw std::logic_error("invalid projection model");
            for (auto &tuple : out.forbidden) {
                bool differs = false;
                for (unsigned j = 0; j < vars.size(); ++j)
                    differs |= r.model->at(vars[j]) != tuple[j];
                if (!differs)
                    throw std::logic_error("projection condition not met");
            }
            r.validation_seconds = std::chrono::duration<double>(Clock::now() - t).count();
        }
        solver.pop();
    } catch (...) {
        solver.pop();
        throw;
    }
    // Scope has already been popped. Persist only exact complete assignments,
    // even if the witness is late or will lose the online candidate competition.
    if (r.model && options.persistent_blocks) {
        auto blocking = Clock::now();
        auto key = f.key(*r.model);
        out.internal_duplicate = blocked_keys.count(key);
        if (out.internal_duplicate) {
            duplicate_stop = true; // Never retry an inconsistent returned identity indefinitely.
        } else {
            z3::expr_vector different(f.ctx);
            for (unsigned v = 0; v < f.variables.size(); ++v)
                different.push_back(f.nodes[f.variables[v]].expr != f.value(v, r.model->at(v)));
            solver.add(z3::mk_or(different));
            blocked_keys.insert(std::move(key));
            out.block_added = true;
        }
        out.block_seconds = std::chrono::duration<double>(Clock::now() - blocking).count();
        out.blocks_after = blocked_keys.size();
        out.stop_reason = stop_reason();
        stats.times["projection_block_seconds"] += out.block_seconds;
        stats.counts["projection_block_added"] += out.block_added;
        stats.counts["projection_internal_duplicate"] += out.internal_duplicate;
    }
    r.elapsed_seconds = std::chrono::duration<double>(Clock::now() - start).count();
    stats.times["projection_query_seconds"] += r.elapsed_seconds;
    return out;
}
std::string ProjectionResult::condition_json(const Formula &f) const {
    std::ostringstream o;
    o << std::setprecision(17) << "{\"blocks_before\":" << blocks_before
      << ",\"blocks_after\":" << blocks_after << ",\"block_seconds\":" << block_seconds
      << ",\"block_added\":" << (block_added ? "true" : "false")
      << ",\"internal_duplicate\":" << (internal_duplicate ? "true" : "false")
      << ",\"stop_reason\":" << quote(stop_reason) << ",\"variables\":[";
    for (unsigned i = 0; i < variables.size(); ++i) {
        if (i)
            o << ',';
        o << "{\"index\":" << variables[i]
          << ",\"name\":" << quote(f.nodes[f.variables[variables[i]]].expr.decl().name().str()) << '}';
    }
    o << "],\"forbidden\":[";
    for (unsigned i = 0; i < forbidden.size(); ++i) {
        if (i)
            o << ',';
        o << '[';
        for (unsigned j = 0; j < forbidden[i].size(); ++j) {
            if (j)
                o << ',';
            o << quote(forbidden[i][j].get_str());
        }
        o << ']';
    }
    return o.str() + "],\"scope\":\"temporary_projection_only_not_global_unsat\"}";
}
std::string ProjectionTrigger::observe(const std::optional<ResidualCandidate> &candidate) {
    ++plain_opportunities;
    if (candidate)
        consecutive_zero = candidate->gain.covered ? 0 : consecutive_zero + 1;
    if (first && candidate)
        return "initial_complement";
    if (candidate && consecutive_zero >= 4 && plain_opportunities >= 4)
        return "four_zero_candidates";
    return "none";
}
bool projection_better(const ResidualCandidate &a, const ResidualCandidate &b) {
    return a.gain.covered > b.gain.covered ||
           (a.gain.covered == b.gain.covered && a.gain.observed > b.gain.observed);
}
double ProjectionExtraBudget::allowance(double remaining) const {
    double available = std::min({stage_cap, quota - spent - .005, remaining - .05});
    return available >= minimum_stage ? available : 0;
}
} // namespace lia
