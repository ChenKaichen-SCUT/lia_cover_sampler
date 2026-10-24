#include "residual_coverage.hpp"
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
using namespace lia;
namespace {
void stop(int){interrupted=1;}
void flush(std::ofstream &out){out.flush();if(!out)throw std::runtime_error("journal write failed");}
std::string strings(const std::vector<std::string> &rows) {
    std::string out="[";for(size_t i=0;i<rows.size();++i){if(i)out+=',';out+=rows[i];}return out+']';
}
}
int main(int argc,char **argv) {try {
    if(argc!=8)throw std::invalid_argument("residual_online_probe FORMULA PREFIX_VALUES SEED_VALUES global_pack|residual_cardinality SECONDS RNG OUTPUT_DIRECTORY");
    const std::string mode=argv[4];if(mode!="global_pack" && mode!="residual_cardinality")throw std::invalid_argument("mode");
    const double seconds=std::stod(argv[5]);if(!std::isfinite(seconds) || seconds<=0)throw std::invalid_argument("positive finite session budget required");
    const unsigned rng_seed=std::stoul(argv[6]);
    Budget session;session.seconds=seconds;session.label="online_session";
    std::signal(SIGTERM,stop);std::signal(SIGINT,stop);
    const std::filesystem::path directory(argv[7]);
    if(!std::filesystem::create_directory(directory))throw std::runtime_error("output directory must be new");
    std::ofstream initial(directory/"initial.json"),models(directory/"models.jsonl"),receipts(directory/"commits.jsonl"),
        checkpoints(directory/"checkpoints.jsonl"),rounds(directory/"rounds.jsonl");
    for(auto out:{&initial,&models,&receipts,&checkpoints,&rounds}) {*out<<std::setprecision(17);if(!*out)throw std::runtime_error("journal unavailable");}
    const double setup_seconds=session.elapsed();
    Formula f(argv[1]);const double formula_seconds=session.elapsed()-setup_seconds;
    auto prefix=read_residual_values(f,argv[2]),seeds=read_residual_values(f,argv[3]);
    if(seeds.size()!=1)throw std::invalid_argument("one seed required");
    const Assignment seed=seeds.front();OnlineCoverage online(f);bool seed_added=online.restore(prefix,seed);
    const double restore_seconds=session.elapsed()-formula_seconds-setup_seconds;
    ResidualGenerator generator(f,rng_seed);Assignment current=seed;
    const double initialization_seconds=session.elapsed();
    initial<<"{\"mode\":"<<quote(mode)<<",\"rng_seed\":"<<rng_seed<<",\"source_sha256\":"<<quote(LIA_SOURCE_SHA)
        <<",\"budget_seconds\":"<<seconds<<",\"round_cap_seconds\":1,\"target_calls_cap\":3,\"window_cap\":8"
        <<",\"seed_added\":"<<(seed_added?"true":"false")<<",\"seed\":"<<f.model_json(seed)<<",\"prefix\":[";
    for(size_t i=0;i<prefix.size();++i){if(i)initial<<',';initial<<f.model_json(prefix[i]);}
    initial<<"],\"coverage\":"<<online.state().json()<<",\"dedup_keys\":[";bool first=true;
    for(auto &key:online.keys()){if(!first)initial<<',';first=false;initial<<quote(key);}
    initial<<"],\"coverage_version\":0,\"new_outputs\":0,\"setup_seconds\":"<<setup_seconds
        <<",\"formula_seconds\":"<<formula_seconds<<",\"restore_seconds\":"<<restore_seconds
        <<",\"engine_seconds\":"<<initialization_seconds-setup_seconds-formula_seconds-restore_seconds
        <<",\"initialization_seconds\":"<<initialization_seconds<<"}\n";flush(initial);
    const double initial_evidence_seconds=session.elapsed()-initialization_seconds;
    unsigned stalled=0,round_index=0,overrun_rounds=0;uint64_t marginal_sum=0;
    double sum_round_seconds=0,round_logging_seconds=0;std::string terminal="budget_exhausted",error;
    try {
        while(!session.done() && session.left()>.01 && stalled<8) {
            Budget round;round.seconds=std::min(1.,session.left());round.parent=&session;round.label="online_round";
            const double round_started=std::chrono::duration<double>(round.start-session.start).count();
            const double deadline=std::min(seconds,round_started+round.seconds);
            const auto covered_before=online.state().covered(),version_before=online.version();
            Stats stats;std::vector<std::string> writes;
            auto checkpoint=[&](const ResidualCandidate &candidate) {
                checkpoints<<"{\"round\":"<<round_index<<",\"query\":"<<candidate.query
                    <<",\"coverage_version\":"<<candidate.coverage_version<<",\"origin\":"<<quote(candidate.origin)
                    <<",\"gain\":"<<candidate.gain.covered<<",\"observations\":"<<candidate.gain.observed
                    <<",\"window_hits\":"<<candidate.window_hits<<",\"validated_timestamp\":"<<session.elapsed()
                    <<",\"candidate\":"<<f.model_json(candidate.model)<<"}\n";flush(checkpoints);
                const double finished=session.elapsed();
                std::ostringstream w;w<<std::setprecision(17)<<"{\"query\":"<<candidate.query<<",\"completion_timestamp\":"<<finished
                    <<",\"timely\":"<<(finished<=deadline?"true":"false")<<'}';writes.push_back(w.str());
                return finished-round_started;
            };
            auto result=generator.generate(mode=="residual_cardinality",online,current,round,stats,checkpoint);
            bool committed=false;double completion=-1,receipt_finished=-1;std::string id;
            if(result.best && !round.done()) {
                Timer submission(stats,"final_submission_seconds");
                // A final common validation is inside the delivery reserve. A
                // complete model line is the output; its receipt records flush time.
                auto checked=online.prepare(result.best->model,result.window,result.best->origin,result.best->query);
                if(checked && !round.done()) {
                    id="round-"+std::to_string(round_index)+"-query-"+std::to_string(checked->query);
                    models<<"{\"model_id\":"<<quote(id)<<",\"round\":"<<round_index<<",\"query\":"<<checked->query
                        <<",\"output_index\":"<<online.outputs()+1<<",\"coverage_version_before\":"<<version_before
                        <<",\"coverage_version_after\":"<<version_before+1<<",\"covered_before\":"<<covered_before
                        <<",\"covered_after\":"<<covered_before+checked->gain.covered<<",\"actual_marginal_gain\":"<<checked->gain.covered
                        <<",\"new_observation_count\":"<<checked->gain.observed<<",\"first_observations\":"<<checked->first_observations
                        <<",\"window_hits\":"<<checked->window_hits<<",\"candidate_origin\":"<<quote(checked->origin)
                        <<",\"model\":"<<f.model_json(checked->model)<<"}\n";flush(models);completion=session.elapsed();
                    bool timely=completion<=deadline;
                    receipts<<"{\"model_id\":"<<quote(id)<<",\"round\":"<<round_index<<",\"completion_timestamp\":"<<completion
                        <<",\"deadline_timestamp\":"<<deadline<<",\"timely\":"<<(timely?"true":"false")<<"}\n";flush(receipts);receipt_finished=session.elapsed();
                    // Receipt and state bookkeeping cost remain charged. A late
                    // checkpoint cannot masquerade as a timely formal output.
                    if(timely) {
                        committed=online.commit(*checked,completion,deadline);
                        if(!committed)throw std::logic_error("timely immutable output could not commit");
                        current=checked->model;marginal_sum+=checked->gain.covered;
                    }
                }
            }
            auto gain=online.state().covered()-covered_before;
            stalled=gain?0:stalled+1;
            if(marginal_sum!=online.state().covered()-online.initial_covered())throw std::logic_error("online telescoping identity failed");
            const double round_seconds=round.elapsed();sum_round_seconds+=round_seconds;
            overrun_rounds+=round_seconds>round.seconds;
            const auto log_start=Clock::now();
            rounds<<"{\"round\":"<<round_index<<",\"started_timestamp\":"<<round_started<<",\"budget_seconds\":"<<round.seconds
                <<",\"deadline_timestamp\":"<<deadline<<",\"full_seconds\":"<<round_seconds
                <<",\"search_seconds\":"<<result.full_seconds<<",\"reserve_seconds\":"<<result.reserve_seconds
                <<",\"coverage_version_before\":"<<version_before<<",\"coverage_version_after\":"<<online.version()
                <<",\"covered_before\":"<<covered_before<<",\"covered_after\":"<<online.state().covered()
                <<",\"window\":"<<residual_targets_json(result.window.targets)<<",\"window_complete\":"<<(result.window.complete?"true":"false")
                <<",\"window_probes\":"<<result.window.probes<<",\"target_calls\":"<<result.target_calls<<",\"exploration_calls\":"<<result.exploration_calls
                <<",\"queries\":"<<strings(result.queries)<<",\"assessments\":"<<strings(result.assessments)
                <<",\"checkpoint_writes\":"<<strings(writes)<<",\"best_query\":"<<(result.best?std::to_string(result.best->query):"null")
                <<",\"best_checkpoint_round_seconds\":"<<(result.best?std::to_string(result.best->checkpoint_completed):"null")
                <<",\"committed\":"<<(committed?"true":"false")<<",\"model_id\":"<<(id.empty()?"null":quote(id))
                <<",\"completion_timestamp\":"<<completion<<",\"receipt_finished_timestamp\":"<<receipt_finished
                <<",\"actual_marginal_gain\":"<<gain<<",\"stalled\":"<<stalled<<",\"duplicates\":"<<result.duplicates
                <<",\"late_candidates\":"<<result.late<<",\"terminal\":"<<quote(result.terminal)<<",\"statistics\":"<<stats.json()<<"}\n";flush(rounds);
            round_logging_seconds+=std::chrono::duration<double>(Clock::now()-log_start).count();++round_index;
        }
        terminal=interrupted?"cancelled":stalled>=8?"eight_rounds_without_coverage":"budget_exhausted";
    } catch(const std::exception &e) {terminal="exception";error=e.what();}
    const double full_seconds=session.elapsed();std::ofstream summary(directory/"summary.json");
    summary<<std::setprecision(17)<<"{\"mode\":"<<quote(mode)<<",\"rng_seed\":"<<rng_seed<<",\"source_sha256\":"<<quote(LIA_SOURCE_SHA)
        <<",\"budget_seconds\":"<<seconds<<",\"initialization_seconds\":"<<initialization_seconds
        <<",\"initial_evidence_seconds\":"<<initial_evidence_seconds<<",\"sum_round_seconds\":"<<sum_round_seconds
        <<",\"round_logging_seconds\":"<<round_logging_seconds<<",\"session_seconds\":"<<full_seconds
        <<",\"session_overrun_seconds\":"<<std::max(0.,full_seconds-seconds)<<",\"rounds\":"<<round_index
        <<",\"overrun_rounds\":"<<overrun_rounds<<",\"outputs\":"<<online.outputs()<<",\"coverage_version\":"<<online.version()
        <<",\"initial_covered\":"<<online.initial_covered()<<",\"actual_gain\":"<<marginal_sum
        <<",\"final_coverage\":"<<online.state().json()<<",\"terminal\":"<<quote(terminal)<<",\"error\":"<<quote(error)<<"}\n";flush(summary);
    return error.empty()?0:1;
}catch(const std::exception &e){std::cerr<<e.what()<<'\n';return 1;}}
