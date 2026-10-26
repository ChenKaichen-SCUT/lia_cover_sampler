#include "native_phase_feedback.hpp"
#include <algorithm>
#include <bit>
#include <cmath>
#include <iomanip>
#include <limits>

namespace lia {
namespace {
double seconds(Clock::time_point t){return std::chrono::duration<double>(Clock::now()-t).count();}
double clipped(double x){return std::clamp(x,0.,1.);}
template<class T> void array_json(std::ostream&o,const T&v){o<<'[';bool comma=false;for(auto x:v){if(comma)o<<',';comma=true;o<<x;}o<<']';}
double dot(const NativeFeatures&a,const NativeFeatures&b){double s=0;for(unsigned i=0;i<native_feature_count;++i)s+=a[i]*b[i];return s;}
unsigned bits(const Z&v){return v==0?0:mpz_sizeinbase(v.get_mpz_t(),2);}
}
NativeRidge::NativeRidge(){for(auto&g:gram_)for(unsigned i=0;i<native_feature_count;++i)g[i][i]=1;}
void NativeRidge::adjust(const Sample&s,double sign){
    auto a=s.action;
    for(unsigned i=0;i<native_feature_count;++i){
        rhs_gain_[a][i]+=sign*s.x[i]*s.reward;rhs_cost_[a][i]+=sign*s.x[i]*s.cost;
        for(unsigned j=0;j<native_feature_count;++j)gram_[a][i][j]+=sign*s.x[i]*s.x[j];
    }
}
void NativeRidge::fit(unsigned a){
    Matrix l{};
    for(unsigned i=0;i<native_feature_count;++i)for(unsigned j=0;j<=i;++j){
        double v=gram_[a][i][j];for(unsigned k=0;k<j;++k)v-=l[i][k]*l[j][k];
        if(i==j){if(!std::isfinite(v)||v<=0)throw std::runtime_error("ridge_nonpositive");l[i][j]=std::sqrt(v);}
        else l[i][j]=v/l[j][j];
    }
    auto solve=[&](const NativeFeatures&b){NativeFeatures y{},w{};
        for(unsigned i=0;i<native_feature_count;++i){double v=b[i];for(unsigned j=0;j<i;++j)v-=l[i][j]*y[j];y[i]=v/l[i][i];}
        for(int i=native_feature_count-1;i>=0;--i){double v=y[i];for(unsigned j=i+1;j<native_feature_count;++j)v-=l[j][i]*w[j];w[i]=v/l[i][i];}return w;};
    weight_gain_[a]=solve(rhs_gain_[a]);weight_cost_[a]=solve(rhs_cost_[a]);
}
NativePrediction NativeRidge::predict(const NativeFeatures&x,double max_cost) const {
    NativePrediction p;
    for(unsigned a=0;a<2;++a){double g=dot(weight_gain_[a],x),t=dot(weight_cost_[a],x);
        if(!std::isfinite(g)||!std::isfinite(t)){p.finite=false;g=0;t=.001;}
        p.gain[a]=clipped(g);p.cost[a]=std::clamp(t,.001,std::max(.001,max_cost));p.utility[a]=p.gain[a]/p.cost[a];
    }return p;
}
void NativeRidge::observe(const NativeFeatures&x,PhaseAction a,double reward,double full_cost){
    if(!std::isfinite(reward)||reward<0||reward>1||!std::isfinite(full_cost)||full_cost<0)throw std::invalid_argument("invalid observed feedback");
    for(double v:x)if(!std::isfinite(v))throw std::invalid_argument("invalid feature");
    Sample s{x,unsigned(a),reward,full_cost};adjust(s,1);window_.push_back(s);++feedback_counts[s.action];
    unsigned expired=2;if(window_.size()>256){expired=window_.front().action;adjust(window_.front(),-1);window_.pop_front();}
    fit(s.action);if(expired<2&&expired!=s.action)fit(expired);
}
NativeChoice NativeRidge::choose(const NativeFeatures&x,std::mt19937_64&rng,bool learning,double max_cost) const {
    NativeChoice c;c.prediction=learning?predict(x,max_cost):NativePrediction{};
    c.cold=learning&&(feedback_counts[0]<8||feedback_counts[1]<8);
    auto draw=[&]{return std::generate_canonical<double,53>(rng);};
    if(!learning||c.cold){c.action_draw=draw();c.action=c.action_draw<.5?PhaseAction::KeepNative:PhaseAction::FlipOnce;c.probability=.5;c.explore=true;return c;}
    unsigned best=c.prediction.finite&&c.prediction.utility[1]>c.prediction.utility[0]?1:0;
    c.exploration_draw=draw();c.explore=c.exploration_draw<.2;
    unsigned a=best;if(c.explore){c.action_draw=draw();a=c.action_draw<.5?0:1;}
    c.action=PhaseAction(a);c.probability=a==best?.9:.1;return c;
}
bool NativeAtom::truth(const Assignment&m) const {
    Z v=linear.at(m);bool r=false;
    switch(relation){case Z3_OP_EQ:r=v==0;break;case Z3_OP_LE:r=v<=0;break;case Z3_OP_LT:r=v<0;break;case Z3_OP_GE:r=v>=0;break;case Z3_OP_GT:r=v>0;break;default:throw std::logic_error("unsupported relation");}
    return r!=negated;
}
NativePhaseFeedback::NativePhaseFeedback(Formula&f,z3::solver&s,Budget&b,const std::string&mode,unsigned seed,bool random_position):
    f_(f),budget_(b),solver_(s),mode_(mode),rng_(uint64_t(seed)^0xd7846ba219ULL),position_rng_(uint64_t(seed)^0x8a5cd789635d2dffULL),random_position_(random_position),quota_seconds(.05*b.seconds){
    auto began=Clock::now();variable_related_.resize(f.variables.size());
    for(unsigned i=0;i<f.nodes.size();++i){original_[f.nodes[i].expr.id()]=i;
        if((i&63)==63&&(b.done()||seconds(began)>=quota_seconds)){stop_reason="feature_initialization_budget";mechanism_seconds+=seconds(began);return;}}
    for(unsigned i=0;i<f.variables.size();++i){
        int node=f.variables[i];if(f.nodes[node].expr.is_int())variables_[f.nodes[node].expr.id()]=i;
        std::set<int> seen;std::vector<int> queue{node};
        for(unsigned j=0;j<queue.size()&&j<64;++j){int n=queue[j];if(!seen.insert(n).second)continue;variable_related_[i].push_back(n);
            for(auto [parent,position]:f.nodes[n].parents){(void)position;if(queue.size()<64)queue.push_back(parent);}}
        if(b.done()||seconds(began)>=quota_seconds){stop_reason="feature_initialization_budget";break;}
    }
    attempts.reserve(32);mechanism_seconds+=seconds(began);
}
NativePhaseFeedback::~NativePhaseFeedback(){try{clear();}catch(...) {}}
bool NativePhaseFeedback::available(){
    if(stop_reason.empty()&&mechanism_seconds>=quota_seconds)stop_reason="mechanism_cost_quota";
    if(stop_reason.empty()&&budget_.done())stop_reason=interrupted?"external_cancel":"session_deadline";
    return stop_reason.empty();
}
void NativePhaseFeedback::charge(double t){mechanism_seconds+=std::max(0.,t);available();}
void NativePhaseFeedback::set_catalog(const ConditionalObservations&summary){
    auto t=Clock::now();for(auto&a:summary.atoms){
        if(!available())break;
        z3::expr e=f_.nodes[a.node].expr.simplify();while(e.is_not())e=e.arg(0);
        catalog_.insert(e.id());catalog_refs_.push_back(e);
    }charge(seconds(t));
}
Affine NativePhaseFeedback::parse_linear(const z3::expr&e,NativeAtom&entry,unsigned depth){
    if(++entry.operations>atom_operation_cap||depth>depth_cap)throw std::runtime_error("atom_operation_cap");
    if(!e.is_int())throw std::runtime_error("non_integer_term");
    Affine result;
    if(e.is_numeral()){result.c=Z(Z3_get_numeral_string(e.ctx(),e));if(bits(result.c)>4096)throw std::runtime_error("constant_size_cap");return result;}
    auto v=variables_.find(e.id());if(v!=variables_.end()){result.a[v->second]=1;return result;}
    if(!e.is_app())throw std::runtime_error("non_application");
    auto k=e.decl().decl_kind();unsigned n=e.num_args();
    if(k==Z3_OP_ADD||k==Z3_OP_SUB){for(unsigned i=0;i<n;++i)result.add(parse_linear(e.arg(i),entry,depth+1),k==Z3_OP_SUB&&i?Z(-1):Z(1));}
    else if(k==Z3_OP_UMINUS&&n==1)result.add(parse_linear(e.arg(0),entry,depth+1),-1);
    else if(k==Z3_OP_MUL&&n==2){auto a=parse_linear(e.arg(0),entry,depth+1),b=parse_linear(e.arg(1),entry,depth+1);
        if(!a.a.empty()&&!b.a.empty())throw std::runtime_error("nonlinear_product");
        if(a.a.empty())result.add(b,a.c);else result.add(a,b.c);
    }else throw std::runtime_error(n==0?"unknown_auxiliary_or_original_noninteger":"unsupported_term");
    if(result.a.size()>variable_cap)throw std::runtime_error("variable_cap");
    if(bits(result.c)>4096)throw std::runtime_error("coefficient_size_cap");
    for(auto&[i,c]:result.a){(void)i;if(bits(c)>4096)throw std::runtime_error("coefficient_size_cap");}
    return result;
}
NativeAtom* NativePhaseFeedback::classify(const z3::expr&atom){
    if(auto found=cache_.find(atom.id());found!=cache_.end())return found->second.get();
    if(cache_.size()>=cache_cap){stop_reason="cache_cap";return nullptr;}
    auto entry=std::make_unique<NativeAtom>(atom,entries_.size());NativeAtom* p=entry.get();
    auto original=original_.find(atom.id());if(original!=original_.end())p->original_node=original->second;
    try{
        z3::expr e=atom;while(e.is_not()){p->negated=!p->negated;e=e.arg(0);if(++p->operations>atom_operation_cap)throw std::runtime_error("atom_operation_cap");}
        p->in_catalog=catalog_.count(e.id());
        p->relation=e.is_app()?e.decl().decl_kind():-1;
        if(!e.is_app()||e.num_args()!=2||(p->relation!=Z3_OP_EQ&&p->relation!=Z3_OP_LE&&p->relation!=Z3_OP_LT&&p->relation!=Z3_OP_GE&&p->relation!=Z3_OP_GT))throw std::runtime_error("unsupported_relation");
        p->linear=parse_linear(e.arg(0),*p,0);p->linear.add(parse_linear(e.arg(1),*p,0),-1);
        p->maximum_bits=bits(p->linear.c);std::set<int> related;unsigned work=0;
        for(auto&[v,c]:p->linear.a){p->variables.push_back(v);p->maximum_bits=std::max(p->maximum_bits,bits(c));
            for(int n:variable_related_[v]){if(work>=512||related.size()>=related_cap)break;++work;related.insert(n);}}
        if(p->maximum_bits>4096)throw std::runtime_error("coefficient_size_cap");
        if(p->variables.empty())throw std::runtime_error("constant_relation");
        if(p->variables.size()>variable_cap)throw std::runtime_error("variable_cap");
        p->related_nodes.assign(related.begin(),related.end());p->supported=true;p->reason="supported_original_integer_relation";
    }catch(const std::exception&e){p->reason=e.what();}
    entries_.push_back(p);cache_.emplace(atom.id(),std::move(entry));return p;
}
NativeFeatures NativePhaseFeedback::features(NativeAtom&a,bool native,unsigned decision,unsigned conflicts) const {
    NativeFeatures x{};x[0]=1;x[1]=a.relation==Z3_OP_EQ;x[2]=clipped(std::log2(1.+a.variables.size())/8.);
    x[3]=clipped(a.maximum_bits/64.);x[4]=native?1:-1;x[5]=clipped(decision/32.);x[6]=clipped(std::log2(1.+conflicts)/8.);
    if(last_model_){x[7]=a.truth(*last_model_)?1:-1;x[8]=1;}
    x[9]=coverage_ratio_;x[10]=recent_zero_;
    unsigned total=0,single=0,unseen=0;
    for(int n:a.related_nodes){auto s=coverage_->states[n];uint64_t mask=f_.nodes[n].boolean?1:~uint64_t(0);total+=f_.nodes[n].boolean?1:64;single+=std::popcount((s.zero^s.one)&mask);unseen+=std::popcount(~(s.zero|s.one)&mask);}
    x[11]=total?double(single)/total:0;x[12]=total?double(unseen)/total:0;x[13]=clipped(last_cost_/2.);x[14]=last_timeout_;x[15]=clipped(granted_ms_/2000.);return x;
}
int NativePhaseFeedback::callback(void*opaque,Z3_ast atom,bool native,unsigned ordinal,unsigned conflicts) noexcept {
    auto*self=static_cast<NativePhaseFeedback*>(opaque);
    try{return self->on_decision(atom,native,ordinal,conflicts);}catch(...){self->stop_reason="callback_exception";return 3;}
}
int NativePhaseFeedback::on_decision(Z3_ast atom,bool native,unsigned ordinal,unsigned conflicts){
    auto began=Clock::now();
    if(!available()){attempts.push_back({ordinal,conflicts,-1,stop_reason.empty()?"session_deadline":stop_reason});return 3;}
    auto a=classify(z3::expr(f_.ctx,atom));
    if(!a||!a->supported||(mode_=="catalog_random_one"&&!a->in_catalog)){
        attempts.push_back({ordinal,conflicts,a?int(a->cache_index):-1,!a?stop_reason:!a->supported?a->reason:"outside_catalog"});
        auto elapsed=seconds(began);feature_seconds+=elapsed;charge(elapsed);return available()?0:3;
    }
    if(random_position_){
        auto reason=position_.visit(a->expression.id(),ordinal);
        if(reason!="selected"){
            attempts.push_back({ordinal,conflicts,int(a->cache_index),reason,position_.rank});
            auto elapsed=seconds(began);feature_seconds+=elapsed;charge(elapsed);
            // Existing callback ABI: 0 means keep the native phase and continue
            // searching. Here it is an intentional positional skip, not an
            // unsupported atom. 1=consume/Keep, 2=consume/Flip, 3=disable.
            return available()?0:3;
        }
    }
    auto x=features(*a,native,ordinal,conflicts);for(double v:x)if(!std::isfinite(v)){stop_reason="nonfinite_feature";charge(seconds(began));attempts.push_back({ordinal,conflicts,int(a->cache_index),stop_reason});return 3;}
    double prep=seconds(began);feature_seconds+=prep;charge(prep);if(!available()){attempts.push_back({ordinal,conflicts,int(a->cache_index),stop_reason.empty()?"session_deadline":stop_reason});return 3;}
    auto t=Clock::now();auto choice=learner.choose(x,rng_,mode_=="native_feedback_one",budget_.seconds);
    auto pred=seconds(t);prediction_seconds+=pred;charge(pred);if(!available()){attempts.push_back({ordinal,conflicts,int(a->cache_index),stop_reason.empty()?"session_deadline":stop_reason});return 3;}
    decision.selected=true;decision.native_truth=native;decision.desired_truth=native!=(choice.action==PhaseAction::FlipOnce);
    decision.ordinal=ordinal;decision.conflicts=conflicts;decision.atom=a;decision.features=x;decision.choice=choice;decision.training_counts=learner.feedback_counts;
    decision.supported_rank=random_position_?position_.rank:1;
    attempts.push_back({ordinal,conflicts,int(a->cache_index),"selected",decision.supported_rank});
    return choice.action==PhaseAction::FlipOnce?2:1;
}
void NativePhaseFeedback::prepare(const Coverage&c,const Assignment*m,unsigned version,unsigned query,unsigned grant,double recent_zero,double last_cost,bool last_timeout){
    clear();decision=NativeDecision{};decision.query=query;decision.coverage_version=version;attempts.clear();hook_log="{}";
    position_eligible_=false;position_.reset(1);
    if(!version||!available())return;
    position_eligible_=true;
    if(random_position_)position_.reset(std::uniform_int_distribution<unsigned>(1,4)(position_rng_));
    coverage_=&c;last_model_=m;coverage_ratio_=f_.total_bits()?double(c.covered())/f_.total_bits():0;
    recent_zero_=recent_zero;last_cost_=last_cost;last_timeout_=last_timeout;granted_ms_=grant;
    if(version&&available()){Z3_solver_set_native_phase_callback(f_.ctx,solver_,callback,this,query);f_.ctx.check_error();installed_=true;}
}
z3::check_result NativePhaseFeedback::check(){
    try{auto before=mechanism_seconds;auto start=Clock::now();auto result=solver_.check();last_check_seconds=seconds(start);
        start=Clock::now();last_hook_seconds=0;
        if(installed_){hook_log=Z3_solver_get_native_phase_log(f_.ctx,solver_);f_.ctx.check_error();
            auto at=hook_log.find("\"hook_seconds\":");if(at!=std::string::npos)last_hook_seconds=std::stod(hook_log.substr(at+15));}
        clear();last_capture_seconds=seconds(start);
        charge(std::max(0.,last_hook_seconds-(mechanism_seconds-before))+last_capture_seconds);return result;}
    catch(...){clear();throw;}
}
void NativePhaseFeedback::clear(){if(installed_){Z3_solver_clear_native_phase_callback(f_.ctx,solver_);f_.ctx.check_error();installed_=false;}}
double NativePhaseFeedback::observe(const NativeDecision&d,double reward,double cost){
    if(!d.selected||mode_!="native_feedback_one"||!available())return 0;
    auto t=Clock::now();try{learner.observe(d.features,d.choice.action,reward,cost);}catch(...){stop_reason="ridge_numeric_failure";}
    auto elapsed=seconds(t);training_seconds+=elapsed;charge(elapsed);return elapsed;
}
std::string NativePhaseFeedback::decision_json() const {
    auto&d=decision;std::ostringstream o;o<<std::setprecision(17)<<"{\"selected\":"<<(d.selected?"true":"false")<<",\"query\":"<<d.query<<",\"coverage_version\":"<<d.coverage_version;
    if(d.selected){o<<",\"cache_index\":"<<d.atom->cache_index<<",\"original_node\":"<<d.atom->original_node<<",\"in_catalog\":"<<(d.atom->in_catalog?"true":"false")<<",\"ordinal\":"<<d.ordinal<<",\"conflicts\":"<<d.conflicts
        <<",\"native_truth\":"<<(d.native_truth?"true":"false")<<",\"desired_truth\":"<<(d.desired_truth?"true":"false")<<",\"action\":"<<quote(d.choice.action==PhaseAction::FlipOnce?"FlipOnce":"KeepNative")
        <<",\"probability\":"<<d.choice.probability<<",\"cold_start\":"<<(d.choice.cold?"true":"false")<<",\"exploration\":"<<(d.choice.explore?"true":"false")
        <<",\"exploration_draw\":"<<d.choice.exploration_draw<<",\"action_draw\":"<<d.choice.action_draw<<",\"training_counts\":";array_json(o,d.training_counts);
        o<<",\"prediction_enabled\":"<<(mode_=="native_feedback_one"?"true":"false")<<",\"features\":";array_json(o,d.features);o<<",\"predicted_gain\":";array_json(o,d.choice.prediction.gain);o<<",\"predicted_cost\":";array_json(o,d.choice.prediction.cost);o<<",\"predicted_utility\":";array_json(o,d.choice.prediction.utility);o<<",\"predictions_finite\":"<<(d.choice.prediction.finite?"true":"false");
    }else o<<",\"reason\":"<<quote(stop_reason.empty()?"no_eligible_opportunity":stop_reason);
    o<<'}';return o.str();
}
std::string NativePhaseFeedback::attempts_json() const {
    std::ostringstream o;o<<'[';bool comma=false;for(auto&a:attempts){if(comma)o<<',';comma=true;o<<"{\"ordinal\":"<<a.ordinal<<",\"conflicts\":"<<a.conflicts<<",\"cache_index\":"<<a.cache_index<<",\"reason\":"<<quote(a.reason)<<",\"supported_rank\":"<<a.supported_rank<<'}';}return o.str()+']';
}
std::string NativePhaseFeedback::position_json() const {
    std::ostringstream o;o<<"{\"eligible\":"<<(position_eligible_?"true":"false")<<",\"policy\":"<<quote(random_position_?"random_supported_rank":"first")
        <<",\"target\":"<<position_.target<<",\"distinct_supported\":"<<(random_position_?position_.rank:unsigned(decision.selected))
        <<",\"reached\":"<<(decision.selected?"true":"false")<<",\"selected_rank\":"<<decision.supported_rank
        <<",\"reason\":"<<quote(decision.selected?"selected":!position_eligible_?"disabled":!stop_reason.empty()?stop_reason:"target_not_reached")<<'}';return o.str();
}
std::string NativePhaseFeedback::new_atoms_jsonl(){
    std::ostringstream o;
    for(;journaled_entries_<entries_.size();++journaled_entries_){auto*a=entries_[journaled_entries_];a->logged=true;o<<"{\"cache_index\":"<<a->cache_index<<",\"local_ast_id\":"<<a->expression.id()<<",\"original_node\":"<<a->original_node<<",\"supported\":"<<(a->supported?"true":"false")<<",\"reason\":"<<quote(a->reason)<<",\"in_catalog\":"<<(a->in_catalog?"true":"false")<<",\"relation\":"<<a->relation<<",\"negated\":"<<(a->negated?"true":"false")<<",\"operations\":"<<a->operations<<",\"maximum_bits\":"<<a->maximum_bits;
        o<<",\"expression\":";if(a->supported)o<<quote(a->expression.to_string());else o<<"null";
        o<<",\"variables\":";array_json(o,a->variables);o<<",\"related_nodes\":";array_json(o,a->related_nodes);o<<"}\n";
    }return o.str();
}
}
