#include <z3++.h>
#include <iostream>
#include <set>
#include <string>
#include <vector>
#include <thread>
#include <atomic>
#include <chrono>
#include <stdexcept>
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
}
int main(){try{native_interface();std::cout<<"native callback fixed-operation regression passed\n";return 0;}catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
