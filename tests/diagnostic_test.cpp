#include "search.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
using namespace lia;
static void check(bool ok,const std::string &why) {if(!ok) throw std::runtime_error(why);}
static std::string fixture(const std::string &text) {
    static unsigned id=0;auto p=std::filesystem::temp_directory_path()/("lia-diagnosis-"+std::to_string(++id)+".smt2");
    std::ofstream(p)<<text;return p.string();
}
int main() {try {
    std::set<std::string> witnessed;
    auto run=[&](const std::string &formula,bool line,const Assignment &base,const std::vector<Assignment> &prefix,
        const Assignment &direction,const std::string &target_name,unsigned bit_index,bool value,
        const std::string &expected,const std::string &special="") {
        Formula f(fixture(formula));Coverage coverage(f);std::set<std::string> emitted;
        for(const auto &m:prefix) {check(f.validate(m),"fixture legal prefix");coverage.accept(f.evaluate(m));emitted.insert(f.key(m));}
        auto frozen=coverage.json();Search search(f);search.options.line=line;search.options.candidate_cap=4;
        DiagnosticTrace trace;search.diagnostic_trace=&trace;Stats stats;Budget budget;budget.seconds=2;
        int target=-1;
        for(size_t i=0;i<f.variables.size();++i) if(f.nodes[f.variables[i]].expr.decl().name().str()==target_name) target=f.variables[i];
        if(target>=0) search.diagnostic_targets=[&]{
            if(special=="pool_budget") budget.seconds=0;
            if(special=="empty_pool") return std::vector<Target>{};
            Target t{target,bit_index,value};return special=="duplicate_batch"?std::vector<Target>{t,t}:std::vector<Target>{t};
        };
        if(!direction.empty()) search.diagnostic_directions=std::vector<Direction>{{direction,"synthetic",line?"structural_line":"safe_region","fixture"}};
        if(special=="expired") budget.seconds=0;
        if(special=="feasibility_budget") search.options.line_limits.dag_nodes=0;
        if(special=="witness_timeout") search.diagnostic_boundary=[&](const std::string &stage){if(stage=="witness_found") budget.seconds=0;};
        std::mt19937_64 rng(0);
        auto call=[&]{return line?search.line(base,coverage,budget,rng,stats,{},[&](const auto &k){return emitted.count(k);}) :
            search.safe(base,coverage,budget,rng,stats,{},[&](const auto &k){return emitted.count(k);});};
        auto result=call();if(special=="duplicate_action") result=call();
        check(coverage.json()==frozen,"diagnosis freezes coverage");
        check(trace.json().find("\""+expected+"\"")!=std::string::npos,"missing diagnosis "+expected+": "+trace.json());
        if(result.model) check(f.validate(*result.model),"valid diagnostic result");
        if(special=="witness_timeout") check(trace.json().find("\"witness_found\":true")!=std::string::npos,"witness timeout evidence");
        witnessed.insert(expected);
        const char *output=std::getenv("LIA_DIAGNOSTIC_FIXTURES");
        if(output) {
            std::filesystem::create_directories(output);auto p=std::filesystem::path(output)/(expected+"-"+special+".json");
            std::ofstream(p)<<"{\"formula\":"<<quote(formula)<<",\"expected\":"<<quote(expected)<<",\"diagnostics\":"<<trace.json()<<"}\n";
        }
    };
    const std::string bounded="(declare-const x Int)(assert (and (<= -1 x)(<= x 0)))";
    run("(declare-const x Int)(assert (= x 0))",false,{0},{{0}}, {},"x",63,true,"NO_DIRECTION_PROVED");
    run(bounded,false,{0},{{0}},{},"x",63,true,"DIRECTION_GENERATION_INCOMPLETE","expired");
    run(bounded,false,{0},{{0}},{1},"x",63,true,"FEASIBILITY_NOT_CHECKED","pool_budget");
    run(bounded,false,{0},{{0}},{1},"x",63,true,"TARGET_POOL_EMPTY","empty_pool");
    run("(declare-const x Int)(assert (>= (* x x) 0))",true,{0},{{0}},{1},"x",63,true,"FEASIBILITY_UNSUPPORTED");
    run(bounded,true,{0},{{0}},{1},"x",63,true,"FEASIBILITY_BUDGET_EXCEEDED","feasibility_budget");
    run("(declare-const x Int)(assert (and (>= x 0)(<= x 0)))",true,{0},{{0}},{1},"x",63,true,"ORIGIN_ONLY");
    run("(declare-const x Int)(assert (and (>= x 0)(<= x 3)))",true,{0},{{0}},{1},"x",63,true,"BIT_VALUE_UNREACHABLE");
    const std::string reach="(declare-const x Int)(declare-const y Int)(assert (and (>= x 0)(or (>= x 0)(= y 1))))";
    run(reach,true,{0,1},{{0,1}},{0,1},"y",0,false,"REACH_EMPTY");
    run(reach,false,{0,1},{{0,1}},{0,1},"y",0,false,"NUMERIC_HIT_NOT_OBSERVED");
    run(reach,false,{0,1},{{0,1}},{0,1},"y",0,false,"ZERO_INCREMENT");
    run(bounded,false,{0},{},{1},"x",63,true,"NEW_OBSERVATION_ONLY");
    run(bounded,false,{0},{{0}},{1},"x",63,true,"NEW_COVERAGE");
    run(bounded,false,{0},{{0},{-1}},{1},"x",63,true,"DUPLICATE_IN_COMMITTED_OUTPUT");
    run(bounded,false,{0},{{0}},{1},"x",63,true,"DUPLICATE_IN_BATCH","duplicate_batch");
    run(bounded,false,{0},{{0}},{1},"x",63,true,"DUPLICATE_IN_ACTION","duplicate_action");
    run(bounded,false,{0},{{0}},{1},"x",63,true,"TARGET_ANALYSIS_BUDGET_EXCEEDED","witness_timeout");
    run("(declare-const b Bool)(declare-const x Int)(assert (or b (>= x 0)))",false,{1,0},{{1,0}},{0,1},"b",0,false,"TARGET_UNSUPPORTED");
    check(diagnostic_duplicate_reason(true,true,true,true)=="SAME_AS_BASE","fixed primary duplicate priority");
    check(diagnostic_duplicate_reason(false,true,true,true)=="DUPLICATE_IN_BATCH","batch priority");
    // Diagnostic on/off must preserve decisions and RNG for a completed operation-limited call.
    Formula f(fixture(bounded));Coverage coverage(f);coverage.accept(f.evaluate({0}));
    Stats a,b;Search on(f),off(f);off.options.diagnostics=false;Budget ba,bb;ba.seconds=bb.seconds=20;
    std::mt19937_64 ra(7),rb(7);on.options.attempt_cap=off.options.attempt_cap=8;
    auto x=on.safe({0},coverage,ba,ra,a,{},[](const auto&){return false;});
    auto y=off.safe({0},coverage,bb,rb,b,{},[](const auto&){return false;});
    check(x.model==y.model && x.attempts==y.attempts && ra==rb,"diagnostics preserve bounded choices and RNG");
    std::cout<<"diagnostic tests passed: "<<witnessed.size()<<" distinct reasons\n";return 0;
} catch(const std::exception &e) {std::cerr<<e.what()<<'\n';return 1;}}
