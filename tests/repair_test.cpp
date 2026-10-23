#include "core.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
using namespace lia;
static void check(bool v,const std::string &s) { if(!v) throw std::runtime_error(s); }
int main() {
    try {
        auto path=std::filesystem::temp_directory_path()/"lia-repair.smt2";
        std::ofstream(path)<<"(declare-const p Bool)(declare-const q Bool)(declare-const r Bool)"
            "(assert (<= (+ (ite p 1 0) (ite q 1 0) (ite r 1 0)) 2))";
        Formula f(path.string()); Stats stats; Budget b; b.seconds=10;
        std::vector<Target> targets;
        for(int node:f.variables) targets.push_back({node,0,true});
        Global g(f,0); g.pack_size=3; unsigned feedbacks=0;
        auto result=g.repair(targets,b,stats,
            [&](const Assignment &m){return Gain{Z(m[0]+m[1]+m[2]).get_ui(),0};},
            [&](const GlobalResult &){++feedbacks;});
        check(result.model && f.validate(*result.model),"repair returns complete legal witness");
        check(stats.counts["repair_core_removals"]==1 && feedbacks==2,"same action repairs triple core");
        for(auto t:targets) check(!g.unreachable(t),"combination conflict does not disable members");
        for(unsigned i=0;i<3;++i) {
            auto pair=targets; pair.erase(pair.begin()+i); g.pack_size=2;
            check(g.query(pair,b,stats).code==SolveStatus::Sat,"all pairs remain compatible");
        }
        Global mock(f,0); mock.pack_size=1;
        unsigned calls=0;
        mock.repair_check=[&](const std::vector<Target> &pack,Budget &,Stats &) {
            GlobalResult out; out.attempted=pack;
            if(++calls==1) {
                out.model=Assignment{1,0,0}; out.code=SolveStatus::Sat; out.status="sat";
            } else { out.code=SolveStatus::Timeout; out.status="timeout"; out.detail="mock deadline"; }
            return out;
        };
        auto preserved=mock.repair(targets,b,stats,[](const Assignment &){return Gain{1,1};},
                                   [](const GlobalResult &){},4,2);
        check(preserved.model && calls==2,"later timeout preserves earlier witness");
        check(mock.conflicts.cores.empty(),"timeout creates no logical conflict");
        for(auto t:targets) check(!mock.unreachable(t),"timeout never disables targets");
        Global bounded(f,0); bounded.pack_size=1; unsigned limited=0;
        bounded.repair_check=[&](const std::vector<Target> &pack,Budget &,Stats &) {
            ++limited; GlobalResult out; out.code=SolveStatus::Sat; out.status="sat";
            out.model=Assignment{1,0,0}; out.attempted=pack; return out;
        };
        bounded.repair(targets,b,stats,[](const Assignment &){return Gain{1,1};},
                       [](const GlobalResult &){},1,2);
        check(limited==1,"query cap shared by whole repair action");
        Budget expired; expired.seconds=0;
        auto exhausted=bounded.repair(targets,expired,stats,[](const Assignment &){return Gain{};},
                       [](const GlobalResult &){});
        check(limited==1,"expired shared action never starts a new query");
        check(exhausted.code==SolveStatus::BudgetExhausted && exhausted.status=="budget_exhausted",
              "expired repair has an explicit non-solver status");
        auto empty=bounded.repair({},b,stats,[](const Assignment &){return Gain{};},[](const GlobalResult &){});
        check(empty.code==SolveStatus::NoTarget && empty.status=="no_target" && limited==1,
              "empty pack does not invent a solver query or an empty status");
        std::cout<<"repair tests passed\n"; return 0;
    } catch(const std::exception &e) {std::cerr<<e.what()<<'\n';return 1;}
}
