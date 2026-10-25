#include "soft_phase_bridge.hpp"
namespace lia {
SoftPhaseBridge::~SoftPhaseBridge(){try{clear_preferences();}catch(...) {}}
void SoftPhaseBridge::replace_preferences(const std::vector<SoftPhasePreference>&ps,uint64_t epoch){
    auto start=Clock::now();clear_preferences();std::vector<Z3_ast> atoms;std::vector<unsigned> truths,groups;
    if(ps.size()>128 || epoch>UINT_MAX)throw std::invalid_argument("soft table bounds");
    for(auto &p:ps){if(&p.original_atom.ctx()!=&solver_.ctx()||!p.original_atom.is_bool())throw std::invalid_argument("Boolean atom in solver context required");if(p.source_group>UINT_MAX)throw std::invalid_argument("group bounds");atoms.push_back(p.original_atom);truths.push_back(p.preferred_truth);groups.push_back(p.source_group);}
    installed_=true;
    try { Z3_solver_set_soft_phases(solver_.ctx(),solver_,atoms.size(),atoms.data(),truths.data(),groups.data(),epoch);solver_.ctx().check_error(); }
    catch(...) { clear_preferences();throw; }
    install_seconds+=std::chrono::duration<double>(Clock::now()-start).count();
}
void SoftPhaseBridge::clear_preferences(){if(installed_){Z3_solver_clear_soft_phases(solver_.ctx(),solver_);solver_.ctx().check_error();installed_=false;}}
z3::check_result SoftPhaseBridge::check(){
    try{auto r=solver_.check();auto start=Clock::now();last_log=Z3_solver_get_soft_phase_log(solver_.ctx(),solver_);solver_.ctx().check_error();clear_preferences();finish_seconds+=std::chrono::duration<double>(Clock::now()-start).count();return r;}
    catch(...){clear_preferences();throw;}
}
}
