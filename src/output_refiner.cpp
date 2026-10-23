#include "output_refiner_api.hpp"
#include "observation_preservation.hpp"
#include "search.hpp"
namespace output_refiner {
using namespace lia;
struct Refiner::Impl {
    Formula formula;
    Search search;
    Stats stats;
    explicit Impl(const std::string &input):formula(input),search(formula) {search.options.line=false;}
};
Refiner::Refiner(const std::string &input):impl(std::make_unique<Impl>(input)) {}
Refiner::~Refiner()=default;
void cancel() {lia::interrupted=1;}
uint64_t independent_seed(uint64_t run_seed,uint64_t index) {
    uint64_t v=run_seed ^ (index*0x9e3779b97f4a7c15ULL) ^ 0x504f4c4953484142ULL;
    v=(v^(v>>30))*0xbf58476d1ce4e5b9ULL;v=(v^(v>>27))*0x94d049bb133111ebULL;
    return v^(v>>31);
}
Proposal Refiner::propose(const Values &raw,const Seen &seen,double seconds,uint64_t seed,
    const std::function<bool(const std::string &)> &committed) {
    auto &f=impl->formula;auto &search=impl->search;auto &stats=impl->stats;
    Budget budget;budget.seconds=seconds;budget.label="polish_total";
    if(seen.size()!=f.nodes.size()) throw std::logic_error("A/B and refiner AST index mismatch");
    Coverage coverage(f);
    for(size_t i=0;i<seen.size();++i) coverage.states[i]={seen[i][0],seen[i][1]};
    const auto snapshot=coverage.json();const auto original=f.evaluate(raw);
    Proposal output;output.model=raw;auto best_gain=coverage.gain(original);
    output.gain_before=output.gain_after=best_gain.covered;
    bool better_found=false;
    search.begin_action();
    stats.counts["safe_region_rank_known_last"]=0;
    search.candidate_observer=[&](const Assignment &model,const Observation &observation,Gain gain) {
        const auto start=Clock::now();++stats.counts["replacement_candidates_checked"];
        better_found|=gain.covered>output.gain_before;
        bool preserves=preserves_new_observations(f,coverage,original,observation);
        ++stats.counts[preserves?"replacement_preserves_observations":"replacement_rejected_by_preservation"];
        if(preserves && (gain.covered>best_gain.covered ||
           (gain.covered==best_gain.covered && gain.observed>best_gain.observed))) {
            output.model=model;best_gain=gain;output.gain_after=gain.covered;output.changed=raw!=model;
        }
        stats.times["replacement_preservation_checks"]+=std::chrono::duration<double>(Clock::now()-start).count();
    };
    struct Reset {Search &search;~Reset(){search.candidate_observer={};}} reset{search};
    std::mt19937_64 rng(seed);
    auto result=search.polish_candidate(raw,coverage,budget,rng,stats,[&](const std::string &key) {
        bool duplicate=committed(key);stats.counts["replacement_duplicate"]+=duplicate;return duplicate;
    });
    ++stats.counts["output_polish_calls"];
    stats.counts["polish_found_better_gain"]+=better_found;
    output.attempts=result.attempted_steps;
    output.rank_known=stats.counts["safe_region_rank_known_last"];
    output.no_free_direction=output.rank_known && result.moves==0 && stats.counts["safe_region_free_dimension_last"]==0;
    if(snapshot!=coverage.json()) throw std::logic_error("refiner mutated frozen formal coverage");
    if(output.changed && (!f.validate(output.model) ||
        !preserves_new_observations(f,coverage,original,f.evaluate(output.model))))
        throw std::logic_error("refiner final preservation assertion: "+f.model_json(output.model));
    return output;
}
std::string Refiner::statistics() const {return impl->stats.json();}
}
