#include "search.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <numeric>

using namespace lia;
static void check(bool value, const std::string &message) {
    if (!value) throw std::runtime_error(message);
}
static std::string fixture(const std::string &text) {
    static unsigned sequence = 0;
    auto path = std::filesystem::temp_directory_path() / ("lia-search-" + std::to_string(++sequence) + ".smt2");
    std::ofstream(path) << text;
    return path.string();
}
static int variable(Formula &f, const std::string &name) {
    for (size_t i=0;i<f.variables.size();++i)
        if (f.nodes[f.variables[i]].expr.decl().name().str()==name) return i;
    throw std::runtime_error("missing variable");
}
int main() {
    try {
        {
            std::string text;
            for (unsigned i=0;i<40;++i) text += "(declare-const a"+std::to_string(i)+" Int)";
            text += "(declare-const u Int)(declare-const v Int)(assert (= (+ u (* 2 v)) 10))";
            for (unsigned i=0;i<40;++i) text += "(assert (>= a"+std::to_string(i)+" -10))";
            Formula f(fixture(text));
            Assignment seed(f.variables.size()); seed[variable(f,"v")]=5;
            Budget budget; budget.seconds=3;
            auto observation=f.evaluate(seed);
            Region region(f,seed,observation,budget);
            DirectionSystem system;
            system.width=seed.size(); system.equations=region.eq;
            std::mt19937_64 rng(7); Stats stats;
            auto dirs=quota_directions(system,{},seed,budget,rng,stats,8);
            bool related=false;
            for (const auto &direction:dirs) {
                check(system.kernel(direction.delta),"complete kernel check");
                if (direction.source=="equation_kernel" && direction.delta[variable(f,"u")]!=0 &&
                    direction.delta[variable(f,"v")]!=0) related=true;
            }
            check(related,"free coordinates must not crowd out u+2v kernel direction");
            check(stats.counts["safe_region_rank_last"]==1,"exact rank diagnosis");
        }
        {
            std::string text;
            for (int i=0;i<6;++i) {
                auto name="x"+std::to_string(i);
                text+="(declare-const "+name+" Int)(assert (and (>= "+name+" -1) (<= "+name+" 0)))";
            }
            Formula f(fixture(text));
            Assignment initial(6), seed(6); seed[0]=-1;
            Coverage cov(f); cov.accept(f.evaluate(initial));
            auto before=cov.json();
            Search search(f); search.options.polish_steps=32;
            search.options.direction_cap=24; search.options.attempt_cap=1024;
            search.options.candidate_cap=128;
            Budget budget; budget.seconds=4;
            Stats stats; std::mt19937_64 rng(13);
            auto result=search.polish_candidate(seed,cov,budget,rng,stats,
                [&](const std::string &key){return key==f.key(initial);});
            check(cov.json()==before,"internal candidates never change committed coverage");
            check(f.validate(result.model),"polished model satisfies original formula");
            for (const auto &value:result.model) check(value==-1,"independent variables cooperate in one model");
            check(result.after.covered>=result.before.covered,"non-decreasing single-model gain");
            check(result.retained+result.added==result.after.covered &&
                  result.retained+result.lost==result.before.covered,"refinement attribution counted once");
            auto emitted=cov.accept(f.evaluate(result.model));
            check(emitted.covered==result.after.covered,"only final complete model is committed");
        }
        {
            Formula f(fixture("(declare-const x Int)(declare-const y Int)(declare-const z Int)"
                "(assert (and (>= x -10)(<= x 10)(>= y -10)(<= y 10)(>= z -10)(<= z 10)))"));
            Assignment seed(3); Coverage cov(f); cov.accept(f.evaluate(seed));
            Search search(f);
            search.options.direction_cap=8; search.options.attempt_cap=8; search.options.candidate_cap=100;
            Budget budget; budget.seconds=2; std::mt19937_64 rng(17); Stats stats;
            auto batch=search.safe(seed,cov,budget,rng,stats,{},[](const auto&){return false;});
            check(batch.direction_attempts.size()>=3 && batch.direction_attempts.size()<=8,
                  "fairness fixture retains all coordinate directions within cap");
            auto range=std::minmax_element(batch.direction_attempts.begin(),batch.direction_attempts.end());
            check(*range.first>=1 && *range.second-*range.first<=1,
                  "every selected direction receives a first attempt before second-round allocation");
            check(search.targets.rotation_cursor()>0,"real target rotation advances");
        }
        {
            Formula f(fixture("(declare-const x Int)(declare-const y Int)"
                              "(assert (= y (ite (>= x 0) x (- x))))"));
            Budget budget;
            Assignment a{1,1}, b{-1,1};
            auto oa=f.evaluate(a), ob=f.evaluate(b);
            Region first(f,a,oa,budget), second(f,b,ob,budget);
            check(first.contains(a)&&!first.contains(b),"first branch context");
            check(second.contains(b)&&!second.contains(a),"new model rebuilds branch context");
        }
        {
            Formula f(fixture("(declare-const x Int)(assert (>= x 0))(assert (<= x 3))"));
            Coverage cov(f); cov.accept(f.evaluate(Assignment{0}));
            Global global(f,0); Stats stats; Budget budget;
            Target target{f.variables[0],63,true};
            // This is a logical/caching regression, not a 50ms latency test.
            // Instrumented old and new Z3 both exceed 50ms on some hosts.
            // Keep the UNSAT requirement strict; use a bounded test-only allowance.
            budget.seconds=3;global.target_budget.requested_timeout_ms=2000;
            auto result=global.query({target},budget,stats,2000);
            check(result.code==SolveStatus::Unsat,"unreachable sign target: status="+result.status+
                  " reason="+result.detail+" granted_ms="+std::to_string(result.actual_timeout_ms));
            TargetManager targets(f);
            targets.globally_unreachable=[&](Target t){return global.unreachable(t);};
            check(!targets.eligible(target,cov,budget.elapsed()),"local pool filters globally impossible target");
            check(cov.covered()==0 && f.total_bits()>0,"unreachable targets do not alter denominator or coverage");
        }
        {
            Formula f(fixture("(declare-const x Int)(declare-const y Int)"
                "(assert (and (<= -1 x)(<= x 0)(<= -1 y)(<= y 0)))"));
            Assignment seed(2);Coverage cov(f);cov.accept(f.evaluate(seed));
            for (bool line_mode:{false,true}) {
                Search search(f);search.options.line=line_mode;
                search.options.direction_cap=8;Budget budget;budget.seconds=3;
                search.direction_boundary=[&](unsigned i){if(i==1) budget.seconds=0;};
                Stats stats;std::mt19937_64 rng(3);
                auto result=line_mode ? search.line(seed,cov,budget,rng,stats,{},[](const auto&){return false;}) :
                    search.safe(seed,cov,budget,rng,stats,{},[](const auto&){return false;});
                check(result.model && f.validate(*result.model),"anytime preserves first direction witness at later deadline");
                check(result.first_valid_seconds>=0 && result.gain.covered>0,"first candidate latency and gain recorded");
                check(search.line_supported,"deadline never permanently disables line analysis");
            }
        }
        {
            std::string input;
            for(unsigned i=0;i<12;++i) input+="(declare-const x"+std::to_string(i)+" Int)(assert (and (<= -1 x"+
                std::to_string(i)+")(<= x"+std::to_string(i)+" 0)))";
            Formula f(fixture(input));Assignment seed(12);Coverage coverage(f);coverage.accept(f.evaluate(seed));
            Search search(f);search.options.polish_steps=24;search.options.polish_flat_steps=24;
            search.options.polish_step_candidates=2;search.options.direction_cap=1;
            Budget budget;budget.seconds=4;std::mt19937_64 rng(21);Stats stats;
            auto result=search.polish_candidate(seed,coverage,budget,rng,stats,[](const auto&){return false;});
            check(result.moves>=3,"deterministic bounded coordinate refinement performs multiple accepted steps");
            for(auto count:result.candidates_per_step) check(count<=2,"per-step candidate budget respected");
            check(search.options.candidate_cap==24,"shared options unchanged");
            Search throwing(f);Budget other;other.seconds=4;bool thrown=false;
            try {throwing.polish_candidate(seed,coverage,other,rng,stats,[](const auto&)->bool {
                throw std::runtime_error("injected predicate failure");});} catch(const std::runtime_error &) {thrown=true;}
            check(thrown && throwing.options.candidate_cap==24,"exception path cannot mutate shared candidate quota");
            TargetManager targets(f);std::set<size_t> scanned;
            targets.on_rotation_scan=[&](size_t node){scanned.insert(node);};
            for(unsigned i=0;i<4;++i) targets.select(coverage,rng,f.nodes.size(),budget);
            check(scanned.size()==f.nodes.size(),"deterministic scan visits all residue classes");
        }
        std::cout << "search tests passed\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
