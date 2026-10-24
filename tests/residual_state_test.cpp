#include "residual_coverage.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
using namespace lia;
static void check(bool ok,const char *message) {if(!ok)throw std::runtime_error(message);}
int main() {try {
    auto path=std::filesystem::temp_directory_path()/"lia-residual-state.smt2";
    std::ofstream(path)<<"(declare-const x Int)(assert (and (>= x 0) (<= x 7)))";
    Formula f(path.string());OnlineCoverage state(f);Assignment zero{0},seed{1},other{3};
    check(state.restore({zero},seed),"seed submitted once");
    auto initial=state.initial_covered();check(initial>0 && state.outputs()==0,"seed not counted as new output");
    Global global(f,0);Budget budget;budget.seconds=2;ResidualTargetWindow selector(f,0);
    auto window=selector.select(state.state(),state.version(),global,budget);
    for(auto t:window.targets)check(ResidualTargetWindow::missing_opposite(state.state(),t),"only true remaining gaps");
    auto before=state.state().json();auto candidate=state.prepare(other,window,"test",1);
    check(candidate && state.state().json()==before,"internal candidate cannot update coverage");
    check(!state.commit(*candidate,1.1,1.0) && state.state().json()==before,"late output excluded");
    check(state.commit(*candidate,.5,1.0),"timely candidate committed");
    check(state.version()==1 && state.outputs()==1,"version increments once");
    check(state.state().covered()-initial==candidate->gain.covered,"marginal equals actual union");
    check(!state.commit(*candidate,.6,1.0),"duplicate/stale commit excluded");
    auto next=selector.select(state.state(),state.version(),global,budget);
    for(auto t:next.targets)check(ResidualTargetWindow::missing_opposite(state.state(),t),"completed gaps removed");
    OnlineCoverage duplicate_seed(f);check(!duplicate_seed.restore({zero,seed},seed),"already submitted seed skipped");
    check(duplicate_seed.initial_covered()==initial,"same S0 when seed already present");
    bool bad=false;try {duplicate_seed.prepare(Assignment{},ResidualWindow{},"bad",0);}catch(const std::invalid_argument &){bad=true;}
    check(bad,"complete candidate type required");
    std::cout<<"residual online state tests passed\n";return 0;
}catch(const std::exception &e){std::cerr<<e.what()<<'\n';return 1;}}
