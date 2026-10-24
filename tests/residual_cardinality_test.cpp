#include "residual_coverage.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
using namespace lia;
namespace {
void check(bool ok,const char *message){if(!ok)throw std::runtime_error(message);}
std::string file(const std::string &text){static unsigned i=0;auto p=std::filesystem::temp_directory_path()/("lia-cardinality-"+std::to_string(++i)+".smt2");std::ofstream(p)<<text;return p.string();}
Target target(const Formula &f,const std::string &name,unsigned bit=0,bool desired=true){for(int n:f.variables)if(f.nodes[n].expr.decl().name().str()==name)return {n,bit,desired};throw std::runtime_error("test variable missing");}
unsigned hits(Formula &f,const Assignment &m,const std::vector<Target> &ts){auto o=f.evaluate(m);unsigned c=0;for(auto t:ts)c+=o.visited[t.node] && bit(o.values[t.node],t.bit)==t.desired;return c;}
GlobalResult sat(Assignment m,const ResidualWindow &w){GlobalResult r;r.model=m;r.code=SolveStatus::Sat;r.status="sat";r.attempted=w.targets;return r;}
void window(ResidualGenerator &g,std::vector<Target> ts){g.window_override=[ts](const OnlineCoverage &s){ResidualWindow w;w.targets=ts;w.coverage_version=s.version();return w;};}
}
int main(){try {
    {
        Formula f(file("(declare-const x Int)(declare-const y Int)(declare-const z Int)(assert (and (<= 0 x)(<= x 1)(<= 0 y)(<= y 1)(<= 0 z)(<= z 1)(<= (+ x y) 1)))"));
        std::vector<Target> ts{target(f,"x"),target(f,"y"),target(f,"z")};Stats stats;Budget b;b.seconds=10;
        Global conjunction(f,0);check(conjunction.query(ts,b,stats).code==SolveStatus::Unsat,"fixed conjunction must fail");
        Global g(f,0);auto r=g.query_at_least(ts,2,0,b,stats);
        check(r.model && f.validate(*r.model) && hits(f,*r.model,ts)==2,"choose compatible pair from conflicting window");
        check(r.model->size()==3,"auxiliary variables excluded from output");
        // Exhaustive finite-window oracle: every subset and every threshold.
        for(unsigned mask=1;mask<8;++mask) {
            std::vector<Target> subset;for(unsigned j=0;j<3;++j)if(mask&(1<<j))subset.push_back(ts[j]);
            unsigned max_hits=0;
            for(int x=0;x<=1;++x)for(int y=0;y<=1;++y)for(int z=0;z<=1;++z) {
                Assignment m{x,y,z};if(f.validate(m))max_hits=std::max(max_hits,hits(f,m,subset));
            }
            for(unsigned k=1;k<=subset.size()+1;++k) {
                auto q=g.query_at_least(subset,k,mask,b,stats);
                check((q.code==SolveStatus::Sat)==(k<=max_hits),"enumerated SAT threshold mismatch");
                check((q.code==SolveStatus::Unsat)==(k>max_hits),"enumerated UNSAT threshold mismatch");
                if(q.model)check(hits(f,*q.model,subset)>=k,"threshold witness actually hits targets");
            }
        }
        check(g.conflicts.cores.empty(),"threshold UNSAT never enters conjunction core cache");
        for(auto t:ts)check(!g.unreachable(t),"threshold conflict not singleton unreachable");
        check(!g.threshold_conclusions.empty() && g.threshold_conclusions.back().coverage_version==7,"versioned threshold evidence");
        check(g.query({ts[0]},b,stats).model.has_value(),"threshold scope popped before old query");
        check(g.query_at_least({ts[1]},1,99,b,stats).model.has_value(),"new window unaffected by old threshold");
        bool bad=false;try{g.query_at_least({ts[0],ts[0]},1,0,b,stats);}catch(const std::invalid_argument&){bad=true;}check(bad,"duplicate node-bit rejected");
        Global limited(f,0,1);auto unknown=limited.query_at_least(ts,1,0,b,stats);
        check(unknown.code!=SolveStatus::Unsat && limited.threshold_conclusions.empty(),"resource exhaustion makes no proof");
        Budget empty;empty.seconds=0;auto no=g.query_at_least(ts,1,0,empty,stats);
        check(no.code==SolveStatus::BudgetExhausted && no.attempted.empty() && !no.target_encoding_complete,"encoding cutoff explicit");
    }
    {
        // Actual q truth need not equal the truth of an implication selector.
        z3::context c;z3::solver solver(c);auto a=c.bool_const("a"),q=c.bool_const("q");
        solver.add(z3::implies(a,q));solver.add(q);solver.add(!a);
        check(solver.check()==z3::sat && solver.get_model().eval(q).is_true() && solver.get_model().eval(a).is_false(),"single implication allows unselected true target");
        Formula f(file("(declare-const x Int)(assert (<= (- 1000) x))"));OnlineCoverage state(f);state.restore({{0}},{0});
        auto ts=std::vector<Target>{target(f,"x",0),target(f,"x",1)};
        ResidualGenerator g(f,0);window(g,ts);std::vector<unsigned> ks;
        g.threshold_override=[&](unsigned i,const ResidualWindow &w,unsigned k){ks.push_back(k);auto r=sat({i?3:-3},w);r.selected_activations=0;return std::optional(r);};
        Budget b;b.seconds=2;Stats stats;unsigned saved=0;auto r=g.generate(true,state,{0},b,stats,[&](const auto&){++saved;return b.elapsed();});
        check(r.best && r.best->model==Assignment{-3} && saved==1 && r.target_calls==2,"keep larger full gain even when later window count grows");
        check(ks==std::vector<unsigned>{1,2},"next k uses actual q truth, not activation count");
        check(state.version()==0,"internal candidates never advance formal state");
        ResidualGenerator skip(f,0);window(skip,ts);std::vector<unsigned> requested;
        skip.threshold_override=[&](unsigned,const ResidualWindow &w,unsigned k){requested.push_back(k);return std::optional(sat({3},w));};
        Budget b2;b2.seconds=2;auto all=skip.generate(true,state,{0},b2,stats,{});
        check(all.best && requested==std::vector<unsigned>{1},"SAT hitting entire window finishes without redundant solve");
        ResidualGenerator timeout(f,0);window(timeout,ts);
        timeout.threshold_override=[](unsigned i,const ResidualWindow &w,unsigned){if(!i)return std::optional(sat({1},w));GlobalResult r;r.code=SolveStatus::Timeout;r.status="timeout";r.attempted=w.targets;return std::optional(r);};
        Budget b3;b3.seconds=2;auto kept=timeout.generate(true,state,{0},b3,stats,{});
        check(kept.best && kept.target_calls==2 && state.commit(*kept.best,b3.elapsed(),b3.seconds),"later failure preserves timely deliverable witness");
        OnlineCoverage clean(f);clean.restore({{0}},{0});ResidualGenerator overrun(f,0);window(overrun,ts);
        Budget b4;b4.seconds=2;overrun.threshold_override=[&](unsigned i,const ResidualWindow &w,unsigned){if(!i)return std::optional(sat({1},w));b4.start-=std::chrono::seconds(3);GlobalResult q;q.code=SolveStatus::BudgetExhausted;q.status="budget_exhausted";return std::optional(q);};
        auto late=overrun.generate(true,clean,{0},b4,stats,{});
        check(late.best && !clean.commit(*late.best,b4.elapsed(),b4.seconds),"retained internal checkpoint cannot backdate formal output");
        ResidualGenerator latewrite(f,0);window(latewrite,ts);latewrite.threshold_override=[](unsigned,const ResidualWindow&w,unsigned){return std::optional(sat({3},w));};
        Budget b5;b5.seconds=2;auto rejected=latewrite.generate(true,clean,{0},b5,stats,[&](const auto&){b5.start-=std::chrono::seconds(3);return b5.elapsed();});
        check(!rejected.best && rejected.late==1 && clean.version()==0,"late checkpoint excluded");
        check(!clean.commit(*late.best,std::numeric_limits<double>::quiet_NaN(),1),"NaN completion rejected");
    }
    {
        Formula f(file("(declare-const b Bool)(declare-const x Int)(assert (or b (= x 0)))"));
        OnlineCoverage s(f);s.restore({{1,0}},{1,0});auto x=target(f,"x");
        check(!ResidualTargetWindow::missing_opposite(s.state(),x),"short-circuit unvisited x not assumed zero");
        ResidualWindow w;auto c=s.prepare({0,0},w,"exploration",1);
        check(c && c->first_observations>=64 && c->window_hits==0,"first observation not main-window gain");
        check(s.commit(*c,.1,1),"first observation committed");
        check(ResidualTargetWindow::missing_opposite(s.state(),x),"first observation becomes future opposite-value target");
        // x=1 alone cannot satisfy the complete Reach condition here.
        Global g(f,0);Budget budget;Stats stats;auto q=g.query_at_least({x},1,s.version(),budget,stats);
        check(q.code==SolveStatus::Unsat,"target includes original short-circuit Reach");
        bool bad=false;try{s.prepare({2,0},ResidualWindow{1},"bad",1);}catch(const std::invalid_argument&){bad=true;}check(bad,"Boolean typing enforced");
    }
    {
        Formula f(file("(declare-const b Bool)(assert (or b (not b)))"));OnlineCoverage s(f);s.restore({{1}},{1});
        ResidualGenerator g(f,0);window(g,{});Budget b;b.seconds=2;Stats stats;auto r=g.generate(true,s,{1},b,stats,{});
        check(r.best && r.exploration_calls==1 && r.target_calls==0 && r.best->origin=="exploration","common fallback handles empty window");
        check(s.commit(*r.best,b.elapsed(),b.seconds),"fallback shares same commitment");
        check(!s.prepare(r.best->model,ResidualWindow{s.version()},"repeat",2),"duplicate candidate not output again");
    }
    {
        const Z huge=-(pow2(90))+1;
        Formula f(file("(declare-const x Int)(assert (= x "+huge.get_str()+"))"));Global g(f,0);Stats stats;Budget b;
        auto q=g.query_at_least({target(f,"x",0)},1,0,b,stats);
        check(q.model && (*q.model)[0]==huge && f.validate(*q.model),"negative large integer never truncated");
        auto valuefile=file(huge.get_str()+"\n");auto values=read_residual_values(f,valuefile);
        check(values==std::vector<Assignment>{{huge}},"exact typed decimal snapshot round trip");
        bool bad=false;try{read_residual_values(f,file("1.5\n"));}catch(const std::invalid_argument&){bad=true;}check(bad,"invalid input rejected");
    }
    std::cout<<"residual cardinality regression and exhaustive finite thresholds passed\n";return 0;
}catch(const std::exception &e){std::cerr<<e.what()<<'\n';return 1;}}
