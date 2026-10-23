#include "search.hpp"
#include "observation_preservation.hpp"
#include <fstream>
#include <iomanip>
#include <iostream>
using namespace lia;

// This is a cold fixed-snapshot mechanism call, never an online sampler.
int main(int argc,char **argv) {try {
    if(argc!=8) throw std::invalid_argument("preparation_probe FORMULA PREFIX_VALUES SEED_VALUES old|reuse SECONDS RNG OUTPUT");
    const std::string mode=argv[4];
    if(mode!="old" && mode!="reuse") throw std::invalid_argument("unknown preparation mode");
#ifndef LIA_PREPARATION_REUSE_AVAILABLE
    if(mode!="old") throw std::invalid_argument("frozen reference only supports old mode");
#endif
    Budget budget;budget.seconds=std::stod(argv[5]);budget.label="cold_snapshot_call";
    if(budget.seconds<=0) throw std::invalid_argument("positive budget required");
    Stats stats;
    Formula f(argv[1]);
    const double formula_seconds=budget.elapsed();
    Coverage coverage(f);std::set<std::string> emitted;
    auto read=[&](const std::string &path,bool prefix) {
        std::ifstream in(path);if(!in) throw std::runtime_error("snapshot file missing: "+path);
        std::vector<Assignment> models;std::string line;
        while(std::getline(in,line)) {
            std::istringstream row(line);Assignment m;std::string value;
            while(row>>value) m.emplace_back(value=="true"?"1":value=="false"?"0":value);
            if(!f.validate(m)) throw std::logic_error("invalid snapshot model");
            if(prefix) {coverage.accept(f.evaluate(m));if(!emitted.insert(f.key(m)).second) throw std::logic_error("duplicate snapshot prefix");}
            else models.push_back(std::move(m));
        }
        return models;
    };
    read(argv[2],true);auto seeds=read(argv[3],false);
    if(seeds.size()!=1) throw std::invalid_argument("exactly one input model required");
    Assignment raw=seeds.front(),best=raw;
    const auto original=f.evaluate(raw);const auto before=coverage.gain(original);
    auto best_gain=before;const auto snapshot=coverage.json();
    const auto prefix_covered=coverage.covered();
    const double restore_seconds=budget.elapsed()-formula_seconds;
    double first_valid=-1,first_acceptable=-1;
    unsigned valid=0,timely_valid=0,qualified=0,rejected=0;
    double preservation_seconds=0;
    const double search_init_start=budget.elapsed();
    Search search(f);search.options.line=false;
#ifdef LIA_PREPARATION_REUSE_AVAILABLE
    search.options.preparation_reuse=mode=="reuse";
#endif
    search.begin_action();std::mt19937_64 rng(std::stoull(argv[6]));
    search.candidate_observer=[&](const Assignment &model,const Observation &observation,Gain gain) {
        ++valid;if(first_valid<0) first_valid=budget.elapsed();
        timely_valid+=!budget.done();const auto start=Clock::now();
        bool preserves=preserves_new_observations(f,coverage,original,observation);
        if(preserves) ++qualified;else ++rejected;
        if(preserves && (gain.covered>best_gain.covered ||
            (gain.covered==best_gain.covered && gain.observed>best_gain.observed))) {
            if(gain.covered>before.covered && first_acceptable<0) first_acceptable=budget.elapsed();
            best=model;best_gain=gain;
        }
        preservation_seconds+=std::chrono::duration<double>(Clock::now()-start).count();
    };
    const double search_init_seconds=budget.elapsed()-search_init_start;
    const double setup_seconds=budget.elapsed();const bool setup_exhausted=budget.done();
    PolishResult result;result.model=raw;result.before=result.after=before;
    if(!setup_exhausted) result=search.polish_candidate(raw,coverage,budget,rng,stats,
        [&](const std::string &key){return emitted.count(key)!=0;});
    const auto validation_start=Clock::now();
    if(snapshot!=coverage.json()) throw std::logic_error("frozen coverage mutated");
    if(!f.validate(best)) throw std::logic_error("final proposed model invalid");
    const auto final_observation=f.evaluate(best);const auto verified_gain=coverage.gain(final_observation);
    if(!preserves_new_observations(f,coverage,original,final_observation) || verified_gain.covered!=best_gain.covered || best_gain.covered<before.covered)
        throw std::logic_error("preservation or gain invariant failed");
    const bool duplicate=emitted.count(f.key(best));
    const bool changed=best!=raw;
    const uint64_t potential=duplicate?0:best_gain.covered-before.covered;
    const double final_validation_seconds=std::chrono::duration<double>(Clock::now()-validation_start).count();
    const double full_seconds=budget.elapsed();
    const bool completed_in_budget=full_seconds<=budget.seconds;
    const uint64_t accepted_extra=completed_in_budget?potential:0;
    const Assignment &delivered=completed_in_budget && !duplicate?best:raw;
    // Serialization is evidence I/O, outside the algorithm; process wall is also recorded by the runner.
    std::ofstream out(argv[7]);if(!out) throw std::runtime_error("output unavailable");
    auto nullable=[&](double x){return x<0?std::string("null"):std::to_string(x);};
    out<<std::setprecision(12)<<"{\"mode\":"<<quote(mode)<<",\"budget_seconds\":"<<budget.seconds<<",\"rng_seed\":"<<std::stoull(argv[6])
        <<",\"source_sha256\":"<<quote(LIA_SOURCE_SHA)<<",\"setup_exhausted\":"<<(setup_exhausted?"true":"false")
        <<",\"setup_seconds\":"<<setup_seconds<<",\"formula_initialization_seconds\":"<<formula_seconds
        <<",\"snapshot_restore_seconds\":"<<restore_seconds<<",\"search_initialization_seconds\":"<<search_init_seconds
        <<",\"full_seconds\":"<<full_seconds<<",\"final_validation_seconds\":"<<final_validation_seconds
        <<",\"preservation_seconds\":"<<preservation_seconds<<",\"overrun_seconds\":"<<std::max(0.,full_seconds-budget.seconds)
        <<",\"completed_in_budget\":"<<(completed_in_budget?"true":"false")
        <<",\"prefix_covered\":"<<prefix_covered<<",\"total\":"<<f.total_bits()
        <<",\"input_gain\":"<<before.covered<<",\"proposed_gain\":"<<best_gain.covered
        <<",\"accepted_extra_coverage\":"<<accepted_extra<<",\"late_extra_coverage\":"<<(completed_in_budget?0:potential)
        <<",\"extra_per_full_second\":"<<accepted_extra/std::max(1e-9,full_seconds)
        <<",\"valid_candidates\":"<<valid<<",\"timely_valid_candidates\":"<<timely_valid
        <<",\"preservation_qualified\":"<<qualified<<",\"preservation_rejected\":"<<rejected
        <<",\"first_valid_seconds\":"<<nullable(first_valid)<<",\"first_acceptable_seconds\":"<<nullable(first_acceptable)
        <<",\"proposed_changed\":"<<(changed?"true":"false")<<",\"proposed_duplicate\":"<<(duplicate?"true":"false")
        <<",\"internal_moves\":"<<result.moves<<",\"attempted_steps\":"<<result.attempted_steps
        <<",\"candidate\":"<<f.model_json(delivered)<<",\"proposed_candidate\":"<<f.model_json(best)
        <<",\"input_model\":"<<f.model_json(raw)<<",\"snapshot\":"<<snapshot<<",\"statistics\":"<<stats.json()<<"}\n";
    if(!out) throw std::runtime_error("output write failed");return 0;
} catch(const std::exception &e){std::cerr<<e.what()<<'\n';return 1;}}
