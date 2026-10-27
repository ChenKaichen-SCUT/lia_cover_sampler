#include "arith_coverage_bridge.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <unistd.h>
#include <thread>
#include <atomic>
using namespace lia;
namespace {
void check(bool b,const char*why){if(!b)throw std::runtime_error(why);}
std::string fixture(const std::string&s){static unsigned n=0;auto p=std::filesystem::temp_directory_path()/("arith-test-"+std::to_string(getpid())+"-"+std::to_string(n++)+".smt2");std::ofstream(p)<<s;return p.string();}
struct Callback {std::vector<Assignment> pool;unsigned calls=0,choose=0;std::string meta;z3::solver* solver=nullptr;bool test_reentry=false,cancel_after_pool=false;unsigned rejected=0;};
unsigned choose(void*p,unsigned n,unsigned nv,const char*const*data,const char*meta){auto&c=*static_cast<Callback*>(p);++c.calls;
    if(c.test_reentry){
        try{c.solver->push();}catch(z3::exception const&){++c.rejected;}
        try{c.solver->get_model();}catch(z3::exception const&){++c.rejected;}
        try{c.solver->check();}catch(z3::exception const&){++c.rejected;}
    }c.meta=meta;c.pool.clear();for(unsigned i=0;i<n;++i){Assignment a;for(unsigned j=0;j<nv;++j)a.emplace_back(data[i*nv+j]);c.pool.push_back(a);}if(c.cancel_after_pool)Z3_interrupt(c.solver->ctx());return c.choose && n>1?1:0;}
void install(Formula&f,z3::solver&s,PureEvaluationPlan&p,Callback&cb,unsigned q,double budget=1){
    // One second is a deterministic micro-regression limit, never a formal action limit.
    Z3_solver_set_arith_sampling(f.ctx,s,f.root,p.variables.size(),p.variables.data(),p.terms.size(),p.terms.data(),choose,&cb,q,0,arith_stream_seed(17,3,q),budget);f.ctx.check_error();
}
z3::expr blocker(Formula&f,const Assignment&a){z3::expr_vector v(f.ctx);for(unsigned i=0;i<a.size();++i)v.push_back(f.nodes[f.variables[i]].expr!=f.value(i,a[i]));return z3::mk_or(v);}
void metric(){
    check(arith_coverage_choice({{2,3},{2,3}},5)==0,"noop loses tie");
    check(arith_coverage_choice({{0,0},{0,1}},5)==1,"new observation ignored");
    check(arith_coverage_choice({{1,10},{2,0}},5)==1,"covered not primary score");
    check(arith_coverage_choice({{0,0},{2,1},{2,1}},7)==arith_coverage_choice({{0,0},{2,1},{2,1}},7),"unstable tie RNG");
    auto path=fixture("(declare-const x Int)(declare-const y Int)(declare-const b Bool)(assert (and (or b (> x 0) (> y 4)) (= (ite b (+ x x) (- y)) (ite b (* 2 x) (- y)))))");
    Formula f(path);PureEvaluationPlan p(f);Budget b;p.prepare(b);check(p.ready,"pure plan not ready");
    for(const char*s:{"-18446744073709551617","-1","0","1","9223372036854775808"})for(int y:{-5,0,5})for(int truth:{0,1}){
        Assignment a;for(int id:f.variables){auto name=f.nodes[id].expr.decl().name().str();a.push_back(name=="x"?Z(s):name=="y"?Z(y):Z(truth));}
        auto raw=f.evaluate(a),pure=p.evaluate(a),trace=f.evaluate(a,true);std::vector<int>unique_events;std::set<int>seen_events;for(int e:trace.events)if(seen_events.insert(e).second)unique_events.push_back(e);
        check(raw.values==pure.values && raw.visited==pure.visited && unique_events==pure.events,"pure evaluator changes frozen traversal");
        Coverage one(f),two(f);auto g=one.gain(raw);check(g.covered==0 && g.observed>0,"first observation semantics");one.accept(raw);two.accept(pure);check(one.json()==two.json(),"pure coverage mismatch");
        auto frozen=one.json();two.accept(f.evaluate(Assignment(a.size(),Z(0))));check(one.json()==frozen,"coverage copy mutated");
    }
    std::filesystem::remove(path);
}
std::vector<std::vector<Assignment>> run_case(const std::string&text,unsigned rounds,unsigned choice,std::function<bool(const Assignment&,const Formula&)>oracle,bool expect_hook=true){
    auto path=fixture(text);Formula f(path);PureEvaluationPlan p(f);Budget prep;p.prepare(prep);z3::solver s(f.ctx,z3::solver::simple{});s.add(f.root);s.set("random_seed",17u);s.set("rlimit",1000000u);
    std::set<std::string> seen;std::vector<std::vector<Assignment>> pools;unsigned calls=0,nontrivial=0;
    for(unsigned q=0;q<rounds;++q){Callback cb;cb.choose=choice;install(f,s,p,cb,q);auto result=s.check();std::string log=Z3_solver_get_arith_sampling_log(f.ctx,s);Z3_solver_clear_arith_sampling(f.ctx,s);
        check(cb.calls<=1,"once latch reset after FC_CONTINUE");check(log.find("\"implementation_failure\":true")==std::string::npos,"commit invariant failure");calls+=cb.calls;
        if(result==z3::unsat)break;check(result==z3::sat,"micro fixture unexpected Unknown");
        auto model=f.complete(s.get_model());check(f.validate(model)&&oracle(model,f),"delivered model fails independent oracle");check(seen.insert(f.key(model)).second,"repeated blocked model");
        std::set<std::string>distinct;for(auto const&a:cb.pool){check(oracle(a,f)&&f.validate(a),"proposal fails independent oracle or original formula");check(distinct.insert(f.key(a)).second,"duplicate pool model");}
        // Earlier output blockers remain in the active solver; audit independently.
        for(auto const&a:cb.pool){auto k=f.key(a);check(k==f.key(model)||!seen.count(k),"pool violates previous complete blocker");}
        nontrivial+=cb.pool.size()>1;pools.push_back(cb.pool);s.add(blocker(f,model));
        std::cout<<"{\"test\":\"hook\",\"query\":"<<q<<",\"choice\":"<<choice<<",\"pool_size\":"<<cb.pool.size()<<",\"log\":"<<log<<"}\n";
    }
    if(expect_hook)check(calls>0,"no actual callback in supported fixture");
    std::filesystem::remove(path);return pools;
}
Z named(const Assignment&a,const Formula&f,const std::string&name){for(unsigned i=0;i<a.size();++i)if(f.nodes[f.variables[i]].expr.decl().name().str()==name)return a[i];throw std::runtime_error("missing variable");}
void hooks(){
    const std::string pair="(declare-const x Int)(declare-const y Int)(assert (and (= (+ x y) 255) (<= 0 x) (<= x 255) (<= 0 y) (<= y 255)))";
    auto oracle=[](auto&a,auto&f){Z x=named(a,f,"x"),y=named(a,f,"y");return x+y==255&&x>=0&&x<=255&&y>=0&&y<=255;};
    auto first=run_case(pair,5,0,oracle),second=run_case(pair,5,0,oracle),changed=run_case(pair,5,1,oracle);
    check(first==second,"fixed-work same stream different candidate pools");check(first[0]==changed[0],"selection affected proposal pool");check(first[0].size()>1,"supported oracle has no moves");
    for(auto relation:{"(= (* 2 y) x)","(= (* 3 y) (* 2 x))"}){
        std::string rel=relation;run_case("(declare-const x Int)(declare-const y Int)(assert (and "+rel+" (<= -12 x) (<= x 12) (<= -12 y) (<= y 12)))",7,1,[&](auto&a,auto&f){Z x=named(a,f,"x"),y=named(a,f,"y");return (rel.find("3")!=std::string::npos?Z(3*y)==2*x:Z(2*y)==x)&&x>=-12&&x<=12&&y>=-12&&y<=12;});
    }
    run_case("(declare-const x Int)(assert (and (<= 0 x) (<= x 4) (not (= x 2))))",5,1,[](auto&a,auto&f){auto x=named(a,f,"x");return x>=0&&x<=4&&x!=2;});
    run_case("(declare-const x Int)(assert (= x 7))",2,1,[](auto&a,auto&f){return named(a,f,"x")==7;},false);
    run_case("(declare-const x Int)(assert (or (= x 0) (= x 255)))",3,1,[](auto&a,auto&f){auto x=named(a,f,"x");return x==0||x==255;},false);
    run_case("(declare-const x Int)(declare-const y Int)(declare-const z Int)(assert (and (= x y) (= y z) (<= -2 x) (<= x 2)))",6,1,[](auto&a,auto&f){Z x=named(a,f,"x");return x==named(a,f,"y")&&x==named(a,f,"z")&&x>=-2&&x<=2;},false);
    // Deadline, scopes, reset and false original formula do not fabricate models.
    auto path=fixture(pair);Formula f(path);PureEvaluationPlan p(f);Budget b;p.prepare(b);z3::solver s(f.ctx,z3::solver::simple{});s.add(f.root);
    Callback cb;install(f,s,p,cb,0,0);check(s.check()==z3::sat&&cb.calls==0,"zero action budget executed callback");Z3_solver_clear_arith_sampling(f.ctx,s);
    s.push();s.add(f.ctx.bool_val(false));install(f,s,p,cb,1);check(s.check()==z3::unsat&&cb.calls==0,"UNSAT generated proposal");s.pop();Z3_solver_clear_arith_sampling(f.ctx,s);
    install(f,s,p,cb,2);check(s.check()==z3::sat&&cb.calls<=1,"scope recovery failure");s.reset();Z3_solver_clear_arith_sampling(f.ctx,s);s.add(f.ctx.bool_val(false));check(s.check()==z3::unsat,"reset polluted solver");
    // Reentry must be rejected before touching the live context.
    z3::solver guarded(f.ctx,z3::solver::simple{});guarded.add(f.root);Callback reentrant;reentrant.solver=&guarded;reentrant.test_reentry=true;install(f,guarded,p,reentrant,3);
    bool outer_rejected=false;try{guarded.check();}catch(z3::exception const&){outer_rejected=true;}check(outer_rejected && reentrant.calls==1 && reentrant.rejected==3,"callback permitted solver reentry");Z3_solver_clear_arith_sampling(f.ctx,guarded);check(guarded.check()==z3::sat,"rejected callback mutated solver");
    // An active assumption restricts Q beyond the registered original formula.
    z3::solver assumed(f.ctx,z3::solver::simple{});assumed.add(f.root);z3::expr_vector assumptions(f.ctx);auto original_x=f.nodes[f.variables[0]].expr;assumptions.push_back(original_x>=100);
    Callback constrained;constrained.choose=1;install(f,assumed,p,constrained,4);check(assumed.check(assumptions)==z3::sat,"assumption SAT changed");
    check(constrained.calls==1 && !constrained.pool.empty(),"assumption pool not tested");for(auto const&a:constrained.pool)check(a[0]>=100,"proposal violates current assumption");Z3_solver_clear_arith_sampling(f.ctx,assumed);
    z3::solver limited(f.ctx,z3::solver::simple{});limited.add(f.root);limited.set("rlimit",1u);Callback exhausted;install(f,limited,p,exhausted,6);
    check(limited.check()==z3::unknown && exhausted.calls<=1,"resource Unknown fabricated candidate");Z3_solver_clear_arith_sampling(f.ctx,limited);limited.set("rlimit",1000000u);check(limited.check()==z3::sat,"Unknown polluted later check");
    z3::solver cancelled(f.ctx,z3::solver::simple{});cancelled.add(f.root);Callback cancellation;cancellation.solver=&cancelled;cancellation.cancel_after_pool=true;cancellation.choose=1;install(f,cancelled,p,cancellation,7);
    auto cancelled_result=cancelled.check();std::string cancel_log=Z3_solver_get_arith_sampling_log(f.ctx,cancelled);check(cancellation.calls==1 && cancel_log.find("\"reason\":\"cancelled\"")!=std::string::npos && cancel_log.find("\"committed_move\":false")!=std::string::npos,"cancellation submitted pending move");
    check(cancelled_result==z3::unknown,"cancelled hook not Unknown");Z3_solver_clear_arith_sampling(f.ctx,cancelled);cancelled.reset();cancelled.add(f.root);check(cancelled.check()==z3::sat,"cancellation poisoned reset solver");
    // Unsupported engine remains able to solve; no forced switch to theory_lra.
    z3::solver other(f.ctx,z3::solver::simple{});other.set("arith.solver",2u);other.add(f.root);Callback unsupported;install(f,other,p,unsupported,5);check(other.check()==z3::sat && unsupported.calls==0,"unsupported engine was forced or changed");Z3_solver_clear_arith_sampling(f.ctx,other);
    std::filesystem::remove(path);
}
}
int main(){try{metric();hooks();std::cout<<"{\"passed\":true}\n";return 0;}catch(std::exception const&e){std::cerr<<e.what()<<'\n';return 1;}}
