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
    const std::function<bool(const std::string &)> &emitted,CandidateLimits limits) {
    CandidateBatch result; const auto begin=Clock::now();
    Timer action_path(stats,line_mode ? "line_analysis" : "safe_generation");
    const std::string prefix=line_mode ? "line_" : "safe_";
    const unsigned dc=std::min(options.direction_cap,limits.directions);
    const unsigned cc=std::min(options.candidate_cap,limits.candidates);
    const unsigned ac=std::min(options.attempt_cap,limits.attempts);
    auto full=[&]{return budget.done() || result.scored>=cc || result.attempts>=ac;};
    auto observation=f.evaluate(seed);
    std::unique_ptr<Region> region;
    DirectionSystem system;system.width=seed.size();
    if (line_mode) {
        if (!syntax) syntax=std::make_unique<AffineSyntax>(f,options.line_limits);
        if (!structural || !structural->complete) structural=structural_equalities(f,*syntax,budget);
        stats.counts["structural_collection_complete"]=structural->complete;
        system.scope="structural_line";system.equations=structural->rows;
        for(size_t i=0;i<seed.size();++i) if(f.nodes[f.variables[i]].boolean) system.booleans.insert(i);
    } else {
        region=std::make_unique<Region>(f,seed,observation,budget);
        if (!region->supported) {result.status="unsupported";return result;}
        system.equations=region->eq;
        for (auto [i,v]:region->boolean) {(void)v;system.booleans.insert(i);}
    }
    struct State {
        Direction direction;
        Budget slice;
        std::unique_ptr<LineAnalyzer> analyzer;
        std::vector<Interval> ranges;
        std::vector<Target> goals;
        size_t next=0;
        unsigned candidates=0;
        bool exact=false;
    };
    std::vector<std::unique_ptr<State>> directions;
    std::set<std::string> scored{f.key(seed)};
    auto arm=[&](State &state) {
        state.slice.start=Clock::now();state.slice.parent=&budget;
        state.slice.seconds=std::min(budget.left(),std::max(.001,budget.seconds/std::max(1U,std::min(dc,4U))));
    };
    auto attempt=[&](size_t i) {
        auto &state=*directions[i];
        if (full() || state.next>=state.goals.size()) return;
        arm(state);Timer per_source(stats,prefix+"direction_"+state.direction.source+"_seconds");
        auto target=state.goals[state.next++];
        ++result.attempts;++result.direction_attempts[i];++stats.counts["candidate_attempts"];
        ++stats.counts["direction_"+state.direction.source+"_attempts"];
        std::optional<Z> z;
        try {
            if (line_mode) {
                auto domain=f.nodes[target.node].boolean ? state.analyzer->boolean_target(target) :
                                                         state.analyzer->observable(target.node);
                if (domain.status!=LineStatus::Exact) {
                    ++stats.counts["line_target_"+line_status_name(domain.status)];return;
                }
                if(f.nodes[target.node].boolean) z=line_bit_witness(domain.domain,0,0,0,false,rng);
                else {
                    auto [alpha,beta]=state.analyzer->numeric(target.node);
                    if(alpha==0 && bit(beta,target.bit)==target.desired) ++stats.counts["line_constant_reach_opportunities"];
                    z=line_bit_witness(domain.domain,alpha,beta,target.bit,target.desired,rng,options.random);
                }
            } else {
                if (f.nodes[target.node].boolean) return;
                auto affine=region->linear(target.node,false);
                Z alpha=affine.dot(state.direction.delta),beta=affine.at(seed);
                LineDomain domain(state.ranges);
                z=line_bit_witness(domain,alpha,beta,target.bit,target.desired,rng,options.random);
            }
        } catch(const LineFailure &error) {++stats.counts[prefix+"target_"+line_status_name(error.status)];return;}
          catch(const std::domain_error &) {++stats.counts[prefix+"target_unsupported"];return;}
        if(!z || state.slice.done()) return;
        Assignment candidate=seed;
        for(size_t j=0;j<seed.size();++j) candidate[j]+=*z*state.direction.delta[j];
        auto key=f.key(candidate);
        if(!scored.insert(key).second || emitted(key) || !action_scored.insert(key).second) {
            ++result.duplicates;++stats.counts["duplicate_candidates"];return;
        }
        last_candidate=candidate;auto observed=f.evaluate(candidate);
        if(!f.validate(candidate) || (line_mode && !options.random &&
            (!observed.visited[target.node] || bit(observed.values[target.node],target.bit)!=target.desired))) {
            last_failure="{\"scope\":"+quote(system.scope)+",\"seed\":"+f.model_json(seed)+
                ",\"candidate\":"+f.model_json(candidate)+",\"target\":"+quote(target.key())+
                ",\"snapshot\":"+coverage.json()+"}";
            throw std::logic_error("streamed candidate violates original formula or line target");
        }
        auto gain=coverage.gain(observed);
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
        if(line_mode && !budget.done()) {
            if(!region) region=std::make_unique<Region>(f,seed,observation,budget);
            if(region->supported && !region->contains(candidate)) ++stats.counts["line_cross_branch_candidate"];
        }
    };
    auto prepare=[&](const Direction &direction) {
        if(full()) return false;
        if(direction_boundary) direction_boundary(directions.size());
        if(full()) return false;
        const auto preparation_start=Clock::now();
        ++stats.counts[prefix+"directions_considered"];
        auto state=std::make_unique<State>();state->direction=direction;arm(*state);
        auto alpha=[&](int node)->std::optional<Z> {
            try {return line_mode ? syntax->linear(node,state->slice).dot(direction.delta) : region->linear(node,false).dot(direction.delta);}
            catch(const LineFailure &) {return {};}
            catch(const std::domain_error &) {return {};}
        };
        state->goals=options.matching ?
            (options.directional_targets ? targets.related(coverage,direction.delta,rng,16,state->slice,stats,alpha) :
             targets.select(coverage,rng,32,state->slice,&direction.delta)) : active_targets(f,coverage,rng,32);
        if(!state->goals.empty() && !state->slice.done()) {
            ++stats.counts[prefix+"directions_analyzed"];
            if(line_mode) {
                state->analyzer=std::make_unique<LineAnalyzer>(f,seed,state->direction.delta,*syntax,state->slice);
                auto feasible=state->analyzer->feasible();
                ++stats.counts["line_domain_"+line_status_name(feasible.status)];
                if(feasible.status!=LineStatus::Exact) {
                    ++stats.counts[feasible.status==LineStatus::Unsupported ? "line_analysis_unsupported" : "line_analysis_timeout"];
                    // This direction may have exhausted its slice. Never discard earlier models.
                    state->goals.clear();
                } else {
                    state->exact=true;
                    stats.counts["line_nonzero_components"]+=feasible.domain.without(0).intervals.size();
                    if(feasible.domain.without(0).intervals.empty()) state->goals.clear();
                }
            } else {
                auto interval=region->interval(seed,direction.delta);
                state->ranges=LineDomain({interval}).without(0).intervals;
                if(state->ranges.empty()) state->goals.clear();
            }
        } else state->goals.clear();
        directions.push_back(std::move(state));result.direction_attempts.push_back(0);
        stats.times[prefix+"direction_"+direction.source+"_seconds"]+=
            std::chrono::duration<double>(Clock::now()-preparation_start).count();
        // Complete the first bounded attempt before preparing another direction.
        attempt(directions.size()-1);
        return !full();
    };
    if(options.quotas || line_mode)
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
    for(const auto &state:directions)
        if(line_mode && state->exact && !state->candidates) ++stats.counts["line_exact_but_no_candidate"];
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
    return result;
}
}
