#include "search.hpp"
#include <fstream>
#include <iostream>
#include <iomanip>
using namespace lia;
int main(int argc,char **argv) {
    try {
        if(argc!=10) throw std::invalid_argument("snapshot_probe FORMULA PREFIX_VALUES SEED_VALUES MODULE MODE SECONDS RNG OUTPUT STEP_CAP");
        Formula f(argv[1]);Coverage coverage(f);std::vector<Assignment> prefix;
        auto read=[&](const std::string &path) {
            std::vector<Assignment> models;std::ifstream in(path);std::string line;
            if(!in) throw std::runtime_error("snapshot input missing");
            while(std::getline(in,line)) {
                std::istringstream row(line);Assignment model;std::string value;
                while(row>>value) model.emplace_back(value=="true" ? "1" : value=="false" ? "0" : value);
                if(!f.validate(model)) throw std::logic_error("invalid complete snapshot assignment");
                models.push_back(std::move(model));
            }
            return models;
        };
        prefix=read(argv[2]);auto seeds=read(argv[3]);
        if(seeds.size()!=1) throw std::invalid_argument("one snapshot seed required");
        std::set<std::string> emitted;
        for(const auto &model:prefix) {coverage.accept(f.evaluate(model));emitted.insert(f.key(model));}
        const auto snapshot=coverage.json();auto seed=seeds.front();
        auto prior=coverage.gain(f.evaluate(seed));
        Search search(f);search.options.line=true;
        std::string mode=argv[5],module=argv[4];
        if(mode!="eager" && mode!="stream" && mode!="new" && mode!="diagnostic" && mode!="diagnostic_off") throw std::invalid_argument("snapshot mode");
        DiagnosticTrace diagnostics;
        if(mode=="diagnostic") search.diagnostic_trace=&diagnostics;
        search.options.diagnostics=mode!="diagnostic_off";
        search.options.streaming=mode!="eager";search.options.directional_targets=mode!="eager";
        search.options.polish_split=mode!="eager" && mode!="stream";search.targets.rotation_fix=mode!="eager";
        search.options.polish_step_candidates=std::stoul(argv[9]);
        Stats stats;std::mt19937_64 rng(std::stoull(argv[7]));Budget budget;budget.seconds=std::stod(argv[6]);
        std::optional<Assignment> candidate;Gain gain;double first=-1,positive=-1;
        unsigned moves=0,steps=0;std::string status="export";
        auto is_emitted=[&](const std::string &key){return emitted.count(key)!=0;};
        if(module=="polish") {
            auto result=search.polish_candidate(seed,coverage,budget,rng,stats,is_emitted);
            candidate=result.model;gain=result.after;moves=result.moves;steps=result.attempted_steps;status="candidate";
            first=result.first_valid_seconds;positive=result.first_positive_seconds;
        } else if(module=="safe" || module=="line") {
            auto result=module=="safe" ? search.safe(seed,coverage,budget,rng,stats,{},is_emitted) :
                search.line(seed,coverage,budget,rng,stats,{},is_emitted);
            candidate=result.model;gain=result.gain;first=result.first_valid_seconds;
            positive=result.first_positive_seconds;status=result.status;
        } else if(module!="export") throw std::invalid_argument("snapshot module");
        const double elapsed=budget.elapsed();
        if(snapshot!=coverage.json()) throw std::logic_error("module changed frozen coverage");
        if(candidate && (!f.validate(*candidate) || coverage.gain(f.evaluate(*candidate)).covered!=gain.covered))
            throw std::logic_error("snapshot final model/gain mismatch");
        std::ofstream out(argv[8]);out<<std::setprecision(12)<<"{\"module\":"<<quote(module)<<",\"mode\":"<<quote(mode)
            <<",\"elapsed_seconds\":"<<elapsed<<",\"source_sha256\":"<<quote(LIA_SOURCE_SHA)
            <<",\"before_gain\":"<<prior.covered<<",\"gain\":"<<gain.covered
            <<",\"first_valid_seconds\":"<<(first<0 ? "null" : std::to_string(first))
            <<",\"first_positive_seconds\":"<<(positive<0 ? "null" : std::to_string(positive))
            <<",\"moves\":"<<moves<<",\"attempted_steps\":"<<steps<<",\"status\":"<<quote(status)
            <<",\"candidate\":"<<(candidate ? f.model_json(*candidate) : "null")
            <<",\"seed\":"<<f.model_json(seed)<<",\"snapshot\":"<<snapshot<<",\"diagnostics\":"<<diagnostics.json()
            <<",\"statistics\":"<<stats.json()<<"}\n";
        return 0;
    } catch(const std::exception &error) {std::cerr<<error.what()<<'\n';return 1;}
}
