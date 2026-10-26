#include "native_phase_feedback.hpp"
#include "bounded_delivery.hpp"
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <cstdlib>
#include <cmath>

using namespace lia;
namespace {
void stop(int){interrupted=1;}
const char* jb(bool x){return x?"true":"false";}
void flush(std::ofstream&s){s.flush();if(!s)throw std::runtime_error("journal write failed");}
z3::expr block(Formula&f,const Assignment&a){z3::expr_vector terms(f.ctx);for(size_t i=0;i<a.size();++i)terms.push_back(f.nodes[f.variables[i]].expr!=f.value(i,a[i]));return z3::mk_or(terms);}
template<class T>void numbers(std::ostream&o,const T&v){o<<'[';bool comma=false;for(auto x:v){if(comma)o<<',';comma=true;o<<x;}o<<']';}
std::string query_statistics(z3::solver&s){
    auto st=s.statistics();std::ostringstream o;o<<std::setprecision(17)<<'{';bool comma=false;
    for(unsigned i=0;i<st.size();++i){auto k=st.key(i);
        if(k!="conflicts"&&k!="decisions"&&k!="propagations"&&k!="restarts"&&k!="rlimit count"&&k!="num checks"&&k!="arith conflicts")continue;
        if(comma)o<<',';comma=true;o<<quote(k)<<':';
        if(st.is_uint(i))o<<st.uint_value(i);else if(st.is_double(i))o<<st.double_value(i);else o<<"null";
    }return o.str()+'}';
}
}
int main(int argc,char**argv){try{
    if(argc!=6)throw std::invalid_argument("native_delivery_sampler F CONFIG SECONDS SEED NEW_OUT");
    Budget budget;budget.seconds=std::stod(argv[3]);
    if(auto p=std::getenv("BOFILL_START_MONO_NS"))budget.start=Clock::time_point(std::chrono::duration_cast<Clock::duration>(std::chrono::nanoseconds(std::stoll(p))));
    if(!std::isfinite(budget.seconds)||budget.seconds<=0)throw std::invalid_argument("seconds");
    std::string config=argv[2];bool position=config=="position_immediate"||config=="position_batch",batch=config=="first_batch"||config=="position_batch";
    bool fixed4=config=="fixed4_immediate";
    if(!position&&!batch&&!fixed4&&config!="first_immediate")throw std::invalid_argument("configuration");
    auto position_policy=fixed4?NativePositionPolicy::FixedFourth:position?NativePositionPolicy::UniformOneToFour:NativePositionPolicy::FixedFirst;
    unsigned seed=std::stoul(argv[4]),max_queries=UINT_MAX;
    if(auto p=std::getenv("NATIVE_TEST_MAX_QUERIES"))max_queries=std::stoul(p);
    constexpr double delivery_reserve=.100,query_reserve=.025;
    auto generation_left=[&]{return std::max(0.,budget.left()-delivery_reserve);};
    std::signal(SIGTERM,stop);std::signal(SIGINT,stop);
    std::filesystem::path dir(argv[5]);if(!std::filesystem::create_directory(dir))throw std::runtime_error("new directory required");
    std::ofstream candidates(dir/"candidates.jsonl"),generation_commits(dir/"generation_commits.jsonl"),generation_receipts(dir/"generation_receipts.jsonl"),
        deliveries(dir/"deliveries.jsonl"),delivery_commits(dir/"delivery_commits.jsonl"),delivery_receipts(dir/"delivery_receipts.jsonl"),
        queries(dir/"queries.jsonl"),positions(dir/"positions.jsonl"),batches(dir/"batches.jsonl"),atoms(dir/"atoms.jsonl"),blocks(dir/"blocks.jsonl"),costs(dir/"costs.jsonl"),diagnostics(dir/"diagnostics.jsonl");
    for(auto p:{&candidates,&generation_commits,&generation_receipts,&deliveries,&delivery_commits,&delivery_receipts,&queries,&positions,&batches,&atoms,&blocks,&costs,&diagnostics})*p<<std::setprecision(17);
    Stats stats;double t=budget.elapsed();Formula f(argv[1]);if(!f.metric_compatible)throw std::runtime_error("metric unsupported");stats.times["load"]=budget.elapsed()-t;
    t=budget.elapsed();z3::solver solver(f.ctx,z3::solver::simple{});solver.add(f.root);solver.set("random_seed",seed);stats.times["solver_initialization"]=budget.elapsed()-t;
    GenerationState generation(f);DeliveryState delivery(f);SolveBudgetState control;control.fixed_levels=true;
    std::unique_ptr<NativePhaseFeedback> policy;std::unique_ptr<ConditionalObservations> summaries;std::deque<bool> recent_zero;
    unsigned query=0;double first_sat=-1,last_cost=0;bool last_timeout=false;std::string terminal="generation_deadline";
    double common_delivery_cost=0,write_cost=0,all_delivery_cost=0;
    auto emit=[&](const Candidate&c,unsigned batch_id,const Gain&g)->std::optional<double>{
        double began=budget.elapsed();if(budget.done())return std::nullopt;
        if(delivery.identities.count(c->key))throw std::logic_error("duplicate formal candidate");
        Coverage next=delivery.coverage;accept_masks(next,c->observations);auto hash=state_fingerprint(next);
        common_delivery_cost+=budget.elapsed()-began;if(budget.done())return std::nullopt;
        auto write_start=budget.elapsed();unsigned index=delivery.version+1;
        deliveries<<"{\"formal_output_index\":"<<index<<",\"candidate_generation_index\":"<<c->index<<",\"query\":"<<c->query
            <<",\"generation_version\":"<<generation.version<<",\"delivery_version\":"<<delivery.version<<",\"batch_id\":"<<batch_id
            <<",\"candidate_ready_time\":"<<c->ready<<",\"gain\":"<<g.covered<<",\"observations\":"<<g.observed
            <<",\"covered\":"<<next.covered()<<",\"state_fnv1a64\":"<<quote(std::to_string(hash))<<",\"model\":"<<c->serialized<<"}\n";flush(deliveries);
        double model_complete=budget.elapsed();
        delivery_receipts<<"{\"formal_output_index\":"<<index<<",\"candidate_generation_index\":"<<c->index<<",\"model_write_complete\":"<<model_complete<<"}\n";flush(delivery_receipts);
        double completed=budget.elapsed();bool timely=completed<=budget.seconds&&!interrupted;
        delivery_commits<<"{\"formal_output_index\":"<<index<<",\"candidate_generation_index\":"<<c->index<<",\"query\":"<<c->query
            <<",\"generation_version\":"<<generation.version<<",\"delivery_version\":"<<(delivery.version+unsigned(timely))<<",\"batch_id\":"<<batch_id
            <<",\"model_write_complete\":"<<model_complete<<",\"delivery_complete_time\":"<<completed<<",\"candidate_ready_time\":"<<c->ready
            <<",\"timely\":"<<jb(timely)<<",\"cancelled\":"<<jb(bool(interrupted))<<",\"gain\":"<<(timely?g.covered:0)<<",\"observations\":"<<(timely?g.observed:0)
            <<",\"covered\":"<<(timely?next.covered():delivery.coverage.covered())<<",\"state_fnv1a64\":"<<quote(std::to_string(timely?hash:state_fingerprint(delivery.coverage)))<<"}\n";flush(delivery_commits);
        write_cost+=budget.elapsed()-write_start;
        double update=budget.elapsed();
        if(timely){delivery.coverage.states.swap(next.states);delivery.identities.insert(c->key);++delivery.version;delivery.marginal_sum+=g.covered;delivery.observation_sum+=g.observed;}
        common_delivery_cost+=budget.elapsed()-update;
        return timely?std::optional<double>(completed):std::nullopt;
    };
    auto journal=[&](const BatchRecord&b){
        batches<<"{\"batch_id\":"<<b.id<<",\"reason\":"<<quote(b.reason)<<",\"fallback\":"<<quote(b.fallback)
            <<",\"start\":"<<b.start<<",\"finished\":"<<b.finished<<",\"maintenance_seconds\":"<<b.maintenance<<",\"trigger_delay\":"<<b.trigger_delay
            <<",\"before_fnv\":"<<quote(std::to_string(b.before))<<",\"expected_fnv\":"<<quote(std::to_string(b.expected))<<",\"after_fnv\":"<<quote(std::to_string(b.after))
            <<",\"complete\":"<<jb(b.complete)<<",\"members\":";numbers(batches,b.members);batches<<",\"choices\":[";
        bool comma=false;for(auto&x:b.choices){if(comma)batches<<',';comma=true;batches<<"{\"candidate_generation_index\":"<<x.candidate<<",\"gain\":"<<x.gain.covered
            <<",\"observations\":"<<x.gain.observed<<",\"delivery_complete_time\":"<<x.completed<<",\"waiting\":"<<x.waiting<<",\"fifo\":"<<jb(x.fifo)<<",\"maintenance_at_selection\":"<<x.maintenance_at_selection<<'}';}batches<<"]}\n";flush(batches);
    };
    BoundedDelivery manager(generation,delivery,batch?DeliveryPolicy::BoundedGreedyBatch:DeliveryPolicy::Immediate,budget.seconds,[&]{return budget.elapsed();},[&]{return budget.done();},emit,journal);
    auto refresh=[&](bool ending){double began=budget.elapsed();if(ending)manager.flush("generation_deadline",true);else manager.refresh_if_due();all_delivery_cost+=budget.elapsed()-began;};
    {std::ofstream o(dir/"initial.json");o<<std::setprecision(17)<<"{\"mode\":"<<quote(config)<<",\"seed\":"<<seed<<",\"seconds\":"<<budget.seconds
        <<",\"source_sha256\":"<<quote(LIA_SOURCE_SHA)<<",\"z3_version\":"<<quote(Z3_get_full_version())<<",\"total_bits\":"<<f.total_bits()
        <<",\"batch_capacity\":32,\"wait_trigger\":0.5,\"batch_maintenance_cap\":0.020,\"delivery_management_quota_fraction\":0.05,\"delivery_reserve\":0.100,\"query_reserve\":0.025"
        <<",\"position_range\":[1,2,3,4],\"decision_window\":32,\"selected_limit\":1,\"flip_probability\":0.5,\"learning\":false,\"test_query_limit\":"<<max_queries
        <<",\"position_policy\":"<<quote(position_policy_name(position_policy))<<",\"action_sampling\":\"after_target_and_prechoice_budget_checks\",\"mechanism_quota_fraction\":0.05"
        <<",\"formula_nodes\":"<<f.nodes.size()<<",\"original_variables\":"<<f.variables.size()<<",\"solver_kind\":\"simple_incremental\",\"solver_random_seed\":"<<seed
        <<",\"action_rng_seed\":"<<quote(std::to_string(uint64_t(seed)^0xd7846ba219ULL))<<",\"position_rng_seed\":"<<quote(std::to_string(uint64_t(seed)^0x8a5cd789635d2dffULL))
        <<",\"formula_load_seconds\":"<<stats.times["load"]<<",\"solver_initialization_seconds\":"<<stats.times["solver_initialization"]<<",\"initialization_record_seconds\":"<<budget.elapsed()<<"}\n";flush(o);}
    while(!budget.done()&&query<max_queries){
        refresh(generation_left()<=query_reserve);if(manager.failed()||generation_left()<=query_reserve)break;
        double cycle_start=budget.elapsed(),preparation_start=cycle_start;double delivery_before=all_delivery_cost;
        double mechanism_before=policy?policy->mechanism_seconds:0;
        if(generation.version&&!policy){
            t=budget.elapsed();policy=std::make_unique<NativePhaseFeedback>(f,solver,budget,"native_random_one",seed,position_policy);stats.times["feature_initialization"]+=budget.elapsed()-t;
            if(policy->available()){
                t=budget.elapsed();Budget preparation;preparation.parent=&budget;preparation.seconds=std::min(1.,std::max(0.,policy->quota_seconds-policy->mechanism_seconds));
                summaries=std::make_unique<ConditionalObservations>(f,preparation);std::ofstream o(dir/"summaries.json");o<<summaries->json()<<'\n';flush(o);
                auto elapsed=budget.elapsed()-t;stats.times["catalog_preparation_and_log"]+=elapsed;policy->charge(elapsed);policy->set_catalog(*summaries);
            }
        }
        auto grant=grant_query(control.requested_timeout_ms,generation_left(),generation_left(),query_reserve);
        if(!grant.granted_ms){terminal="preparation_budget";break;}
        double install=0;
        if(policy){t=budget.elapsed();double zero=0;for(bool z:recent_zero)zero+=z;if(!recent_zero.empty())zero/=recent_zero.size();
            policy->prepare(generation.coverage,generation.last_generated_model?&*generation.last_generated_model:nullptr,generation.version,query,grant.granted_ms,zero,last_cost,last_timeout);
            install=budget.elapsed()-t;policy->charge(install);if(!policy->available())policy->clear();}
        grant=grant_query(control.requested_timeout_ms,generation_left(),generation_left(),query_reserve);
        if(!grant.granted_ms){if(policy)policy->clear();terminal="preparation_budget";break;}
        if(policy)policy->set_grant(grant.granted_ms);
        const auto began=budget.elapsed();GlobalResult r;r.requested_timeout_ms=grant.requested_ms;r.actual_timeout_ms=grant.granted_ms;r.budget_clip_reason=grant.clip_reason;
        r.retry_level_before=control.retry_level;r.session_remaining_before_check=budget.left();r.action_remaining_before_check=generation_left();
        solver.set("timeout",grant.granted_ms);t=budget.elapsed();const auto check_call_start=t;r.check_started=true;auto answer=policy?policy->check():solver.check();const auto check_call_finish=budget.elapsed();
        r.check_seconds=policy?policy->last_check_seconds:budget.elapsed()-t;
        r.raw_status=answer==z3::sat?"sat":answer==z3::unsat?"unsat":"unknown";r.detail=answer==z3::unknown?solver.reason_unknown():"";
        r.exit_evidence={r.detail,grant.granted_ms,r.check_seconds,bool(interrupted),budget.left()<=0,false,false};r.exit_decision=interpret_query_exit(r.raw_status,r.exit_evidence);r.code=r.exit_decision.code;
        t=budget.elapsed();auto solver_statistics=query_statistics(solver);double statistics_seconds=budget.elapsed()-t;
        stats.times["query_statistics_capture"]+=statistics_seconds;if(policy)policy->charge(statistics_seconds);
        unsigned version=generation.version;bool valid=false,duplicate=false,generated=false;Gain gain;unsigned candidate_index=0;double ready=-1,scoring=0,journaling=0,blocking=0;
        if(answer==z3::sat){
            ++stats.counts["sat"];if(first_sat<0)first_sat=budget.elapsed();t=budget.elapsed();auto a=f.complete(solver.get_model());r.completion_seconds=budget.elapsed()-t;
            t=budget.elapsed();valid=f.validate(a);r.validation_seconds=budget.elapsed()-t;double validated=budget.elapsed();if(!valid)throw std::logic_error("invalid original model");
            auto key=f.key(a);duplicate=generation.identities.count(key);if(duplicate)++stats.counts["duplicates"];
            t=budget.elapsed();auto observation=f.evaluate(a);Coverage single(f);single.accept(observation);Coverage next=generation.coverage;gain=mask_gain(next,single.states);accept_masks(next,single.states);auto hash=state_fingerprint(next);scoring=budget.elapsed()-t;
            auto c=std::make_shared<SavedCandidate>();c->index=generation.version+1;c->query=query;c->validated=validated;c->assignment=a;c->key=key;c->serialized=f.model_json(a);c->observations=std::move(single.states);
            t=budget.elapsed();candidates<<"{\"query\":"<<query<<",\"candidate_generation_index\":"<<c->index<<",\"generation_version\":"<<generation.version
                <<",\"validated_seconds\":"<<validated<<",\"observation_completed_time\":"<<t<<",\"valid\":true,\"duplicate\":"<<jb(duplicate)<<",\"model\":"<<c->serialized<<"}\n";flush(candidates);
            generation_receipts<<"{\"query\":"<<query<<",\"candidate_generation_index\":"<<c->index<<"}\n";flush(generation_receipts);ready=budget.elapsed();c->ready=ready;
            generated=!duplicate&&ready<=budget.seconds&&!interrupted;candidate_index=generated?c->index:0;
            generation_commits<<"{\"query\":"<<query<<",\"candidate_generation_index\":"<<c->index<<",\"generation_version\":"<<(generation.version+unsigned(generated))
                <<",\"candidate_ready_time\":"<<ready<<",\"timely\":"<<jb(generated)<<",\"duplicate\":"<<jb(duplicate)<<",\"cancelled\":"<<jb(bool(interrupted))
                <<",\"gain\":"<<(generated?gain.covered:0)<<",\"observations\":"<<(generated?gain.observed:0)<<",\"covered\":"<<(generated?next.covered():generation.coverage.covered())
                <<",\"state_fnv1a64\":"<<quote(std::to_string(generated?hash:state_fingerprint(generation.coverage)))<<"}\n";flush(generation_commits);journaling=budget.elapsed()-t;
            if(generated){
                generation.coverage.states.swap(next.states);generation.identities.insert(key);generation.last_generated_model=a;++generation.version;generation.marginal_sum+=gain.covered;generation.observation_sum+=gain.observed;
                // This complete no-good is installed before any batch selection.
                t=budget.elapsed();solver.add(block(f,a));blocking=budget.elapsed()-t;++stats.counts["blocks"];
                blocks<<"{\"candidate_generation_index\":"<<c->index<<",\"query\":"<<query<<",\"complete_time\":"<<budget.elapsed()<<"}\n";flush(blocks);
                t=budget.elapsed();manager.offer(c);all_delivery_cost+=budget.elapsed()-t;
            }
        }
        refresh(generation_left()<=query_reserve);
        stats.times["check_including_hook"]+=r.check_seconds;stats.times["completion"]+=r.completion_seconds;stats.times["validation"]+=r.validation_seconds;stats.times["generation_scoring"]+=scoring;
        stats.times["generation_journal"]+=journaling;stats.times["blocking"]+=blocking;stats.times["install"]+=install;
        double generation_cycle=budget.elapsed()-cycle_start-(all_delivery_cost-delivery_before);
        control.observe(r.exit_decision,std::max(0.,generation_cycle-(began-preparation_start)),grant);r.retry_level_after=control.retry_level;
        t=budget.elapsed();std::string decision=policy?policy->decision_json():"{\"selected\":false,\"reason\":\"before_first\"}";
        if(policy){atoms<<policy->new_atoms_jsonl();flush(atoms);policy->charge(budget.elapsed()-t);}
        positions<<"{\"query\":"<<query<<",\"generation_version\":"<<version<<",\"position\":"<<(policy?policy->position_json():"{\"eligible\":false,\"target\":0,\"reached\":false,\"reason\":\"before_first\"}")
            <<",\"decision\":"<<decision<<",\"attempts\":"<<(policy?policy->attempts_json():"[]")<<",\"hook\":"<<(policy?policy->hook_log:"{}")
            <<",\"mechanism\":"<<(policy?policy->mechanism_json():"null")<<"}\n";flush(positions);
        t=budget.elapsed();queries<<"{\"query\":"<<query<<",\"start\":"<<began<<",\"check_call_start\":"<<check_call_start<<",\"check_call_finish\":"<<check_call_finish<<",\"finished\":"<<budget.elapsed()<<",\"generation_version_before\":"<<version<<",\"generation_version\":"<<generation.version
            <<",\"delivery_version\":"<<delivery.version<<",\"candidate_generation_index\":"<<candidate_index<<",\"candidate_ready_time\":"<<ready<<",\"evidence\":"<<r.evidence_json()
            <<",\"valid\":"<<jb(valid)<<",\"duplicate\":"<<jb(duplicate)<<",\"generated\":"<<jb(generated)<<",\"decision\":"<<decision<<",\"hook\":"<<(policy?policy->hook_log:"{}")
            <<",\"generation_gain\":"<<(generated?gain.covered:0)<<",\"generation_observations\":"<<(generated?gain.observed:0)<<",\"generation_scoring_seconds\":"<<scoring
            <<",\"blocking_seconds\":"<<blocking<<",\"preparation_seconds\":"<<began-preparation_start<<",\"mechanism_seconds\":"<<(policy?policy->mechanism_seconds:0)<<"}\n";flush(queries);
        if(policy)policy->charge(budget.elapsed()-t);
        t=budget.elapsed();diagnostics<<"{\"query\":"<<query<<",\"cycle_start\":"<<cycle_start<<",\"generation_version_before\":"<<version
            <<",\"mechanism_before_seconds\":"<<mechanism_before<<",\"mechanism\":"<<(policy?policy->mechanism_json():"null")
            <<",\"solver_statistics_raw\":"<<solver_statistics<<",\"statistics_capture_seconds\":"<<statistics_seconds
            <<",\"statistics_semantics\":\"raw_snapshot_after_check; do_not_assume_per_query_delta\",\"record_seconds\":"<<budget.elapsed()<<"}\n";flush(diagnostics);
        double diagnostic_log_seconds=budget.elapsed()-t;stats.times["diagnostic_journal"]+=diagnostic_log_seconds;if(policy)policy->charge(diagnostic_log_seconds);
        last_cost=budget.elapsed()-cycle_start-(all_delivery_cost-delivery_before);last_timeout=r.code==SolveStatus::Timeout;recent_zero.push_back(!generated||gain.observed==0);if(recent_zero.size()>16)recent_zero.pop_front();
        costs<<"{\"query\":"<<query<<",\"cycle_start\":"<<cycle_start<<",\"cycle_end\":"<<budget.elapsed()<<",\"generation_cost\":"<<last_cost<<",\"delivery_cost\":"<<all_delivery_cost-delivery_before<<"}\n";flush(costs);
        ++query;
        if(manager.failed()){terminal="delivery_not_timely";break;}
        if(answer==z3::unsat){terminal="exhausted_full_original_models";break;}
        if(r.code==SolveStatus::ResourceExhausted||r.exit_decision.stop){terminal=r.exit_decision.classification;break;}
    }
    if(query>=max_queries)terminal="fixed_test_query_limit";
    t=budget.elapsed();manager.flush(budget.done()?"deadline_or_cancel":terminal,budget.left()<=delivery_reserve+query_reserve);all_delivery_cost+=budget.elapsed()-t;
    if(policy)policy->clear();if(interrupted)terminal="external_cancel";
    if(generation.marginal_sum!=generation.coverage.covered()||delivery.marginal_sum!=delivery.coverage.covered()||!observation_subset(delivery.coverage,generation.coverage))throw std::logic_error("dual coverage invariant");
    std::ofstream tail(dir/"undelivered.jsonl");tail<<std::setprecision(17);for(auto&c:manager.pending())tail<<"{\"candidate_generation_index\":"<<c->index<<",\"query\":"<<c->query<<",\"candidate_ready_time\":"<<c->ready<<",\"reason\":"<<quote(terminal)<<"}\n";flush(tail);
    stats.times["delivery_all"]+=all_delivery_cost;stats.times["delivery_common_processing"]+=common_delivery_cost;stats.times["model_and_receipt_write"]+=write_cost;stats.times["delivery_management"]+=manager.maintenance_seconds;
    std::ofstream o(dir/"summary.json");o<<std::setprecision(17)<<"{\"terminal\":"<<quote(terminal)<<",\"elapsed\":"<<budget.elapsed()<<",\"queries\":"<<query<<",\"first_sat_seconds\":"<<first_sat
        <<",\"generated_count\":"<<generation.version<<",\"formal_count\":"<<delivery.version<<",\"undelivered_count\":"<<manager.pending().size()
        <<",\"generation_marginal_sum\":"<<generation.marginal_sum<<",\"delivery_marginal_sum\":"<<delivery.marginal_sum<<",\"generation_observation_sum\":"<<generation.observation_sum<<",\"delivery_observation_sum\":"<<delivery.observation_sum
        <<",\"delivery_management_seconds\":"<<manager.maintenance_seconds<<",\"fifo_batches\":"<<manager.fifo_batches<<",\"quota_fallbacks\":"<<manager.quota_fallbacks<<",\"quota_stop_at\":"<<manager.quota_stop_at<<",\"quota_stop_cost\":"<<manager.quota_stop_cost
        <<",\"mechanism_seconds\":"<<(policy?policy->mechanism_seconds:0)<<",\"mechanism_stop\":"<<quote(policy?policy->stop_reason:"")<<",\"mechanism_diagnostics\":"<<(policy?policy->mechanism_json():"null")<<",\"training_seconds\":0,\"stats\":"<<stats.json()
        <<",\"generation_coverage\":"<<generation.coverage.json()<<",\"coverage\":"<<delivery.coverage.json()<<"}\n";flush(o);return 0;
}catch(const std::exception&e){std::cerr<<"native_delivery_sampler: "<<e.what()<<'\n';return 1;}}
