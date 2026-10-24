#include "macro_choice.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
using namespace lia;
static void require(bool b, const char *s) { if (!b) throw std::runtime_error(s); }
int main() {
 try {
    QueryExitEvidence e{"canceled",50,.05,false,false,false,false};
    auto d=interpret_query_exit("unknown",e);
    require(d.code==SolveStatus::Timeout && d.inferred && d.allow_upgrade,"near timer canceled is inferred");
    e.check_seconds=.001;require(interpret_query_exit("unknown",e).code==SolveStatus::Unknown,"construction time cannot prove timeout");
    e.check_seconds=.05;e.external_interrupt=true;d=interpret_query_exit("unknown",e);
    require(d.stop && d.code==SolveStatus::Cancelled && !d.allow_upgrade,"external stop");
    e.external_interrupt=false;e.session_deadline_reached=true;d=interpret_query_exit("unknown",e);
    require(d.stop && d.code==SolveStatus::BudgetExhausted,"global stop");
    e.session_deadline_reached=false;e.raw_reason="max. resource limit exceeded";e.resource_limit_enabled=true;
    d=interpret_query_exit("unknown",e);require(d.code==SolveStatus::ResourceExhausted && !d.allow_upgrade,"resource not time");
    auto grant=grant_query(1000,.1,2.);require(grant.granted_ms==100 && !grant.full(),"explicit parent clipping");
    SolveBudgetState state;state.fixed_levels=true;state.retry_level=2;state.requested_timeout_ms=1000;
    d=interpret_query_exit("unknown",QueryExitEvidence{"canceled",100,.100,false,false,false,false});
    state.observe(d,.2,grant);require(state.retry_level==2 && state.requested_timeout_ms==1000 && state.cumulative_seconds==.2,"clipped failure charged without consuming tier");
    state.observe(d,1.,grant_query(1000,3.,2.));require(state.retry_level==3 && state.requested_timeout_ms==2000,"full tier grows");
    BroadBudgetPool pool(4.);require(pool.available(10.)>2.,"continuous call not old 320ms cap");pool.charge(2.1);require(pool.available(10.)<2. && pool.spent==2.1,"full action cost deducted");
    auto path=std::filesystem::temp_directory_path()/"lia-query-exit.smt2";
    std::ofstream(path)<<"(declare-const x Int)(declare-const y Int)(assert (and (= (+ x y) 10)(or (= x 0)(= x 10))))";
    Formula f(path.string());Budget b;b.seconds=10;ChoiceCatalog cat(f,b);MacroChoiceGenerator macro(f,cat,0);Stats stats;
    std::vector<unsigned> actual;
    macro.timed_query_override=[&](unsigned ms){actual.push_back(ms);GlobalResult r;r.raw_status=ms<200?"unknown":"sat";r.detail=ms<200?"canceled":"";r.check_seconds=ms<200?ms/1000.:.18;if(ms>=200)r.model=Assignment{10,0};return std::optional(r);};
    auto first=macro.query_choices({{0,1}},b,stats);auto second=macro.query_choices({{0,1}},b,stats);
    require(actual==std::vector<unsigned>{50,200} && first.raw_status=="unknown" && first.detail=="canceled" && second.model.has_value(),"injected 200ms recovery chain; not real performance");
    require(!cat.groups[0].alternatives[1].impossible,"no false proof");
    Budget expired;expired.seconds=0;auto no=macro.query_choices({{0,1}},expired,stats);require(!no.check_started && actual.size()==2,"no check past deadline");
    interrupted=1;auto cancel=macro.query_choices({{0,1}},b,stats);interrupted=0;require(cancel.code==SolveStatus::Cancelled && actual.size()==2,"no check on interrupt");
    std::cout<<"query exit tests passed\n";return 0;
 } catch(const std::exception &e){interrupted=0;std::cerr<<e.what()<<'\n';return 1;}
}
