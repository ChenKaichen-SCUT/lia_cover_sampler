#include "arith_coverage_bridge.hpp"
#include <algorithm>
#include <iomanip>
#include <climits>

namespace lia {
namespace {
struct Incomplete:std::exception{};
void tick(Budget*b){if(b&&b->done())throw Incomplete();}
double number(const std::string&s,const std::string&k,double fallback=0){auto at=s.find('"'+k+'"'+':');return at==std::string::npos?fallback:std::stod(s.substr(at+k.size()+3));}
bool flag(const std::string&s,const std::string&k){auto at=s.find('"'+k+'"'+':');return at!=std::string::npos&&s.compare(at+k.size()+3,4,"true")==0;}
}
unsigned arith_coverage_choice(const std::vector<Gain>&scores,uint64_t seed){
    if(scores.empty())throw std::invalid_argument("empty score pool");
    std::vector<unsigned>winners;Gain best;for(unsigned i=0;i<scores.size();++i){auto g=scores[i];
        if(winners.empty()||std::tie(g.covered,g.observed)>std::tie(best.covered,best.observed)){best=g;winners={i};}else if(std::tie(g.covered,g.observed)==std::tie(best.covered,best.observed))winners.push_back(i);}
    if(winners.front()==0)return 0;std::mt19937_64 rng(seed);return winners[std::uniform_int_distribution<unsigned>(0,winners.size()-1)(rng)];
}
uint64_t arith_stream_seed(unsigned seed,uint64_t tag,unsigned query){
    uint64_t x=uint64_t(seed)^tag^(uint64_t(query)*0x9e3779b97f4a7c15ULL);
    x+=0x9e3779b97f4a7c15ULL;x=(x^(x>>30))*0xbf58476d1ce4e5b9ULL;x=(x^(x>>27))*0x94d049bb133111ebULL;return x^(x>>31);
}
std::string arith_assignment_hash(const Assignment&a){uint64_t h=14695981039346656037ULL;for(auto const&v:a){for(unsigned char c:v.get_str()){h^=c;h*=1099511628211ULL;}h^=';';h*=1099511628211ULL;}std::ostringstream o;o<<std::hex<<h;return o.str();}
PureEvaluationPlan::PureEvaluationPlan(const Formula&f):f_(f),nodes(f.nodes.size()),affine_cache_(f.nodes.size(),-1),constant_cache_(f.nodes.size(),-1){}
void PureEvaluationPlan::prepare(Budget&b){
    while(cursor_<nodes.size()){
        tick(&b);auto const&n=f_.nodes[cursor_];auto k=n.expr.decl().decl_kind();
        if(n.variable<0 && k!=Z3_OP_ANUM && k!=Z3_OP_TRUE && k!=Z3_OP_FALSE && k!=Z3_OP_AND && k!=Z3_OP_OR && k!=Z3_OP_NOT && k!=Z3_OP_ITE && k!=Z3_OP_ADD && k!=Z3_OP_SUB && k!=Z3_OP_UMINUS && k!=Z3_OP_MUL && k!=Z3_OP_EQ && k!=Z3_OP_LE && k!=Z3_OP_LT && k!=Z3_OP_GE && k!=Z3_OP_GT)throw std::runtime_error("unsupported_operator");
        if(!n.boolean&&!n.expr.is_int())throw std::runtime_error("unsupported_sort");
        if(mpz_sizeinbase(n.numeral.get_mpz_t(),2)>4096)throw std::runtime_error("integer_bits");
        nodes[cursor_]={k,n.children,n.variable,n.boolean,n.numeral};++cursor_;
    }
    while(variables.size()<f_.variables.size()){
        tick(&b);auto const&n=f_.nodes[f_.variables[variables.size()]];variables.push_back(n.expr);booleans.push_back(n.boolean);names.push_back(n.expr.decl().name().str());
    }
    while(affine_cursor_<nodes.size() && terms.size()<256){
        tick(&b);auto id=affine_cursor_;
        if(!nodes[id].boolean && affine(id,b) && !constant_cache_[id])terms.push_back(f_.nodes[id].expr);
        ++affine_cursor_;
    }
    ready=true;
}
bool PureEvaluationPlan::affine(int id,Budget&b,unsigned depth){
    tick(&b);if(depth>512)return false;if(affine_cache_[id]>=0)return affine_cache_[id];
    auto const&n=nodes[id];bool valid=!n.boolean,constant=n.variable<0;
    if(n.variable>=0){valid=!n.boolean;constant=false;}
    else if(n.op==Z3_OP_ANUM){}
    else if(n.op==Z3_OP_ADD||n.op==Z3_OP_SUB||n.op==Z3_OP_UMINUS||n.op==Z3_OP_MUL){
        unsigned nonconstant=0;for(int c:n.children){if(!affine(c,b,depth+1))valid=false;constant&=constant_cache_[c]>0;nonconstant+=constant_cache_[c]==0;}
        if(n.op==Z3_OP_MUL && nonconstant>1)valid=false;
    }else valid=false;
    constant_cache_[id]=constant;affine_cache_[id]=valid;return valid;
}
Observation PureEvaluationPlan::evaluate(const Assignment&input,Budget*b) const {
    if(!ready||input.size()!=variables.size())throw std::runtime_error("incomplete pure assignment");
    for(unsigned i=0;i<input.size();++i)if(booleans[i]&&input[i]!=0&&input[i]!=1)throw std::runtime_error("invalid Boolean assignment");
    Observation o;o.values.resize(nodes.size());o.visited.resize(nodes.size());unsigned operations=0;
    std::function<Z(int,unsigned)>eval=[&](int id,unsigned depth)->Z {
        if((++operations&31)==0)tick(b);if(depth>1024)throw std::runtime_error("evaluation_depth");
        if(o.visited[id])return o.values[id];auto const&n=nodes[id];auto const&c=n.children;Z v;auto at=[&](unsigned i){return eval(c[i],depth+1);};
        if(n.variable>=0)v=input[n.variable];else switch(n.op){
        case Z3_OP_ANUM:v=n.numeral;break;case Z3_OP_TRUE:v=1;break;case Z3_OP_FALSE:v=0;break;
        case Z3_OP_AND:v=1;for(int i:c)if(eval(i,depth+1)==0){v=0;break;}break;
        case Z3_OP_OR:v=0;for(int i:c)if(eval(i,depth+1)!=0){v=1;break;}break;
        case Z3_OP_NOT:v=at(0)==0;break;case Z3_OP_ITE:v=at(0)!=0?at(1):at(2);break;
        case Z3_OP_ADD:v=0;for(int i:c)v+=eval(i,depth+1);break;
        case Z3_OP_SUB:v=at(0);for(unsigned i=1;i<c.size();++i)v-=at(i);break;
        case Z3_OP_UMINUS:v=-at(0);break;case Z3_OP_MUL:v=1;for(int i:c)v*=eval(i,depth+1);break;
        case Z3_OP_EQ:case Z3_OP_LE:case Z3_OP_LT:case Z3_OP_GE:case Z3_OP_GT:{Z left=at(0),right=at(1);v=n.op==Z3_OP_EQ?left==right:n.op==Z3_OP_LE?left<=right:n.op==Z3_OP_LT?left<right:n.op==Z3_OP_GE?left>=right:left>right;break;}
        default:throw std::runtime_error("unsupported pure operator; no Z3 fallback");}
        if(mpz_sizeinbase(v.get_mpz_t(),2)>4096)throw std::runtime_error("integer_bits");
        o.values[id]=v;o.visited[id]=1;o.events.push_back(id);return v;
    };eval(0,0);tick(b);return o;
}
std::string PureEvaluationPlan::model_json(const Assignment&input) const {std::ostringstream o;o<<'[';for(unsigned i=0;i<input.size();++i){if(i)o<<',';o<<"{\"name\":"<<quote(names[i])<<",\"sort\":"<<quote(booleans[i]?"Bool":"Int")<<",\"value\":"<<(booleans[i]?(input[i]!=0?"true":"false"):quote(input[i].get_str()))<<'}';}return o.str()+']';}
ArithCoverageBridge::ArithCoverageBridge(Formula&f,z3::solver&s,Budget&b,const DeliveryState&d,const GenerationState&g,const std::string&mode,unsigned seed):f_(f),solver_(s),session_(b),delivery_(d),generation_(g),mode_(mode),seed_(seed),quota(.05*b.seconds){}
ArithCoverageBridge::~ArithCoverageBridge(){if(installed_)Z3_solver_clear_arith_sampling(f_.ctx,solver_);}
void ArithCoverageBridge::stop_if_needed(){if(stop_reason.empty() && cumulative>=quota)stop_reason="mechanism_cost_quota";if(!stop_reason.empty()&&stop_at<0){stop_at=session_.elapsed();stop_query=query_;}}
void ArithCoverageBridge::charge(double seconds){cumulative+=std::max(0.,seconds);stop_if_needed();}
void ArithCoverageBridge::prepare(unsigned query,unsigned granted_ms){
    auto begin=Clock::now();
    query_=query;version_=delivery_.version;reason_="disabled";backend_="{}";pool_models_="[]";pool_scores_="[]";original_.clear();selected_.clear();offered_=pool_size_=0;score_complete_=pool_received_=fatal_=false;expected_covered_=expected_observed_=scoring_=prepare_=callback_=0;predicted_={};
    if(installed_){Z3_solver_clear_arith_sampling(f_.ctx,solver_);installed_=false;}
    stop_if_needed();if(!stop_reason.empty()){reason_=stop_reason;return;}if(mode_=="arith_probe" && probe_states_>=32){reason_="probe_state_cap";return;}if(!delivery_.version){reason_="before_first";return;}
    if(first_prepare_at<0)first_prepare_at=session_.elapsed();Budget local;local.start=begin;local.parent=&session_;local.seconds=std::min({.002,quota-cumulative,double(granted_ms)/1000});
    bool initialization=!plan_ || !plan_->ready;
    try{
        if(!plan_)plan_=std::make_unique<PureEvaluationPlan>(f_);
        if(!plan_->ready)plan_->prepare(local);tick(&local);
        Z3_solver_set_arith_sampling(f_.ctx,solver_,f_.root,plan_->variables.size(),plan_->variables.data(),plan_->terms.size(),plan_->terms.data(),callback,this,query,version_,arith_stream_seed(seed_,0x70726f706f73616cULL,query),local.left());f_.ctx.check_error();installed_=true;reason_="awaiting_hook";if(first_install_at<0)first_install_at=session_.elapsed();
    }catch(Incomplete const&){reason_=initialization?"prepare_incomplete":"prepare_budget";}
    catch(std::exception const&e){reason_=e.what();stop_reason=reason_;}
    prepare_=std::chrono::duration<double>(Clock::now()-begin).count();if(initialization)initialization_seconds+=prepare_;charge(prepare_);
    if(!stop_reason.empty()&&installed_){Z3_solver_clear_arith_sampling(f_.ctx,solver_);installed_=false;}
}
unsigned ArithCoverageBridge::callback(void*p,unsigned count,unsigned vars,const char*const*data,const char*meta) noexcept {
    auto*self=static_cast<ArithCoverageBridge*>(p);auto start=Clock::now();unsigned result=0;
    try{result=self->select(count,vars,data,meta);}catch(Incomplete const&){self->reason_="score_incomplete";self->score_complete_=false;self->pool_models_="[]";self->pool_scores_="[]";}
    catch(std::exception const&e){self->reason_=std::string("callback_failure: ")+e.what();self->fatal_=true;result=UINT_MAX;}
    self->callback_=std::chrono::duration<double>(Clock::now()-start).count();return result;
}
unsigned ArithCoverageBridge::select(unsigned count,unsigned vars,const char*const*data,const char*meta){
    auto start=Clock::now();std::string info(meta);Budget local;local.parent=&session_;local.seconds=std::min(number(info,"callback_remaining_seconds"),std::max(0.,quota-cumulative));
    if(vars!=f_.variables.size()||count<1||count>16)throw std::runtime_error("invalid pool shape");
    pool_received_=true;pool_size_=count;std::vector<Assignment>models;std::set<std::string>identities;
    for(unsigned i=0;i<count;++i){tick(&local);Assignment model;for(unsigned j=0;j<vars;++j){Z value(data[i*vars+j]);if(mpz_sizeinbase(value.get_mpz_t(),2)>4096)throw std::runtime_error("candidate bit cap");model.push_back(std::move(value));}
        auto key=f_.key(model);if(!identities.insert(key).second || generation_.identities.count(key))throw std::runtime_error("candidate violates complete model blocker");models.push_back(std::move(model));}
    original_=models[0];selected_=original_;
    std::mt19937_64 rng(arith_stream_seed(seed_,0x73656c656374696fULL,query_));
    if(mode_=="arith_random"){
        offered_=std::uniform_int_distribution<unsigned>(0,count-1)(rng);selected_=models[offered_];reason_="random_selected";callback_=std::chrono::duration<double>(Clock::now()-start).count();return offered_;
    }
    auto scoring_start=Clock::now();struct ScoreTimer {double&v;Clock::time_point t;bool done=false;void finish(){if(!done){v=std::chrono::duration<double>(Clock::now()-t).count();done=true;}}~ScoreTimer(){finish();}} score_timer{scoring_,scoring_start};std::vector<Gain>scores;
    for(unsigned i=0;i<count;++i){tick(&local);auto observation=plan_->evaluate(models[i],&local);if(observation.values[0]==0)throw std::runtime_error("candidate violates original formula");auto gain=delivery_.coverage.gain(observation);scores.push_back(gain);tick(&local);
    }
    score_complete_=true;score_timer.finish();
    for(auto g:scores){expected_covered_+=double(g.covered)/count;expected_observed_+=double(g.observed)/count;}
    offered_=arith_coverage_choice(scores,arith_stream_seed(seed_,0x73656c656374696fULL,query_));selected_=models[offered_];predicted_=scores[offered_];
    std::ostringstream sc;sc<<'[';for(unsigned i=0;i<count;++i){if(i)sc<<',';sc<<"["<<scores[i].covered<<','<<scores[i].observed<<']';}pool_scores_=sc.str()+']';
    if(mode_=="arith_probe"){
        std::ostringstream ms;ms<<'[';for(unsigned i=0;i<count;++i){if(i)ms<<',';ms<<plan_->model_json(models[i]);}pool_models_=ms.str()+']';reason_="probe_no_commit";
    }else reason_="coverage_selected";
    tick(&local);callback_=std::chrono::duration<double>(Clock::now()-start).count();return mode_=="arith_probe"?0:offered_;
}
void ArithCoverageBridge::capture(){
    if(!installed_)return;auto begin=Clock::now();backend_=Z3_solver_get_arith_sampling_log(f_.ctx,solver_);f_.ctx.check_error();Z3_solver_clear_arith_sampling(f_.ctx,solver_);f_.ctx.check_error();installed_=false;
    if(flag(backend_,"opportunity_consumed"))++probe_states_;
    charge(number(backend_,"hook_seconds")+std::chrono::duration<double>(Clock::now()-begin).count());
    if(flag(backend_,"implementation_failure"))fatal_=true;
}
std::string ArithCoverageBridge::record(const Assignment*final,bool delivered,Gain actual) const {
    auto const&actual_selected=flag(backend_,"committed_move")?selected_:original_;bool same=final && !actual_selected.empty() && *final==actual_selected;unsigned survived=0;if(final&&!actual_selected.empty())for(unsigned i=0;i<final->size();++i)survived+=(*final)[i]==actual_selected[i];
    std::ostringstream o;o<<std::setprecision(17)<<"{\"query_id\":"<<query_<<",\"mode\":"<<quote(mode_)<<",\"seed\":"<<seed_<<",\"coverage_version_before\":"<<version_<<",\"reason\":"<<quote(reason_)<<",\"backend\":"<<backend_
     <<",\"implementation_failure\":"<<(fatal_?"true":"false")<<",\"pool_received\":"<<(pool_received_?"true":"false")<<",\"score_complete\":"<<(score_complete_?"true":"false")<<",\"offered_candidate\":"<<offered_<<",\"offered_is_noop\":"<<(offered_==0?"true":"false")
     <<",\"identity_algorithm\":\"fnv1a64_decimal_values_semicolon\",\"original_assignment_hash\":"<<quote(original_.empty()?"":arith_assignment_hash(original_))<<",\"offered_assignment_hash\":"<<quote(selected_.empty()?"":arith_assignment_hash(selected_))<<",\"selected_assignment_hash\":"<<quote(actual_selected.empty()?"":arith_assignment_hash(actual_selected))
     <<",\"final_assignment_hash\":"<<quote(final?arith_assignment_hash(*final):"")<<",\"selected_values_survival\":"<<(actual_selected.empty()||!final?"null":std::to_string(double(survived)/actual_selected.size()))<<",\"postcheck_changed\":"<<(final&&!actual_selected.empty()&&!same?"true":"false")
     <<",\"final_model_produced\":"<<(final?"true":"false")<<",\"formally_delivered\":"<<(delivered?"true":"false")<<",\"actual_delta_covered\":"<<actual.covered<<",\"actual_delta_observed\":"<<actual.observed
     <<",\"prediction_semantics\":\"offered_candidate_before_backend_commit_decision\",\"selected_is_noop\":"<<(!flag(backend_,"committed_move")?"true":"false")<<",\"predicted_delta_covered\":"<<(score_complete_?std::to_string(predicted_.covered):"null")<<",\"predicted_delta_observed\":"<<(score_complete_?std::to_string(predicted_.observed):"null")
     <<",\"random_expected_covered\":"<<(score_complete_?std::to_string(expected_covered_):"null")<<",\"random_expected_observed\":"<<(score_complete_?std::to_string(expected_observed_):"null")
     <<",\"prepare_time\":"<<prepare_<<",\"scoring_time\":"<<scoring_<<",\"callback_time\":"<<callback_<<",\"action_soft_overrun\":"<<(prepare_+number(backend_,"hook_seconds")>.002?"true":"false")
     <<",\"mechanism_cumulative_time\":"<<cumulative<<",\"stop_reason\":"<<quote(stop_reason)<<",\"stop_query\":"<<stop_query<<",\"stop_at_seconds\":"<<stop_at
     <<",\"pool_scores\":"<<pool_scores_<<",\"probe_models\":"<<pool_models_<<'}';return o.str();
}
std::string ArithCoverageBridge::summary() const {std::ostringstream o;o<<std::setprecision(17)<<"{\"mechanism_seconds\":"<<cumulative<<",\"quota_seconds\":"<<quota<<",\"initialization_seconds\":"<<initialization_seconds<<",\"first_prepare_at\":"<<first_prepare_at<<",\"first_install_at\":"<<first_install_at<<",\"stop_reason\":"<<quote(stop_reason)<<",\"stop_at\":"<<stop_at<<",\"stop_query\":"<<stop_query<<",\"observed_hook_states\":"<<probe_states_<<'}';return o.str();}
}
