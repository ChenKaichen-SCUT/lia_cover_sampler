#include "search.hpp"
#include <algorithm>
#include <deque>

namespace lia {
std::vector<Target> TargetManager::related(const Coverage &coverage, const Assignment &direction,
    std::mt19937_64 &rng, size_t cap, Budget &budget, Stats &stats,
    const std::function<std::optional<Z>(int)> &alpha) {
    // Walk the reverse AST edges from changed variables, not random node IDs.
    // Keep the walk bounded; immutable reverse lists are reused on subsequent calls.
    std::vector<int> nodes; std::set<int> seen_nodes;
    for (size_t v=0;v<direction.size() && !budget.done();++v) if (direction[v]!=0) {
        std::deque<int> queue{f.variables[v]}; std::set<int> seen;
        for (size_t work=0;!queue.empty() && work<cap*4 && !budget.done();++work) {
            int node=queue.front();queue.pop_front();
            if (!seen.insert(node).second) continue;
            if (seen_nodes.insert(node).second) nodes.push_back(node);
            for (auto [parent,position]:f.nodes[node].parents) { (void)position;queue.push_back(parent); }
        }
        variable_observers[v].assign(seen.begin(),seen.end());
    }
    std::vector<std::pair<double,Target>> pool; std::set<Target> used;
    const size_t linked=cap>3 ? cap*3/4 : cap;
    for (int node:nodes) {
        if (budget.done() || pool.size()>=cap*4) break;
        const auto &description=f.nodes[node];
        if (description.expr.is_numeral() || description.expr.is_true() || description.expr.is_false()) continue;
        auto coefficient=description.boolean ? std::optional<Z>{} : alpha(node);
        bool changing=coefficient && *coefficient!=0;
        auto state=coverage.states[node];
        for (unsigned j=0;j<(description.boolean ? 1U : 4U) && !budget.done();++j) {
            unsigned bit_index=description.boolean ? 0 : j==0 ? 63 : j==1 ? 0 : rng()%64;
            uint64_t mask=uint64_t(1)<<bit_index;
            if ((state.zero&state.one)&mask) continue;
            bool desired=state.zero&mask ? true : state.one&mask ? false : bool(rng()&1);
            Target t{node,bit_index,desired};
            if (!used.insert(t).second || !eligible(t,coverage,budget.total_elapsed())) continue;
            // alpha==0 is retained: the observer's Reach may still change.
            double score=(changing ? 8 : 1)+(((state.zero|state.one)&mask) ? 4 : 0);
            if (description.boolean) score+=4;
            pool.push_back({score,t});
        }
    }
    std::stable_sort(pool.begin(),pool.end(),[](const auto &a,const auto &b){return a.first>b.first;});
    std::vector<Target> result;
    for (size_t i=0;i<std::min(linked,pool.size());++i) {
        result.push_back(pool[i].second);
        ++stats.counts[pool[i].first>=8 ? "target_source_direction" : "target_source_reach"];
    }
    // Separate random/rotating tail includes observers affected only through Reach.
    for (auto t:select(coverage,rng,cap,budget)) {
        if (result.size()>=cap) break;
        if (std::find(result.begin(),result.end(),t)==result.end()) {
            result.push_back(t);++stats.counts["target_source_random_rotation"];
        }
    }
    return result;
}
CandidateBatch Search::safe(const Assignment &seed,const Coverage &coverage,Budget &budget,
    std::mt19937_64 &rng,Stats &stats,const std::vector<Assignment> &history,
    const std::function<bool(const std::string &)> &emitted,CandidateLimits limits) {
    return options.streaming ? generate(false,seed,coverage,budget,rng,stats,history,emitted,limits) :
        safe_eager(seed,coverage,budget,rng,stats,history,emitted,limits);
}
CandidateBatch Search::line(const Assignment &seed,const Coverage &coverage,Budget &budget,
    std::mt19937_64 &rng,Stats &stats,const std::vector<Assignment> &history,
    const std::function<bool(const std::string &)> &emitted,CandidateLimits limits) {
    return options.streaming ? generate(true,seed,coverage,budget,rng,stats,history,emitted,limits) :
        line_eager(seed,coverage,budget,rng,stats,history,emitted,limits);
}
CandidateBatch Search::generate(bool line_mode,const Assignment &seed,const Coverage &coverage,Budget &budget,
    std::mt19937_64 &rng,Stats &stats,const std::vector<Assignment> &history,
    const std::function<bool(const std::string &)> &emitted,CandidateLimits limits,
    const std::function<bool(const CandidateBatch &,bool)> &batch_boundary,unsigned batch_candidates) {
    CandidateBatch result; const auto begin=Clock::now();
    CandidateBatch pending;
    bool stopped=false,notified=false,pending_budget_failure=false;
    if(batch_boundary && (!batch_candidates || line_mode))
        throw std::invalid_argument("prepared batches require a positive safe candidate quota");
    auto notify_batch=[&](bool terminal) {
        if(!batch_boundary) return;
        pending.budget_exhausted=pending_budget_failure || budget.done();
        pending.attempt_limited=pending.attempts>=options.attempt_cap && pending.scored<batch_candidates;
        pending.status=pending.budget_exhausted?"budget_exhausted":pending.model?"candidate":"no_candidate";
        stopped=!batch_boundary(pending,terminal);
        notified=true;pending=CandidateBatch{};pending_budget_failure=false;
    };
    DiagnosticUnit batch(stats,diagnostic_trace,options.diagnostics,line_mode?"line":"safe","batch",line_mode?"structural_line":"safe_region");
    Timer action_path(stats,line_mode ? "line_analysis" : "safe_generation");
    const std::string prefix=line_mode ? "line_" : "safe_";
    const unsigned dc=std::min(options.direction_cap,limits.directions);
    const unsigned cc=std::min(options.candidate_cap,limits.candidates);
    // Prepared calls own several original-sized batches. The caller supplies the
    // remaining whole-polish bound; each batch still has the original attempt cap.
    const unsigned ac=batch_boundary?limits.attempts:std::min(options.attempt_cap,limits.attempts);
    auto full=[&]{return stopped || budget.done() || result.scored>=cc || result.attempts>=ac;};
    Observation observation;
    {
        Timer evaluation_timer(stats,"preparation_base_evaluation_seconds");
        ++stats.counts["preparation_base_evaluations"];
        observation=f.evaluate(seed);
    }
    std::unique_ptr<Region> region;
    DirectionSystem system;system.width=seed.size();
    if (line_mode) {
        if (!syntax) syntax=std::make_unique<AffineSyntax>(f,options.line_limits);
        if (!structural || !structural->complete) structural=structural_equalities(f,*syntax,budget);
        stats.counts["structural_collection_complete"]=structural->complete;
        system.scope="structural_line";system.equations=structural->rows;
        for(size_t i=0;i<seed.size();++i) if(f.nodes[f.variables[i]].boolean) system.booleans.insert(i);
    } else {
        {
            Timer region_timer(stats,"preparation_region_seconds");
            ++stats.counts["preparation_region_builds"];
            region=std::make_unique<Region>(f,seed,observation,budget);
            if(region->supported) {
                system.equations=region->eq;
                for (auto [i,v]:region->boolean) {(void)v;system.booleans.insert(i);}
            }
        }
        if (!region->supported) {
            result.status="unsupported";
            result.budget_exhausted=budget.done();
            batch.event("batch",budget.done()?"DIRECTION_GENERATION_INCOMPLETE":"FEASIBILITY_UNSUPPORTED","region",
                std::chrono::duration<double>(Clock::now()-begin).count(),diagnostic_budget(budget,false,false,false));
            notify_batch(true);return result;
        }
    }
    if(diagnostic_boundary) diagnostic_boundary("region_prepared");
    struct State {
        Direction direction;
        Budget slice;
        std::unique_ptr<LineAnalyzer> analyzer;
        std::vector<Interval> ranges;
        std::vector<Target> goals;
        size_t next=0;
        unsigned candidates=0;
        bool exact=false;
        std::unique_ptr<DiagnosticUnit> diagnostic;
    };
    std::vector<std::unique_ptr<State>> directions;
    const auto base_key=f.key(seed);
    std::set<std::string> scored{base_key};
    auto arm=[&](State &state) {
        state.slice.start=Clock::now();state.slice.parent=&budget;state.slice.label="direction";
        state.slice.seconds=std::min(budget.left(),std::max(.001,budget.seconds/std::max(1U,std::min(dc,4U))));
    };
    auto attempt_once=[&](size_t i) {
        auto &state=*directions[i];
        if (full() || state.next>=state.goals.size()) return;
        arm(state);Timer per_source(stats,prefix+"direction_"+state.direction.source+"_seconds");
        auto target=state.goals[state.next++];
        const auto target_start=Clock::now();
        bool witness=false,constructed=false,validated=false;
        std::string detail=diagnostic_trace ? ",\"target_node\":"+std::to_string(target.node)+",\"target_bit\":"+
            std::to_string(target.bit)+",\"target_desired\":"+(target.desired?"true":"false") : "";
        auto outcome=[&](const std::string &code,const std::string &stage,const std::string &extra="") {
            if(code=="TARGET_ANALYSIS_BUDGET_EXCEEDED") {
                pending_budget_failure=true;result.budget_exhausted=true;
            }
            state.diagnostic->event("target",code,stage,std::chrono::duration<double>(Clock::now()-target_start).count(),
                diagnostic_trace ? detail+extra+diagnostic_budget(state.slice,witness,constructed,validated) : "",true);
        };
        ++result.attempts;++result.direction_attempts[i];++stats.counts["candidate_attempts"];
        if(batch_boundary) ++pending.attempts;
        ++stats.counts["direction_"+state.direction.source+"_attempts"];
        std::optional<Z> z;
        try {
            if (line_mode) {
                auto domain=f.nodes[target.node].boolean ? state.analyzer->boolean_target(target) :
                                                         state.analyzer->observable(target.node);
                if (domain.status!=LineStatus::Exact) {
                    ++stats.counts["line_target_"+line_status_name(domain.status)];
                    outcome(domain.status==LineStatus::BudgetExceeded?"TARGET_ANALYSIS_BUDGET_EXCEEDED":"TARGET_UNSUPPORTED","observation");return;
                }
                if(domain.domain.without(0).intervals.empty()) {
                    bool reach_empty=!f.nodes[target.node].boolean || state.analyzer->last_observable_empty;
                    outcome(reach_empty?"REACH_EMPTY":"BIT_VALUE_UNREACHABLE","observation",
                        ",\"proof_scope\":\"current_nonzero_line\"");return;
                }
                if(f.nodes[target.node].boolean) z=line_bit_witness(domain.domain,0,0,0,false,rng);
                else {
                    auto [alpha,beta]=state.analyzer->numeric(target.node);
                    if(alpha==0 && bit(beta,target.bit)==target.desired) ++stats.counts["line_constant_reach_opportunities"];
                    z=line_bit_witness(domain.domain,alpha,beta,target.bit,target.desired,rng,options.random);
                }
            } else {
                if (f.nodes[target.node].boolean) {outcome("TARGET_UNSUPPORTED","target_value");return;}
                auto affine=region->linear(target.node,false);
                Z alpha=affine.dot(state.direction.delta),beta=affine.at(seed);
                LineDomain domain(state.ranges);
                z=line_bit_witness(domain,alpha,beta,target.bit,target.desired,rng,options.random);
            }
        } catch(const LineFailure &error) {
            ++stats.counts[prefix+"target_"+line_status_name(error.status)];
            outcome(error.status==LineStatus::BudgetExceeded?"TARGET_ANALYSIS_BUDGET_EXCEEDED":"TARGET_UNSUPPORTED","target_value");return;
        } catch(const std::domain_error &) {
            ++stats.counts[prefix+"target_unsupported"];
            outcome(state.slice.done()?"TARGET_ANALYSIS_BUDGET_EXCEEDED":"TARGET_UNSUPPORTED","target_value");return;
        }
        witness=z.has_value();
        if(z) {
            if(diagnostic_trace) detail+=",\"z\":"+quote(z->get_str());
            state.diagnostic->event("stage","WITNESS_FOUND","witness",0,detail);
            if(diagnostic_boundary) diagnostic_boundary("witness_found");
        }
        if(state.slice.done()) {outcome("TARGET_ANALYSIS_BUDGET_EXCEEDED",z?"before_construction":"target_value");return;}
        if(!z) {outcome("BIT_VALUE_UNREACHABLE","target_value",
            ",\"proof_scope\":"+quote(line_mode?"feasible_and_observable_nonzero_line":"numeric_only_safe_region"));return;}
        Assignment candidate=seed;
        for(size_t j=0;j<seed.size();++j) candidate[j]+=*z*state.direction.delta[j];
        constructed=true;
        if(diagnostic_boundary) diagnostic_boundary("model_constructed");
        if(state.slice.done()) state.diagnostic->event("stage","BUDGET_OBSERVED","before_deduplication",0,
            diagnostic_budget(state.slice,witness,constructed,validated));
        auto key=f.key(candidate);
        bool same=key==base_key,in_batch=scored.count(key),in_action=action_scored.count(key),in_output=emitted(key);
        // Preserve the original short-circuit insertion semantics and fixed primary priority.
        bool duplicate=!scored.insert(key).second || in_output || !action_scored.insert(key).second;
        if(duplicate) {
            ++result.duplicates;++stats.counts["duplicate_candidates"];
            if(batch_boundary) ++pending.duplicates;
            std::string flags=",\"duplicate_flags\":{\"base\":"+std::string(same?"true":"false")+
                ",\"batch\":"+(in_batch?"true":"false")+",\"action\":"+(in_action?"true":"false")+
                ",\"committed\":"+(in_output?"true":"false")+"},\"exhaustiveness\":\"selected_witness_only\"";
            outcome(diagnostic_duplicate_reason(same,in_batch,in_action,in_output),"deduplication",flags);return;
        }
        state.diagnostic->event("stage","NEW_ASSIGNMENT","deduplication",0,detail);
        last_candidate=candidate;auto observed=f.evaluate(candidate);
        if(!f.validate(candidate) || (line_mode && !options.random &&
            (!observed.visited[target.node] || bit(observed.values[target.node],target.bit)!=target.desired))) {
            last_failure="{\"scope\":"+quote(system.scope)+",\"seed\":"+f.model_json(seed)+
                ",\"candidate\":"+f.model_json(candidate)+",\"target\":"+quote(target.key())+
                ",\"snapshot\":"+coverage.json()+"}";
            throw std::logic_error("streamed candidate violates original formula or line target");
        }
        validated=true;
        auto gain=coverage.gain(observed);
        const bool reached=observed.visited[target.node];
        if(reached && !options.random && bit(observed.values[target.node],target.bit)!=target.desired) {
            last_failure="{\"reason\":\"target_value_mismatch\",\"candidate\":"+f.model_json(candidate)+"}";
            throw std::logic_error("diagnostic target arithmetic assertion mismatch");
        }
        const auto mask=uint64_t(1)<<target.bit;
        const auto old=coverage.states[target.node];
        bool opposite=target.desired ? (old.zero&mask) : (old.one&mask);
        bool missing=target.desired ? !(old.one&mask) : !(old.zero&mask);
        if(reached && opposite && missing && bit(observed.values[target.node],target.bit)==target.desired && !gain.covered) {
            last_failure="{\"reason\":\"coverage_contract\",\"candidate\":"+f.model_json(candidate)+",\"snapshot\":"+coverage.json()+"}";
            throw std::logic_error("observed opposite missing bit must add coverage");
        }
        if(!line_mode && !reached) state.diagnostic->event("stage","NUMERIC_HIT_NOT_OBSERVED","observation",0,detail);
        outcome(gain.covered?"NEW_COVERAGE":gain.observed?"NEW_OBSERVATION_ONLY":"ZERO_INCREMENT","coverage",
            ",\"gain\":"+std::to_string(gain.covered)+",\"new_observations\":"+std::to_string(gain.observed));
        if(candidate_observer) candidate_observer(candidate,observed,gain);
        ++result.scored;++stats.counts["unique_candidates_scored"];
        if(!state.candidates++) ++stats.counts[prefix+"directions_produced_candidates"];
        ++stats.counts[prefix+"direction_"+state.direction.source+"_successes"];
        stats.counts["direction_"+state.direction.source+"_candidate_gain"]+=gain.covered;
        double elapsed=std::chrono::duration<double>(Clock::now()-begin).count();
        if(result.first_valid_seconds<0) result.first_valid_seconds=elapsed;
        if(gain.covered && result.first_positive_seconds<0) result.first_positive_seconds=elapsed;
        if(!result.model || gain.covered>result.gain.covered ||
           (gain.covered==result.gain.covered && gain.observed>result.gain.observed)) {
            result.model=candidate;result.gain=gain;result.direction_source=state.direction.source;
        }
        if(batch_boundary) {
            ++pending.scored;
            if(!pending.model || gain.covered>pending.gain.covered ||
               (gain.covered==pending.gain.covered && gain.observed>pending.gain.observed)) {
                pending.model=candidate;pending.gain=gain;pending.direction_source=state.direction.source;
            }
            if(pending.first_valid_seconds<0) pending.first_valid_seconds=elapsed;
            if(gain.covered && pending.first_positive_seconds<0) pending.first_positive_seconds=elapsed;
            if(pending.scored>=batch_candidates) notify_batch(false);
        }
        if(line_mode && !budget.done()) {
            if(!region) region=std::make_unique<Region>(f,seed,observation,budget);
            if(region->supported && !region->contains(candidate)) ++stats.counts["line_cross_branch_candidate"];
        }
    };
    auto attempt=[&](size_t i) {
        attempt_once(i);
        if(batch_boundary && !stopped && pending.attempts>=options.attempt_cap)
            notify_batch(false);
    };
    auto prepare=[&](const Direction &direction) {
        if(full()) return false;
        if(direction_boundary) direction_boundary(directions.size());
        if(full()) return false;
        const auto preparation_start=Clock::now();
        ++stats.counts[prefix+"directions_considered"];
        auto state=std::make_unique<State>();state->direction=direction;arm(*state);
        state->diagnostic=std::make_unique<DiagnosticUnit>(stats,diagnostic_trace,options.diagnostics,
            line_mode?"line":"safe",direction.source,system.scope);
        if(diagnostic_trace) {
            z3::solver scope(f.ctx);scope.add(line_mode?f.root:region->expression());
            state->diagnostic->context(f,seed,direction.delta,scope.to_smt2(),system.signature());
        }
        state->diagnostic->event("stage","DIRECTION_GENERATED","direction");
        if((diagnostic_trace || diagnostic_directions) && !system.kernel(direction.delta)) throw std::logic_error("diagnostic direction violates declared equality scope");
        state->diagnostic->event("stage","DIRECTION_VALID","direction");
        auto phase=[&](const std::string &code,const std::string &stage,const std::string &extra="") {
            if(code=="FEASIBILITY_BUDGET_EXCEEDED" || (code=="FEASIBILITY_NOT_CHECKED" && state->slice.done())) {
                pending_budget_failure=true;result.budget_exhausted=true;
            }
            state->diagnostic->event("phase",code,stage,std::chrono::duration<double>(Clock::now()-preparation_start).count(),extra,code!="NONZERO_FEASIBLE");
        };
        auto alpha=[&](int node)->std::optional<Z> {
            try {return line_mode ? syntax->linear(node,state->slice).dot(direction.delta) : region->linear(node,false).dot(direction.delta);}
            catch(const LineFailure &) {return {};}
            catch(const std::domain_error &) {return {};}
        };
        {
            Timer targets_timer(stats,"preparation_target_pool_seconds");
            ++stats.counts["preparation_target_pool_builds"];
            state->goals=diagnostic_targets ? diagnostic_targets() : options.matching ?
                (options.directional_targets ? targets.related(coverage,direction.delta,rng,16,state->slice,stats,alpha) :
                 targets.select(coverage,rng,32,state->slice,&direction.delta)) : active_targets(f,coverage,rng,32);
        }
        if(!state->goals.empty() && !state->slice.done()) {
            Timer feasibility_timer(stats,"preparation_feasibility_seconds");
            ++stats.counts["preparation_feasibility_builds"];
            ++stats.counts[prefix+"directions_analyzed"];
            if(line_mode) {
                state->analyzer=std::make_unique<LineAnalyzer>(f,seed,state->direction.delta,*syntax,state->slice);
                auto feasible=state->analyzer->feasible();
                ++stats.counts["line_domain_"+line_status_name(feasible.status)];
                if(feasible.status!=LineStatus::Exact) {
                    ++stats.counts[feasible.status==LineStatus::Unsupported ? "line_analysis_unsupported" : "line_analysis_timeout"];
                    phase(feasible.status==LineStatus::Unsupported?"FEASIBILITY_UNSUPPORTED":"FEASIBILITY_BUDGET_EXCEEDED","feasibility",diagnostic_budget(state->slice,false,false,false));
                    // This direction may have exhausted its slice. Never discard earlier models.
                    state->goals.clear();
                } else {
                    state->exact=true;
                    stats.counts["line_nonzero_components"]+=feasible.domain.without(0).intervals.size();
                    bool origin=feasible.domain.without(0).intervals.empty();
                    phase(origin?"ORIGIN_ONLY":"NONZERO_FEASIBLE","feasibility",
                        diagnostic_trace?",\"domain\":"+feasible.domain.json():"");
                    if(origin) state->goals.clear();
                }
            } else {
                auto interval=region->interval(seed,direction.delta);
                state->ranges=LineDomain({interval}).without(0).intervals;
                state->exact=true;
                phase(state->ranges.empty()?"ORIGIN_ONLY":"NONZERO_FEASIBLE","feasibility",
                    diagnostic_trace?",\"domain\":"+LineDomain({interval}).json():"");
                if(state->ranges.empty()) state->goals.clear();
            }
        } else {
            if(state->goals.empty()) state->diagnostic->event("stage","TARGET_POOL_EMPTY","target_pool");
            phase("FEASIBILITY_NOT_CHECKED",state->slice.done()?"target_pool_budget":"target_pool_empty",diagnostic_budget(state->slice,false,false,false));
            state->goals.clear();
        }
        directions.push_back(std::move(state));result.direction_attempts.push_back(0);
        stats.times[prefix+"direction_"+direction.source+"_seconds"]+=
            std::chrono::duration<double>(Clock::now()-preparation_start).count();
        // Complete the first bounded attempt before preparing another direction.
        attempt(directions.size()-1);
        if(!options.fair) while(!full() && directions.back()->next<directions.back()->goals.size())
            attempt(directions.size()-1);
        return !full();
    };
    const auto no_direction_before=stats.counts["direction_nullspace_zero"];
    if(diagnostic_directions) {
        for(const auto &direction:*diagnostic_directions) if(!prepare(direction)) break;
    } else if(options.quotas || line_mode)
        quota_directions(system,history,seed,budget,rng,stats,dc,options.quotas,prepare);
    else for(auto &d:lia::directions(*region,rng,stats,dc)) {
        if(!prepare({d,"legacy",system.scope,system.signature()})) break;
    }
    for(unsigned round=1;round<32 && !full();++round) {
        bool remaining=false;
        for(size_t i=0;i<directions.size() && !full();++i) {
            remaining|=directions[i]->next<directions[i]->goals.size();attempt(i);
        }
        if(!remaining) break;
    }
    // A final partial batch is real work even when no candidate was found.
    // Never retry an incomplete preparation after returning from this stack.
    if(batch_boundary && !stopped && (pending.attempts || pending.scored || !notified || pending_budget_failure || budget.done()))
        notify_batch(true);
    for(const auto &state:directions) {
        if(line_mode && state->exact && !state->candidates) ++stats.counts["line_exact_but_no_candidate"];
        if(state->next<state->goals.size()) state->diagnostic->event("stage",budget.done()?"BUDGET_EXIT":"BOUNDED_SEARCH_INCOMPLETE","remaining_targets",0,diagnostic_budget(budget,false,false,false));
        state->diagnostic->finish("NO_TARGET_ATTEMPT");
    }
    if(directions.empty()) {
        const bool proved=stats.counts["direction_nullspace_zero"]>no_direction_before;
        std::string proof;
        if(proved && diagnostic_trace) {
            z3::solver query(f.ctx);z3::expr_vector delta(f.ctx),nonzero(f.ctx);
            for(size_t i=0;i<system.width;++i) {
                delta.push_back(f.ctx.int_const(("__diagnostic_delta_"+std::to_string(i)).c_str()));
                nonzero.push_back(delta[i]!=0);
            }
            for(const auto &row:system.equations) {
                auto sum=f.ctx.int_val(0);
                for(const auto &[i,v]:row.a) sum=sum+f.ctx.int_val(v.get_str().c_str())*delta[i];
                query.add(sum==0);
            }
            for(int i:system.booleans) query.add(delta[i]==0);
            query.add(z3::mk_or(nonzero));proof=",\"proof_query\":"+quote(query.to_smt2());
        }
        batch.event("batch",proved?"NO_DIRECTION_PROVED":"DIRECTION_GENERATION_INCOMPLETE","direction_generation",
            std::chrono::duration<double>(Clock::now()-begin).count(),
            diagnostic_budget(budget,false,false,false)+proof);
    }
    if(result.model && line_mode && region && region->supported && !region->contains(*result.model))
        ++stats.counts["line_cross_branch_moves"];
    if(result.first_valid_seconds>=0) {
        stats.times[prefix+"time_to_first_valid_candidate"]+=result.first_valid_seconds;
        ++stats.counts[prefix+"batches_with_valid_candidate"];
    }
    if(result.first_positive_seconds>=0) {
        stats.times[prefix+"time_to_first_positive_gain_candidate"]+=result.first_positive_seconds;
        ++stats.counts[prefix+"batches_with_positive_candidate"];
    }
    result.status=result.model ? "candidate" : budget.done() ? "budget_exhausted" : "no_candidate";
    result.budget_exhausted=result.budget_exhausted || budget.done();
    batch.event("batch",result.model?(result.gain.covered?"NEW_COVERAGE":result.gain.observed?"NEW_OBSERVATION_ONLY":"ZERO_INCREMENT"):
        budget.done()?"BUDGET_EXIT":"NO_NEW_CANDIDATE_FOUND","terminal",std::chrono::duration<double>(Clock::now()-begin).count(),
        ",\"proof_scope\":\"bounded_search_only\"");
    return result;
}
}
