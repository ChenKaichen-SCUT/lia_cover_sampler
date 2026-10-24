#include "coverage_block_generator.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
using namespace lia;
namespace {
void check(bool ok,const std::string &why){if(!ok)throw std::runtime_error(why);}
std::filesystem::path file(const std::string &text) {
    static unsigned n=0;auto p=std::filesystem::temp_directory_path()/("lia-block-test-"+std::to_string(++n)+".smt2");
    std::ofstream(p)<<text;return p;
}
Target target(const Formula &f,const std::string &name,unsigned bit=0,bool desired=true) {
    for(int n:f.variables) if(f.nodes[n].expr.decl().name().str()==name) return {n,bit,desired};
    throw std::runtime_error("missing test variable");
}
auto none=[](const std::string&){return false;};
BlockResult run(CoverageBlockGenerator &g,Formula &f,const Assignment &m,const Coverage &s,std::vector<Target> p) {
    Budget b;b.seconds=2;Stats stats;auto before=s.json();
    auto r=g.generate(m,s,b,0,true,stats,none,{},p);
    check(before==s.json(),"internal candidates never commit coverage");
    if(r.model)check(f.validate(*r.model) && *r.model!=m && s.gain(f.evaluate(*r.model)).covered==r.gain.covered,"complete different original-formula model and exact gain");
    return r;
}
}
int main(){try {
    {
        Formula f(file("(declare-const x Int)(declare-const y Int)(assert (= (+ x y) 10))(assert (or (= x 0) (= x 10)))").string());
        Assignment m{0,10};Coverage s(f);s.accept(f.evaluate(m));CoverageBlockGenerator g(f,0);
        auto r=run(g,f,m,s,{target(f,"x",1)});
        check(r.model && (*r.model)[0]==10 && (*r.model)[1]==0 && r.branch_changes>0,"cross original safe branch");
        Budget rb;auto o=f.evaluate(m);Region region(f,m,o,rb);
        check(region.supported && !region.contains(*r.model),"new model is outside original safe region");
        check(r.initial_block==2 && r.gain.covered>0,"necessary equality releases coordinated variable");
    }
    {
        Formula f(file("(declare-const x Int)(declare-const y Int)(assert (= (+ x y) 10))").string());
        Assignment m{0,10};Coverage s(f);s.accept(f.evaluate(m));CoverageBlockGenerator g(f,0);g.options.equality_variables=0;
        auto r=run(g,f,m,s,{target(f,"x",1)});
        check(r.model && r.initial_block==1 && r.expansions==1 && r.final_block==2,"core-driven coordinated release");
        check(r.queries[0].status=="unsat" && !r.queries[0].fixed_core.empty() && !r.queries[0].global_proof,"local unsat is scoped");
        check(g.global_conflicts()==0,"local conflict not cached globally");
    }
    {
        Formula f(file("(declare-const b Bool)(declare-const x Int)(assert (or (and b (= x 0)) (and (not b) (= x 10))))").string());
        Assignment m{1,0};Coverage s(f);s.accept(f.evaluate(m));CoverageBlockGenerator g(f,1);
        auto r=run(g,f,m,s,{target(f,"x",1)});
        check(r.model && (*r.model)[0]==0 && (*r.model)[1]==10,"Boolean Reach dependencies released; conditional equation not permanent");
    }
    {
        Formula f(file("(declare-const x Int)(declare-const y Int)(assert (= x y))").string());
        Global g(f,0);auto t=target(f,"x");Budget b;b.seconds=2;Stats stats;GlobalRestrictions local({0,0});local.free_variables.insert(0);
        auto a=g.query({t},b,stats,1000,false,&local);
        check(a.code==SolveStatus::Unsat && a.fixed_core==std::vector<unsigned>{1} && !g.unreachable(t),"fixed core does not prove target unreachable");
        local.free_variables.insert(1);auto good=g.query({t},b,stats,1000,false,&local);
        check(good.model.has_value(),"release inside same incremental solver recovers SAT");
        GlobalRestrictions next({2,2});next.free_variables.insert(0);auto d=g.query({t},b,stats,1000,false,&next);
        check(d.code==SolveStatus::Unsat && !g.unreachable(t),"new anchor has fresh local assumptions");
        check(g.query({t},b,stats,1000,false).model.has_value(),"dormant local constraints do not constrain unscoped query");
    }
    {
        Formula f(file("(declare-const x Int)(assert (and (<= 0 x)(<= x 1)))").string());
        Global g(f,0);Budget b;b.seconds=2;Stats stats;auto t=target(f,"x",0,false);GlobalRestrictions scope({0});scope.free_variables.insert(0);scope.excluded.push_back({0});
        auto r=g.query({t},b,stats,1000,false,&scope);
        check(r.code==SolveStatus::Unsat && r.exclusion_in_core && !r.global_target_proof && !g.unreachable(t),"blocking-scoped target conflict not global");
        check(g.query({t},b,stats,1000,false).model.has_value(),"excluded witness remains globally reachable");
        Global limited(f,0,1);auto unknown=limited.query({target(f,"x")},b,stats,1000,false);
        check(unknown.code!=SolveStatus::Unsat && limited.conflicts.cores.empty(),"actual solver resource exhaustion gives no proof");
    }
    {
        Formula f(file("(declare-const x Int)(assert (= x 0))").string());
        Global g(f,0);Budget b;Stats stats;auto t=target(f,"x");auto r=g.query({t},b,stats,1000,false);
        check(r.code==SolveStatus::Unsat && r.global_target_proof && g.unreachable(t),"only global target proof enters cache");
    }
    {
        Formula f(file("(declare-const x Int)(declare-const z Int)(assert (>= x 0))(assert (>= z 0))").string());
        Coverage s(f);s.accept(f.evaluate({0,0}));CoverageBlockGenerator g(f,0);
        g.query_override=[](const std::vector<Target>& p,unsigned){GlobalResult r;r.code=SolveStatus::Unknown;r.status="unknown";r.attempted=p;return std::optional(r);};
        auto r=run(g,f,{0,0},s,{target(f,"x")});
        check(!r.model && r.queries.size()==2 && g.global_conflicts()==0 && r.fallback_reason=="nonproof_restricted_result","Unknown bounded fallback, no proof");
    }
    {
        Formula f(file("(declare-const x Int)(assert (and (<= -3 x)(<= x 3)))").string());
        Coverage s(f);s.accept(f.evaluate({0}));CoverageBlockGenerator g(f,0);Budget b;b.seconds=2;Stats stats;
        g.query_override=[&](const std::vector<Target>& p,unsigned i){GlobalResult r;r.attempted=p;
            if(i==0){r.model=Assignment{1};r.code=SolveStatus::Sat;r.status="sat";}
            else {b.start-=std::chrono::seconds(3);r.code=SolveStatus::Timeout;r.status="timeout";}
            return std::optional(r);};
        unsigned saved=0;auto r=g.generate({0},s,b,0,true,stats,none,[&](const Assignment&,Gain,double){++saved;return b.elapsed();},{target(f,"x"),target(f,"x",1)});
        check(r.model && (*r.model)[0]==1 && saved==1 && r.queries.size()==2 && r.checkpoint_seconds<2 && r.full_seconds>2,"later deadline does not discard already delivered checkpoint");
        check(g.global_conflicts()==0,"later timeout does not invent conflict");
        CoverageBlockGenerator best(f,0);best.query_override=[](const std::vector<Target>& p,unsigned i){GlobalResult q;q.model=Assignment{i?3:-3};q.attempted=p;q.code=SolveStatus::Sat;q.status="sat";return std::optional(q);};
        auto better=run(best,f,{0},s,{target(f,"x"),target(f,"x",1)});
        check(better.model && (*better.model)[0]==-3 && better.queries.size()==2,"keep true best marginal gain, not target count or last witness");
    }
    {
        Formula f(file("(declare-const x Int)(assert (and (<= 0 x)(<= x 2)))").string());
        Coverage s(f);s.accept(f.evaluate({0}));CoverageBlockGenerator g(f,0);
        auto r=run(g,f,{0},s,{target(f,"x"),target(f,"x",1)});
        check(r.model && (*r.model)[0]==1 && r.queries.size()>=2,"conflicting additional goal retains SAT checkpoint");
        bool conflict=false;for(auto &q:r.queries)conflict|=q.status=="unsat";
        check(conflict,"package conflict was actually queried");
        Coverage empty(f);CoverageBlockGenerator first(f,0);
        auto fresh=run(first,f,{0},empty,{target(f,"x",1)});
        check(fresh.model && fresh.first_observation && fresh.gain.covered==0,"first observation is marked, not immediately covered bit");
    }
    {
        Formula f(file("(declare-const x Int)(assert (<= x (- 18446744073709551616)))").string());
        Assignment m{Z("-18446744073709551616")};Coverage s(f);s.accept(f.evaluate(m));CoverageBlockGenerator g(f,0);
        auto r=run(g,f,m,s,{target(f,"x",63)});
        check(r.model && (*r.model)[0]<m[0] && bit((*r.model)[0],63)==1,"exact unbounded negative integer bit without truncation");
    }
    {
        std::string smt;for(int i=0;i<12;++i)smt+="(declare-const x"+std::to_string(i)+" Int)(assert (>= x"+std::to_string(i)+" 0))";
        Formula f(file(smt).string());Assignment m(12);Coverage s(f);s.accept(f.evaluate(m));CoverageBlockGenerator g(f,0);
        auto r=run(g,f,m,s,{target(f,"x0")});check(r.model && r.initial_block==1 && r.final_block==1,"weakly coupled formula keeps small block");
        CoverageBlockGenerator bounded(f,0);bounded.options.dependency_operations=1;
        auto fallback=run(bounded,f,m,s,{target(f,"x0")});check(!fallback.dependency_complete && !fallback.fallback_reason.empty(),"bounded incomplete analysis falls back without full closure");
        Budget expired;expired.seconds=0;Stats stats;auto z=g.generate(m,s,expired,0,true,stats,none);
        check(!z.model && z.queries.empty(),"expired action starts no query");
    }
    std::cout<<"coverage block: cross-branch, equations, scope, resource/unknown, checkpoint, packing, integer and bound tests passed\n";
    return 0;
}catch(const std::exception &e){std::cerr<<e.what()<<'\n';return 1;}}
