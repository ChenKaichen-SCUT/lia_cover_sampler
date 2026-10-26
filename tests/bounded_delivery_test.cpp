#include "bounded_delivery.hpp"
#include "native_phase_feedback.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <unistd.h>
using namespace lia;
void require(bool x,const char*m){if(!x)throw std::runtime_error(m);}
struct Fixture {
    Formula&f;GenerationState g;DeliveryState d;double now=0,tick=0,write=0;bool cancel=false;unsigned call=0;
    std::vector<unsigned> order;std::vector<BatchRecord> records;std::unique_ptr<BoundedDelivery> manager;
    Fixture(Formula&f,DeliveryPolicy p=DeliveryPolicy::BoundedGreedyBatch,double total=100):f(f),g(f),d(f){
        manager=std::make_unique<BoundedDelivery>(g,d,p,total,[&]{now+=tick;return now;},[&,total]{return cancel||now>=total;},
        [&,total](const Candidate&c,unsigned,const Gain&gain)->std::optional<double>{
            ++call;now+=write;if(cancel||now>=total)return std::nullopt;
            auto expected=mask_gain(d.coverage,c->observations);require(gain.covered==expected.covered&&gain.observed==expected.observed,"stale gain");
            accept_masks(d.coverage,c->observations);d.identities.insert(c->key);++d.version;order.push_back(c->index);return now;
        },[&](const BatchRecord&b){records.push_back(b);});
    }
    Candidate add(int value){
        auto c=std::make_shared<SavedCandidate>();c->index=++g.version;c->key=std::to_string(value);c->ready=now;c->assignment=Assignment(f.variables.size(),value);
        Coverage single(f);single.accept(f.evaluate(c->assignment));c->observations=single.states;
        g.identities.insert(c->key);g.last_generated_model=c->assignment;accept_masks(g.coverage,c->observations);manager->offer(c);return c;
    }
};
int main(){try{
    auto path=std::filesystem::temp_directory_path()/("bounded_delivery_"+std::to_string(getpid())+".smt2");
    {std::ofstream o(path);o<<"(declare-const x Int)(assert (>= x 0))";}Formula f(path);std::filesystem::remove(path);
    {Fixture x(f);x.add(0);require(x.order==std::vector<unsigned>{1},"first waited");x.add(1);x.add(3);require(x.order.size()==1,"premature flush");x.now=.51;x.manager->refresh_if_due();require(x.order==std::vector<unsigned>({1,3,2}),"greedy/re-score/tie order");require(x.records.back().complete&&same_observations(x.g.coverage,x.d.coverage),"batch lost information");require(x.records.back().choices[1].gain.covered==0,"zero gain candidate discarded");}
    {Fixture x(f);x.add(0);for(int i=1;i<=32;++i)x.add(i);require(x.manager->pending().empty()&&x.order.size()==33,"capacity failed");require(x.records.back().reason=="capacity","wrong capacity trigger");}
    {Fixture x(f);x.add(0);x.add(1);x.add(3);x.now=2.5;x.manager->refresh_if_due();require(x.records.back().trigger_delay>=2.,"blocking check delay hidden");}
    {Fixture x(f);x.add(0);x.add(1);x.add(3);x.tick=.03;x.manager->flush("test");require(x.order==std::vector<unsigned>({1,2,3}),"cost fallback not FIFO");require(x.records.back().fallback=="batch_20ms","batch cost not reported");}
    {Fixture x(f,DeliveryPolicy::BoundedGreedyBatch,1);x.add(0);x.add(1);x.add(3);x.tick=.01;x.manager->flush("test");x.tick=0;x.add(7);require(x.manager->quota_fallbacks==1&&x.manager->pending().empty(),"session fallback not permanent immediate");}
    {Fixture x(f);x.add(0);x.add(1);x.cancel=true;x.manager->refresh_if_due(true);require(x.order.size()==1&&x.manager->pending().size()==1,"cancel fabricated output");require(observation_subset(x.d.coverage,x.g.coverage),"subset broken");}
    {Fixture x(f,DeliveryPolicy::BoundedGreedyBatch,1);x.add(0);x.add(1);x.write=2;x.manager->refresh_if_due(true);require(x.manager->failed()&&x.order.size()==1&&x.manager->pending().size()==1,"late receipt accepted");}
    {Fixture x(f);auto c=x.add(0);bool rejected=false;try{x.manager->offer(c);}catch(...){rejected=true;}require(rejected,"duplicate admitted");auto future=std::make_shared<SavedCandidate>();future->key="not_generated";rejected=false;try{x.manager->offer(future);}catch(...){rejected=true;}require(rejected,"future candidate admitted");}
    // Previously unseen observations can require two models to cover a bit.
    {Coverage empty(f);std::vector<State> a(empty.states.size()),b(empty.states.size());a[0].zero=1;b[0].one=1;
        require(mask_gain(empty,a).covered==0&&mask_gain(empty,a).observed==1,"first observation counted covered");accept_masks(empty,a);require(mask_gain(empty,b).covered==1,"paired first observation lost");}
    // Fixed membership FIFO reference: ordering changes prefixes, never union.
    {Fixture greedy(f),fifo(f,DeliveryPolicy::Immediate);for(int v:{0,1,3,7,15}){greedy.add(v);fifo.add(v);}greedy.manager->flush("test");require(same_observations(greedy.d.coverage,fifo.d.coverage),"fixed stream union differs");}
    for(unsigned k=1;k<=4;++k){SupportedPosition p;p.reset(k);for(unsigned i=1;i<k;++i){require(p.visit(i,i)=="position_before_target","position not skipped");require(p.visit(i,i+1)=="duplicate_supported_atom","backtrack counted twice");}require(p.visit(k,k)=="selected","target not selected");require(p.visit(99,10)=="opportunity_consumed","restart renewed opportunity");}
    {SupportedPosition p;p.reset(4);p.visit(1,1);require(!p.consumed&&p.rank==1,"unreached target fabricated");require(p.visit(2,33)=="decision_window"&&p.rank==1,"decision window expanded");}
    std::cout<<"{\"passed\":true,\"controlled_clock\":true,\"first_observation_pairing\":true,\"bounded_rank\":true}\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
