#include "conditional_observation.hpp"
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <thread>
#include <cstdlib>
#include <cmath>
using namespace lia;
namespace {
void stop(int){interrupted=1;}
void flush(std::ofstream&s){s.flush();if(!s)throw std::runtime_error("journal write failed");}
uint64_t fingerprint(const Coverage&c){uint64_t h=14695981039346656037ULL;for(auto&s:c.states)for(uint64_t v:{s.zero,s.one})for(unsigned i=0;i<8;++i){h^=(v>>(8*i))&255;h*=1099511628211ULL;}return h;}
z3::expr block(Formula&f,const Assignment&a){z3::expr_vector terms(f.ctx);for(size_t i=0;i<a.size();++i)terms.push_back(f.nodes[f.variables[i]].expr!=f.value(i,a[i]));return z3::mk_or(terms);}
}
int main(int argc,char**argv){try{
    if(argc!=5&&argc!=6)throw std::invalid_argument("soft_phase_sampler F [plain|random_soft|coverage_soft] SECONDS SEED NEW_OUT");
    bool explicit_mode=argc==6;
    Budget budget;budget.seconds=std::stod(argv[explicit_mode?3:2]);if(auto p=std::getenv("BOFILL_START_MONO_NS"))budget.start=Clock::time_point(std::chrono::duration_cast<Clock::duration>(std::chrono::nanoseconds(std::stoll(p))));
    if(!std::isfinite(budget.seconds)||budget.seconds<=0)throw std::invalid_argument("seconds");
    std::string mode=explicit_mode?argv[2]:"plain";if(mode!="plain"&&mode!="random_soft"&&mode!="coverage_soft")throw std::invalid_argument("mode");unsigned seed=std::stoul(argv[explicit_mode?4:3]);
    std::signal(SIGTERM,stop);std::signal(SIGINT,stop);std::filesystem::path dir(argv[explicit_mode?5:4]);if(!std::filesystem::create_directory(dir))throw std::runtime_error("new directory required");
    std::ofstream models(dir/"models.jsonl"),commits(dir/"commits.jsonl"),queries(dir/"queries.jsonl"),candidates(dir/"candidates.jsonl");for(auto p:{&models,&commits,&queries,&candidates})*p<<std::setprecision(17);
    Stats stats;double t=budget.elapsed();Formula f(argv[1]);if(!f.metric_compatible)throw std::runtime_error("metric unsupported");stats.times["load"]=budget.elapsed()-t;
    t=budget.elapsed();z3::solver solver(f.ctx,z3::solver::simple{});solver.add(f.root);solver.set("random_seed",seed);stats.times["solver_initialization"]=budget.elapsed()-t;
    SoftPhaseBridge bridge(solver);Coverage coverage(f);SolveBudgetState control;control.fixed_levels=true;
    std::unique_ptr<ConditionalObservations> summaries;std::set<std::string> seen;std::mt19937_64 rng(uint64_t(seed)^0x935602ab7dULL);
    {std::ofstream o(dir/"initial.json");o<<std::setprecision(17)<<"{\"mode\":"<<quote(mode)<<",\"seed\":"<<seed<<",\"seconds\":"<<budget.seconds<<",\"source_sha256\":"<<quote(LIA_SOURCE_SHA)<<",\"z3_version\":"<<quote(Z3_get_full_version())<<",\"solver\":\"simple_persistent_original\",\"original_variables\":"<<f.variables.size()<<",\"total_bits\":"<<f.total_bits()<<",\"phase_override_limit\":8,\"conflict_limit\":64,\"preparation_cap_seconds\":1,\"original_assertions\":1}\n";flush(o);}
    unsigned query=0,formal=0;unsigned long marginal_sum=0;double first_sat=-1;std::string terminal="deadline";
    while(!budget.done()){
        if(budget.left()<=.025){std::this_thread::sleep_for(std::chrono::milliseconds(1));continue;}
        if(formal && mode!="plain" && !summaries){t=budget.elapsed();summaries=std::make_unique<ConditionalObservations>(f,budget);stats.times["summary_preparation"]=budget.elapsed()-t;t=budget.elapsed();std::ofstream o(dir/"summaries.json");o<<summaries->json()<<'\n';flush(o);stats.times["summary_log"]=budget.elapsed()-t;}
        std::vector<SoftPhasePreference> preferences;std::ostringstream table;table<<'[';bool comma=false;
        t=budget.elapsed();if(summaries && !budget.done())for(auto &a:summaries->atoms){
            auto zero=summaries->score(a.sides[0],coverage),one=summaries->score(a.sides[1],coverage);bool truth;
            if(!zero.observed&&!one.observed)continue;
            if(mode=="random_soft")truth=(rng()&1)!=0;
            else {auto x=std::make_pair(zero.covered,zero.observed),y=std::make_pair(one.covered,one.observed);if(x==y)continue;truth=y>x;}
            preferences.push_back({f.nodes[a.node].expr,truth,a.group,formal});if(comma)table<<',';comma=true;
            table<<"{\"node\":"<<a.node<<",\"ast_id\":"<<f.nodes[a.node].expr.id()<<",\"group\":"<<a.group<<",\"truth\":"<<(truth?"true":"false")<<",\"coverage_version\":"<<formal<<",\"lower_bound\":"<<(truth?one.covered:zero.covered)<<",\"zero_covered\":"<<zero.covered<<",\"one_covered\":"<<one.covered<<",\"zero_observed\":"<<zero.observed<<",\"one_observed\":"<<one.observed<<"}";
        }table<<']';stats.times["feedback_scoring_and_table_encoding"]+=budget.elapsed()-t;
        bool soft=formal&&mode!="plain";
        if(soft&&!budget.done()){t=budget.elapsed();bridge.replace_preferences(preferences,query);stats.times["table_install"]+=budget.elapsed()-t;}
        auto grant=grant_query(control.requested_timeout_ms,budget.left(),budget.left(),.025);
        if(!grant.granted_ms){bridge.clear_preferences();std::this_thread::sleep_for(std::chrono::milliseconds(1));continue;}
        const auto began=budget.elapsed();GlobalResult r;r.requested_timeout_ms=grant.requested_ms;r.actual_timeout_ms=grant.granted_ms;r.budget_clip_reason=grant.clip_reason;r.retry_level_before=control.retry_level;r.session_remaining_before_check=r.action_remaining_before_check=budget.left();
        solver.set("timeout",grant.granted_ms);t=budget.elapsed();r.check_started=true;double finish_before=bridge.finish_seconds;auto answer=soft?bridge.check():solver.check();double full_check=budget.elapsed()-t;
        r.check_seconds=full_check-(bridge.finish_seconds-finish_before);stats.times["check"]+=r.check_seconds;stats.times["hook_log_capture_and_clear"]+=bridge.finish_seconds-finish_before;
        r.raw_status=answer==z3::sat?"sat":answer==z3::unsat?"unsat":"unknown";r.detail=answer==z3::unknown?solver.reason_unknown():"";r.exit_evidence={r.detail,grant.granted_ms,r.check_seconds,bool(interrupted),budget.left()<=0,false,false};r.exit_decision=interpret_query_exit(r.raw_status,r.exit_evidence);r.code=r.exit_decision.code;
        auto version=formal;bool valid=false,duplicate=false,emitted=false;double completed=-1,validated=-1,score=0,write=0,blocking=0;Gain gain;
        if(answer==z3::sat){++stats.counts["sat"];if(first_sat<0)first_sat=budget.elapsed();t=budget.elapsed();auto a=f.complete(solver.get_model());r.completion_seconds=budget.elapsed()-t;
            t=budget.elapsed();valid=f.validate(a);r.validation_seconds=budget.elapsed()-t;validated=budget.elapsed();t=budget.elapsed();auto serialized=f.model_json(a);candidates<<"{\"query\":"<<query<<",\"coverage_version\":"<<formal<<",\"validated_seconds\":"<<validated<<",\"valid\":"<<(valid?"true":"false")<<",\"model\":"<<serialized<<"}\n";flush(candidates);stats.times["candidate_journal"]+=budget.elapsed()-t;if(!valid)throw std::logic_error("invalid original model");auto key=f.key(a);duplicate=seen.count(key);if(duplicate)++stats.counts["duplicates"];
            if(!duplicate&&!budget.done()){
                t=budget.elapsed();auto observation=f.evaluate(a);gain=coverage.gain(observation);Coverage next=coverage;next.accept(observation);auto hash=fingerprint(next);score=budget.elapsed()-t;
                if(!budget.done()){t=budget.elapsed();models<<"{\"query\":"<<query<<",\"index\":"<<formal+1<<",\"validated_seconds\":"<<validated<<",\"gain\":"<<gain.covered<<",\"observations\":"<<gain.observed<<",\"covered\":"<<next.covered()<<",\"state_fnv1a64\":"<<quote(std::to_string(hash))<<",\"model\":"<<serialized<<"}\n";flush(models);completed=budget.elapsed();emitted=completed<=budget.seconds&&!interrupted;
                    commits<<"{\"query\":"<<query<<",\"index\":"<<formal+1<<",\"completion\":"<<completed<<",\"timely\":"<<(emitted?"true":"false")<<"}\n";flush(commits);write=budget.elapsed()-t;
                    if(emitted){coverage.states.swap(next.states);seen.insert(key);++formal;marginal_sum+=gain.covered;if(gain.covered)++stats.counts["positive_outputs"];if(gain.observed)++stats.counts["new_observation_outputs"];}
                }
            }
            if(!budget.done()){t=budget.elapsed();solver.add(block(f,a));blocking=budget.elapsed()-t;++stats.counts["blocks"];}
        }
        stats.times["completion"]+=r.completion_seconds;stats.times["validation"]+=r.validation_seconds;stats.times["scoring"]+=score;stats.times["model_write"]+=write;stats.times["blocking"]+=blocking;
        control.observe(r.exit_decision,budget.elapsed()-began,grant);r.retry_level_after=control.retry_level;
        t=budget.elapsed();queries<<"{\"query\":"<<query++<<",\"start\":"<<began<<",\"finished\":"<<budget.elapsed()<<",\"coverage_version\":"<<version<<",\"evidence\":"<<r.evidence_json()<<",\"preferences\":"<<table.str()<<",\"hook\":"<<(soft?bridge.last_log:"{}")<<",\"valid\":"<<(valid?"true":"false")<<",\"duplicate\":"<<(duplicate?"true":"false")<<",\"emitted\":"<<(emitted?"true":"false")<<",\"completion\":"<<completed<<",\"gain\":"<<gain.covered<<"}\n";flush(queries);stats.times["query_log"]+=budget.elapsed()-t;
        if(answer==z3::unsat){terminal="exhausted_full_original_models";break;}if(r.code==SolveStatus::ResourceExhausted||r.exit_decision.stop){terminal=r.exit_decision.classification;break;}
    }
    bridge.clear_preferences();if(interrupted)terminal="external_cancel";if(marginal_sum!=coverage.covered())throw std::logic_error("coverage telescoping");
    std::ofstream o(dir/"summary.json");o<<std::setprecision(17)<<"{\"terminal\":"<<quote(terminal)<<",\"elapsed\":"<<budget.elapsed()<<",\"first_sat_seconds\":"<<first_sat<<",\"queries\":"<<query<<",\"formal_count\":"<<formal<<",\"marginal_sum\":"<<marginal_sum<<",\"stats\":"<<stats.json()<<",\"coverage\":"<<coverage.json()<<"}\n";flush(o);return 0;
}catch(const std::exception&e){std::cerr<<"soft_phase_sampler: "<<e.what()<<'\n';return 1;}}
