#pragma once
#include "output_refiner_api.hpp"
#include <fstream>
#include <iomanip>
// Compiled only with the frozen A/B core in its own namespace.
namespace lia {
inline uint64_t output_opportunity_signature(const Formula &f,const Observation &o,uint64_t covered) {
    uint64_t h=1469598103934665603ULL;
    for(size_t i=0;i<f.nodes.size();++i) if(f.nodes[i].boolean) {
        h^=static_cast<uint64_t>(o.visited[i])+(o.visited[i] && o.values[i]!=0 ? 2 : 0);h*=1099511628211ULL;
    }
    return h^(covered*0x9e3779b97f4a7c15ULL);
}
class FormalOutput {
    Formula &f;
    Budget &overall;
    bool enabled;
    unsigned run_seed;
    std::ofstream &typed,&legacy,&emission;
    std::ofstream raw_log;
    std::ofstream cost_log;
    std::unique_ptr<output_refiner::Refiner> refiner;
    std::set<std::string> delivered;
    unsigned failures=0;
    uint64_t prior_opportunity=0;
    bool prior_locked=false;
    double reserve=.02;
    uint64_t opportunity(const Observation &o) const {
        return output_opportunity_signature(f,o,covered);
    }
  public:
    Coverage coverage;
    Stats stats;
    unsigned count=0;
    uint64_t covered=0;
    double extra_seconds=0,common_output_seconds=0,overrun_seconds=0;
    // Offline replay only: historical generator cost allocates credit, never an emission timestamp.
    double replay_generator_credit=-1;
    FormalOutput(Formula &formula,Budget &budget,bool polish,unsigned seed,const std::string &folder,
        std::ofstream &models,std::ofstream &samples,std::ofstream &timestamps)
        :f(formula),overall(budget),enabled(polish),run_seed(seed),typed(models),legacy(samples),emission(timestamps),
         raw_log(folder+"/raw_generator.jsonl"),cost_log(folder+"/output_transactions.jsonl"),coverage(formula) {}
    double generator_seconds() const {return replay_generator_credit>=0?replay_generator_credit:
        std::max(0.,overall.elapsed()-extra_seconds-common_output_seconds);}
    void deliver(const Assignment &raw,const Observation &original,const std::string &category,unsigned raw_index,uint64_t raw_covered) {
        auto common_start=Clock::now();
        raw_log<<"{\"raw_generator_index\":"<<raw_index<<",\"raw_generator_elapsed\":"<<std::setprecision(12)<<overall.elapsed()
            <<",\"raw_generator_covered\":"<<raw_covered<<",\"category\":"<<quote(category)<<",\"assignment\":"<<f.model_json(raw)<<"}\n";
        raw_log.flush();if(!raw_log) throw std::runtime_error("raw generator log write failed");
        const Assignment *selected=&raw;Observation observation=original;auto gain=coverage.gain(original);
        output_refiner::Proposal proposal;
        common_output_seconds+=std::chrono::duration<double>(Clock::now()-common_start).count();
        if(enabled && raw_index>0) {
            auto extra_start=Clock::now();
            const double allowance=.06+.05*generator_seconds();
            const double credit=allowance-extra_seconds;
            const double available=std::min({.06,credit,overall.left()-reserve});
            if(available<.001 || overall.left()<reserve+.001 || (!refiner && overall.left()<1)) ++stats.counts["gate_insufficient_credit_or_deadline"];
            else {
                const auto signature=opportunity(original);
                bool changed=signature!=prior_opportunity;
                if(!changed && ((prior_locked && raw_index%16) || (failures>=3 && raw_index%8)))
                    ++stats.counts[prior_locked?"gate_same_region_locked":"gate_low_recent_gain"];
                else {
                    prior_opportunity=signature;if(changed) failures=0;
                    if(!refiner) {
                        auto t=Clock::now();refiner=std::make_unique<output_refiner::Refiner>(input_path);
                        double cost=std::chrono::duration<double>(Clock::now()-t).count();
                        stats.times["extra_formula_initialization"]+=cost;reserve=std::max(reserve,2*cost);
                    }
                    double remaining=available-std::chrono::duration<double>(Clock::now()-extra_start).count();
                    if(remaining>=.001 && overall.left()>reserve) {
                        output_refiner::Seen seen;seen.reserve(coverage.states.size());
                        for(auto state:coverage.states) seen.push_back({state.zero,state.one});
                        remaining=std::min(available-std::chrono::duration<double>(Clock::now()-extra_start).count(),overall.left()-reserve);
                        proposal=refiner->propose(raw,seen,remaining,output_refiner::independent_seed(run_seed,raw_index),
                            [&](const auto &key){return delivered.count(key)!=0;});
                        ++stats.counts["polish_attempted_raw_models"];
                        prior_locked=proposal.no_free_direction;
                        stats.counts["gate_rank_unknown"]+=!proposal.rank_known;
                        stats.counts["gate_region_locked_structure_unknown"]+=prior_locked;
                        if(proposal.gain_after>proposal.gain_before) failures=0;else ++failures;
                        if(proposal.changed && !delivered.count(f.key(proposal.model))) {
                            if(!f.validate(proposal.model)) throw std::logic_error("polished output invalid in original AB formula");
                            observation=f.evaluate(proposal.model);gain=coverage.gain(observation);selected=&proposal.model;
                            ++stats.counts["replacement_accepted"];
                        } else if(proposal.changed) ++stats.counts["replacement_duplicate_fallback"];
                    } else ++stats.counts["gate_initialization_consumed_credit"];
                }
            }
            extra_seconds+=std::chrono::duration<double>(Clock::now()-extra_start).count();
            const double overrun=std::max(0.,extra_seconds-allowance);
            if(overrun>0) {++stats.counts["extra_budget_overrun_events"];overrun_seconds=std::max(overrun_seconds,overrun);}
        } else if(enabled) ++stats.counts["first_raw_emitted_without_polish"];
        common_start=Clock::now();
        if(selected==&raw) ++stats.counts["fallback_to_raw"];
        auto key=f.key(*selected);
        if(delivered.count(key)) ++stats.counts["raw_already_in_formal_output"];
        else if(overall.done()) ++stats.counts["deadline_before_formal_write"];
        else {
            typed<<"{\"index\":"<<count<<",\"emitted_index\":"<<count<<",\"raw_generator_index\":"<<raw_index
                <<",\"elapsed\":"<<std::setprecision(12)<<overall.elapsed()<<",\"category\":"<<quote(category)
                <<",\"new_coverage\":"<<gain.covered<<",\"new_observations\":"<<gain.observed
                <<",\"covered\":"<<covered+gain.covered<<",\"total\":"<<f.total_bits()
                <<",\"replaced\":"<<(selected==&raw?"false":"true")<<",\"assignment\":"<<f.model_json(*selected)<<"}\n";
            typed.flush();if(!typed) throw std::runtime_error("formal output write failed");
            if(legacy.is_open()) {
                legacy<<count<<": ";for(size_t i=0;i<selected->size();++i) legacy<<f.nodes[f.variables[i]].expr.decl().name().str()<<":"<<(*selected)[i]<<";";
                legacy<<'\n';legacy.flush();
            }
            emission<<count<<","<<std::setprecision(12)<<overall.elapsed()<<'\n';emission.flush();
            if(!emission || (legacy.is_open() && !legacy)) throw std::runtime_error("formal output flush failed");
            coverage.accept(observation);covered+=gain.covered;delivered.insert(key);++count;
        }
        common_output_seconds+=std::chrono::duration<double>(Clock::now()-common_start).count();
        const auto log_start=Clock::now();
        cost_log<<"{\"raw_generator_index\":"<<raw_index<<",\"emitted_samples\":"<<count<<",\"emitted_covered\":"<<covered
            <<",\"extra_seconds\":"<<std::setprecision(12)<<extra_seconds<<",\"ab_main_seconds\":"<<generator_seconds()
            <<",\"max_overrun_seconds\":"<<overrun_seconds<<",\"replacement_selected\":"<<(selected==&raw?"false":"true")<<"}\n";
        cost_log.flush();if(!cost_log) throw std::runtime_error("output transaction log write failed");
        common_output_seconds+=std::chrono::duration<double>(Clock::now()-log_start).count();
    }
    std::string input_path;
    std::string fields(unsigned raw_count,uint64_t raw_covered) const {
        std::ostringstream out;out<<std::setprecision(12)<<",\"raw_generator_samples\":"<<raw_count
            <<",\"raw_generator_covered\":"<<raw_covered<<",\"emitted_samples\":"<<count<<",\"emitted_covered\":"<<covered
            <<",\"ab_main_seconds\":"<<generator_seconds()<<",\"extra_seconds\":"<<extra_seconds
            <<",\"extra_allowance_seconds\":"<<.06+.05*generator_seconds()<<",\"extra_budget_overrun_seconds\":"<<overrun_seconds
            <<",\"common_output_seconds\":"<<common_output_seconds<<",\"output_statistics\":"<<stats.json()
            <<",\"refinement_statistics\":"<<(refiner?refiner->statistics():"{}")
            <<",\"generator_source_commit\":\"269de45379671277fb9b3780734a4c66e388e8e2\"";
        return out.str();
    }
};
}
