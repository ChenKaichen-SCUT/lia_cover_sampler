#include "search.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>

using namespace lia;
static void check(bool ok,const std::string &message) {
    if(!ok) throw std::runtime_error(message);
}
static std::string fixture(const std::string &text) {
    static unsigned n=0;
    auto path=std::filesystem::temp_directory_path()/("lia-preparation-"+std::to_string(++n)+".smt2");
    std::ofstream(path)<<text;return path.string();
}
static void synthetic_targets(Search &search,Formula &f) {
    search.diagnostic_directions=std::vector<Direction>{{{1},"synthetic","safe_region","fixture"}};
    search.diagnostic_targets=[&f] {
        std::vector<Target> goals;
        for(unsigned bit_index=0;bit_index<32;++bit_index) goals.push_back({f.variables[0],bit_index,false});
        return goals;
    };
}
int main() {try {
    const std::string wide="(declare-const x Int)(assert (and (<= -1000000 x)(<= x 1000000)))";
    {
        Formula f(fixture(wide));Coverage coverage(f);coverage.accept(f.evaluate({0}));
        const auto frozen=coverage.json();
        Stats old_stats,new_stats;PolishResult old_result,new_result;
        for(bool reuse:{false,true}) {
            Search search(f);search.options.preparation_reuse=reuse;synthetic_targets(search,f);
            Budget budget;budget.seconds=5;std::mt19937_64 rng(13);
            auto &stats=reuse?new_stats:old_stats;
            auto result=search.polish_candidate({-1},coverage,budget,rng,stats,[](const auto&){return false;});
            check(result.model==Assignment{-1},"maximal input remains fallback after flat batches");
            check(result.attempted_steps==2,"two genuinely completed flat batches stop polish");
            check(result.candidates_per_step==std::vector<unsigned>({4,4}),"four distinct candidates per full batch");
            check(stats.counts["polish_completed_flat_batches"]==2,"flat count excludes incomplete work");
            check(coverage.json()==frozen,"preparation cannot mutate committed coverage");
            if(reuse) new_result=result;else old_result=result;
        }
        check(old_stats.counts["preparation_region_builds"]==2 &&
              old_stats.counts["preparation_repeated_region_builds"]==1,"control rebuilds same base per short step");
        check(new_stats.counts["preparation_region_builds"]==1 &&
              new_stats.counts["preparation_repeated_region_builds"]==0 &&
              new_stats.counts["preparation_reuse_hits"]==1,"reuse keeps one base preparation across batches");
        check(new_stats.counts["preparation_feasibility_builds"]==1,"feasible interval prepared once for retained direction");
    }
    {
        // A deterministic injected expiry occurs after preparation but before
        // target work. A later invocation must rebuild, never reuse half state.
        Formula f(fixture(wide));Coverage coverage(f);coverage.accept(f.evaluate({0}));
        Search search(f);search.options.preparation_reuse=true;synthetic_targets(search,f);
        Budget budget;budget.seconds=5;Stats stats;std::mt19937_64 rng(3);
        search.diagnostic_boundary=[&](const auto &stage){if(stage=="region_prepared") budget.seconds=0;};
        auto first=search.polish_candidate({-1},coverage,budget,rng,stats,[](const auto&){return false;});
        check(first.model==Assignment{-1} && stats.counts["polish_completed_flat_batches"]==0,
              "budget expiry is not a completed flat search");
        check(stats.counts["polish_budget_exits"]==1 && stats.counts["preparation_region_builds"]==1,
              "budget exit charged once without retrying preparation");
        search.diagnostic_boundary={};search.begin_action();Budget fresh;fresh.seconds=5;
        auto second=search.polish_candidate({-1},coverage,fresh,rng,stats,[](const auto&){return false;});
        check(second.attempted_steps==2 && stats.counts["preparation_region_builds"]==2,
              "fresh invocation reconstructs a complete region");
    }
    {
        Formula f(fixture("(declare-const x Int)(assert (= x 0))"));
        Coverage coverage(f);coverage.accept(f.evaluate({0}));Search search(f);
        search.options.preparation_reuse=true;Stats stats;Budget budget;budget.seconds=5;
        std::mt19937_64 rng(0);
        auto result=search.polish_candidate({0},coverage,budget,rng,stats,[](const auto&){return false;});
        check(result.model==Assignment{0} && result.attempted_steps==1,"proved local lock terminates without a duplicate call");
        check(stats.counts["direction_nullspace_zero"]==1 && stats.counts["preparation_region_builds"]==1,
              "one complete zero-dimensional proof is sufficient");
        check(stats.counts["candidate_attempts"]==0,"locked state does not attempt targets");
    }
    {
        std::string text;
        for(unsigned i=0;i<8;++i) text+="(declare-const x"+std::to_string(i)+" Int)(assert (and (<= -1 x"+
            std::to_string(i)+")(<= x"+std::to_string(i)+" 0)))";
        Formula f(fixture(text));Assignment seed(8);Coverage coverage(f);coverage.accept(f.evaluate(seed));
        const auto frozen=coverage.json();Search search(f);search.options.preparation_reuse=true;
        search.options.direction_cap=1;search.options.polish_step_candidates=1;
        search.options.polish_flat_steps=6;
        Stats stats;Budget budget;budget.seconds=5;std::mt19937_64 rng(21);
        unsigned observed=0;
        search.candidate_observer=[&](const Assignment &candidate,const Observation &,Gain) {
            ++observed;check(f.validate(candidate),"every observed candidate satisfies the original formula");
        };
        auto result=search.polish_candidate(seed,coverage,budget,rng,stats,[](const auto&){return false;});
        check(result.moves>=2 && stats.counts["preparation_move_invalidations"]==result.moves,
              "accepted moves invalidate all base-dependent preparation");
        check(stats.counts["preparation_region_builds"]>=result.moves &&
              stats.counts["preparation_repeated_region_builds"]==0,"each rebuilt region has a different base");
        check(observed==stats.counts["unique_candidates_scored"],"observer receives every valid candidate, not just batch winners");
        check(result.attempted_steps<=6 && observed<=24 && stats.counts["candidate_attempts"]<=1536,
              "whole-call batch, candidate and attempt bounds");
        check(coverage.json()==frozen && f.validate(result.model),"moves preserve snapshot and final legality");
    }
    {
        Formula f(fixture(wide));Coverage coverage(f);coverage.accept(f.evaluate({0}));
        Search search(f);search.options.preparation_reuse=true;search.options.attempt_cap=3;
        search.options.polish_flat_steps=10;
        synthetic_targets(search,f);Stats stats;Budget budget;budget.seconds=5;std::mt19937_64 rng(9);
        auto result=search.polish_candidate({-1},coverage,budget,rng,stats,[](const auto&){return false;});
        check(stats.counts["candidate_attempts"]==18 && result.attempted_steps==6,
              "six attempt-limited partial batches retain the original total bound");
        check(stats.counts["polish_stopped_by_attempt_budget"]==1 &&
              stats.counts["polish_attempt_limited_batches"]==6,"per-batch and whole-call attempt stops are explicit");
        check(stats.counts["preparation_region_builds"]==1,"attempt-limited batches continue the same preparation");
    }
    {
        Formula f(fixture("(declare-const x Int)(assert (and (<= -1 x)(<= x 0)))"));
        Coverage coverage(f);coverage.accept(f.evaluate({0}));Search search(f);search.options.preparation_reuse=true;
        search.diagnostic_directions=std::vector<Direction>{{{1},"synthetic","safe_region","fixture"}};
        search.diagnostic_targets=[&]{return std::vector<Target>{{f.variables[0],63,true}};};
        Stats stats;Budget budget;budget.seconds=5;std::mt19937_64 rng(0);
        search.candidate_observer=[&](const Assignment &,const Observation &,Gain){budget.seconds=0;};
        auto result=search.polish_candidate({0},coverage,budget,rng,stats,[](const auto&){return false;});
        check(result.model==Assignment{-1} && result.moves==1,"deadline retains a valid improvement from a partial final batch");
        check(stats.counts["polish_budget_exits"]==1 && stats.counts["polish_completed_flat_batches"]==0,
              "partial deadline batch retains result but never increments flat");
    }
    std::cout<<"preparation reuse tests passed\n";return 0;
} catch(const std::exception &e) {std::cerr<<e.what()<<'\n';return 1;}}
