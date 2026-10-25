#pragma once
#include "soft_phase_bridge.hpp"
namespace lia {
struct GuaranteedObservation {unsigned node;uint64_t zero,one;};
struct ConditionalSummary {
    std::map<int,Z> values;
    std::vector<unsigned> equations;
    std::vector<GuaranteedObservation> observations;
};
struct PhaseAtom {
    unsigned node,group;
    ConditionalSummary sides[2];
};
class ConditionalObservations {
    Formula &f_;
public:
    static constexpr unsigned operation_cap=2000000, group_cap=64, observation_cap=200000;
    std::vector<PhaseAtom> atoms;
    unsigned operations=0,skipped=0,conflicts=0,summary_nodes=0;
    std::map<std::string,unsigned> reasons;
    bool complete=true;
    double seconds=0;
    explicit ConditionalObservations(Formula&,Budget&);
    Gain score(const ConditionalSummary&,const Coverage&) const;
    bool contains(const ConditionalSummary&,const Observation&) const;
    std::string json() const;
};
}
