#include "core.hpp"
#include "ab_output.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
using namespace lia;
int main(int argc,char **argv) {try {
    if(argc!=6) throw std::invalid_argument("ab_replay FORMULA VALUES RAW_TIMES OUTPUT SEED");
    Formula f(argv[1]);Coverage raw_coverage(f);Budget budget;budget.seconds=300;
    std::filesystem::path out=argv[4];std::filesystem::create_directories(out);
    if(std::filesystem::exists(out/"models.jsonl")) throw std::runtime_error("replay output exists");
    std::ofstream models(out/"models.jsonl"),legacy,emission(out/"emission.csv"),proof(out/"prefix_preservation.jsonl");
    FormalOutput formal(f,budget,true,std::stoul(argv[5]),out.string(),models,legacy,emission);formal.input_path=argv[1];
    std::ifstream input(argv[2]),times(argv[3]);std::string line;unsigned raw_count=0;
    while(std::getline(input,line)) {
        Assignment raw;std::istringstream row(line);std::string value;
        while(row>>value) raw.emplace_back(value=="true"?"1":value=="false"?"0":value);
        if(!f.validate(raw)) throw std::logic_error("invalid saved AB model");
        double original_time;if(!(times>>original_time)) throw std::logic_error("missing historical allocation time");
        formal.replay_generator_credit=original_time;
        auto observation=f.evaluate(raw);raw_coverage.accept(observation);
        formal.deliver(raw,observation,"fixed_raw_replay",raw_count++,raw_coverage.covered());
        for(size_t i=0;i<f.nodes.size();++i) {
            auto a=raw_coverage.states[i],b=formal.coverage.states[i];
            if((a.zero&~b.zero) || (a.one&~b.one)) throw std::logic_error("raw prefix observations lost");
        }
        proof<<"{\"raw_prefix\":"<<raw_count<<",\"raw_covered\":"<<raw_coverage.covered()<<",\"emitted_samples\":"<<formal.count
            <<",\"emitted_covered\":"<<formal.covered<<",\"observations_preserved\":true}\n";
    }
    std::ofstream(out/"summary.json")<<"{\"status\":\"fixed_raw_replay\",\"samples\":"<<formal.count<<",\"covered\":"<<formal.covered
        <<",\"total\":"<<f.total_bits()<<",\"elapsed\":"<<budget.elapsed()<<formal.fields(raw_count,raw_coverage.covered())<<"}\n";
    return 0;
} catch(const std::exception &e){std::cerr<<e.what()<<'\n';return 2;}}
