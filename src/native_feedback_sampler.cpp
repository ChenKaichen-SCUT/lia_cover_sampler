#include "native_phase_feedback.hpp"
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <cstdlib>
#include <cmath>

using namespace lia;
namespace {
void stop(int){interrupted=1;}
void flush(std::ofstream&s){s.flush();if(!s)throw std::runtime_error("journal write failed");}
uint64_t fingerprint(const Coverage&c){uint64_t h=14695981039346656037ULL;for(auto&s:c.states)for(uint64_t v:{s.zero,s.one})for(unsigned i=0;i<8;++i){h^=(v>>(8*i))&255;h*=1099511628211ULL;}return h;}
z3::expr block(Formula&f,const Assignment&a){z3::expr_vector terms(f.ctx);for(size_t i=0;i<a.size();++i)terms.push_back(f.nodes[f.variables[i]].expr!=f.value(i,a[i]));return z3::mk_or(terms);}
struct Pending {
    unsigned query=0;
    double start=0;
    NativeDecision decision;
    bool timely=false,valid=false,duplicate=false,unknown=false,timeout=false;
    unsigned long gain=0,observations=0;
    std::optional<bool> final_truth;
};
}
int main(int argc,char**argv){try{
    if(argc!=6)throw std::invalid_argument("native_feedback_sampler F MODE SECONDS SEED NEW_OUT");
    Budget budget;budget.seconds=std::stod(argv[3]);
    if(auto p=std::getenv("BOFILL_START_MONO_NS"))budget.start=Clock::time_point(std::chrono::duration_cast<Clock::duration>(std::chrono::nanoseconds(std::stoll(p))));
    if(!std::isfinite(budget.seconds)||budget.seconds<=0)throw std::invalid_argument("seconds");
    std::string mode=argv[2];
    if(mode!="plain"&&mode!="catalog_random_one"&&mode!="native_random_one"&&mode!="native_feedback_one")throw std::invalid_argument("mode");
    unsigned seed=std::stoul(argv[4]),max_queries=UINT_MAX;
    if(auto p=std::getenv("NATIVE_TEST_MAX_QUERIES"))max_queries=std::stoul(p);
    std::signal(SIGTERM,stop);std::signal(SIGINT,stop);
    std::filesystem::path dir(argv[5]);if(!std::filesystem::create_directory(dir))throw std::runtime_error("new directory required");
    std::ofstream models(dir/"models.jsonl"),commits(dir/"commits.jsonl"),queries(dir/"queries.jsonl"),candidates(dir/"candidates.jsonl"),
                  feedback(dir/"feedback.jsonl"),costs(dir/"costs.jsonl"),atoms(dir/"atoms.jsonl");
    for(auto p:{&models,&commits,&queries,&candidates,&feedback,&costs,&atoms})*p<<std::setprecision(17);
    Stats stats;double t=budget.elapsed();Formula f(argv[1]);if(!f.metric_compatible)throw std::runtime_error("metric unsupported");stats.times["load"]=budget.elapsed()-t;
    t=budget.elapsed();z3::solver solver(f.ctx,z3::solver::simple{});solver.add(f.root);solver.set("random_seed",seed);stats.times["solver_initialization"]=budget.elapsed()-t;
    Coverage coverage(f);SolveBudgetState control;control.fixed_levels=true;std::set<std::string> seen;
    std::unique_ptr<NativePhaseFeedback> policy;std::unique_ptr<ConditionalObservations> summaries;
    std::optional<Assignment> last_model;std::deque<bool> recent_zero;std::optional<Pending> pending;
    unsigned query=0,formal=0;unsigned long marginal_sum=0;double first_sat=-1,last_cost=0,cost_sum=0,last_boundary=0;
    bool last_timeout=false;std::string terminal="deadline";
    {std::ofstream o(dir/"initial.json");o<<std::setprecision(17)<<"{\"mode\":"<<quote(mode)<<",\"seed\":"<<seed<<",\"seconds\":"<<budget.seconds
        <<",\"source_sha256\":"<<quote(LIA_SOURCE_SHA)<<",\"z3_version\":"<<quote(Z3_get_full_version())
        <<",\"solver\":\"simple_persistent_original\",\"original_variables\":"<<f.variables.size()<<",\"total_bits\":"<<f.total_bits()
        <<",\"decision_window\":32,\"selected_limit\":1,\"feature_count\":16,\"ridge_lambda\":1,\"feedback_window\":256,\"warmup_per_action\":8,\"epsilon\":0.2,\"tau_min\":0.001,\"mechanism_quota_fraction\":0.05,\"cache_cap\":4096,\"original_assertions\":1,\"test_query_limit\":"<<max_queries<<"}\n";flush(o);}
    auto finalize=[&](double end){
        if(!pending)return;
        auto p=*pending;pending.reset();last_cost=end-p.start;last_boundary=end;cost_sum+=last_cost;last_timeout=p.timeout;
        double reward=f.total_bits()?double(p.gain)/f.total_bits():0,trained=0;
        unsigned before=policy?policy->learner.feedback_counts[0]+policy->learner.feedback_counts[1]:0;
        if(policy&&p.decision.selected)trained=policy->observe(p.decision,reward,last_cost);
        unsigned after=policy?policy->learner.feedback_counts[0]+policy->learner.feedback_counts[1]:0;
        auto journal_start=budget.elapsed();
        costs<<"{\"query\":"<<p.query<<",\"start\":"<<p.start<<",\"end\":"<<end<<",\"full_cycle_seconds\":"<<last_cost<<",\"gain\":"<<p.gain
             <<",\"selected\":"<<(p.decision.selected?"true":"false")<<"}\n";flush(costs);
        if(p.decision.selected){
            feedback<<"{\"query\":"<<p.query<<",\"coverage_version\":"<<p.decision.coverage_version<<",\"action\":"<<quote(p.decision.choice.action==PhaseAction::FlipOnce?"FlipOnce":"KeepNative")
                <<",\"reward\":"<<reward<<",\"gain\":"<<p.gain<<",\"full_cycle_seconds\":"<<last_cost<<",\"probability\":"<<p.decision.choice.probability
                <<",\"timely\":"<<(p.timely?"true":"false")<<",\"valid\":"<<(p.valid?"true":"false")<<",\"duplicate\":"<<(p.duplicate?"true":"false")
                <<",\"unknown\":"<<(p.unknown?"true":"false")<<",\"final_truth\":";
            if(p.final_truth)feedback<<(*p.final_truth?"true":"false");else feedback<<"null";
            feedback<<",\"trained\":"<<(after>before?"true":"false")<<",\"training_seconds\":"<<trained<<",\"training_feedback_before\":"<<before<<",\"training_feedback_after\":"<<after
                <<",\"label_finalized_at\":"<<end<<",\"training_finished_at\":"<<budget.elapsed()<<",\"unselected_action_reward\":null,\"unselected_action_cost\":null}\n";flush(feedback);
        }
        double journaling=budget.elapsed()-journal_start;stats.times["cost_feedback_journal"]+=journaling;stats.times["training"]+=trained;
        if(policy&&p.decision.selected)policy->charge(journaling);
        recent_zero.push_back(p.observations==0);if(recent_zero.size()>16)recent_zero.pop_front();
    };
    while(!budget.done()&&query<max_queries){
        if(budget.left()<=.025)break;
        double cycle_start=query?budget.elapsed():0;finalize(cycle_start);
        double preparation_start=budget.elapsed();
        if(formal&&mode!="plain"&&!policy){
            t=budget.elapsed();policy=std::make_unique<NativePhaseFeedback>(f,solver,budget,mode,seed);stats.times["feature_initialization"]+=budget.elapsed()-t;
            if(policy->available()){
                t=budget.elapsed();Budget preparation;preparation.parent=&budget;preparation.seconds=std::min(1.,std::max(0.,policy->quota_seconds-policy->mechanism_seconds));
                summaries=std::make_unique<ConditionalObservations>(f,preparation);
                std::ofstream o(dir/"summaries.json");o<<summaries->json()<<'\n';flush(o);
                double elapsed=budget.elapsed()-t;stats.times["catalog_preparation_and_log"]+=elapsed;policy->charge(elapsed);policy->set_catalog(*summaries);
            }
        }
        auto grant=grant_query(control.requested_timeout_ms,budget.left(),budget.left(),.025);
        if(!grant.granted_ms){terminal="preparation_budget";break;}
        double install=0;
        if(policy){
            t=budget.elapsed();double zero=0;for(bool z:recent_zero)zero+=z;if(!recent_zero.empty())zero/=recent_zero.size();
            policy->prepare(coverage,last_model?&*last_model:nullptr,formal,query,grant.granted_ms,zero,last_cost,last_timeout);
            install=budget.elapsed()-t;policy->charge(install);
            if(!policy->available())policy->clear();
        }
        // Re-grant after feature/install preparation, retaining the same parent deadline.
        grant=grant_query(control.requested_timeout_ms,budget.left(),budget.left(),.025);
        if(!grant.granted_ms){if(policy)policy->clear();terminal="preparation_budget";break;}
        if(policy)policy->set_grant(grant.granted_ms);
        const auto began=budget.elapsed();GlobalResult r;r.requested_timeout_ms=grant.requested_ms;r.actual_timeout_ms=grant.granted_ms;r.budget_clip_reason=grant.clip_reason;
        r.retry_level_before=control.retry_level;r.session_remaining_before_check=r.action_remaining_before_check=budget.left();
        solver.set("timeout",grant.granted_ms);t=budget.elapsed();r.check_started=true;
        auto answer=policy?policy->check():solver.check();double full_check=budget.elapsed()-t;
        r.check_seconds=policy?policy->last_check_seconds:full_check;
        r.raw_status=answer==z3::sat?"sat":answer==z3::unsat?"unsat":"unknown";r.detail=answer==z3::unknown?solver.reason_unknown():"";
        r.exit_evidence={r.detail,grant.granted_ms,r.check_seconds,bool(interrupted),budget.left()<=0,false,false};
        r.exit_decision=interpret_query_exit(r.raw_status,r.exit_evidence);r.code=r.exit_decision.code;
        unsigned version=formal;bool valid=false,duplicate=false,emitted=false;double completed=-1,validated=-1,score=0,write=0,blocking=0,candidate_journal=0,final_eval=0;
        Gain gain;std::optional<bool> final_truth;
        if(answer==z3::sat){
            ++stats.counts["sat"];if(first_sat<0)first_sat=budget.elapsed();t=budget.elapsed();auto a=f.complete(solver.get_model());r.completion_seconds=budget.elapsed()-t;
            t=budget.elapsed();valid=f.validate(a);r.validation_seconds=budget.elapsed()-t;validated=budget.elapsed();
            t=budget.elapsed();auto serialized=f.model_json(a);candidates<<"{\"query\":"<<query<<",\"coverage_version\":"<<formal<<",\"validated_seconds\":"<<validated<<",\"valid\":"<<(valid?"true":"false")<<",\"model\":"<<serialized<<"}\n";flush(candidates);candidate_journal=budget.elapsed()-t;
            if(!valid)throw std::logic_error("invalid original model");
            auto key=f.key(a);duplicate=seen.count(key);if(duplicate)++stats.counts["duplicates"];
            if(policy&&policy->decision.selected){t=budget.elapsed();final_truth=policy->decision.atom->truth(a);final_eval=budget.elapsed()-t;policy->charge(final_eval);}
            if(!duplicate&&!budget.done()){
                t=budget.elapsed();auto observation=f.evaluate(a);gain=coverage.gain(observation);Coverage next=coverage;next.accept(observation);auto hash=fingerprint(next);score=budget.elapsed()-t;
                if(!budget.done()){
                    t=budget.elapsed();models<<"{\"query\":"<<query<<",\"index\":"<<formal+1<<",\"validated_seconds\":"<<validated<<",\"gain\":"<<gain.covered<<",\"observations\":"<<gain.observed<<",\"covered\":"<<next.covered()<<",\"state_fnv1a64\":"<<quote(std::to_string(hash))<<",\"model\":"<<serialized<<"}\n";flush(models);completed=budget.elapsed();emitted=completed<=budget.seconds&&!interrupted;
                    commits<<"{\"query\":"<<query<<",\"index\":"<<formal+1<<",\"completion\":"<<completed<<",\"timely\":"<<(emitted?"true":"false")<<"}\n";flush(commits);write=budget.elapsed()-t;
                    if(emitted){coverage.states.swap(next.states);seen.insert(key);++formal;marginal_sum+=gain.covered;last_model=a;if(gain.covered)++stats.counts["positive_outputs"];if(gain.observed)++stats.counts["new_observation_outputs"];}
                }
            }
            if(!budget.done()){t=budget.elapsed();solver.add(block(f,a));blocking=budget.elapsed()-t;++stats.counts["blocks"];}
        }
        double hook=policy?policy->last_hook_seconds:0,capture=policy?policy->last_capture_seconds:0;
        stats.times["check_including_hook"]+=r.check_seconds;stats.times["hook_nested_in_check"]+=hook;stats.times["hook_capture"]+=capture;
        stats.times["completion"]+=r.completion_seconds;stats.times["validation"]+=r.validation_seconds;stats.times["scoring"]+=score;stats.times["model_write"]+=write;stats.times["blocking"]+=blocking;stats.times["candidate_journal"]+=candidate_journal;stats.times["install"]+=install;stats.times["final_atom_evaluation"]+=final_eval;
        control.observe(r.exit_decision,budget.elapsed()-began,grant);r.retry_level_after=control.retry_level;
        t=budget.elapsed();std::string decision=policy?policy->decision_json():"{\"selected\":false,\"reason\":\"disabled_before_first_or_plain\"}";
        std::string attempts=policy?policy->attempts_json():"[]";if(policy){atoms<<policy->new_atoms_jsonl();flush(atoms);}
        double metadata=budget.elapsed()-t;if(policy)policy->charge(metadata);stats.times["decision_atom_metadata"]+=metadata;
        double finished=budget.elapsed();
        t=budget.elapsed();queries<<"{\"query\":"<<query<<",\"start\":"<<began<<",\"cycle_start\":"<<cycle_start<<",\"finished\":"<<finished<<",\"coverage_version\":"<<version
            <<",\"evidence\":"<<r.evidence_json()<<",\"decision\":"<<decision<<",\"attempts\":"<<attempts<<",\"hook\":"<<(policy?policy->hook_log:"{}")
            <<",\"valid\":"<<(valid?"true":"false")<<",\"duplicate\":"<<(duplicate?"true":"false")<<",\"emitted\":"<<(emitted?"true":"false")<<",\"completion\":"<<completed
            <<",\"gain\":"<<(emitted?gain.covered:0)<<",\"observations\":"<<(emitted?gain.observed:0)<<",\"final_truth\":";
        if(final_truth)queries<<(*final_truth?"true":"false");else queries<<"null";
        queries<<",\"preparation_seconds\":"<<began-preparation_start<<",\"install_seconds\":"<<install<<",\"capture_seconds\":"<<capture
            <<",\"hook_seconds_nested\":"<<hook<<",\"score_seconds\":"<<score<<",\"output_seconds\":"<<write<<",\"blocking_seconds\":"<<blocking
            <<",\"candidate_journal_seconds\":"<<candidate_journal<<",\"metadata_seconds\":"<<metadata<<",\"final_atom_seconds\":"<<final_eval
            <<",\"mechanism_seconds_to_record\":"<<(policy?policy->mechanism_seconds:0)<<",\"mechanism_stop\":"<<quote(policy?policy->stop_reason:"")<<"}\n";flush(queries);
        stats.times["query_log"]+=budget.elapsed()-t;if(policy)policy->charge(budget.elapsed()-t);
        pending=Pending{query,cycle_start,policy?policy->decision:NativeDecision{},emitted,valid,duplicate,answer==z3::unknown,r.code==SolveStatus::Timeout,emitted?gain.covered:0,emitted?gain.observed:0,final_truth};
        ++query;
        if(answer==z3::unsat){terminal="exhausted_full_original_models";break;}
        if(r.code==SolveStatus::ResourceExhausted||r.exit_decision.stop){terminal=r.exit_decision.classification;break;}
    }
    if(query>=max_queries)terminal="fixed_test_query_limit";
    finalize(budget.elapsed());if(policy)policy->clear();if(interrupted)terminal="external_cancel";
    if(marginal_sum!=coverage.covered())throw std::logic_error("coverage telescoping");
    double elapsed=budget.elapsed(),tail=elapsed-last_boundary;
    std::ofstream o(dir/"summary.json");o<<std::setprecision(17)<<"{\"terminal\":"<<quote(terminal)<<",\"elapsed\":"<<elapsed<<",\"first_sat_seconds\":"<<first_sat
        <<",\"queries\":"<<query<<",\"formal_count\":"<<formal<<",\"marginal_sum\":"<<marginal_sum<<",\"cycle_cost_sum\":"<<cost_sum<<",\"final_tail_seconds\":"<<tail
        <<",\"mechanism_seconds\":"<<(policy?policy->mechanism_seconds:0)<<",\"mechanism_stop\":"<<quote(policy?policy->stop_reason:"")
        <<",\"feature_seconds\":"<<(policy?policy->feature_seconds:0)<<",\"prediction_seconds\":"<<(policy?policy->prediction_seconds:0)<<",\"training_seconds\":"<<(policy?policy->training_seconds:0)
        <<",\"stats\":"<<stats.json()<<",\"coverage\":"<<coverage.json()<<"}\n";flush(o);return 0;
}catch(const std::exception&e){std::cerr<<"native_feedback_sampler: "<<e.what()<<'\n';return 1;}}
