#pragma once
#include "core.hpp"
namespace lia {
inline bool preserves_new_observations(const Formula &f,const Coverage &committed,
                                      const Observation &raw,const Observation &candidate) {
    for(size_t i=0;i<f.nodes.size();++i) if(raw.visited[i]) {
        uint64_t mask=f.nodes[i].boolean?1:~uint64_t(0);
        Z r=mod(raw.values[i],pow2(64));uint64_t value=r.get_ui()&mask;
        auto state=committed.states[i];uint64_t need_one=value&~state.one;
        uint64_t need_zero=(~value&mask)&~state.zero;
        if(!need_one && !need_zero) continue;
        if(!candidate.visited[i]) return false;
        Z p=mod(candidate.values[i],pow2(64));uint64_t replacement=p.get_ui()&mask;
        if((need_one&~replacement) || (need_zero&replacement)) return false;
    }
    return true;
}
}
