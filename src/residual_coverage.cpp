#include "residual_coverage.hpp"
#include <algorithm>
#include <bit>
#include <cmath>
#include <fstream>
#include <regex>

namespace lia {
bool ResidualTargetWindow::missing_opposite(const Coverage &coverage, Target t) {
    if(t.node<0 || size_t(t.node)>=coverage.states.size() || t.bit>=64) return false;
    auto s=coverage.states.at(t.node);const uint64_t mask=uint64_t(1)<<t.bit;
    return ((t.desired?s.zero:s.one)&mask) && !((t.desired?s.one:s.zero)&mask);
}
ResidualWindow ResidualTargetWindow::select(const Coverage &coverage,uint64_t version,const Global &global,Budget &budget) {
    ResidualWindow out;out.coverage_version=version;
    std::set<std::pair<int,unsigned>> used;
    auto offer=[&](int n,unsigned bit_index) {
        ++out.probes;auto &node=f.nodes[n];
        if(node.expr.is_numeral() || node.expr.is_true() || node.expr.is_false()) return;
        if(node.boolean && bit_index) return;
        auto s=coverage.states[n];const uint64_t mask=uint64_t(1)<<bit_index;
        if(!((s.zero^s.one)&mask)) return;
        Target t{n,bit_index,bool(s.zero&mask)};
        if(global.unreachable(t) || !used.insert({n,bit_index}).second) return;
        out.targets.push_back(t);
    };
    // Same bounded random pool and rotating tail for both modes. Never traverse
    // the whole AST×64 product just to obtain a target window.
    for(unsigned i=0;i<256 && out.targets.size()<capacity && !budget.done();++i) {
        int n=rng()%f.nodes.size();
        offer(n,f.nodes[n].boolean?0:(i%3==0?rng()%8:rng()%64));
    }
    const size_t cells=f.nodes.size()*64;
    for(unsigned i=0;i<512 && out.targets.size()<capacity && !budget.done();++i) {
        size_t cell=cursor++%cells;offer(cell/64,cell%64);
    }
    out.complete=!budget.done();return out;
}
bool OnlineCoverage::restore(const std::vector<Assignment> &prefix,const Assignment &seed) {
    if(initialized) throw std::logic_error("online state already restored");
    auto typed=[&](const Assignment &m) {
        if(m.size()!=f.variables.size()) throw std::invalid_argument("incomplete input model");
        for(size_t v=0;v<m.size();++v)
            if(f.nodes[f.variables[v]].boolean && m[v]!=0 && m[v]!=1)
                throw std::invalid_argument("non-Boolean input value");
    };
    for(const auto &m:prefix) {
        typed(m);
        if(!f.validate(m)) throw std::logic_error("invalid formal prefix");
        if(!emitted.insert(f.key(m)).second) throw std::logic_error("duplicate formal prefix");
        coverage.accept(f.evaluate(m));
    }
    typed(seed);if(!f.validate(seed)) throw std::logic_error("invalid input seed");
    bool added=emitted.insert(f.key(seed)).second;
    if(added) coverage.accept(f.evaluate(seed));
    initial_covered_=coverage.covered();initialized=true;return added;
}
bool OnlineCoverage::duplicate(const Assignment &model) const {return emitted.count(f.key(model));}
std::optional<ResidualCandidate> OnlineCoverage::prepare(const Assignment &model,const ResidualWindow &window,
                                                       const std::string &origin,unsigned query) const {
    if(!initialized || window.coverage_version!=version_) throw std::logic_error("stale coverage version");
    if(model.size()!=f.variables.size()) throw std::invalid_argument("incomplete candidate");
    for(unsigned v=0;v<model.size();++v)
        if(f.nodes[f.variables[v]].boolean && model[v]!=0 && model[v]!=1) throw std::invalid_argument("non-Boolean value");
    if(!f.validate(model)) throw std::logic_error("candidate violates original formula");
    if(duplicate(model)) return {};
    ResidualCandidate out;out.model=model;out.observation=f.evaluate(model);out.gain=coverage.gain(out.observation);
    out.coverage_version=version_;out.origin=origin;out.query=query;
    std::set<std::pair<int,unsigned>> identities;
    for(auto t:window.targets) {
        if(!identities.insert({t.node,t.bit}).second || !ResidualTargetWindow::missing_opposite(coverage,t))
            throw std::logic_error("window contains a duplicate/completed/first-observation target");
        out.window_hits+=out.observation.visited[t.node] && bit(out.observation.values[t.node],t.bit)==t.desired;
    }
    for(size_t i=0;i<coverage.states.size();++i) if(out.observation.visited[i]) {
        uint64_t mask=f.nodes[i].boolean?1:~uint64_t(0);
        out.first_observations+=std::popcount(mask&~(coverage.states[i].zero|coverage.states[i].one));
    }
    if(out.window_hits>out.gain.covered) throw std::logic_error("window hits exceed true marginal coverage");
    return out;
}
bool OnlineCoverage::commit(const ResidualCandidate &candidate,double finished,double deadline) {
    if(!std::isfinite(finished) || !std::isfinite(deadline) || finished<0 || finished>deadline || candidate.coverage_version!=version_ || duplicate(candidate.model)) return false;
    // Recompute before changing formal state; the caller's output transaction
    // uses an already checked immutable candidate. No internal query commits.
    if(!f.validate(candidate.model)) throw std::logic_error("commit violates original formula");
    auto observed=f.evaluate(candidate.model);auto gain=coverage.gain(observed);
    if(gain.covered!=candidate.gain.covered || gain.observed!=candidate.gain.observed)
        throw std::logic_error("stale candidate gain at commit");
    coverage.accept(observed);emitted.insert(f.key(candidate.model));++version_;++outputs_;return true;
}
ResidualRound ResidualGenerator::generate(bool cardinality,const OnlineCoverage &online,const Assignment &current,
    Budget &round,Stats &stats,const std::function<double(const ResidualCandidate &)> &checkpoint) {
    ResidualRound out;
    const double definition_start=global.registered_definition_seconds;
    double delivery_limit=round.seconds;
    for(auto ancestor=round.parent;ancestor;ancestor=ancestor->parent)
        delivery_limit=std::min(delivery_limit,ancestor->seconds+
            std::chrono::duration<double>(ancestor->start-round.start).count());
    out.reserve_seconds=std::min(round.left(),std::max(.01,round.left()*options.reserve_fraction));
    Budget work;work.start=round.start;work.seconds=std::max(0.,round.seconds-out.reserve_seconds);
    work.parent=&round;work.label="residual_search";
    {Timer timer(stats,"window_selection_seconds");out.window=window_override?window_override(online):windows.select(online.state(),online.version(),global,work);}
    unsigned query_index=0;std::string origin;unsigned requested=0,last_hits=0;bool scored=false;
    std::vector<Target> last_encoded;
    auto log_query=[&](const GlobalResult &q,const std::string &kind,double wall) {
        ++query_index;origin=kind;requested=q.requested_threshold;out.terminal=q.status;
        last_encoded=q.attempted;scored=false;
        std::ostringstream row;row.precision(17);
        row<<"{\"query\":"<<query_index<<",\"kind\":"<<quote(kind)<<",\"status\":"<<quote(q.status)
           <<",\"reason\":"<<quote(q.detail)<<",\"threshold\":"<<q.requested_threshold
           <<",\"encoded_window\":"<<residual_targets_json(q.attempted)
           <<",\"encoding_complete\":"<<(kind=="global_pack" && q.attempted.empty()?"null":q.target_encoding_complete?"true":"false")
           <<",\"encoding_scope\":"<<quote(kind=="global_pack"?"queried_pack_only; unqueried preparation completion unknown":"threshold_window")
           <<",\"selected_activations\":"<<q.selected_activations<<",\"threshold_proof\":"<<(q.threshold_proof?"true":"false")
           <<",\"proof_scope\":"<<quote(q.threshold_proof?"original_formula_window_threshold":"not_threshold_proof")
           <<",\"coverage_version\":"<<online.version()<<",\"timeout_ms\":"<<q.actual_timeout_ms
           <<",\"query_seconds\":"<<q.elapsed_seconds<<",\"wrapper_seconds\":"<<wall
           <<",\"wrapper_scope\":"<<quote(kind=="global_pack"?"since_previous_feedback_including_repair_preparation_and_scoring":"query_call")
           <<",\"finished_round_seconds\":"<<round.elapsed()<<'}';out.queries.push_back(row.str());
    };
    auto score=[&](const Assignment &model) -> Gain {
        if(boundary) boundary("before_validation");
        const auto start=Clock::now();
        std::optional<ResidualCandidate> candidate;
        {Timer validation(stats,"validation_scoring_seconds");candidate=online.prepare(model,out.window,origin,query_index);}
        if(!candidate) {++out.duplicates;return {};}
        {Timer recount(stats,"window_recount_seconds");last_hits=0;
         for(auto t:last_encoded) last_hits+=candidate->observation.visited[t.node] && bit(candidate->observation.values[t.node],t.bit)==t.desired;}
        scored=true;
        if(cardinality && origin=="residual_cardinality" && last_hits<requested)
            throw std::logic_error("threshold witness fails original-model reevaluation");
        bool timely=!round.done();
        bool better=!out.best || candidate->gain.covered>out.best->gain.covered ||
            (candidate->gain.covered==out.best->gain.covered && candidate->gain.observed>out.best->gain.observed);
        bool saved=false;
        if(timely && better) {
            if(boundary) boundary("before_checkpoint");
            {Timer writing(stats,"checkpoint_write_seconds");
             candidate->checkpoint_completed=checkpoint?checkpoint(*candidate):round.elapsed();}
            // The callback reports actual flush completion, never the query's timestamp.
            timely=std::isfinite(candidate->checkpoint_completed) && candidate->checkpoint_completed>=0 &&
                   candidate->checkpoint_completed<=delivery_limit;
            if(timely) {out.best=*candidate;saved=true;}
        }
        if(!timely) ++out.late;
        std::ostringstream row;row.precision(17);
        row<<"{\"query\":"<<query_index<<",\"window_hits\":"<<candidate->window_hits
           <<",\"actual_marginal_gain\":"<<candidate->gain.covered<<",\"new_observation_count\":"<<candidate->gain.observed
           <<",\"first_observations\":"<<candidate->first_observations<<",\"timely\":"<<(timely?"true":"false")
           <<",\"checkpoint_saved\":"<<(saved?"true":"false")<<",\"full_assessment_seconds\":"
           <<std::chrono::duration<double>(Clock::now()-start).count()<<'}';out.assessments.push_back(row.str());
        return timely?candidate->gain:Gain{};
    };
    if(!out.window.targets.empty() && !work.done()) {
        if(cardinality) {
            unsigned k=1;
            for(unsigned i=0;i<options.target_calls && !work.done();++i) {
                auto start=Clock::now();
                auto mock=threshold_override?threshold_override(i,out.window,k):std::optional<GlobalResult>{};
                auto result=mock?*mock:global.query_at_least(out.window.targets,k,online.version(),work,stats);
                result.requested_threshold=k;++out.target_calls;
                log_query(result,"residual_cardinality",std::chrono::duration<double>(Clock::now()-start).count());
                if(!result.model) break;
                score(*result.model);
                // Count actual original observations independently of selection labels.
                // The scorer already evaluated the complete model, even if it was worse.
                if(!scored || round.done()) break;
                unsigned c=last_hits;
                if(c<k) throw std::logic_error("encoded threshold not met");
                if(c==result.attempted.size()) break;
                k=c+1;
            }
        } else {
            auto previous=Clock::now();
            global.repair(out.window.targets,work,stats,score,[&](const GlobalResult &q) {
                ++out.target_calls;auto now=Clock::now();
                log_query(q,"global_pack",std::chrono::duration<double>(now-previous).count());previous=Clock::now();
            },options.target_calls,2);
        }
    }
    if((!out.best || !out.best->gain.covered) && !work.done()) {
        const auto start=Clock::now();auto result=global.explore(current,work,stats,0xffffffffU,true);
        ++out.exploration_calls;
        log_query(result,"exploration",std::chrono::duration<double>(Clock::now()-start).count());
        if(result.model) score(*result.model);
    }
    stats.times["registered_definition_seconds"]=global.registered_definition_seconds-definition_start;
    out.full_seconds=round.elapsed();return out;
}
std::string residual_targets_json(const std::vector<Target> &targets) {
    std::ostringstream out;out<<'[';bool first=true;
    for(auto t:targets) {if(!first)out<<',';first=false;out<<'['<<t.node<<','<<t.bit<<','<<(t.desired?"true":"false")<<']';}
    return out.str()+']';
}
std::vector<Assignment> read_residual_values(const Formula &f,const std::string &path) {
    std::ifstream in(path);if(!in) throw std::runtime_error("missing snapshot values: "+path);
    std::vector<Assignment> models;std::string line;
    const std::regex integer("-?(0|[1-9][0-9]*)");
    while(std::getline(in,line)) {
        std::istringstream row(line);Assignment m;std::string token;
        for(size_t v=0;v<f.variables.size();++v) {
            if(!(row>>token)) throw std::invalid_argument("missing typed value");
            if(f.nodes[f.variables[v]].boolean) {
                if(token!="true" && token!="false") throw std::invalid_argument("Boolean value requires true/false");
                m.emplace_back(token=="true"?1:0);
            } else {
                if(!std::regex_match(token,integer)) throw std::invalid_argument("integer value requires exact decimal");
                m.emplace_back(token);
            }
        }
        if(row>>token) throw std::invalid_argument("extra snapshot value");
        models.push_back(std::move(m));
    }
    return models;
}
} // namespace lia
