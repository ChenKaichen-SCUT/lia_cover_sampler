#include "macro_choice.hpp"
#include <cmath>
#include <iomanip>
#include <numeric>
namespace lia {
ChoiceCatalog::ChoiceCatalog(Formula &formula, Budget &budget) : f(formula) {
    const auto start = Clock::now();
    auto tick = [&]() {
        if (++operations > operation_cap || budget.done()) {
            complete = false;
            return false;
        }
        return true;
    };
    std::vector<int> mandatory, todo{0};
    std::set<int> seen;
    while (!todo.empty() && tick()) {
        int n = todo.back();
        todo.pop_back();
        if (!seen.insert(n).second)
            continue;
        if (++scanned_nodes > node_cap) {
            complete = false;
            break;
        }
        auto k = f.nodes[n].expr.decl().decl_kind();
        if (k == Z3_OP_AND)
            for (int c : f.nodes[n].children)
                todo.push_back(c);
        else
            mandatory.push_back(n);
    }
    auto constant = [&](int n) -> std::optional<Z> {
        auto &e = f.nodes[n].expr;
        if (e.is_numeral())
            return f.nodes[n].numeral;
        if (e.is_true())
            return Z(1);
        if (e.is_false())
            return Z(0);
        if (e.decl().decl_kind() == Z3_OP_UMINUS && f.nodes[n].children.size() == 1) {
            int c = f.nodes[n].children[0];
            if (f.nodes[c].expr.is_numeral())
                return Z(-f.nodes[c].numeral);
        }
        return {};
    };
    for (int n : mandatory) {
        if (!tick())
            break;
        if (f.nodes[n].expr.decl().decl_kind() != Z3_OP_OR)
            continue;
        auto &children = f.nodes[n].children;
        if (children.size() < 2 || children.size() > alternative_cap || groups.size() >= group_cap) {
            ++skipped;
            continue;
        }
        ChoiceGroup group;
        group.node = n;
        bool supported = true;
        for (int alt : children) {
            ChoiceAlternative a{alt, {}, 0, false, {}};
            std::vector<int> pending{alt};
            while (!pending.empty() && supported) {
                if (!tick()) {
                    supported = false;
                    break;
                }
                int v = pending.back();
                pending.pop_back();
                auto &node = f.nodes[v];
                if (node.expr.decl().decl_kind() == Z3_OP_AND) {
                    for (int c : node.children)
                        pending.push_back(c);
                    continue;
                }
                if (node.expr.decl().decl_kind() != Z3_OP_EQ || node.children.size() != 2) {
                    supported = false;
                    break;
                }
                int x = node.children[0], y = node.children[1];
                if (f.nodes[x].variable < 0)
                    std::swap(x, y);
                auto value = constant(y);
                if (f.nodes[x].variable < 0 || !value) {
                    supported = false;
                    break;
                }
                int var = f.nodes[x].variable;
                auto it = a.assignments.find(var);
                if (it != a.assignments.end() && it->second != *value) {
                    supported = false;
                    break;
                }
                a.assignments[var] = *value;
                if (a.assignments.size() > 64) {
                    supported = false;
                    break;
                }
            }
            if (!supported)
                break;
            for (auto &[v, z] : a.assignments)
                group.variables.insert(v);
            group.alternatives.push_back(std::move(a));
        }
        if (!supported) {
            ++skipped;
            continue;
        }
        group.exclusive = true;
        for (size_t i = 0; i < group.alternatives.size() && complete; ++i)
            for (size_t j = 0; j < i && complete; ++j) {
                bool conflict = false;
                for (auto &[v, z] : group.alternatives[i].assignments) {
                    if (!tick())
                        break;
                    auto it = group.alternatives[j].assignments.find(v);
                    if (it != group.alternatives[j].assignments.end() && it->second != z)
                        conflict = true;
                }
                if (!conflict)
                    group.exclusive = false;
            }
        if (!complete)
            group.exclusive = false;
        groups.push_back(std::move(group));
    }
    // Bounded reverse AST walk gives a syntactic arithmetic range. Necessary
    // equalities guide diagnostics only; no branch equality becomes an assertion.
    std::set<int> equalities;
    for (int n : mandatory)
        if (f.nodes[n].expr.decl().decl_kind() == Z3_OP_EQ)
            equalities.insert(n);
    for (auto &g : groups) {
        std::vector<int> pending;
        std::set<int> visited, related_equalities;
        for (int v : g.variables)
            pending.push_back(f.variables[v]);
        while (!pending.empty() && tick()) {
            int n = pending.back();
            pending.pop_back();
            if (!visited.insert(n).second)
                continue;
            if (!f.nodes[n].boolean && f.nodes[n].variable < 0 && !f.nodes[n].expr.is_numeral())
                g.arithmetic_nodes.insert(n);
            if (equalities.count(n))
                related_equalities.insert(n);
            for (auto [p, i] : f.nodes[n].parents)
                pending.push_back(p);
        }
        pending.assign(related_equalities.begin(), related_equalities.end());
        visited.clear();
        while (!pending.empty() && tick()) {
            int n = pending.back();
            pending.pop_back();
            if (!visited.insert(n).second)
                continue;
            if (f.nodes[n].variable >= 0)
                g.equality_variables.insert(f.nodes[n].variable);
            for (int c : f.nodes[n].children)
                pending.push_back(c);
        }
        if (!complete)
            break;
    }
    seconds = std::chrono::duration<double>(Clock::now() - start).count();
}
std::vector<unsigned> ChoiceCatalog::satisfied(const ChoiceGroup &g, const Assignment &m) const {
    std::vector<unsigned> result;
    for (unsigned i = 0; i < g.alternatives.size(); ++i) {
        bool yes = true;
        for (auto &[v, z] : g.alternatives[i].assignments)
            if (m.at(v) != z) {
                yes = false;
                break;
            }
        if (yes)
            result.push_back(i);
    }
    return result;
}
std::string ChoiceCatalog::signature(const Assignment &m) const {
    std::string result;
    for (auto &g : groups) {
        result += std::to_string(g.node) + ":";
        for (unsigned a : satisfied(g, m))
            result += std::to_string(a) + ",";
        result += ';';
    }
    return result;
}
void ChoiceCatalog::commit(const Assignment &m) {
    for (auto &g : groups) {
        std::string key;
        for (unsigned a : satisfied(g, m)) {
            ++g.alternatives[a].visits;
            key += std::to_string(a) + ",";
        }
        g.visited_modes.insert(key);
    }
}
std::string ChoiceCatalog::json() const {
    std::ostringstream o;
    o << std::setprecision(17) << "{\"complete\":" << (complete ? "true" : "false")
      << ",\"operations\":" << operations << ",\"scanned_nodes\":" << scanned_nodes
      << ",\"skipped_mandatory_or\":" << skipped << ",\"seconds\":" << seconds << ",\"groups\":[";
    auto ints = [&](const auto &xs) {
        o << '[';
        bool first = true;
        for (auto x : xs) {
            if (!first)
                o << ',';
            first = false;
            o << x;
        }
        o << ']';
    };
    bool first = true;
    for (auto &g : groups) {
        if (!first)
            o << ',';
        first = false;
        o << "{\"node\":" << g.node << ",\"exclusive\":" << (g.exclusive ? "true" : "false")
          << ",\"variables\":";
        ints(g.variables);
        o << ",\"arithmetic_nodes\":";
        ints(g.arithmetic_nodes);
        o << ",\"equality_variables\":";
        ints(g.equality_variables);
        o << ",\"visited_modes\":" << g.visited_modes.size() << ",\"alternatives\":[";
        for (size_t j = 0; j < g.alternatives.size(); ++j) {
            if (j)
                o << ',';
            auto &a = g.alternatives[j];
            o << "{\"node\":" << a.node << ",\"visits\":" << a.visits << ",\"attempt\":" << a.attempt.json()
              << ",\"impossible\":" << (a.impossible ? "true" : "false") << ",\"assignments\":[";
            bool af = true;
            for (auto &[v, z] : a.assignments) {
                if (!af)
                    o << ',';
                af = false;
                o << "{\"variable\":" << v
                  << ",\"name\":" << quote(f.nodes[f.variables[v]].expr.decl().name().str())
                  << ",\"value\":" << quote(z.get_str()) << '}';
            }
            o << "]}";
        }
        o << "]}";
    }
    return o.str() + "]}";
}
} // namespace lia
namespace lia {
std::string AlternativeAttemptState::json() const {
    std::ostringstream o;
    o << std::setprecision(17) << "{\"attempts\":" << attempts << ",\"sat_count\":" << sat_count
      << ",\"timeout_like_count\":" << timeout_like_count << ",\"unknown_count\":" << unknown_count
      << ",\"cumulative_seconds\":" << cumulative_seconds << ",\"retry_level\":" << retry_level
      << ",\"completed_levels\":" << completed_levels << ",\"clipped_attempts\":" << clipped_attempts
      << ",\"last_attempt_order\":" << last_attempt_order
      << ",\"eligible_after_order\":" << eligible_after_order
      << ",\"retired\":" << (retired ? "true" : "false") << '}';
    return o.str();
}
std::optional<std::pair<unsigned, unsigned>> MacroChoiceGenerator::select(const Assignment &current) {
    std::vector<std::pair<unsigned, unsigned>> fresh, retry, legacy;
    for (unsigned g = 0; g < catalog.groups.size(); ++g) {
        auto satisfied = catalog.satisfied(catalog.groups[g], current);
        for (unsigned a = 0; a < catalog.groups[g].alternatives.size(); ++a) {
            auto &alt = catalog.groups[g].alternatives[a];
            auto &s = alt.attempt;
            if (alt.impossible || s.retired || s.attempts >= 6 || s.sat_count ||
                std::find(satisfied.begin(), satisfied.end(), a) != satisfied.end())
                continue;
            // The ablation retains old ranking. Fair retry also defers one other opportunity.
            legacy.emplace_back(g, a);
            if (!s.attempts)
                fresh.emplace_back(g, a);
            else if (attempt_order >= s.eligible_after_order &&
                     (s.timeout_like_count || s.unknown_count == 1))
                retry.emplace_back(g, a);
        }
    }
    auto rank = [&](auto &v, bool retries) {
        std::shuffle(v.begin(), v.end(), rng);
        std::stable_sort(v.begin(), v.end(), [&](auto x, auto y) {
            auto &gx = catalog.groups[x.first], &gy = catalog.groups[y.first];
            auto &ax = gx.alternatives[x.second], &ay = gy.alternatives[y.second];
            if (retries && ax.attempt.last_attempt_order != ay.attempt.last_attempt_order)
                return ax.attempt.last_attempt_order < ay.attempt.last_attempt_order;
            return ax.visits != ay.visits ? ax.visits < ay.visits
                                          : gx.arithmetic_nodes.size() > gy.arithmetic_nodes.size();
        });
    };
    if (!fair_retry) {
        rank(legacy, false);
        selected_queue = "legacy_rank";
        if (legacy.empty())
            return {};
        return legacy.front();
    }
    bool use_retry = !retry.empty() && (new_since_retry >= 2 || fresh.empty());
    auto &queue = use_retry ? retry : fresh;
    if (queue.empty()) {
        selected_queue = "empty";
        return {};
    }
    rank(queue, use_retry);
    selected_queue = use_retry ? "retry" : "new";
    if (use_retry)
        new_since_retry = 0;
    else
        ++new_since_retry;
    return queue.front();
}
MacroChoiceGenerator::MacroChoiceGenerator(Formula &formula, ChoiceCatalog &c, unsigned seed)
    : f(formula), solver(f.ctx, z3::solver::simple()), rng(seed), catalog(c) {
    solver.add(f.root);
    solver.set("random_seed", seed);
}
GlobalResult MacroChoiceGenerator::query_choices(const std::vector<std::pair<unsigned, unsigned>> &choices,
                                                 Budget &b, Stats &stats, unsigned requested_ms) {
    GlobalResult r;
    auto start = Clock::now();
    if (b.done()) {
        r.code = interrupted ? SolveStatus::Cancelled : SolveStatus::BudgetExhausted;
        r.status = status_name(r.code);
        r.exit_decision.stop = true;
        return r;
    }
    solver.push();
    QueryGrant grant;
    AlternativeAttemptState *attempt =
        choices.size() == 1 ? &catalog.groups.at(choices[0].first).alternatives.at(choices[0].second).attempt
                            : nullptr;
    unsigned level = attempt ? attempt->retry_level : 0;
    if (requested_ms)
        for (unsigned i = 0; i < 4; ++i)
            if (requested_ms == SolveBudgetState::levels[i])
                level = i;
    try {
        auto construct = Clock::now();
        for (auto [g, a] : choices)
            solver.add(f.nodes[catalog.groups.at(g).alternatives.at(a).node].expr);
        r.constraint_seconds = std::chrono::duration<double>(Clock::now() - construct).count();
        stats.times["macro_constraint_seconds"] += r.constraint_seconds;
        const Budget *root = &b;
        while (root->parent)
            root = root->parent;
        r.session_remaining_before_check = root->left();
        r.action_remaining_before_check = b.left();
        grant = grant_query(requested_ms ? requested_ms : SolveBudgetState::levels[level], root->left(),
                            b.left());
        r.requested_timeout_ms = grant.requested_ms;
        r.actual_timeout_ms = grant.granted_ms;
        r.budget_clip_reason = grant.clip_reason;
        r.retry_level_before = level;
        if (!grant.granted_ms || b.done()) {
            r.code = interrupted ? SolveStatus::Cancelled : SolveStatus::BudgetExhausted;
            r.status = status_name(r.code);
            r.exit_decision.stop = interrupted || root->done();
            solver.pop();
            r.elapsed_seconds = std::chrono::duration<double>(Clock::now() - start).count();
            return r;
        }
        solver.set("timeout", grant.granted_ms);
        auto mocked = timed_query_override ? timed_query_override(grant.granted_ms)
                      : query_override     ? query_override(calls_seen)
                                           : std::optional<GlobalResult>{};
        ++calls_seen;
        r.check_started = true;
        if (mocked) {
            r.model = mocked->model;
            r.raw_status = mocked->raw_status == "not_checked"
                               ? (mocked->code == SolveStatus::Sat     ? "sat"
                                  : mocked->code == SolveStatus::Unsat ? "unsat"
                                                                       : "unknown")
                               : mocked->raw_status;
            r.detail = mocked->detail;
            r.check_seconds = mocked->check_seconds;
        } else {
            auto check_start = Clock::now();
            auto status = solver.check();
            r.check_seconds = std::chrono::duration<double>(Clock::now() - check_start).count();
            r.raw_status = status == z3::sat ? "sat" : status == z3::unsat ? "unsat" : "unknown";
            if (status == z3::sat) {
                auto complete = Clock::now();
                r.model = f.complete(solver.get_model());
                r.completion_seconds = std::chrono::duration<double>(Clock::now() - complete).count();
            } else if (status == z3::unknown)
                r.detail = solver.reason_unknown();
        }
        stats.times["macro_check_seconds"] += r.check_seconds;
        stats.times["macro_completion_seconds"] += r.completion_seconds;
        r.exit_evidence = {
            r.detail, grant.granted_ms, r.check_seconds, bool(interrupted), root->left() <= 0, b.left() <= 0,
            false};
        r.exit_decision = interpret_query_exit(r.raw_status, r.exit_evidence);
        r.code = r.exit_decision.code;
        r.status = status_name(r.code);
        if (r.model) {
            auto validation = Clock::now();
            if (!f.validate(*r.model))
                throw std::logic_error("invalid macro model");
            for (auto [g, a] : choices)
                for (auto &[v, z] : catalog.groups.at(g).alternatives.at(a).assignments)
                    if (r.model->at(v) != z)
                        throw std::logic_error("macro model misses forced alternative");
            r.validation_seconds = std::chrono::duration<double>(Clock::now() - validation).count();
            stats.times["macro_query_validation_seconds"] += r.validation_seconds;
        }
        solver.pop();
    } catch (...) {
        solver.pop();
        throw;
    }
    r.elapsed_seconds = std::chrono::duration<double>(Clock::now() - start).count();
    ++attempt_order;
    if (attempt) {
        ++attempt->attempts;
        attempt->last_attempt_order = attempt_order;
        attempt->cumulative_seconds += r.elapsed_seconds;
        attempt->eligible_after_order = attempt_order + 1;
        if (r.code == SolveStatus::Sat)
            ++attempt->sat_count;
        if (r.code == SolveStatus::Timeout)
            ++attempt->timeout_like_count;
        if (r.code == SolveStatus::Unknown)
            ++attempt->unknown_count;
        if (!grant.full())
            ++attempt->clipped_attempts;
        if (grant.full() &&
            (r.code == SolveStatus::Sat || r.code == SolveStatus::Unsat || r.code == SolveStatus::Timeout))
            attempt->completed_levels |= 1U << level;
        if (r.exit_decision.allow_upgrade && grant.full()) {
            if (level < 3)
                attempt->retry_level = level + 1;
            else
                attempt->retired = true;
        }
        if (attempt->unknown_count >= 2 || attempt->clipped_attempts >= 2 || attempt->attempts >= 6 ||
            r.code == SolveStatus::ResourceExhausted)
            attempt->retired = true;
    }
    r.retry_level_after = attempt ? attempt->retry_level : level;
    // F + exactly one unblocked alternative is the ONLY global proof scope.
    if (r.code == SolveStatus::Unsat && choices.size() == 1) {
        auto [g, a] = choices[0];
        catalog.groups[g].alternatives[a].impossible = true;
        r.global_target_proof = true;
    }
    return r;
}

namespace {
bool better_candidate(const ResidualCandidate &a, const ResidualCandidate &b) {
    return a.gain.covered > b.gain.covered ||
           (a.gain.covered == b.gain.covered && a.gain.observed > b.gain.observed);
}
void assess_broad(BroadResult &out, const GlobalResult &q, const OnlineCoverage &online, Budget &budget,
                  Stats &stats, const std::string &origin,
                  const std::function<double(const ResidualCandidate &)> &checkpoint, ChoiceCatalog *catalog,
                  const Assignment &current) {
    if (!q.model)
        return;
    auto start = Clock::now();
    ResidualWindow window;
    window.coverage_version = online.version();
    std::optional<ResidualCandidate> c;
    {
        Timer validation(stats, "broad_validation_seconds");
        c = online.prepare(*q.model, window, origin, out.calls);
    }
    if (!c) {
        ++out.duplicates;
        return;
    }
    double delivery_limit = budget.seconds;
    for (auto ancestor = budget.parent; ancestor; ancestor = ancestor->parent)
        delivery_limit = std::min(delivery_limit,
                                  ancestor->seconds +
                                      std::chrono::duration<double>(ancestor->start - budget.start).count());
    bool timely = !budget.done(), saved = false;
    if (timely && (!out.best || better_candidate(*c, *out.best))) {
        c->checkpoint_completed = checkpoint ? checkpoint(*c) : budget.elapsed();
        timely = std::isfinite(c->checkpoint_completed) && c->checkpoint_completed >= 0 &&
                 c->checkpoint_completed <= delivery_limit;
        if (timely) {
            out.best = *c;
            saved = true;
            if (c->gain.covered && out.first_positive_seconds < 0)
                out.first_positive_seconds = budget.elapsed();
        }
    }
    if (!timely)
        ++out.late;
    std::ostringstream row;
    row << std::setprecision(17) << "{\"query\":" << out.calls << ",\"origin\":" << quote(origin)
        << ",\"gain\":" << c->gain.covered << ",\"observations\":" << c->gain.observed
        << ",\"timely\":" << (timely ? "true" : "false")
        << ",\"checkpoint_saved\":" << (saved ? "true" : "false") << ",\"mode_changed\":";
    if (catalog)
        row << (catalog->signature(current) != catalog->signature(c->model) ? "true" : "false");
    else
        row << "null";
    row << ",\"full_assessment_seconds\":" << std::chrono::duration<double>(Clock::now() - start).count()
        << ",\"model\":" << online.model_json(c->model) << '}';
    out.assessments.push_back(row.str());
}
} // namespace
BroadResult MacroChoiceGenerator::generate(bool structured, Global &global, const OnlineCoverage &online,
                                           const Assignment &current, Budget &budget, Stats &stats,
                                           const std::function<double(const ResidualCandidate &)> &checkpoint,
                                           unsigned max_calls) {
    BroadResult out;
    Budget work;
    work.start = budget.start;
    work.seconds = std::max(0., budget.seconds - .025);
    work.parent = &budget;
    std::vector<std::pair<unsigned, unsigned>> previous;
    while (out.calls < max_calls && !work.done()) {
        if (catalog.groups.empty() || !structured)
            return plain_broad(global, online, current, budget, stats, checkpoint, max_calls);
        std::optional<std::pair<unsigned, unsigned>> chosen;
        {
            Timer selection(stats, "macro_selection_seconds");
            chosen = select(current);
        }
        std::vector<std::pair<unsigned, unsigned>> choices;
        if (chosen)
            choices.push_back(*chosen);
        if (allow_combo && !previous.empty() && chosen && previous[0].first != chosen->first)
            choices.insert(choices.begin(), previous[0]);
        bool macro_next = chosen.has_value();
        std::string before =
            chosen ? catalog.groups[chosen->first].alternatives[chosen->second].attempt.json() : "null";
        GlobalResult q;
        std::string origin = macro_next ? "macro_choice" : "exploration";
        unsigned forced = 0;
        if (macro_next) {
            for (auto [g, a] : choices)
                for (auto &[v, z] : catalog.groups[g].alternatives[a].assignments)
                    forced += current[v] != z;
            q = query_choices(choices, work, stats);
            ++out.macro_calls;
        } else {
            q = global.explore(current, work, stats, 0xffffffffU, true);
            ++out.fallback_calls;
            fallback_opportunity();
        }
        ++out.calls;
        std::ostringstream row;
        row << std::setprecision(17) << "{\"query\":" << out.calls << ",\"origin\":" << quote(origin)
            << ",\"status\":" << quote(q.status) << ",\"reason\":" << quote(q.detail)
            << ",\"evidence\":" << q.evidence_json() << ",\"query_seconds\":" << q.elapsed_seconds
            << ",\"completed_seconds\":" << budget.elapsed()
            << ",\"global_single_alternative_proof\":" << (q.global_target_proof ? "true" : "false")
            << ",\"forced_assignments_changed\":" << forced
            << ",\"selection_queue\":" << quote(selected_queue) << ",\"attempt_before\":" << before
            << ",\"attempt_after\":"
            << (chosen ? catalog.groups[chosen->first].alternatives[chosen->second].attempt.json() : "null")
            << ",\"choices\":[";
        if (macro_next)
            for (size_t i = 0; i < choices.size(); ++i) {
                if (i)
                    row << ',';
                row << '[' << choices[i].first << ',' << choices[i].second << ']';
            }
        row << "]}";
        out.queries.push_back(row.str());
        assess_broad(out, q, online, budget, stats, origin, checkpoint, &catalog, current);
        if (q.exit_decision.stop)
            break;
        if (macro_next && q.model && out.best)
            previous = choices;
    }
    out.full_seconds = budget.elapsed();
    return out;
}
BroadResult plain_broad(Global &global, const OnlineCoverage &online, const Assignment &current,
                        Budget &budget, Stats &stats,
                        const std::function<double(const ResidualCandidate &)> &checkpoint,
                        unsigned max_calls) {
    BroadResult out;
    Budget work;
    work.start = budget.start;
    work.seconds =
        global.exploration_budget.fixed_levels ? std::max(0., budget.seconds - .025) : budget.seconds * .8;
    work.parent = &budget;
    while (out.calls < max_calls && !work.done()) {
        auto q = global.explore(current, work, stats, 0xffffffffU, true);
        ++out.calls;
        ++out.fallback_calls;
        std::ostringstream row;
        row << std::setprecision(17) << "{\"query\":" << out.calls
            << ",\"origin\":\"exploration\",\"status\":" << quote(q.status)
            << ",\"reason\":" << quote(q.detail) << ",\"evidence\":" << q.evidence_json()
            << ",\"query_seconds\":" << q.elapsed_seconds << ",\"completed_seconds\":" << budget.elapsed()
            << '}';
        out.queries.push_back(row.str());
        assess_broad(out, q, online, budget, stats, "exploration", checkpoint, nullptr, current);
        if (q.exit_decision.stop)
            break;
    }
    out.full_seconds = budget.elapsed();
    return out;
}
} // namespace lia
