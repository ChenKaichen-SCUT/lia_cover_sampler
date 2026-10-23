#include "core.hpp"
#include <algorithm>

namespace lia {
bool ConflictCache::blocked(const std::set<int> &pack) const {
    for (auto &c : cores)
        if (std::includes(pack.begin(), pack.end(), c.begin(), c.end()))
            return true;
    return false;
}
void ConflictCache::insert(std::set<int> core) {
    if (core.empty())
        throw std::logic_error("empty target conflict");
    if (blocked(core))
        return;
    std::erase_if(cores,
                  [&](const auto &c) { return std::includes(c.begin(), c.end(), core.begin(), core.end()); });
    cores.push_back(std::move(core));
}
Global::Global(Formula &formula, unsigned seed, unsigned resource_limit)
    : f(formula), solver(f.ctx), exploration(f.ctx) {
    solver.add(f.root);
    solver.set("random_seed", seed);
    solver.set("unsat_core", true);
    exploration.add(f.root);
    exploration.set("random_seed", seed);
    if (resource_limit) {
        solver.set("rlimit", resource_limit);
        exploration.set("rlimit", resource_limit);
    }
}
int Global::ensure(Target t) {
    if (auto it = ids.find(t); it != ids.end())
        return it->second;
    int id = ids.size();
    auto e = f.nodes[t.node].expr;
    // Z3 fresh constants avoid collisions with every original symbol.
    z3::expr a(f.ctx, Z3_mk_fresh_const(f.ctx, "lia_activation", f.ctx.bool_sort()));
    z3::expr q = f.ctx.bool_val(true);
    if (e.is_bool())
        q = t.desired ? e : !e;
    else {
        z3::expr h(f.ctx, Z3_mk_fresh_const(f.ctx, "lia_quotient", f.ctx.int_sort()));
        Z period = pow2(t.bit + 1), half = pow2(t.bit), lo = t.desired ? half : Z(0),
          hi = t.desired ? Z(period - 1) : Z(half - 1);
        auto base = f.ctx.int_val(period.get_str().c_str()) * h;
        q = (base + f.ctx.int_val(lo.get_str().c_str()) <= e) &&
            (e <= base + f.ctx.int_val(hi.get_str().c_str()));
    }
    q = f.reach(t.node) && q;
    solver.add(z3::implies(a, q));
    ids[t] = id;
    registered_targets.push_back(t);
    activations.push_back(a);
    activation_ids[a.id()] = id;
    return id;
}
GlobalResult Global::query(const std::vector<Target> &targets, Budget &budget, Stats &stats) {
    Timer timer(stats, "global_solve");
    std::set<int> pack;
    z3::expr_vector assumptions(f.ctx);
    for (auto t : targets) {
        if (budget.done() || pack.size() >= pack_size)
            break;
        int id = ensure(t);
        auto test = pack;
        test.insert(id);
        if (use_cache && conflicts.blocked(test)) {
            ++stats.counts["cached_target_conflict"];
            continue;
        }
        pack = std::move(test);
    }
    if (pack.empty() || budget.done())
        return {{}, "no_target", "", {}};
    for (int id : pack)
        assumptions.push_back(activations[id]);
    last_assumptions.clear();
    for (int id : pack)
        last_assumptions += activations[id].to_string() + " ";
    unsigned ms = budget.milliseconds(timeout_ms);
    solver.set("timeout", ms);
    ++stats.counts["global_calls"];
    auto res = solver.check(assumptions);
    GlobalResult out;
    if (res == z3::sat) {
        out.model = f.complete(solver.get_model());
        out.status = "sat";
        ++stats.counts["global_sat"];
        if (pack_size < 8)
            ++pack_size;
    } else if (res == z3::unsat) {
        out.status = "unsat";
        std::set<int> core;
        for (auto a : solver.unsat_core()) {
            int id = activation_ids.at(a.id());
            core.insert(id);
            out.core.push_back(id);
        }
        if (core.empty())
            throw std::logic_error("original formula unexpectedly UNSAT after a valid seed");
        if (use_cache)
            conflicts.insert(core);
        ++stats.counts["target_combination_conflict"];
        pack_size = std::max(1U, pack_size / 2);
    } else {
        out.detail = solver.reason_unknown();
        out.status = out.detail.find("timeout") != std::string::npos    ? "timeout"
                     : out.detail.find("cancel") != std::string::npos   ? "cancelled"
                     : out.detail.find("resource") != std::string::npos ? "resource_exhausted"
                                                                        : "unknown";
        ++stats.counts["target_" + out.status];
        timeout_ms = std::min(2000U, timeout_ms * 2);
        pack_size = std::max(1U, pack_size / 2);
    }
    if (trace) {
        std::ostringstream s;
        s << "{\"kind\":\"global\",\"context\":\"original_formula_only\",\"status\":" << quote(out.status)
          << ",\"reason\":" << quote(out.detail) << ",\"timeout_ms\":" << ms << ",\"assumptions\":[";
        bool first = true;
        for (int id : pack) {
            if (!first)
                s << ',';
            first = false;
            s << id;
        }
        s << "],\"core\":[";
        for (size_t i = 0; i < out.core.size(); ++i) {
            if (i)
                s << ',';
            s << out.core[i];
        }
        s << "],\"targets\":[";
        first = true;
        for (auto &[t, id] : ids)
            if (pack.count(id)) {
                if (!first)
                    s << ',';
                first = false;
                s << "{\"id\":" << id << ",\"identity\":" << quote(t.key())
                  << ",\"reach\":" << quote(f.reach(t.node).to_string()) << "}";
            }
        trace(s.str() + "]}");
    }
    return out;
}
void Global::validate_conflicts(const Assignment &model) {
    auto observations = f.evaluate(model);
    for (const auto &core : conflicts.cores) {
        bool all = true;
        for (int id : core) {
            auto target = registered_targets.at(id);
            if (!observations.visited[target.node] ||
                bit(observations.values[target.node], target.bit) != target.desired) {
                all = false;
                break;
            }
        }
        if (all)
            throw std::logic_error("conflict cache blocks a known satisfying assignment");
    }
}
GlobalResult Global::explore(const Assignment &current, Budget &budget, Stats &stats) {
    Timer timer(stats, "exploration_solve");
    z3::expr_vector diff(f.ctx);
    for (size_t i = 0; i < current.size(); ++i)
        diff.push_back(f.nodes[f.variables[i]].expr != f.value(i, current[i]));
    // Blocking clauses live only in this solver. No cores escape this context.
    exploration.add(z3::mk_or(diff));
    exploration.set("timeout", budget.milliseconds(timeout_ms));
    ++stats.counts["exploration_calls"];
    auto res = exploration.check();
    if (res == z3::sat)
        return {f.complete(exploration.get_model()), "sat", "", {}};
    if (res == z3::unsat)
        return {{}, "exploration_exhausted", "blocked exploration context only", {}};
    return {{}, "unknown", exploration.reason_unknown(), {}};
}
} // namespace lia
