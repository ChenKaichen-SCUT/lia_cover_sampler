#include "coverage_block_generator.hpp"
#include "line.hpp"
#include <algorithm>
#include <iomanip>

namespace lia {
namespace {
struct Dependencies {
    Formula &f;Budget &budget;unsigned limit,used=0;bool complete=true;
    std::map<int,bool> required_cache;
    Dependencies(Formula &formula,Budget &action,unsigned operations):f(formula),budget(action),limit(operations) {}
    bool tick() {if(budget.done() || used>=limit) {complete=false;return false;}++used;return true;}
    bool required(int node) {
        if(node==0) return true;
        if(auto it=required_cache.find(node);it!=required_cache.end()) return it->second;
        if(!tick()) return false;
        for(auto [parent,pos]:f.nodes[node].parents) {
            if(!tick()) return false;
            (void)pos;
            if(f.nodes[parent].expr.decl().decl_kind()==Z3_OP_AND && required(parent))
                return required_cache[node]=true;
        }
        return required_cache[node]=false;
    }
    void value(int root,std::set<unsigned> &vars) {
        std::vector<int> pending{root};std::set<int> seen;
        while(!pending.empty() && tick()) {
            int n=pending.back();pending.pop_back();if(!seen.insert(n).second) continue;
            if(f.nodes[n].variable>=0) vars.insert(f.nodes[n].variable);
            for(int c:f.nodes[n].children) {if(!tick()) break;pending.push_back(c);}
        }
    }
    std::set<unsigned> target(Target t) {
        std::set<unsigned> vars;value(t.node,vars);
        std::vector<int> pending{t.node};std::set<int> seen;
        while(!pending.empty() && tick()) {
            int n=pending.back();pending.pop_back();if(!seen.insert(n).second) continue;
            for(auto [parent,pos]:f.nodes[n].parents) {
                if(!tick()) break;
                auto &p=f.nodes[parent];auto kind=p.expr.decl().decl_kind();
                // A necessary conjunction's earlier children are already true
                // under F. No need to release the entire preceding system.
                if((kind==Z3_OP_AND && !required(parent)) || kind==Z3_OP_OR)
                    for(int k=0;k<pos && complete && !budget.done();++k) value(p.children[k],vars);
                if(kind==Z3_OP_ITE && pos>0) value(p.children[0],vars);
                pending.push_back(parent);
            }
        }
        return vars;
    }
    void cooperate(std::set<unsigned> &vars,unsigned cap,unsigned query,std::vector<BlockRelease> &events) {
        if(!cap) return;
        // Explore only ancestors of already relevant variables, not all equations.
        auto initial=vars;std::set<int> seen;unsigned added=0;
        LineLimits limits;limits.dag_nodes=256;limits.affine_terms=1024;
        AffineSyntax syntax(f,limits);
        for(unsigned v:initial) {
            std::vector<int> pending{f.variables[v]};
            while(!pending.empty() && added<cap && tick()) {
                int n=pending.back();pending.pop_back();if(!seen.insert(n).second) continue;
                auto &node=f.nodes[n];auto kind=node.expr.decl().decl_kind();
                if(kind==Z3_OP_EQ && node.children.size()==2 && !f.nodes[node.children[0]].boolean) {
                    if(required(n)) try {
                        auto a=syntax.linear(node.children[0],budget);a.add(syntax.linear(node.children[1],budget),-1);
                        for(const auto &[u,c]:a.a) if(c!=0 && !vars.count(u) && added<cap) {
                            vars.insert(u);++added;events.push_back({unsigned(u),query,"necessary_equality:"+std::to_string(n)});
                        }
                    } catch(const LineFailure &e) {if(e.status==LineStatus::BudgetExceeded) complete=false;}
                    continue;
                }
                // Do not climb through predicates into unrelated constraints.
                if(node.boolean && node.variable<0) continue;
                for(auto [p,pos]:node.parents) {(void)pos;if(!tick()) break;pending.push_back(p);}
            }
            if(added>=cap || !complete) break;
        }
    }
};
bool missing(const Coverage &c,Target t) {
    return !((t.desired?c.states[t.node].one:c.states[t.node].zero)&(uint64_t(1)<<t.bit));
}
bool immediate(const Coverage &c,Target t) {
    return ((t.desired?c.states[t.node].zero:c.states[t.node].one)&(uint64_t(1)<<t.bit))!=0;
}
bool hit(const Observation &o,Target t) {return o.visited[t.node] && bit(o.values[t.node],t.bit)==t.desired;}
unsigned branch_changes(const Formula &f,const Observation &a,const Observation &b) {
    unsigned changed=0;
    for(const auto &n:f.nodes) {
        auto kind=n.expr.decl().decl_kind();
        if(kind==Z3_OP_ITE && !n.children.empty()) {
            int c=n.children[0];changed+=a.visited[c] && b.visited[c] && a.values[c]!=b.values[c];
        } else if(kind==Z3_OP_OR) {
            auto chosen=[&](const Observation &o){for(unsigned i=0;i<n.children.size();++i) {
                int c=n.children[i];if(o.visited[c] && o.values[c]!=0) return int(i);
            }return -1;};
            int x=chosen(a),y=chosen(b);changed+=x>=0 && y>=0 && x!=y;
        }
    }
    return changed;
}
void targets_json(std::ostream &out,const std::vector<Target> &v) {
    out<<'[';bool first=true;for(auto t:v){if(!first)out<<',';first=false;out<<"["<<t.node<<','<<t.bit<<','<<(t.desired?"true":"false")<<']';}out<<']';
}
}
BlockResult CoverageBlockGenerator::generate(const Assignment &seed,const Coverage &coverage,Budget &budget,
    uint64_t target_seed,bool blocks,Stats &stats,const std::function<bool(const std::string&)> &emitted,
    const std::function<double(const Assignment&,Gain,double)> &checkpoint,const std::vector<Target> &explicit_plan) {
    BlockResult result;result.variables=f.variables.size();
    double deadline=budget.seconds;
    for(auto p=budget.parent;p;p=p->parent)
        deadline=std::min(deadline,std::chrono::duration<double>(p->start-budget.start).count()+p->seconds);
    result.reserve_seconds=std::min(budget.left(),std::max(.002,budget.seconds*options.reserve_fraction));
    Budget work;work.parent=&budget;work.seconds=std::max(0.,budget.left()-result.reserve_seconds);work.label="block_search";
    Dependencies deps{f,work,options.dependency_operations};
    Dependencies block_deps{f,work,options.dependency_operations};
    std::vector<Target> plan=explicit_plan;
    {
        Timer timer(stats,"target_selection_seconds");
        if(plan.empty() && !work.done()) {
            std::mt19937_64 rng(target_seed);plan=active_targets(f,coverage,rng,options.pool_size);
            std::stable_sort(plan.begin(),plan.end(),[&](Target a,Target b) {
                if(immediate(coverage,a)!=immediate(coverage,b)) return immediate(coverage,a);
                return (f.nodes[a.node].variable>=0)>(f.nodes[b.node].variable>=0);
            });
        }
        std::erase_if(plan,[&](Target t){return !missing(coverage,t);});
    }
    if(plan.empty() || work.done()) {result.status=work.done()?"budget_exhausted":"no_target";result.full_seconds=budget.elapsed();return result;}
    result.primary=plan.front();result.first_observation=!immediate(coverage,plan.front());
    GlobalRestrictions scope(seed);scope.excluded.push_back(seed);
    std::map<Target,std::set<unsigned>> dependencies;
    std::set<unsigned> relevant;
    {
        Timer timer(stats,"dependency_seconds");
        relevant=deps.target(plan.front());dependencies[plan.front()]=relevant;
        if(blocks) {
            scope.free_variables=relevant;
            for(unsigned v:relevant) result.releases.push_back({v,0,"primary_expression_or_reach"});
            block_deps.cooperate(scope.free_variables,options.equality_variables,0,result.releases);
        } else for(unsigned i=0;i<seed.size();++i) scope.free_variables.insert(i);
    }
    result.initial_block=scope.free_variables.size();
    const unsigned block_cap=std::min<unsigned>(seed.size(),std::max(8U,std::min(options.max_block,unsigned(seed.size()/4))));
    auto fallback=[&](const std::string &why) {
        if(scope.free_variables.size()<seed.size()) {
            result.fallback_reason=why;++stats.counts["block_global_fallbacks"];
            for(unsigned v=0;v<seed.size();++v) if(scope.free_variables.insert(v).second)
                result.releases.push_back({v,unsigned(result.queries.size()),"global_fallback:"+why});
        }
    };
    if(blocks && (!deps.complete || !block_deps.complete || scope.free_variables.size()>block_cap)) fallback(!deps.complete || !block_deps.complete?"dependency_limit":"initial_block_limit");
    std::vector<Target> pack{plan.front()};std::set<Target> tried{plan.front()};
    std::optional<Observation> best_observation;
    Observation original;
    {Timer timer(stats,"seed_evaluation_seconds");original=f.evaluate(seed);}
    while(!pack.empty() && !work.done() && result.queries.size()<options.max_queries) {
        global.pack_size=pack.size();GlobalResult query;
        auto injected=query_override?query_override(pack,result.queries.size()):std::nullopt;
        query=injected?*injected:global.query(pack,work,stats,0xffffffffU,false,&scope);
        result.queries.push_back({query.status,query.detail,unsigned(scope.free_variables.size()),query.actual_timeout_ms,
            scope.free_variables.size()<seed.size(),query.exclusion_in_core,query.global_target_proof,
            query.elapsed_seconds,budget.elapsed(),query.attempted,query.core_targets,query.fixed_core});
        result.status=query.status;
        if(query.model) {
            const auto validation_start=Clock::now();
            if(!f.validate(*query.model)) throw std::logic_error("block generator invalid original-formula witness");
            auto observation=f.evaluate(*query.model);auto gain=coverage.gain(observation);
            for(auto t:query.attempted) if(!hit(observation,t)) throw std::logic_error("Global Reach/bit witness mismatch");
            bool duplicate=*query.model==seed || emitted(f.key(*query.model));
            unsigned branches=branch_changes(f,original,observation);
            ++result.validated_candidates;result.duplicate_candidates+=duplicate;
            bool better=!result.model || gain.covered>result.gain.covered ||
                (gain.covered==result.gain.covered && gain.observed>result.gain.observed);
            stats.times["candidate_validation_seconds"]+=std::chrono::duration<double>(Clock::now()-validation_start).count();
            if(boundary) boundary("candidate_validated");
            if(!duplicate && better && !budget.done()) {
                // An immutable complete model is copied and flushed before it is
                // admitted as a deadline checkpoint; subsequent failures cannot erase it.
                auto saved=*query.model;
                double delivered=budget.elapsed();
                if(checkpoint) {
                    Timer timer(stats,"checkpoint_write_seconds");const double validated=delivered;
                    delivered=checkpoint(saved,gain,validated);
                    if(delivered<validated || delivered>budget.elapsed()) throw std::logic_error("invalid checkpoint acknowledgement time");
                }
                // The writer reports completion of the actual flush. Subsequent
                // bookkeeping cannot revoke a checkpoint already delivered on time.
                if(delivered<=deadline) {
                    result.model=std::move(saved);result.gain=gain;result.branch_changes=branches;
                    result.checkpoint_seconds=delivered;best_observation=std::move(observation);
                    if(gain.covered && result.first_positive_seconds<0) result.first_positive_seconds=result.checkpoint_seconds;
                    ++stats.counts["deliverable_checkpoints"];
                } else ++result.late_candidates;
            } else if(!duplicate && budget.done()) ++result.late_candidates;
            if(duplicate) {scope.excluded.push_back(*query.model);continue;}
            if(work.done() || pack.size()>=options.max_pack) break;
            // Same bounded pack builder in both modes. Only related, not-yet-hit
            // gaps may be added; score is always the full original marginal gain.
            bool added=false;
            {Timer timer(stats,"pack_selection_seconds");
                for(auto t:plan) {
                    if(work.done()) break;
                    if(tried.count(t) || hit(best_observation?*best_observation:observation,t)) continue;
                    if(!dependencies.count(t)) dependencies[t]=deps.target(t);
                    bool related=std::any_of(dependencies[t].begin(),dependencies[t].end(),[&](unsigned v){return relevant.count(v);});
                    if(!related) continue;
                    pack.push_back(t);tried.insert(t);added=true;
                    relevant.insert(dependencies[t].begin(),dependencies[t].end());
                    if(blocks && scope.free_variables.size()<seed.size()) {
                        for(unsigned v:dependencies[t]) if(scope.free_variables.insert(v).second)
                            result.releases.push_back({v,unsigned(result.queries.size()),"additional_target_dependency"});
                        block_deps.cooperate(scope.free_variables,options.equality_variables,result.queries.size(),result.releases);
                        if(!deps.complete || !block_deps.complete || scope.free_variables.size()>block_cap) fallback("pack_dependency_limit");
                    }
                    break;
                }
            }
            if(!added) break;
        } else if(query.code==SolveStatus::Unsat) {
            if(blocks && !query.fixed_core.empty() && scope.free_variables.size()<seed.size()) {
                Timer timer(stats,"conflict_expansion_seconds");
                if(result.expansions>=options.max_expansions || !block_deps.complete) fallback("expansion_limit");
                else {
                    ++result.expansions;auto core=query.fixed_core;std::sort(core.begin(),core.end());unsigned freed=0;
                    for(unsigned v:core) if(scope.free_variables.insert(v).second) {
                        result.releases.push_back({v,unsigned(result.queries.size()),"fixed_assumption_unsat_core"});
                        if(++freed>=options.release_per_core) break;
                    }
                    block_deps.cooperate(scope.free_variables,options.equality_variables,result.queries.size(),result.releases);
                    if(!freed || scope.free_variables.size()>block_cap || !block_deps.complete) fallback("expanded_block_limit");
                }
                continue;
            }
            // Remove the last participating target only; a SAT checkpoint survives.
            auto remove=pack.end();for(auto it=pack.begin();it!=pack.end();++it)
                if(std::find(query.core_targets.begin(),query.core_targets.end(),*it)!=query.core_targets.end()) remove=it;
            if(remove==pack.end()) break;
            pack.erase(remove);++stats.counts["block_pack_core_removals"];
        } else {
            // Unknown/timeout/resource exhaustion provide no proof. At most one
            // direct-global fallback fits inside the same query/time limits.
            if(blocks && scope.free_variables.size()<seed.size() && !work.done()) {fallback("nonproof_restricted_result");continue;}
            break;
        }
    }
    result.dependency_complete=deps.complete && (!blocks || block_deps.complete);result.final_block=scope.free_variables.size();
    stats.counts["dependency_operations"]+=deps.used+block_deps.used;
    stats.counts["generator_query_cap_reached"]+=result.queries.size()>=options.max_queries;
    stats.counts["generator_kept_checkpoint"]+=bool(result.model);
    result.full_seconds=budget.elapsed();
    return result;
}
std::string BlockResult::json(const Formula &f) const {
    std::ostringstream o;o<<std::setprecision(12);
    auto time=[&](double x){if(x<0)o<<"null";else o<<x;};
    o<<"{\"candidate\":"<<(model?f.model_json(*model):"null")<<",\"gain\":"<<gain.covered<<",\"observations\":"<<gain.observed
     <<",\"status\":"<<quote(status)<<",\"primary\":";targets_json(o,{primary});
    o<<",\"first_observation\":"<<(first_observation?"true":"false")<<",\"initial_block\":"<<initial_block<<",\"final_block\":"<<final_block
     <<",\"variables\":"<<variables<<",\"expansions\":"<<expansions<<",\"fallback_reason\":"<<quote(fallback_reason)
     <<",\"dependency_complete\":"<<(dependency_complete?"true":"false")<<",\"validated_candidates\":"<<validated_candidates
     <<",\"duplicates\":"<<duplicate_candidates<<",\"late_candidates\":"<<late_candidates<<",\"branch_changes\":"<<branch_changes
     <<",\"full_seconds\":"<<full_seconds<<",\"reserve_seconds\":"<<reserve_seconds<<",\"first_positive_seconds\":";time(first_positive_seconds);
    o<<",\"checkpoint_seconds\":";time(checkpoint_seconds);o<<",\"releases\":[";
    bool first=true;for(auto &r:releases){if(!first)o<<',';first=false;o<<"{\"variable\":"<<r.variable<<",\"name\":"<<quote(f.nodes[f.variables[r.variable]].expr.decl().name().str())<<",\"query\":"<<r.query<<",\"reason\":"<<quote(r.reason)<<'}';}
    o<<"],\"queries\":[";first=true;
    for(auto &q:queries) {
        if(!first)o<<',';
        first=false;o<<"{\"status\":"<<quote(q.status)<<",\"detail\":"<<quote(q.detail)<<",\"block_size\":"<<q.block_size
         <<",\"restricted\":"<<(q.restricted?"true":"false")<<",\"timeout_ms\":"<<q.timeout_ms<<",\"seconds\":"<<q.seconds<<",\"finished\":"<<q.finished
         <<",\"global_proof\":"<<(q.global_proof?"true":"false")<<",\"exclusion_core\":"<<(q.exclusion_core?"true":"false")<<",\"targets\":";targets_json(o,q.targets);
        o<<",\"core_targets\":";targets_json(o,q.core_targets);o<<",\"fixed_core\":[";
        for(size_t i=0;i<q.fixed_core.size();++i){if(i)o<<',';o<<q.fixed_core[i];}o<<"]}";
    }
    return o.str()+"]}";
}
}
