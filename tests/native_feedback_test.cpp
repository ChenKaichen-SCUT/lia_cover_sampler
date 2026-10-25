#include "native_phase_feedback.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <thread>
#include <atomic>
#include <unistd.h>
using namespace lia;
namespace {
void require(bool x,const char*m){if(!x)throw std::runtime_error(m);}
unsigned field(const std::string&s,const std::string&k){auto p=s.find("\""+k+"\":");require(p!=std::string::npos,"missing native counter");return std::stoul(s.substr(p+k.size()+3));}
struct Callback {std::atomic<unsigned> called{0};unsigned action=1;};
struct Observer: z3::user_propagator_base {Observer(z3::solver&s):user_propagator_base(&s){register_decide();}Observer(z3::context&c):user_propagator_base(c){}void push()override{}void pop(unsigned)override{}user_propagator_base*fresh(z3::context&c)override{return new Observer(c);}};
int decide(void*p,Z3_ast,bool,unsigned ordinal,unsigned){auto&r=*static_cast<Callback*>(p);++r.called;require(ordinal<=32,"window exceeded");return r.action;}
std::vector<std::string> enumerate(unsigned action){
    z3::context c;z3::solver s(c,z3::solver::simple{});s.set("random_seed",17u);s.set("rlimit",1000000u);
    z3::expr_vector xs(c);for(unsigned i=0;i<4;++i){auto x=c.int_const(("x"+std::to_string(i)).c_str());xs.push_back(x);s.add((x==0)||(x==1));}
    std::vector<std::string> trace;Callback callback;callback.action=action;
    for(unsigned q=0;q<17;++q){
        callback.called=0;if(action<4)Z3_solver_set_native_phase_callback(c,s,decide,&callback,q);
        auto result=s.check();if(action<4){std::string log=Z3_solver_get_native_phase_log(c,s);require(field(log,"selected")<=1,"selection renewed");require(field(log,"flips")<=1,"flip renewed");
            if(action==1)require(field(log,"flips")==0,"keep flipped");
            if(action==0)require(field(log,"selected")==0&&callback.called<=32,"skip created slot");
            Z3_solver_clear_native_phase_callback(c,s);}
        if(result==z3::unsat)break;
        require(result==z3::sat,"finite enumeration unknown");
        auto model=s.get_model();std::string key;z3::expr_vector blockers(c);
        for(auto x:xs){auto value=model.eval(x,true);key+=value.to_string();blockers.push_back(x!=value);}
        trace.push_back(key);s.add(z3::mk_or(blockers));
    }require(trace.size()==16,"model set shrunk");return trace;
}
void native_interface(){
    auto plain=enumerate(4),keep=enumerate(1),skip=enumerate(0),flip=enumerate(2);
    require(plain==keep&&plain==skip,"Keep/disabled changed fixed-operation model trajectory");
    require(std::set<std::string>(plain.begin(),plain.end())==std::set<std::string>(flip.begin(),flip.end()),"flip changed feasible models");
    {z3::context c;z3::solver s(c,z3::solver::simple{});auto x=c.bool_const("propagated");s.add(!x);Callback cb;cb.action=2;
        Z3_solver_set_native_phase_callback(c,s,decide,&cb,0);require(s.check()==z3::sat&&!s.get_model().eval(x).is_true()&&cb.called==0,"propagation interfered");Z3_solver_clear_native_phase_callback(c,s);}
    {z3::context c;z3::solver s(c,z3::solver::simple{});auto x=c.bool_const("x"),y=c.bool_const("y");s.add(x!=y);Observer observer(s);Callback cb;cb.action=2;
        Z3_solver_set_native_phase_callback(c,s,decide,&cb,0);require(s.check()==z3::sat&&cb.called==0,"special interface interfered");
        require(field(Z3_solver_get_native_phase_log(c,s),"selected")==0,"special interface consumed action");Z3_solver_clear_native_phase_callback(c,s);}
    // Conflict-heavy normal decisions: single slot survives backtracking/restarts.
    for(bool cancel:{false,true}){
        z3::context c;z3::solver s(c,z3::solver::simple{});unsigned n=10;z3::expr_vector v(c);
        for(unsigned i=0;i<n*(n-1);++i)v.push_back(c.bool_const(("p"+std::to_string(i)).c_str()));
        for(unsigned i=0;i<n;++i){z3::expr_vector row(c);for(unsigned j=0;j<n-1;++j)row.push_back(v[i*(n-1)+j]);s.add(z3::mk_or(row));}
        for(unsigned j=0;j<n-1;++j)for(unsigned i=0;i<n;++i)for(unsigned k=0;k<i;++k)s.add(!v[i*(n-1)+j]||!v[k*(n-1)+j]);
        s.set("timeout",cancel?1000u:50u);s.set("restart_strategy",0u);Callback cb;cb.action=2;
        Z3_solver_set_native_phase_callback(c,s,decide,&cb,1);
        std::thread timer;if(cancel)timer=std::thread([&]{for(unsigned i=0;i<500&&!cb.called.load();++i)std::this_thread::sleep_for(std::chrono::milliseconds(1));Z3_interrupt(c);});
        auto r=s.check();if(timer.joinable())timer.join();require(r==z3::unknown,"bounded pigeonhole expected Unknown");
        auto log=std::string(Z3_solver_get_native_phase_log(c,s));require(field(log,"selected")==1&&field(log,"flips")==1&&cb.called==1,"restart renewed slot");Z3_solver_clear_native_phase_callback(c,s);
    }
    {z3::context c;z3::solver s(c,z3::solver::simple{});s.add(c.bool_val(false));Callback cb;Z3_solver_set_native_phase_callback(c,s,decide,&cb,0);require(s.check()==z3::unsat&&cb.called==0,"UNSAT fabricated decision");Z3_solver_clear_native_phase_callback(c,s);}
}
void learning(){
    NativeFeatures x{};x[0]=1;x[1]=.5;NativeRidge learner;std::mt19937_64 rng(3);
    for(unsigned i=0;i<8;++i)learner.observe(x,PhaseAction::FlipOnce,.1,.2);
    auto p=learner.predict(x,10);require(p.gain[0]==0&&p.gain[1]>0,"unselected action got label");require(learner.choose(x,rng,true,10).cold,"one-action warmup accepted");
    for(unsigned i=0;i<8;++i)learner.observe(x,PhaseAction::KeepNative,0,1);
    bool keep=false,flip=false;for(unsigned i=0;i<1000;++i){auto d=learner.choose(x,rng,true,10);require(!d.cold,"warmup failed");require(d.probability==(d.action==PhaseAction::FlipOnce?.9:.1),"conditional propensity wrong");keep|=d.action==PhaseAction::KeepNative;flip|=d.action==PhaseAction::FlipOnce;}require(keep&&flip,"fixed exploration absent");
    for(unsigned i=0;i<300;++i)learner.observe(x,PhaseAction::KeepNative,0,0);
    require(learner.window_size()==256,"unbounded feedback");
    NativeRidge fresh;require(fresh.feedback_counts[0]==0&&fresh.predict(x,1).gain[1]==0,"cross-session training");
    x[0]=std::numeric_limits<double>::quiet_NaN();require(!learner.predict(x,1).finite,"nonfinite undetected");
    bool rejected=false;try{learner.observe(x,PhaseAction::FlipOnce,0,1);}catch(...){rejected=true;}require(rejected,"NaN feedback accepted");
}
void classification(){
    auto path=std::filesystem::temp_directory_path()/("native_phase_"+std::to_string(getpid())+".smt2");
    {std::ofstream f(path);f<<"(declare-const x Int)(declare-const y Int)(assert (and (>= x 0)(<= x 3)(>= y 0)(<= y 3)(or (= x 0)(= x 3))))";}
    Formula f(path);std::filesystem::remove(path);z3::solver solver(f.ctx,z3::solver::simple{});solver.add(f.root);Budget budget;budget.seconds=100;
    NativePhaseFeedback policy(f,solver,budget,"native_random_one",0);
    auto x=f.ctx.int_const("x"),y=f.ctx.int_const("y");auto*a=policy.inspect(3*x+2*y<=5);
    require(a&&a->supported&&!a->in_catalog&&a->original_node==-1,"new original-variable relation unsupported");
    require(a->variables.size()==2,"lost linear dependencies");Assignment zero(f.variables.size(),0);require(a->truth(zero),"linear truth evaluator");
    auto*neg=policy.inspect(!(3*x+2*y<=5));require(neg->supported&&!neg->truth(zero),"negated relation truth");
    auto*aux=policy.inspect(f.ctx.int_const("unknown_auxiliary!x")==0);require(!aux->supported,"unidentified auxiliary accepted");
    require(!policy.inspect(x*y<=1)->supported,"nonlinear relation accepted");
    auto deep=x;for(unsigned i=0;i<100;++i)deep=deep+x;
    auto*bounded=policy.inspect(deep<=1);require(!bounded->supported&&bounded->reason=="atom_operation_cap","bounded classification did not stop");
    Z huge=Z(1)<<4097;require(!policy.inspect(f.ctx.int_val(huge.get_str().c_str())*x<=1)->supported,"coefficient cap ignored");
    // Strong references prevent a recycled AST id from becoming a cache hit.
    auto saved=a->expression.to_string();for(unsigned i=0;i<4200;++i){auto*p=policy.inspect(x<=int(i));if(!p)break;}
    require(policy.cache_size()==NativePhaseFeedback::cache_cap&&policy.stop_reason=="cache_cap","cache not bounded");
    require(a->expression.to_string()==saved&&a->truth(zero),"cached AST lifetime broken");
    Coverage cov(f);policy.prepare(cov,nullptr,0,0,50,0,0,false);auto r=policy.check();require(r==z3::sat&&!policy.decision.selected,"first model intervened");
    NativePhaseFeedback protected_policy(f,solver,budget,"native_feedback_one",0);protected_policy.quota_seconds=0;
    protected_policy.prepare(cov,&zero,1,1,50,0,0,false);r=protected_policy.check();require(r==z3::sat&&!protected_policy.decision.selected,"quota created action");
}
}
int main(){try{native_interface();learning();classification();std::cout<<"{\"passed\":true,\"finite_models\":16,\"disabled_keep_trace_equal\":true}\n";return 0;}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
