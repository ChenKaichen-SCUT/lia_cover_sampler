// Test-only fixed-model replay. There is no solver.check or generation path.
#include "bounded_delivery.hpp"
#include <fstream>
#include <iostream>
using namespace lia;
int main(int argc,char**argv){try{
    if(argc!=3)throw std::invalid_argument("bounded_delivery_replay FORMULA VALUES");Formula f(argv[1]);GenerationState g(f);DeliveryState d(f);double now=0;
    BoundedDelivery manager(g,d,DeliveryPolicy::BoundedGreedyBatch,100,[&]{return now;},[]{return false;},
        [&](const Candidate&c,unsigned batch,const Gain&gain)->std::optional<double>{accept_masks(d.coverage,c->observations);d.identities.insert(c->key);++d.version;
            std::cout<<"{\"type\":\"delivery\",\"source\":"<<c->index<<",\"batch\":"<<batch<<",\"gain\":"<<gain.covered<<",\"covered\":"<<d.coverage.covered()<<"}\n";return now;},
        [&](const BatchRecord&b){std::cout<<"{\"type\":\"batch\",\"id\":"<<b.id<<",\"members\":[";for(size_t i=0;i<b.members.size();++i){if(i)std::cout<<',';std::cout<<b.members[i];}std::cout<<"],\"complete\":"<<(b.complete?"true":"false")<<"}\n";});
    std::ifstream in(argv[2]);std::string line;
    while(std::getline(in,line)){std::istringstream values(line);Assignment a;std::string token;while(values>>token)a.emplace_back(token);if(!f.validate(a))throw std::runtime_error("invalid saved test model");
        auto c=std::make_shared<SavedCandidate>();c->index=g.version+1;c->assignment=a;c->key=f.key(a);c->ready=now+=.001;Coverage single(f);single.accept(f.evaluate(a));c->observations=single.states;
        if(!g.identities.insert(c->key).second)throw std::runtime_error("duplicate test identity");accept_masks(g.coverage,c->observations);g.last_generated_model=a;++g.version;manager.offer(c);
    }manager.refresh_if_due(true);if(!same_observations(g.coverage,d.coverage))throw std::runtime_error("test union mismatch");
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
