#include "lia_choice_compiler.hpp"
#include "residual_coverage.hpp"
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <ctime>
#include <cmath>
#include <cstdlib>
using namespace lia;
namespace {
void stop(int){interrupted=1;}
void flush(std::ofstream &s){s.flush();if(!s)throw std::runtime_error("write failed");}
std::string solver_statistics(z3::solver &solver){
    auto values=solver.statistics();std::ostringstream o;o<<std::setprecision(17)<<'{';
    for(unsigned i=0;i<values.size();++i){if(i)o<<',';o<<quote(values.key(i))<<':';
        if(values.is_uint(i))o<<values.uint_value(i);else if(values.is_double(i))o<<values.double_value(i);else o<<"null";}
    return o.str()+"}";
}
}
int main(int argc,char **argv){try{
    if(argc!=7 && argc!=8)throw std::invalid_argument("bofill_first_sat_probe F MODE repeated_2s|continuous|verify SECONDS SEED NEW_OUT [saved.values]");
    Budget budget;budget.seconds=std::stod(argv[4]);
    if(auto p=std::getenv("BOFILL_START_MONO_NS"))budget.start=Clock::time_point(std::chrono::duration_cast<Clock::duration>(std::chrono::nanoseconds(std::stoll(p))));
    const auto cpu_start=std::clock();
    if(!std::isfinite(budget.seconds)||budget.seconds<=0)throw std::invalid_argument("seconds");
    auto mode=parse_compile_mode(argv[2]);std::string control_mode=argv[3];
    if(control_mode!="repeated_2s" && control_mode!="continuous" && control_mode!="verify")throw std::invalid_argument("control");
    unsigned seed=std::stoul(argv[5]);std::signal(SIGINT,stop);std::signal(SIGTERM,stop);
    std::filesystem::path dir(argv[6]);if(!std::filesystem::create_directory(dir))throw std::runtime_error("new directory required");
    std::ofstream queries(dir/"queries.jsonl");queries<<std::setprecision(17);Stats stats;
    auto t=budget.elapsed();Formula f(argv[1]);stats.times["load"]=budget.elapsed()-t;
    t=budget.elapsed();CompiledProblem problem(f,mode,budget);stats.times["compile"]=budget.elapsed()-t;
    t=budget.elapsed();{std::ofstream o(dir/"structure.json");o<<problem.json()<<'\n';flush(o);}
    {std::ofstream o(dir/"compiled.smt2");z3::solver export_only(f.ctx);export_only.add(problem.compiled_formula);o<<export_only.to_smt2();flush(o);}
    stats.times["structure_write"]=budget.elapsed()-t;
    if(control_mode=="verify"){
        if(argc!=8)throw std::invalid_argument("saved values required");
        auto assignments=read_residual_values(f,argv[7]);std::ofstream o(dir/"roundtrip.jsonl");
        unsigned index=0;for(auto &a:assignments){auto lifted=problem.lift(a);bool original=f.validate(a),compiled=lifted.eval(problem.compiled_formula,true).is_true(),roundtrip=problem.restore(lifted)==a;
            bool choice_bounds=true;for(auto &c:problem.choices)if(c.choice){auto v=lifted.eval(*c.choice,true);choice_bounds&=v.is_numeral() && (Z(Z3_get_numeral_string(f.ctx,v))==0 || Z(Z3_get_numeral_string(f.ctx,v))==1);}
            o<<"{\"index\":"<<index++<<",\"original\":"<<(original?"true":"false")<<",\"compiled\":"<<(compiled?"true":"false")<<",\"exact_roundtrip\":"<<(roundtrip?"true":"false")<<",\"choice_bounds\":"<<(choice_bounds?"true":"false")<<",\"model\":"<<f.model_json(a)<<"}\n";
            if(!original||!compiled||!roundtrip||!choice_bounds)throw std::logic_error("real witness roundtrip failed");}
        flush(o);std::ofstream summary(dir/"summary.json");summary<<"{\"kind\":\"saved_witness_no_search\",\"count\":"<<index<<",\"elapsed\":"<<budget.elapsed()<<",\"stats\":"<<stats.json()<<"}\n";flush(summary);return 0;
    }
    if(argc==8)throw std::invalid_argument("historical witnesses forbidden in performance probe");
    t=budget.elapsed();z3::solver solver(f.ctx,z3::solver::simple{});solver.add(problem.compiled_formula);solver.set("random_seed",seed);stats.times["solver_initialization"]=budget.elapsed()-t;
    {std::ofstream o(dir/"initial.json");o<<std::setprecision(17)<<"{\"mode\":"<<quote(compile_mode_name(mode))<<",\"requested_mode\":"<<quote(argv[2])<<",\"control\":"<<quote(control_mode)<<",\"seed\":"<<seed<<",\"seconds\":"<<budget.seconds<<",\"source_sha256\":"<<quote(LIA_SOURCE_SHA)<<",\"z3_version\":"<<quote(Z3_get_full_version())<<",\"solver\":\"simple_persistent\",\"validation_reserve_seconds\":0.025,\"initialized_seconds\":"<<budget.elapsed()<<"}\n";flush(o);}
    SolveBudgetState controller;controller.fixed_levels=true;
    std::string terminal="NO_SAT_WITHIN_BUDGET";unsigned query=0;double first_sat=-1,first_legal=-1,written=-1;
    while(!budget.done()){
        unsigned request=control_mode=="continuous"?static_cast<unsigned>(std::ceil(budget.seconds*1000)):controller.requested_timeout_ms;
        auto grant=grant_query(request,budget.left(),budget.left(),.025);if(!grant.granted_ms)break;
        GlobalResult r;r.requested_timeout_ms=request;r.actual_timeout_ms=grant.granted_ms;r.budget_clip_reason=grant.clip_reason;r.retry_level_before=controller.retry_level;
        r.session_remaining_before_check=r.action_remaining_before_check=budget.left();const auto began=budget.elapsed();
        solver.set("timeout",grant.granted_ms);t=budget.elapsed();r.check_started=true;auto answer=solver.check();r.check_seconds=budget.elapsed()-t;
        r.raw_status=answer==z3::sat?"sat":answer==z3::unsat?"unsat":"unknown";r.detail=answer==z3::unknown?solver.reason_unknown():"";
        r.exit_evidence={r.detail,grant.granted_ms,r.check_seconds,bool(interrupted),budget.left()<=0,false,false};r.exit_decision=interpret_query_exit(r.raw_status,r.exit_evidence);r.code=r.exit_decision.code;
        bool valid=false,timely=false;
        if(answer==z3::sat){first_sat=budget.elapsed();t=budget.elapsed();auto a=problem.restore(solver.get_model());r.completion_seconds=budget.elapsed()-t;
            t=budget.elapsed();valid=f.validate(a);r.validation_seconds=budget.elapsed()-t;if(!valid)throw std::logic_error("invalid restored original model");first_legal=budget.elapsed();
            if(!budget.done()){
                std::ofstream o(dir/"candidate.json");o<<std::setprecision(17)<<"{\"query\":"<<query<<",\"validated_seconds\":"<<first_legal<<",\"model\":"<<f.model_json(a)<<"}\n";flush(o);written=budget.elapsed();timely=!budget.done();
                std::ofstream c(dir/"receipt.json");c<<std::setprecision(17)<<"{\"query\":"<<query<<",\"completion\":"<<written<<",\"timely\":"<<(timely?"true":"false")<<"}\n";flush(c);
                if(timely)terminal="SAT";
            }
        }
        stats.times["check"]+=r.check_seconds;stats.times["reconstruction"]+=r.completion_seconds;stats.times["validation"]+=r.validation_seconds;
        // Read public counters only after preserving the completed witness. Charge this cost too.
        t=budget.elapsed();auto counters=solver_statistics(solver);double stat_time=budget.elapsed()-t;stats.times["statistics"]+=stat_time;
        controller.observe(r.exit_decision,budget.elapsed()-began,grant);r.retry_level_after=controller.retry_level;
        queries<<"{\"query\":"<<query++<<",\"start\":"<<began<<",\"finished\":"<<budget.elapsed()<<",\"evidence\":"<<r.evidence_json()<<",\"statistics_read_at\":"<<t<<",\"statistics_seconds\":"<<stat_time<<",\"statistics\":"<<counters<<",\"valid\":"<<(valid?"true":"false")<<",\"timely\":"<<(timely?"true":"false")<<"}\n";flush(queries);
        if(answer==z3::unsat){terminal="UNSAT";break;}
        if(answer==z3::sat || control_mode=="continuous" || r.exit_decision.stop || r.code==SolveStatus::ResourceExhausted)break;
    }
    if(interrupted && terminal!="SAT")terminal="EXTERNAL_CANCEL";
    std::ofstream summary(dir/"summary.json");summary<<std::setprecision(17)<<"{\"terminal\":"<<quote(terminal)<<",\"mode\":"<<quote(compile_mode_name(mode))<<",\"control\":"<<quote(control_mode)<<",\"elapsed\":"<<budget.elapsed()<<",\"cpu_seconds\":"<<double(std::clock()-cpu_start)/CLOCKS_PER_SEC<<",\"first_sat_seconds\":"<<first_sat<<",\"first_legal_seconds\":"<<first_legal<<",\"completion\":"<<written<<",\"queries\":"<<query<<",\"stats\":"<<stats.json()<<"}\n";flush(summary);return 0;
}catch(const std::exception &e){std::cerr<<e.what()<<'\n';return 1;}}
