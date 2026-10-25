#include "bofill_session.hpp"
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <thread>
using namespace lia;
namespace {
void stop(int){interrupted=1;}
void flush(std::ofstream &s){s.flush();if(!s)throw std::runtime_error("journal write failed");}
uint64_t state_fingerprint(const Coverage &c) {
    uint64_t h=14695981039346656037ULL;
    for(auto &s:c.states)for(uint64_t v:{s.zero,s.one})for(unsigned i=0;i<8;++i){h^=(v>>(8*i))&255;h*=1099511628211ULL;}
    return h;
}
}
int main(int argc,char **argv) {
    try {
        if(argc!=6)throw std::invalid_argument("bofill_sampler F MODE[_fallback] SECONDS SEED NEW_OUT");
        Budget budget;budget.seconds=std::stod(argv[3]);
        if(auto p=std::getenv("BOFILL_START_MONO_NS"))budget.start=Clock::time_point(std::chrono::duration_cast<Clock::duration>(std::chrono::nanoseconds(std::stoll(p))));
        if(!std::isfinite(budget.seconds)||budget.seconds<=0)throw std::invalid_argument("seconds");
        const std::string mode=argv[2];const bool fallback_enabled=mode.size()>9 && mode.substr(mode.size()-9)=="_fallback";
        const auto selected=parse_compile_mode(fallback_enabled?mode.substr(0,mode.size()-9):mode);
        unsigned seed=std::stoul(argv[4]);std::signal(SIGTERM,stop);std::signal(SIGINT,stop);
        const std::filesystem::path dir(argv[5]);
        if(!std::filesystem::create_directory(dir))throw std::runtime_error("new output directory required");
        std::ofstream models(dir/"models.jsonl"),commits(dir/"commits.jsonl"),queries(dir/"queries.jsonl");
        for(auto p:{&models,&commits,&queries}){*p<<std::setprecision(17);if(!*p)throw std::runtime_error("journal open failed");}
        Stats stats;double t=budget.elapsed();Formula f(argv[1]);stats.times["load"]=budget.elapsed()-t;
        if(!f.metric_compatible)throw std::runtime_error("unsupported original metric: "+f.metric_reason);
        t=budget.elapsed();CompiledProblem problem(f,selected,budget);stats.times["compile"]=budget.elapsed()-t;
        {t=budget.elapsed();std::ofstream out(dir/"structure.json");out<<problem.json()<<'\n';flush(out);
         std::ofstream smt(dir/"compiled.smt2");z3::solver export_only(f.ctx);export_only.add(problem.compiled_formula);smt<<export_only.to_smt2();flush(smt);stats.times["structure_write"]=budget.elapsed()-t;}
        t=budget.elapsed();BofillSolverSession session(problem,budget,seed);
        Coverage coverage(f);SolveBudgetState control;control.fixed_levels=true;std::set<std::string> seen;std::vector<Assignment> delivered;
        stats.times["solver_initialization"]=budget.elapsed()-t;
        {std::ofstream out(dir/"initial.json");out<<std::setprecision(17)<<"{\"mode\":"<<quote(mode)<<",\"effective_mode\":"<<quote(compile_mode_name(selected))<<",\"seed\":"<<seed
            <<",\"fallback_enabled\":"<<(fallback_enabled?"true":"false")<<",\"first_model_fallback_seconds\":10,\"seconds\":"<<budget.seconds<<",\"source_sha256\":"<<quote(LIA_SOURCE_SHA)<<",\"initialized_seconds\":"<<budget.elapsed()
            <<",\"total_bits\":"<<f.total_bits()<<",\"original_variables\":"<<f.variables.size()<<",\"solver\":\"simple_persistent\",\"timeout_levels_ms\":[50,200,1000,2000]}\n";flush(out);}
        unsigned long query=0,formal=0,marginal_sum=0;std::string terminal="deadline";double first_sat=-1;
        while(!budget.done()) {
            if(session.should_fallback(fallback_enabled,formal)) {
                const auto began=budget.elapsed();const auto remaining=budget.left();
                session.reset_to_original(delivered);control=SolveBudgetState{};control.fixed_levels=true;
                stats.times["fallback_initialization"]+=budget.elapsed()-began;++stats.counts["fallbacks"];
                std::ofstream event(dir/"fallback.json");event<<std::setprecision(17)<<"{\"reason\":\"first_model_deadline\",\"threshold_seconds\":10,\"start\":"<<began
                    <<",\"finished\":"<<budget.elapsed()<<",\"remaining_before\":"<<remaining<<",\"reblocked_formal_models\":"<<delivered.size()<<",\"from_mode\":"<<quote(compile_mode_name(selected))<<"}\n";flush(event);
            }
            const double delivery_limit=fallback_enabled && !session.fallback && selected!=CompileMode::Plain && formal==0
                ?std::min(budget.seconds,BofillSolverSession::first_model_limit_seconds):budget.seconds;
            auto grant=grant_query(control.requested_timeout_ms,budget.left(),session.action_left(fallback_enabled,formal),.025);
            if(!grant.granted_ms){std::this_thread::sleep_for(std::chrono::milliseconds(1));continue;}
            const double began=budget.elapsed();GlobalResult r;r.requested_timeout_ms=grant.requested_ms;r.actual_timeout_ms=grant.granted_ms;
            r.budget_clip_reason=grant.clip_reason;r.retry_level_before=control.retry_level;
            r.session_remaining_before_check=budget.left();r.action_remaining_before_check=session.action_left(fallback_enabled,formal);session.solver().set("timeout",grant.granted_ms);
            t=budget.elapsed();r.check_started=true;auto answer=session.solver().check();r.check_seconds=budget.elapsed()-t;stats.times["check"]+=r.check_seconds;
            r.raw_status=answer==z3::sat?"sat":answer==z3::unsat?"unsat":"unknown";r.detail=answer==z3::unknown?session.solver().reason_unknown():"";
            r.exit_evidence={r.detail,grant.granted_ms,r.check_seconds,bool(interrupted),budget.left()<=0,false,false};
            r.exit_decision=interpret_query_exit(r.raw_status,r.exit_evidence);r.code=r.exit_decision.code;
            bool duplicate=false,emitted=false,valid=false;double completed=-1,validated=-1,score=0,write=0,block=0;Gain gain;
            if(answer==z3::sat) {
                ++stats.counts["sat"];if(first_sat<0)first_sat=budget.elapsed();
                t=budget.elapsed();auto a=session.problem().restore(session.solver().get_model());r.completion_seconds=budget.elapsed()-t;
                t=budget.elapsed();valid=f.validate(a);r.validation_seconds=budget.elapsed()-t;validated=budget.elapsed();
                if(!valid)throw std::logic_error("reconstruction violates original formula");
                auto key=f.key(a);duplicate=seen.count(key);if(duplicate)++stats.counts["restored_duplicates"];
                if(!duplicate&&!budget.done()&&budget.elapsed()<delivery_limit) {
                    t=budget.elapsed();auto observation=f.evaluate(a);gain=coverage.gain(observation);score=budget.elapsed()-t;
                    // Preview only; formal coverage stays unchanged until full model write has completed on time.
                    Coverage next=coverage;next.accept(observation);auto fingerprint=state_fingerprint(next);
                    if(!budget.done()&&budget.elapsed()<delivery_limit) {
                        t=budget.elapsed();models<<"{\"query\":"<<query<<",\"index\":"<<formal+1<<",\"validated_seconds\":"<<validated
                            <<",\"representation\":"<<quote(session.fallback?"plain_fallback":compile_mode_name(selected))<<",\"gain\":"<<gain.covered<<",\"observations\":"<<gain.observed<<",\"covered\":"<<next.covered()
                            <<",\"state_fnv1a64\":"<<quote(std::to_string(fingerprint))<<",\"model\":"<<f.model_json(a)<<"}\n";flush(models);
                        completed=budget.elapsed();emitted=completed<=delivery_limit&&!interrupted;
                        commits<<"{\"query\":"<<query<<",\"index\":"<<formal+1<<",\"completion\":"<<completed<<",\"deadline_seconds\":"<<delivery_limit<<",\"timely\":"<<(emitted?"true":"false")<<"}\n";flush(commits);
                        write=budget.elapsed()-t;
                        if(emitted){coverage.states.swap(next.states);seen.insert(key);delivered.push_back(a);++formal;marginal_sum+=gain.covered;
                            std::string origin=session.fallback?"plain_fallback":selected==CompileMode::Plain?"plain":"compiled";
                            ++stats.counts[origin+"_outputs"];stats.counts[origin+"_coverage"]+=gain.covered;if(gain.covered)++stats.counts["positive_outputs"];}
                    }
                }
                // Exact complete-original-model block; same scope and policy in both modes.
                // No later query is possible when a candidate missed the session deadline.
                if(!budget.done()) {t=budget.elapsed();session.solver().add(session.problem().block_original(a));block=budget.elapsed()-t;++stats.counts["blocks"];}
            }
            stats.times["reconstruction"]+=r.completion_seconds;stats.times["validation"]+=r.validation_seconds;
            stats.times["scoring"]+=score;stats.times["model_receipt_write"]+=write;stats.times["blocking"]+=block;
            control.observe(r.exit_decision,budget.elapsed()-began,grant);r.retry_level_after=control.retry_level;
            t=budget.elapsed();queries<<"{\"query\":"<<query++<<",\"start\":"<<began<<",\"finished\":"<<budget.elapsed()<<",\"evidence\":"<<r.evidence_json()
                <<",\"representation\":"<<quote(session.fallback?"plain_fallback":compile_mode_name(selected))<<",\"valid\":"<<(valid?"true":"false")<<",\"duplicate\":"<<(duplicate?"true":"false")<<",\"emitted\":"<<(emitted?"true":"false")
                <<",\"completion\":"<<completed<<",\"gain\":"<<gain.covered<<",\"scoring_seconds\":"<<score<<",\"write_seconds\":"<<write<<",\"blocking_seconds\":"<<block<<"}\n";flush(queries);stats.times["query_log"]+=budget.elapsed()-t;
            if(answer==z3::unsat){terminal="exhausted_full_original_models";break;}
            if(r.code==SolveStatus::ResourceExhausted){terminal="resource_exhausted";break;}
            if(r.exit_decision.stop){terminal=r.exit_decision.classification;break;}
        }
        if(interrupted)terminal="external_cancel";
        if(marginal_sum!=coverage.covered())throw std::logic_error("coverage telescoping failed");
        std::ofstream summary(dir/"summary.json");summary<<std::setprecision(17)<<"{\"terminal\":"<<quote(terminal)<<",\"elapsed\":"<<budget.elapsed()
            <<",\"fallback_enabled\":"<<(fallback_enabled?"true":"false")<<",\"fallback_occurred\":"<<(session.fallback?"true":"false")<<",\"first_sat_seconds\":"<<first_sat<<",\"queries\":"<<query<<",\"formal_count\":"<<formal<<",\"marginal_sum\":"<<marginal_sum
            <<",\"stats\":"<<stats.json()<<",\"coverage\":"<<coverage.json()<<"}\n";flush(summary);return 0;
    }catch(const std::exception &e){std::cerr<<"bofill_sampler error: "<<e.what()<<'\n';return 1;}
}
