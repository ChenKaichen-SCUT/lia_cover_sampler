#include "conditional_observation.hpp"
#include <fstream>
#include <iostream>
#include <filesystem>
#include <unistd.h>
using namespace lia;
void require(bool b,const char*s){if(!b)throw std::runtime_error(s);}
int main(){try{
 std::vector<std::pair<std::string,unsigned>> cases={
 {"(and (or (and (= x 0) (= y 0)) (and (= x 4795) (= y 4795))) (= z (+ x y)))",1},
 {"(and (or (and (= x 0) (= y 7)) (and (= x 1) (= y 7))) (= z (- x)))",1},
 {"(and (or (= x (- 18446744073709551617)) (= x 18446744073709551618)) (= y x) (= z (- y)))",1},
 {"(and (or (= x 0) (= y 1)) (= x 0) (= y 1) (= z 0))",0},
 {"(and (ite b (or (= x 0) (= x 1)) (= x 2)) (= y 0) (= z 0))",0},
 {"(and (or (= x 0) (= x 1)) (= y 0) (= z 0) (or true (= (+ x 42) 42)))",1},
 {"(and (or (= x 0) (= x 1)) (= y 0) (= z 0) (= (+ x y) (+ x y)))",1},
 {"(and (or (and (= x 0) (= y 0)) (and (= x 1) (= z 1))) (= y 0) (= z 1))",0},
 {"(and (or (and (= x 0) (= y 0)) (and (= x 0) (= y 0))) (= z 0))",0}};
 unsigned checks=0;auto path=std::filesystem::temp_directory_path()/("soft-summary-"+std::to_string(getpid())+".smt2");
 for(auto &[body,expected]:cases){std::ofstream out(path);out<<"(declare-const x Int)(declare-const y Int)(declare-const z Int)(declare-const b Bool)(assert "<<body<<")";out.close();Formula f(path);Budget budget;budget.seconds=20;ConditionalObservations g(f,budget);require(g.complete,"small summary truncated");require(g.atoms.size()==expected,"unsupported catalog inference");
 std::set<std::string> reference;
 for(unsigned mode=0;mode<3;++mode){z3::solver solver(f.ctx,z3::solver::simple{});solver.add(f.root);SoftPhaseBridge bridge(solver);Coverage cov(f);std::set<std::string> models;unsigned epoch=0;
 while(epoch<100){std::vector<SoftPhasePreference> ps;for(auto &a:g.atoms)ps.push_back({f.nodes[a.node].expr,mode==1?bool(epoch%2):!bool(epoch%2),a.group,epoch});unsigned assertions=solver.assertions().size();if(mode)bridge.replace_preferences(ps,epoch);require(solver.assertions().size()==assertions,"summary added constraints");auto r=mode?bridge.check():solver.check();require(std::string(Z3_solver_get_soft_phase_log(f.ctx,solver)).find("\"active\":false")!=std::string::npos,"bridge failed to clear");if(r==z3::unsat)break;require(r==z3::sat,"small unknown");auto m=f.complete(solver.get_model());require(f.validate(m),"invalid full model");require(models.insert(f.key(m)).second,"duplicate");auto obs=f.evaluate(m);auto gain=cov.gain(obs);
 for(auto &a:g.atoms){bool truth=f.as_model(m).eval(f.nodes[a.node].expr,true).is_true();require(g.contains(a.sides[truth],obs),"conditional observation unsound");require(gain.covered>=g.score(a.sides[truth],cov).covered,"lower bound unsound");++checks;}
 cov.accept(obs);z3::expr_vector block(f.ctx);for(size_t i=0;i<m.size();++i)block.push_back(f.nodes[f.variables[i]].expr!=f.value(i,m[i]));solver.add(z3::mk_or(block));++epoch;}
 require(epoch<100,"nonfinite fixture");if(!mode)reference=models;else require(models==reference,"mode changed all-model set");}
 }
 {z3::context c;z3::solver s(c,z3::solver::simple{});SoftPhaseBridge bridge(s);auto x=c.bool_const("x");s.add(x||!x);bridge.replace_preferences({{x,true,0,0}},1);bool caught=false;try{bridge.replace_preferences({{c.int_const("bad"),true,0,0}},2);}catch(const std::invalid_argument&){caught=true;}require(caught,"invalid atom accepted");require(std::string(Z3_solver_get_soft_phase_log(c,s)).find("\"active\":false")!=std::string::npos,"exception retained old preferences");s.set("rlimit",1u);bridge.replace_preferences({{x,true,0,0}},3);bridge.check();require(std::string(Z3_solver_get_soft_phase_log(c,s)).find("\"active\":false")!=std::string::npos,"unknown lifecycle");}
 std::filesystem::remove(path);std::cout<<"conditional implications checked: "<<checks<<'\n';return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
