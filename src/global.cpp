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
    : f(formula), solver(f.ctx), exploration(f.ctx, z3::solver::simple{}), seed(seed), resource_limit(resource_limit) {
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
        ++registered_quotients;
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
static SolveStatus classify_unknown(const std::string &reason, const Budget &budget,
                                    unsigned ms, double elapsed, unsigned resources) {
    if (interrupted) return SolveStatus::Cancelled;
    if (budget.global_done()) return SolveStatus::BudgetExhausted;
    if (resources && (reason.find("resource") != std::string::npos || reason.find("rlimit") != std::string::npos))
        return SolveStatus::ResourceExhausted;
    if (elapsed * 1000 >= ms * .8 && (reason.find("timeout") != std::string::npos ||
        reason.find("cancel") != std::string::npos)) return SolveStatus::Timeout;
    return SolveStatus::Unknown;
}
GlobalResult Global::query(const std::vector<Target> &targets, Budget &budget, Stats &stats, unsigned allocation_ms,
                           bool adapt_pack, GlobalRestrictions *restrictions) {
    const auto begin = Clock::now();
    if (budget.done() || !allocation_ms) {
        GlobalResult out;
        out.code = interrupted ? SolveStatus::Cancelled : SolveStatus::BudgetExhausted;
        out.status = status_name(out.code);
        return out;
    }
    Timer timer(stats, "global_solve");
    std::set<int> pack;
    z3::expr_vector assumptions(f.ctx);
    const auto encoding_start=Clock::now();
    for (auto t : targets) {
        if (budget.done() || pack.size() >= pack_size)
            break;
        int id = ensure(t);
        auto test = pack;
        test.insert(id);
        const auto cache_begin=Clock::now();
        bool blocked=use_cache && conflicts.blocked(test);
        stats.times["conflict_cache_check"]+=std::chrono::duration<double>(Clock::now()-cache_begin).count();
        if (blocked) {
            ++stats.counts["cached_target_conflict"];
            continue;
        }
        pack = std::move(test);
    }
    stats.times["target_encoding_seconds"]+=std::chrono::duration<double>(Clock::now()-encoding_start).count();
    stats.counts["registered_activations"]=ids.size();
    stats.counts["registered_target_definitions"]=ids.size();
    stats.counts["registered_quotients"]=registered_quotients;
    if (pack.empty() || budget.done()) {
        GlobalResult out;out.status="no_target";out.code=SolveStatus::NoTarget;
        if(restrictions && budget.done()) {out.status="budget_exhausted";out.code=SolveStatus::BudgetExhausted;}
        return out;
    }
    for (int id : pack)
        assumptions.push_back(activations[id]);
    std::map<unsigned,unsigned> fixed_ids;
    std::set<unsigned> exclusion_ids;
    if(restrictions) {
        Timer constraints(stats,"fixed_constraint_seconds");
        auto &r=*restrictions;
        if(r.anchor.size()!=f.variables.size() || (r.owner && r.owner!=this))
            throw std::invalid_argument("restriction frame has a different formula or solver owner");
        r.owner=this;
        for(unsigned i=0;i<r.anchor.size() && !budget.done();++i) {
            if(r.free_variables.count(i)) continue;
            auto it=r.fixed_labels.find(i);
            if(it==r.fixed_labels.end()) {
                z3::expr label(f.ctx,Z3_mk_fresh_const(f.ctx,"lia_fixed",f.ctx.bool_sort()));
                solver.add(z3::implies(label,f.nodes[f.variables[i]].expr==f.value(i,r.anchor[i])));
                it=r.fixed_labels.emplace(i,label).first;
                ++stats.counts["fixed_definitions"];
            }
            assumptions.push_back(it->second);fixed_ids[it->second.id()]=i;
        }
        for(size_t j=0;j<r.excluded.size() && !budget.done();++j) {
            if(r.excluded[j].size()!=f.variables.size()) throw std::invalid_argument("incomplete excluded model");
            if(j==r.exclusion_labels.size()) {
                z3::expr_vector differs(f.ctx);
                for(unsigned i=0;i<f.variables.size();++i) {
                    if(budget.done()) break;
                    differs.push_back(f.nodes[f.variables[i]].expr!=f.value(i,r.excluded[j][i]));
                }
                if(budget.done()) break; // Never register a partial exclusion.
                z3::expr label(f.ctx,Z3_mk_fresh_const(f.ctx,"lia_excluded",f.ctx.bool_sort()));
                solver.add(z3::implies(label,z3::mk_or(differs)));
                r.exclusion_labels.push_back(label);++stats.counts["exclusion_definitions"];
            }
            assumptions.push_back(r.exclusion_labels[j]);exclusion_ids.insert(r.exclusion_labels[j].id());
        }
        if(budget.done()) {
            GlobalResult out;out.status="budget_exhausted";out.detail="restriction preparation deadline";
            out.code=SolveStatus::BudgetExhausted;return out;
        }
    }
    last_assumptions.clear();
    for (auto a : assumptions)
        last_assumptions += a.to_string() + " ";
    unsigned ms = budget.milliseconds(std::min(target_budget.requested_timeout_ms, allocation_ms));
    solver.set("timeout", ms);
    ++stats.counts["global_calls"];
    const auto check_start=Clock::now();
    auto res = solver.check(assumptions);
    stats.times["solver_check_seconds"]+=std::chrono::duration<double>(Clock::now()-check_start).count();
    GlobalResult out;
    for (int id : pack) out.attempted.push_back(registered_targets.at(id));
    if (res == z3::sat) {
        {Timer extraction(stats,"model_completion_seconds");out.model = f.complete(solver.get_model());}
        out.status = "sat";
        out.code = SolveStatus::Sat;
        ++stats.counts["global_sat"];
        if (adapt_pack && pack_size < 8)
            ++pack_size;
    } else if (res == z3::unsat) {
        out.status = "unsat";
        out.code = SolveStatus::Unsat;
        std::set<int> core;
        for (auto a : solver.unsat_core()) {
            if(auto it=activation_ids.find(a.id());it!=activation_ids.end()) {
                int id=it->second;core.insert(id);out.core.push_back(id);
                out.core_targets.push_back(registered_targets.at(id));
            } else if(auto it=fixed_ids.find(a.id());it!=fixed_ids.end()) {
                out.fixed_core.push_back(it->second);
            } else if(exclusion_ids.count(a.id())) out.exclusion_in_core=true;
            else throw std::logic_error("unclassified solver assumption in core");
        }
        out.global_target_proof=out.fixed_core.empty() && !out.exclusion_in_core;
        if (core.empty() && out.global_target_proof)
            throw std::logic_error("original formula unexpectedly UNSAT after a valid seed");
        if (use_cache && out.global_target_proof)
            conflicts.insert(core);
        if(out.global_target_proof) {
            ++stats.counts["target_combination_conflict"];
            ++stats.counts[core.size()==1 ? "target_singleton_proofs" : "target_multi_conflicts"];
        } else ++stats.counts["local_restriction_conflicts"];
        if (adapt_pack) pack_size = std::max(1U, pack_size / 2);
    } else {
        out.detail = solver.reason_unknown();
        out.code = classify_unknown(out.detail, budget, ms,
                                    std::chrono::duration<double>(Clock::now() - begin).count(), resource_limit);
        out.status = status_name(out.code);
        ++stats.counts["target_" + out.status];
        if (adapt_pack) pack_size = std::max(1U, pack_size / 2);
    }
    out.actual_timeout_ms = ms;
    out.resource_limit = resource_limit;
    out.elapsed_seconds = std::chrono::duration<double>(Clock::now() - begin).count();
    target_budget.observe(out.code, out.elapsed_seconds, budget.total_elapsed(), ms);
    stats.counts["target_requested_timeout_last_ms"]=target_budget.requested_timeout_ms;
    stats.counts["target_actual_timeout_last_ms"]=ms;
    if (!out.detail.empty()) ++stats.counts["target_reason:"+out.detail];
    if (trace) {
        std::ostringstream s;
        s << "{\"kind\":\"global\",\"context\":" << quote(restrictions ? "original_formula_with_scoped_assumptions" : "original_formula_only") << ",\"status\":" << quote(out.status)
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
GlobalResult Global::repair(const std::vector<Target> &targets, Budget &budget, Stats &stats,
    const std::function<Gain(const Assignment &)> &score,
    const std::function<void(const GlobalResult &)> &feedback,
    unsigned max_calls, unsigned max_expansions) {
    const auto begin=Clock::now();
    const unsigned previous_size=pack_size;
    std::vector<Target> pack;
    std::set<Target> tried;
    for (auto target:targets) if (!unreachable(target) && tried.insert(target).second) {
        pack.push_back(target);
        if (pack.size()>=previous_size) break;
    }
    GlobalResult best, last;
    last.code=interrupted ? SolveStatus::Cancelled : budget.done() ? SolveStatus::BudgetExhausted : SolveStatus::NoTarget;
    last.status=status_name(last.code);
    last.detail="no repair query issued";
    Gain best_gain;
    unsigned expansions=0, calls=0;
    while (!pack.empty() && !budget.done() && calls++<max_calls) {
        pack_size=pack.size();
        last=repair_check ? repair_check(pack,budget,stats) : query(pack,budget,stats,0xffffffffU,false);
        feedback(last);
        ++stats.counts["repair_internal_calls"];
        if (last.model) {
            if (!f.validate(*last.model)) throw std::logic_error("repair witness violates original formula");
            auto gain=score(*last.model);
            if (!best.model || gain.covered>best_gain.covered ||
                (gain.covered==best_gain.covered && gain.observed>best_gain.observed)) {
                best=last; best_gain=gain;
            }
            if (expansions>=max_expansions || pack.size()>=8 || gain.covered==0) break;
            bool extended=false;
            // Targets arrive in the manager's value order; preserve a SAT core pack.
            for (auto target:targets) if (!unreachable(target) && tried.insert(target).second) {
                auto test=pack; test.push_back(target); std::set<int> ids_to_test;
                for (auto t:test) ids_to_test.insert(ensure(t));
                if (use_cache && conflicts.blocked(ids_to_test)) continue;
                pack=std::move(test); extended=true; ++expansions; ++stats.counts["repair_expansions"]; break;
            }
            if (!extended) break;
        } else if (last.code==SolveStatus::Unsat) {
            // Delete only one lowest-ranked member of this actual core. A multi-core
            // never marks its individual members unreachable.
            auto remove=pack.end();
            for (auto it=pack.begin();it!=pack.end();++it) {
                auto id=ids.find(*it);
                if (id!=ids.end() && std::find(last.core.begin(),last.core.end(),id->second)!=last.core.end())
                    remove=it;
            }
            if (remove==pack.end()) break;
            pack.erase(remove); ++stats.counts["repair_core_removals"];
        } else {
            // Unknown/Timeout/ResourceExhausted are scheduling evidence, never proofs.
            ++stats.counts["repair_nonproof_stops"];
            break;
        }
    }
    double elapsed=std::chrono::duration<double>(Clock::now()-begin).count();
    if (best.model && best_gain.covered>0 && elapsed<.2 && expansions)
        pack_size=std::min(8U,previous_size+1);
    else if (!best.model || best_gain.covered==0)
        pack_size=std::max(1U,previous_size/2);
    else pack_size=previous_size;
    stats.counts["repair_returned_witness"]+=bool(best.model);
    if (best.model && last.code!=SolveStatus::Sat) ++stats.counts["repair_preserved_earlier_witness"];
    return best.model ? best : last;
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
void Global::block_model(const Assignment &model, Stats &stats) {
    if (!blocked_model_keys.insert(f.key(model)).second) {
        ++stats.counts["exploration_block_reused"];
        return;
    }
    z3::expr_vector diff(f.ctx);
    for (size_t i = 0; i < model.size(); ++i)
        diff.push_back(f.nodes[f.variables[i]].expr != f.value(i, model[i]));
    exploration.add(z3::mk_or(diff));
    ++stats.counts["exploration_block_added"];
}
void Global::rebuild_exploration(Stats &stats) {
    Timer timer(stats, "exploration_rebuild");
    exploration = z3::solver(f.ctx, z3::solver::simple{});
    exploration.add(f.root);
    exploration.set("random_seed", seed);
    if (resource_limit) exploration.set("rlimit", resource_limit);
    blocked_model_keys.clear();
    ++stats.counts["exploration_rebuilds"];
}
GlobalResult Global::explore(const Assignment &current, Budget &budget, Stats &stats, unsigned allocation_ms,
                             bool bypass_soft_cooldown) {
    Timer timer(stats, "exploration_solve");
    const auto begin = Clock::now();
    GlobalResult out;
    if (budget.done() || !allocation_ms) {
        out.code = interrupted ? SolveStatus::Cancelled : SolveStatus::BudgetExhausted;
        out.status = status_name(out.code);
        return out;
    }
    if (!bypass_soft_cooldown && budget.total_elapsed() < exploration_budget.cooldown_until) {
        out.code = SolveStatus::NoTarget;
        out.status = "cooldown";
        ++stats.counts["exploration_cooldown_skips"];
        return out;
    }
    block_model(current, stats);
    unsigned ms = budget.milliseconds(std::min(exploration_budget.requested_timeout_ms, allocation_ms));
    exploration.set("timeout", ms);
    ++stats.counts["exploration_calls"];
    auto mock = exploration_check ? exploration_check(ms) : std::optional<GlobalResult>{};
    if (mock) out = *mock;
    else {
        auto res = exploration.check();
        if (res == z3::sat) {
            out.model = f.complete(exploration.get_model());
            out.code = SolveStatus::Sat;
        } else if (res == z3::unsat) {
            out.code = SolveStatus::Unsat;
            out.detail = "blocked exploration context only; not original-formula UNSAT";
        } else {
            out.detail = exploration.reason_unknown();
            out.code = classify_unknown(out.detail, budget, ms,
                        std::chrono::duration<double>(Clock::now() - begin).count(), resource_limit);
        }
    }
    if (out.model) {
        if (!f.validate(*out.model)) {
            invalid_exploration_model = *out.model;
            throw std::logic_error("invalid exploration model");
        }
        block_model(*out.model, stats);
    }
    out.status = out.code == SolveStatus::Unsat ? "exploration_exhausted" : status_name(out.code);
    out.actual_timeout_ms = ms;
    out.resource_limit = resource_limit;
    out.elapsed_seconds = std::chrono::duration<double>(Clock::now() - begin).count();
    auto before = exploration_budget.requested_timeout_ms;
    exploration_budget.observe(out.code, out.elapsed_seconds, budget.total_elapsed(), ms);
    if (before < exploration_budget.requested_timeout_ms) ++stats.counts["exploration_timeout_growth"];
    stats.counts["exploration_requested_timeout_last_ms"] = exploration_budget.requested_timeout_ms;
    stats.counts["exploration_actual_timeout_last_ms"] = ms;
    ++stats.counts["exploration_" + out.status];
    if (!out.detail.empty()) ++stats.counts["exploration_reason:"+out.detail];
    if (trace) trace("{\"kind\":\"exploration\",\"status\":" + quote(out.status) +
        ",\"reason\":" + quote(out.detail) + ",\"actual_timeout_ms\":" + std::to_string(ms) +
        ",\"requested_timeout_ms\":" + std::to_string(exploration_budget.requested_timeout_ms) +
        ",\"elapsed_seconds\":" + std::to_string(out.elapsed_seconds) +
        ",\"resource_limit\":" + std::to_string(resource_limit) + "}");
    return out;
}
} // namespace lia
