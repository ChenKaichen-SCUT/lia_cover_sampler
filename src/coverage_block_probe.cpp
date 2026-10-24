#include "coverage_block_generator.hpp"
#include <fstream>
#include <iomanip>
#include <iostream>
using namespace lia;
int main(int argc,char **argv) {try {
    if(argc!=8) throw std::invalid_argument("coverage_block_probe FORMULA PREFIX_VALUES SEED_VALUES global|block SECONDS RNG OUTPUT");
    const std::string mode=argv[4];if(mode!="global" && mode!="block") throw std::invalid_argument("mode");
    const double seconds=std::stod(argv[5]);if(seconds<=0)throw std::invalid_argument("positive budget required");
    const unsigned rng_seed=std::stoul(argv[6]);
    const auto session_start=Clock::now();
    auto elapsed=[&]{return std::chrono::duration<double>(Clock::now()-session_start).count();};
    Formula f(argv[1]);const double formula_seconds=elapsed();
    Coverage coverage(f);std::set<std::string> emitted;
    auto read=[&](const std::string &path,bool prefix) {
        std::ifstream in(path);if(!in)throw std::runtime_error("missing frozen snapshot");
        std::vector<Assignment> models;std::string line;
        while(std::getline(in,line)) {
            std::istringstream row(line);Assignment m;std::string v;
            while(row>>v)m.emplace_back(v=="true"?"1":v=="false"?"0":v);
            if(!f.validate(m))throw std::logic_error("invalid snapshot model");
            if(prefix) {coverage.accept(f.evaluate(m));if(!emitted.insert(f.key(m)).second)throw std::logic_error("duplicate snapshot prefix");}
            else models.push_back(std::move(m));
        }
        return models;
    };
    read(argv[2],true);auto seeds=read(argv[3],false);if(seeds.size()!=1)throw std::invalid_argument("one seed required");
    const auto &seed=seeds.front();const auto snapshot=coverage.json();const auto input_gain=coverage.gain(f.evaluate(seed));
    const double restore_seconds=elapsed()-formula_seconds;
    CoverageBlockGenerator generator(f,rng_seed);
    const double init_seconds=elapsed()-formula_seconds-restore_seconds;
    std::ofstream checkpoints(std::string(argv[7])+".checkpoints.jsonl");
    if(!checkpoints)throw std::runtime_error("checkpoint output unavailable");
    std::vector<std::string> actions;double sum_actions=0,action_evidence=0;
    for(unsigned action=0;action<3;++action) {
        std::vector<std::pair<double,bool>> writes;
        Stats stats;Budget budget;budget.seconds=seconds;budget.label="coverage_generator_action";
        auto save=[&](const Assignment &candidate,Gain gain,double validated) {
            checkpoints<<std::setprecision(12)<<"{\"action\":"<<action<<",\"index\":"<<writes.size()
                <<",\"validated_seconds\":"<<validated<<",\"gain\":"<<gain.covered
                <<",\"candidate\":"<<f.model_json(candidate)<<"}\n";
            checkpoints.flush();if(!checkpoints)throw std::runtime_error("checkpoint write failed");
            double finished=budget.elapsed();writes.emplace_back(finished,finished<=seconds);
            return finished;
        };
        auto result=generator.generate(seed,coverage,budget,uint64_t(rng_seed)*3+action,mode=="block",stats,
            [&](const std::string &key){return emitted.count(key)!=0;},save);
        sum_actions+=result.full_seconds;
        const auto evidence_start=Clock::now();std::ostringstream row;row<<std::setprecision(12);
        row<<"{\"action\":"<<action<<",\"target_seed\":"<<uint64_t(rng_seed)*3+action<<",\"budget_seconds\":"<<seconds
            <<",\"result\":"<<result.json(f)<<",\"statistics\":"<<stats.json()<<",\"checkpoint_writes\":[";
        for(size_t i=0;i<writes.size();++i) {if(i)row<<',';row<<"{\"index\":"<<i<<",\"finished_seconds\":"<<writes[i].first
            <<",\"timely\":"<<(writes[i].second?"true":"false")<<'}';}row<<"]}";actions.push_back(row.str());
        action_evidence+=std::chrono::duration<double>(Clock::now()-evidence_start).count();
    }
    if(snapshot!=coverage.json())throw std::logic_error("internal candidates changed committed coverage");
    const double session_seconds=elapsed();
    std::ofstream out(argv[7]);if(!out)throw std::runtime_error("session output unavailable");
    out<<std::setprecision(12)<<"{\"mode\":"<<quote(mode)<<",\"rng_seed\":"<<rng_seed<<",\"source_sha256\":"<<quote(LIA_SOURCE_SHA)
       <<",\"formula_initialization_seconds\":"<<formula_seconds<<",\"snapshot_restore_seconds\":"<<restore_seconds
       <<",\"engine_initialization_seconds\":"<<init_seconds<<",\"initialization_seconds\":"<<formula_seconds+restore_seconds+init_seconds
       <<",\"sum_action_seconds\":"<<sum_actions<<",\"action_evidence_seconds\":"<<action_evidence
       <<",\"session_seconds\":"<<session_seconds<<",\"input_gain\":"<<input_gain.covered
       <<",\"snapshot\":"<<snapshot<<",\"input_model\":"<<f.model_json(seed)<<",\"actions\":[";
    for(size_t i=0;i<actions.size();++i){if(i)out<<',';out<<actions[i];}out<<"]}\n";
    if(!out)throw std::runtime_error("session output write failed");
    return 0;
}catch(const std::exception &e){std::cerr<<e.what()<<'\n';return 1;}}
