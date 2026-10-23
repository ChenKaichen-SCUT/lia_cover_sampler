#include "search.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
using namespace lia;
static void check(bool v,const std::string &s) { if(!v) throw std::runtime_error(s); }
static std::string fixture(const std::string &s) {
    static unsigned n=0;
    auto p=std::filesystem::temp_directory_path()/("lia-line-"+std::to_string(++n)+".smt2");
    std::ofstream(p)<<s; return p.string();
}
static int variable(Formula &f,const std::string &name) {
    for(size_t i=0;i<f.variables.size();++i)
        if(f.nodes[f.variables[i]].expr.to_string()==name) return i;
    throw std::runtime_error("variable missing");
}
int main() {
    try {
        std::mt19937_64 rng(19);
        {
            Formula f(fixture("(declare-const x Int)(declare-const y Int)"
                "(assert (and (= (+ x y) 10) (or (= x 0) (= x 10))))"));
            Assignment m(2),d(2); int x=variable(f,"x"),y=variable(f,"y");
            m[y]=10; d[x]=1; d[y]=-1;
            Budget b; b.seconds=5; AffineSyntax syntax(f);
            auto eq=structural_equalities(f,syntax,b);
            check(eq.complete && eq.rows.size()==1,"only top-level conjunction equality is structural");
            LineAnalyzer line(f,m,d,syntax,b); auto r=line.feasible();
            check(r.status==LineStatus::Exact && r.domain.intervals.size()==2,"two disconnected components");
            for(int z=-20;z<=20;++z) check(r.domain.contains(z)==(z==0 || z==10),"{0,10} exact");
            for(unsigned bit_index:{1U,3U}) {
                auto z=line_bit_witness(r.domain,1,0,bit_index,true,rng);
                check(z && *z==10,"cross branch bit witness");
            }
            check(!line_bit_witness(r.domain,1,0,0,true,rng),"bit zero has no witness");
            Coverage cov(f); cov.accept(f.evaluate(m)); Search search(f); Stats stats;
            auto candidate=search.line(m,cov,b,rng,stats,{},[](const auto&){return false;});
            check(candidate.model && (*candidate.model)[x]==10 && f.validate(*candidate.model),
                  "integrated structural kernel crosses branch");
            check(stats.counts["line_cross_branch_moves"]>0,"cross branch diagnostic");
        }
        {
            Formula f(fixture("(declare-const x Int)(declare-const y Int)"
                "(assert (= y (ite (>= x 0) x (- x))))"));
            Budget b; AffineSyntax syntax(f);
            check(structural_equalities(f,syntax,b).rows.empty(),"ite relation not global affine equality");
            Assignment m{1,1},d{1,0};
            LineAnalyzer line(f,m,d,syntax,b);
            check(line.feasible().status==LineStatus::Unsupported,"arithmetic ite explicit fallback");
        }
        {
            std::vector<std::string> expressions={
                "(not (<= (* -3 x) 7))", "(not (= (* -2 x) 4))",
                "(and (< x 7) (> (* -2 x) -13))",
                "(or (< x -5) (> x 8))",
                "(=> (< x 0) (> x -4))",
                "(xor (< x -2) (> x 4))",
                "(ite (< x 1) (>= x -7) (<= x 9))",
                "(= (< x 2) (> x -3))",
                "(or (= x 0) (and (= x 10) (= y 0)))"
            };
            uint64_t checks=0;
            for(const auto &expr:expressions) {
                Formula f(fixture("(declare-const x Int)(declare-const y Int)(assert "+expr+")"));
                Assignment m(f.variables.size()),d(f.variables.size()); d[variable(f,"x")]=-2;
                Budget b; b.seconds=10; AffineSyntax syntax(f);
                LineAnalyzer line(f,m,d,syntax,b); auto r=line.feasible();
                check(r.status==LineStatus::Exact,"supported Boolean affine formula");
                for(int z=-25;z<=25;++z) {
                    Assignment sample=m;
                    for(size_t k=0;k<m.size();++k) sample[k]+=z*d[k];
                    bool valid=f.validate(sample);
                    check(valid==r.domain.contains(z),"line domain vs original Z3");
                    if(valid) {
                        auto o=f.evaluate(sample);
                        for(size_t n=0;n<f.nodes.size();++n) {
                            auto reach=line.observable(n);
                            check(reach.status==LineStatus::Exact && reach.domain.contains(z)==bool(o.visited[n]),
                                  "Reach vs original lazy traversal");
                            if(f.nodes[n].boolean) {
                                auto desired=line.boolean_target({int(n),0,true});
                                check(desired.status==LineStatus::Exact &&
                                    desired.domain.contains(z)==(o.visited[n] && o.values[n]!=0),
                                    "Boolean target includes Reach");
                            }
                            ++checks;
                        }
                    }
                }
            }
            check(checks>1000,"sufficient independent semantic comparisons");
        }
        {
            Formula f(fixture("(declare-const x Int)(declare-const y Int)"
                "(assert (and (= y 0) (or (= x 0) (and (= x 10) (= (+ y 1) 1)))))"));
            Assignment m(2),d(2); d[variable(f,"x")]=1;
            Budget b; AffineSyntax syntax(f); LineAnalyzer line(f,m,d,syntax,b);
            int observer=-1;
            for(size_t n=0;n<f.nodes.size();++n)
                if(f.nodes[n].expr.to_string()=="(+ y 1)") observer=n;
            check(observer>=0,"constant affine observer fixture");
            auto [alpha,beta]=line.numeric(observer);
            auto domain=line.observable(observer);
            auto witness=line_bit_witness(domain.domain,alpha,beta,0,true,rng);
            check(alpha==0 && witness && *witness==10,"alpha zero Reach change finds nonlocal branch");
        }
        {
            Z far("100000000000000000000000000000000000000000000000000");
            LineDomain domain({{far,{},false},{Z(-20),Z(-10),false}});
            auto z=line_bit_witness(domain,1,0,63,true,rng);
            check(z && domain.contains(*z) && bit(*z,63),"unbounded huge periods use arithmetic");
            LineDomain only_origin({{Z(0),{},false}});
            auto nonzero=line_bit_witness(only_origin,0,1,63,false,rng);
            check(nonzero && *nonzero!=0,"remove zero before unbounded period choice");
            check(LineDomain::all().complement().intervals.empty(),"all complement empty");
            check(LineDomain{}.complement().contains(far),"empty complement all");
        }
        {
            Formula f(fixture("(declare-const x Int)(assert (or (= x 0) (= x 10)))"));
            Assignment m{0},d{1}; Budget b; LineLimits limits; limits.intervals=1;
            AffineSyntax syntax(f,limits); LineAnalyzer line(f,m,d,syntax,b);
            check(line.feasible().status==LineStatus::BudgetExceeded,"interval cap distinct from unsupported");
            Budget expired; expired.seconds=0;
            LineAnalyzer late(f,m,d,syntax,expired);
            check(late.feasible().status==LineStatus::BudgetExceeded,"deadline explicit");
        }
        std::cout<<"line tests passed\n"; return 0;
    } catch(const std::exception &e) { std::cerr<<e.what()<<'\n'; return 1; }
}
