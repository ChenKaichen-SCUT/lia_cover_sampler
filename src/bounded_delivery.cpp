#include "bounded_delivery.hpp"
#include <algorithm>
#include <bit>

namespace lia {
uint64_t state_fingerprint(const Coverage&c){uint64_t h=14695981039346656037ULL;for(auto&s:c.states)for(uint64_t v:{s.zero,s.one})for(unsigned i=0;i<8;++i){h^=(v>>(8*i))&255;h*=1099511628211ULL;}return h;}
Gain mask_gain(const Coverage&c,const std::vector<State>&o){
    if(c.states.size()!=o.size())throw std::logic_error("observation size");
    Gain g;for(size_t i=0;i<o.size();++i){auto s=c.states[i];auto n=o[i];
        g.covered+=std::popcount(((s.zero|n.zero)&(s.one|n.one))&~(s.zero&s.one));
        g.observed+=std::popcount(n.zero&~s.zero)+std::popcount(n.one&~s.one);
    }return g;
}
void accept_masks(Coverage&c,const std::vector<State>&o){if(c.states.size()!=o.size())throw std::logic_error("observation size");for(size_t i=0;i<o.size();++i){c.states[i].zero|=o[i].zero;c.states[i].one|=o[i].one;}}
bool observation_subset(const Coverage&a,const Coverage&b){if(a.states.size()!=b.states.size())return false;for(size_t i=0;i<a.states.size();++i)if((a.states[i].zero&~b.states[i].zero)||(a.states[i].one&~b.states[i].one))return false;return true;}
bool same_observations(const Coverage&a,const Coverage&b){return observation_subset(a,b)&&observation_subset(b,a);}
BoundedDelivery::BoundedDelivery(GenerationState&g,DeliveryState&d,DeliveryPolicy p,double total,
    std::function<double()> now,std::function<bool()> stop,std::function<std::optional<double>(const Candidate&,unsigned,const Gain&)> emit,std::function<void(const BatchRecord&)> log):
    generation_(g),delivery_(d),policy_(p),now_(std::move(now)),stopped_(std::move(stop)),emit_(std::move(emit)),journal_(std::move(log)),quota_(total*quota_fraction),immediate_(p==DeliveryPolicy::Immediate){}
void BoundedDelivery::offer(Candidate c){
    auto t=now_();
    if(!generation_.identities.count(c->key)||delivery_.identities.count(c->key))throw std::logic_error("invalid candidate availability");
    for(auto&p:pending_)if(p->key==c->key)throw std::logic_error("duplicate pending candidate");
    pending_.push_back(std::move(c));maintenance_seconds+=now_()-t;
    if(delivery_.version==0)flush("first_model",true);
    else if(immediate_)flush(policy_==DeliveryPolicy::Immediate?"immediate":"session_cost_fallback",true);
    else refresh_if_due();
}
void BoundedDelivery::refresh_if_due(bool ending){
    if(pending_.empty()||failed_)return;
    if(ending)flush("generation_ending");
    else if(pending_.size()>=capacity)flush("capacity");
    else if(now_()-pending_.front()->ready>=wait_trigger)flush("wait_threshold");
}
void BoundedDelivery::flush(const std::string&reason,bool force_fifo){
    if(pending_.empty()||failed_)return;
    BatchRecord b;b.id=next_batch_++;b.reason=reason;b.start=now_();b.trigger_delay=std::max(0.,b.start-pending_.front()->ready-wait_trigger);
    auto begin=now_();Coverage expected=delivery_.coverage;b.before=state_fingerprint(expected);
    for(auto&p:pending_){b.members.push_back(p->index);accept_masks(expected,p->observations);}b.expected=state_fingerprint(expected);
    double spent=now_()-begin;maintenance_seconds+=spent;b.maintenance+=spent;
    bool fifo=force_fifo||immediate_;
    while(!pending_.empty()&&!stopped_()){
        begin=now_();
        if(!immediate_&&maintenance_seconds>=quota_){immediate_=true;fifo=true;++quota_fallbacks;b.fallback="session_5pct";quota_stop_at=now_();quota_stop_cost=maintenance_seconds;}
        if(!fifo&&b.maintenance>=batch_cap){fifo=true;b.fallback="batch_20ms";}
        size_t winner=0;Gain best=mask_gain(delivery_.coverage,pending_[0]->observations);
        if(!fifo)for(size_t i=1;i<pending_.size();++i){
            if(now_()-begin+b.maintenance>=batch_cap){fifo=true;b.fallback="batch_20ms";winner=0;best=mask_gain(delivery_.coverage,pending_[0]->observations);break;}
            Gain g=mask_gain(delivery_.coverage,pending_[i]->observations);
            if(g.covered>best.covered||(g.covered==best.covered&&g.observed>best.observed)){winner=i;best=g;}
        }
        Candidate c=pending_[winner];spent=now_()-begin;maintenance_seconds+=spent;b.maintenance+=spent;
        // emit_ includes common coverage bookkeeping and model/receipt writes;
        // these costs remain in wall time but are not sorting-maintenance cost.
        auto completed=emit_(c,b.id,best);
        begin=now_();
        if(!completed){failed_=true;spent=now_()-begin;maintenance_seconds+=spent;b.maintenance+=spent;break;}
        b.choices.push_back({c->index,best,*completed,*completed-c->ready,fifo,b.maintenance});pending_.erase(pending_.begin()+winner);
        if(!observation_subset(delivery_.coverage,generation_.coverage))throw std::logic_error("delivered observations exceed generated");
        spent=now_()-begin;maintenance_seconds+=spent;b.maintenance+=spent;
    }
    begin=now_();b.complete=pending_.empty();b.after=state_fingerprint(delivery_.coverage);
    if(b.complete&&(!same_observations(expected,delivery_.coverage)||!same_observations(delivery_.coverage,generation_.coverage)))throw std::logic_error("batch observation conservation");
    if(!b.fallback.empty())++fifo_batches;
    spent=now_()-begin;maintenance_seconds+=spent;b.maintenance+=spent;b.finished=now_();
    begin=now_();journal_(b);maintenance_seconds+=now_()-begin;
    if(!immediate_&&maintenance_seconds>=quota_){immediate_=true;++quota_fallbacks;quota_stop_at=now_();quota_stop_cost=maintenance_seconds;}
}
}
