// Compile twice, against original frozen and patched libraries. The observer
// consumes no random numbers and never requests a decision change.
#include <z3++.h>
#include <iostream>
struct Trace: z3::user_propagator_base {
 Trace(z3::solver&s):user_propagator_base(&s){register_decide();}
 Trace(z3::context&c):user_propagator_base(c){}
 void push()override{} void pop(unsigned n)override{std::cout<<"pop "<<n<<'\n';}
 user_propagator_base*fresh(z3::context&c)override{return new Trace(c);}
 void decide(z3::expr const&e,unsigned bit,bool pos)override{std::cout<<"decision "<<e<<' '<<bit<<' '<<pos<<'\n';}
};
int main(){for(unsigned seed=0;seed<3;++seed){z3::context c;z3::solver s(c,z3::solver::simple{});s.set("random_seed",seed);s.set("phase_selection",5u);s.set("rlimit",100000u);z3::expr_vector vars(c);for(unsigned i=0;i<5;++i)vars.push_back(c.bool_const(("x"+std::to_string(i)).c_str()));s.add(vars[0]!=vars[1]);s.add(vars[2]||vars[3]||vars[4]);Trace trace(s);for(unsigned i=0;i<vars.size();++i)trace.add(vars[i]);unsigned n=0;while(true){auto r=s.check();std::cout<<"result "<<seed<<' '<<r<<'\n';if(r!=z3::sat)break;auto m=s.get_model();z3::expr_vector block(c);for(auto v:vars){auto val=m.eval(v,true);std::cout<<v<<'='<<val<<' ';block.push_back(v!=val);}std::cout<<'\n';s.add(z3::mk_or(block));if(++n>32)return 1;}}}
