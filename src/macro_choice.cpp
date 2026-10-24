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
            ChoiceAlternative a{alt, {}, 0, false};
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
            o << "{\"node\":" << a.node << ",\"visits\":" << a.visits << ",\"assignments\":[";
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
MacroChoiceGenerator::MacroChoiceGenerator(Formula &formula, ChoiceCatalog &c, unsigned seed)
    : f(formula), solver(f.ctx, z3::solver::simple()), rng(seed), catalog(c) {
    solver.add(f.root);
    solver.set("random_seed", seed);
}
GlobalResult MacroChoiceGenerator::query_choices(const std::vector<std::pair<unsigned, unsigned>> &choices,
                                                 Budget &b, Stats &stats) {
    GlobalResult r;
    auto start = Clock::now();
    if (b.done()) {
        r.code = SolveStatus::BudgetExhausted;
        r.status = "budget_exhausted";
        return r;
    }
    solver.push();
    try {
        {
            Timer scope(stats, "macro_constraint_seconds");
            for (auto [g, a] : choices)
                solver.add(f.nodes[catalog.groups.at(g).alternatives.at(a).node].expr);
        }
        if (b.done()) {
            r.code = SolveStatus::BudgetExhausted;
            r.status = "budget_exhausted";
            solver.pop();
            return r;
        }
        r.actual_timeout_ms = b.milliseconds(adaptive.requested_timeout_ms);
        solver.set("timeout", r.actual_timeout_ms);
        auto mocked = query_override ? query_override(calls_seen) : std::optional<GlobalResult>{};
        ++calls_seen;
        if (mocked)
            r = *mocked;
        else {
            Timer solving(stats, "macro_solve_seconds");
            auto status = solver.check();
            if (status == z3::sat) {
                r.code = SolveStatus::Sat;
                r.status = "sat";
                r.model = f.complete(solver.get_model());
            } else if (status == z3::unsat) {
                r.code = SolveStatus::Unsat;
                r.status = "unsat";
            } else {
                r.detail = solver.reason_unknown();
                r.code = r.detail.find("timeout") != std::string::npos ? SolveStatus::Timeout
                                                                       : SolveStatus::Unknown;
                r.status = status_name(r.code);
            }
        }
        solver.pop();
    } catch (...) {
        solver.pop();
        throw;
    }
    r.elapsed_seconds = std::chrono::duration<double>(Clock::now() - start).count();
    adaptive.observe(r.code, r.elapsed_seconds, b.total_elapsed(), r.actual_timeout_ms);
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
    work.seconds = budget.seconds * .8;
    work.parent = &budget;
    std::vector<std::pair<unsigned, unsigned>> order;
    {
        Timer select(stats, "macro_selection_seconds");
        if (structured)
            for (unsigned g = 0; g < catalog.groups.size(); ++g)
                for (unsigned a = 0; a < catalog.groups[g].alternatives.size(); ++a)
                    if (!catalog.groups[g].alternatives[a].impossible)
                        order.emplace_back(g, a);
        std::shuffle(order.begin(), order.end(), rng);
        std::stable_sort(order.begin(), order.end(), [&](auto a, auto b) {
            auto &ga = catalog.groups[a.first], &gb = catalog.groups[b.first];
            auto va = ga.alternatives[a.second].visits, vb = gb.alternatives[b.second].visits;
            return va != vb ? va < vb : ga.arithmetic_nodes.size() > gb.arithmetic_nodes.size();
        });
    }
    std::vector<std::pair<unsigned, unsigned>> choices;
    if (!order.empty())
        choices.push_back(order.front());
    bool macro_next = !choices.empty();
    while (out.calls < max_calls && !work.done()) {
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
        }
        ++out.calls;
        std::ostringstream row;
        row << std::setprecision(17) << "{\"query\":" << out.calls << ",\"origin\":" << quote(origin)
            << ",\"status\":" << quote(q.status) << ",\"reason\":" << quote(q.detail)
            << ",\"query_seconds\":" << q.elapsed_seconds << ",\"completed_seconds\":" << budget.elapsed()
            << ",\"global_single_alternative_proof\":" << (q.global_target_proof ? "true" : "false")
            << ",\"forced_assignments_changed\":" << forced << ",\"choices\":[";
        if (macro_next)
            for (size_t i = 0; i < choices.size(); ++i) {
                if (i)
                    row << ',';
                row << '[' << choices[i].first << ',' << choices[i].second << ']';
            }
        row << "]}";
        out.queries.push_back(row.str());
        assess_broad(out, q, online, budget, stats, origin, checkpoint, &catalog, current);
        bool combo = false;
        if (macro_next && choices.size() == 1 && out.best && q.model) {
            auto next = std::find_if(order.begin() + 1, order.end(),
                                     [&](auto c) { return c.first != choices.front().first; });
            if (next != order.end()) {
                choices.push_back(*next);
                combo = true;
            }
        }
        macro_next =
            combo; // Failed/duplicate/unsupported first attempt uses remaining common calls for fallback.
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
    work.seconds = budget.seconds * .8;
    work.parent = &budget;
    while (out.calls < max_calls && !work.done()) {
        auto q = global.explore(current, work, stats, 0xffffffffU, true);
        ++out.calls;
        ++out.fallback_calls;
        std::ostringstream row;
        row << std::setprecision(17) << "{\"query\":" << out.calls
            << ",\"origin\":\"exploration\",\"status\":" << quote(q.status)
            << ",\"reason\":" << quote(q.detail) << ",\"query_seconds\":" << q.elapsed_seconds
            << ",\"completed_seconds\":" << budget.elapsed() << '}';
        out.queries.push_back(row.str());
        assess_broad(out, q, online, budget, stats, "exploration", checkpoint, nullptr, current);
    }
    out.full_seconds = budget.elapsed();
    return out;
}
} // namespace lia
