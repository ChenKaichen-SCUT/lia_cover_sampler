#include "bofill_session.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <unistd.h>
using namespace lia;
void require(bool condition,const char *message){if(!condition)throw std::runtime_error(message);}
int main(){try{
    auto path=std::filesystem::temp_directory_path()/("bofill-fallback-"+std::to_string(getpid())+".smt2");
    std::ofstream(path)<<"(declare-const x Int)(declare-const b Bool)(assert (and (or (= x (- 18446744073709551617))(= x 18446744073709551617))(or b (= x 18446744073709551617))))";
    Formula f(path.string());Budget budget;budget.seconds=30;CompiledProblem compiled(f,CompileMode::Full,budget);
    BofillSolverSession session(compiled,budget,0);Coverage coverage(f);std::set<std::string> seen;std::vector<Assignment> saved;
    session.solver().set("timeout",2000u);require(session.solver().check()==z3::sat,"initial SAT");auto first=session.problem().restore(session.solver().get_model());require(f.validate(first),"first validity");
    saved.push_back(first);seen.insert(f.key(first));coverage.accept(f.evaluate(first));auto before=coverage.json();auto original_start=budget.start;
    session.reset_to_original(saved);require(session.problem().mode==CompileMode::Plain,"fallback representation");require(budget.start==original_start,"deadline reset");require(coverage.json()==before,"coverage reset");
    while(true){session.solver().set("timeout",2000u);auto answer=session.solver().check();if(answer==z3::unsat)break;require(answer==z3::sat,"fallback unknown");auto a=session.problem().restore(session.solver().get_model());require(f.validate(a),"fallback validity");require(seen.insert(f.key(a)).second,"fallback repeats formal model");coverage.accept(f.evaluate(a));session.solver().add(session.problem().block_original(a));}
    require(seen.size()==3,"fallback lost original models");
    Budget timed;timed.seconds=12;CompiledProblem p(f,CompileMode::Full,timed);BofillSolverSession initial(p,timed,0);
    require(!initial.should_fallback(true,0),"early fallback");timed.start-=std::chrono::seconds(10);
    require(initial.should_fallback(true,0),"10-second protection absent");require(!initial.should_fallback(false,0),"opt-in absent");require(!initial.should_fallback(true,1),"formal witness ignored");
    require(initial.action_left(true,0)==0,"initial budget not clipped");initial.reset_to_original({});require(initial.action_left(true,0)<=2,"fallback renewed total budget");require(!initial.should_fallback(true,0),"fallback loop");
    timed.start-=std::chrono::seconds(5);require(timed.done(),"parent expiry");
    std::filesystem::remove(path);std::cout<<"fallback full-model and coverage continuity passed\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
