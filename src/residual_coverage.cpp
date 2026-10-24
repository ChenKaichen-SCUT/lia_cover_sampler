#include "residual_coverage.hpp"
#include <algorithm>
#include <bit>
#include <fstream>
#include <regex>

namespace lia {
bool ResidualTargetWindow::missing_opposite(const Coverage &coverage, Target t) {
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
    for(const auto &m:prefix) {
        if(!f.validate(m)) throw std::logic_error("invalid formal prefix");
        if(!emitted.insert(f.key(m)).second) throw std::logic_error("duplicate formal prefix");
        coverage.accept(f.evaluate(m));
    }
    if(!f.validate(seed)) throw std::logic_error("invalid input seed");
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
    if(finished<0 || finished>deadline || candidate.coverage_version!=version_ || duplicate(candidate.model)) return false;
    // Recompute before changing formal state; the caller's output transaction
    // uses an already checked immutable candidate. No internal query commits.
    if(!f.validate(candidate.model)) throw std::logic_error("commit violates original formula");
    auto observed=f.evaluate(candidate.model);auto gain=coverage.gain(observed);
    if(gain.covered!=candidate.gain.covered || gain.observed!=candidate.gain.observed)
        throw std::logic_error("stale candidate gain at commit");
    coverage.accept(observed);emitted.insert(f.key(candidate.model));++version_;++outputs_;return true;
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
