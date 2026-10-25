#include "core.hpp"
#include <fstream>
#include <iostream>
using namespace lia;
int main(int argc,char**argv){std::string path=argv[1];unsigned timeout=std::stoul(argv[2]);Formula f(path);std::vector<Target>ts;for(int n:f.variables)ts.push_back({n,0,true});Stats stats;Budget b;b.seconds=10;Global g(f,0);g.target_budget.requested_timeout_ms=timeout;auto r=g.query(ts,b,stats);std::cout<<"{\"requested_ms\":"<<timeout<<",\"status\":"<<quote(r.status)<<",\"detail\":"<<quote(r.detail)<<",\"actual_timeout_ms\":"<<r.actual_timeout_ms<<",\"elapsed\":"<<r.elapsed_seconds<<",\"stats\":"<<stats.json()<<"}\n";}
