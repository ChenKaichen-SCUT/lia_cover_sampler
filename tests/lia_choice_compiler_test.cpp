#include "lia_choice_compiler.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <unistd.h>
using namespace lia;
static void check(bool b,const char*s){if(!b)throw std::runtime_error(s);}
static unsigned serial=0;
static std::string input(const std::string &text) {
    auto p=std::filesystem::temp_directory_path()/("lia-compile-"+std::to_string(getpid())+"-"+std::to_string(serial++)+".smt2");
    std::ofstream(p)<<text;return p.string();
}
static std::set<std::string> enumerate(Formula &f,CompiledProblem &p,size_t cap=1000) {
    z3::solver s(f.ctx,z3::solver::simple{});s.add(p.compiled_formula);s.set("timeout",2000u);
    std::set<std::string> models;
    while(true){auto r=s.check();if(r==z3::unsat)break;check(r==z3::sat,"small enumeration unknown");
        auto a=p.restore(s.get_model());check(f.validate(a),"invalid original model");
        check(models.insert(f.key(a)).second,"duplicate reconstruction");
        check(models.size()<cap,"enumeration incomplete");
        auto lifted=p.lift(a);check(lifted.eval(p.compiled_formula,true).is_true(),"lift does not satisfy compiled F");
        check(p.restore(lifted)==a,"round trip");s.add(p.block_original(a));}
    return models;
}
static void compare(const std::string &text,size_t expected,ChoiceCompileLimits limits={}) {
    auto path=input(text);Formula f(path);Budget b;b.seconds=30;
    CompiledProblem plain(f,false,b),compiled(f,true,b,limits);
    auto before=enumerate(f,plain),after=enumerate(f,compiled);
    if(before!=after){std::cerr<<text<<"\n"<<compiled.json()<<"\nbefore:";for(auto &x:before)std::cerr<<x<<";";std::cerr<<"\nafter:";for(auto &x:after)std::cerr<<x<<";";throw std::runtime_error("full original model sets differ");}check(before.size()==expected,"unexpected model count");
    // Surjectivity is tested from EVERY original model, not just compiled witnesses.
    z3::solver s(f.ctx);s.add(f.root);
    while(s.check()==z3::sat){auto a=f.complete(s.get_model());auto m=compiled.lift(a);
        check(m.eval(compiled.compiled_formula,true).is_true(),"lost original model");
        check(compiled.restore(m)==a,"original forward round trip");s.add(plain.block_original(a));}
    std::filesystem::remove(path);
}
int main(){try{
    compare("(declare-const x Int)(assert (or (= x 0)(= x 10)))",2);
    compare("(declare-const x Int)(declare-const y Int)(assert (or (and (= x 2)(= y 3))(and (= x 7)(= y (- 4)))))",2);
    compare("(declare-const x Int)(assert (or (= x 2)(= x 2)))",1);
    compare("(declare-const x Int)(declare-const y Int)(assert (and (or (= x 0)(= x 1))(or (and (= x 1)(= y 3))(and (= x 0)(= y 2)))))",2);
    compare("(declare-const b Bool)(declare-const x Int)(assert (and (>= x 0)(<= x 2)(or b (or (= x 0)(= x 1)))))",5);
    compare("(declare-const x Int)(assert (and (>= x 0)(<= x 2)(or (<= x 1)(>= x 1))))",3);
    compare("(declare-const x Int)(declare-const y Int)(assert (and (>= x 0)(<= x 1)(>= y 0)(<= y 1)(or (= x 0)(= y 0))))",3);
    compare("(declare-const x Int)(declare-const y Int)(declare-const z Int)(assert (and (or (= z 0)(= z 1))(= y (+ z 2))(= x (+ y 3))))",2);
    compare("(declare-const x Int)(declare-const y Int)(assert (and (= (+ (* 2 x) y) 7)(>= x 0)(<= x 3)))",4);
    compare("(declare-const x Int)(declare-const y Int)(assert (and (= (+ (* 2 x) (* 2 y)) 7)(>= x 0)(<= x 3)))",0);
    compare("(declare-const x Int)(declare-const y Int)(assert (and (= x y)(= y x)(>= x (- 2))(<= x 2)))",5);
    compare("(declare-const b Bool)(declare-const x Int)(assert (or (and b (= x 18446744073709551617))(and (not b)(= x (- 18446744073709551617)))))",2);
    compare("(declare-const x Int)(declare-const y Int)(assert (and (or (= x 0)(= x 1))(>= y 0)(<= y 2)))",6);
    compare("(declare-const lia_choice!0 Int)(declare-const x Int)(assert (and (= lia_choice!0 3)(or (= x 0)(= x 1))))",2);
    compare("(assert true)",1);compare("(assert false)",0);
    ChoiceCompileLimits zero;zero.seconds=0;compare("(declare-const x Int)(assert (or (= x 0)(= x 2)))",2,zero);
    ChoiceCompileLimits operations;operations.max_operations=25;compare("(declare-const x Int)(declare-const y Int)(assert (and (or (= x 0)(= x 2))(= y (+ x 3))))",2,operations);
    ChoiceCompileLimits growth;growth.max_dag_nodes=1;compare("(declare-const x Int)(assert (or (= x 0)(= x 2)))",2,growth);
    ChoiceCompileLimits big;big.max_coefficient_bits=3;compare("(declare-const x Int)(assert (or (= x 100)(= x 200)))",2,big);
    auto path=input("(declare-const x Int)(declare-const y Int)(assert (or (and (= x 2)(= y 3))(and (= x 7)(= y 8))))");
    Formula f(path);Budget b;b.seconds=2;CompiledProblem p(f,true,b);check(p.choices.size()==1&&p.solving_variables.size()==1,"group requires one shared choice");
    std::filesystem::remove(path);
    std::cout<<"21 complete-model-set and structural regression cases passed\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
