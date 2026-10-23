#pragma once
#include "core.hpp"

namespace lia {
inline std::string diagnostic_duplicate_reason(bool base,bool batch,bool action,bool committed) {
    return base?"SAME_AS_BASE":batch?"DUPLICATE_IN_BATCH":action?"DUPLICATE_IN_ACTION":
        committed?"DUPLICATE_IN_COMMITTED_OUTPUT":"NEW_ASSIGNMENT";
}
// Full records are opt-in and bounded. Collection never draws from sampler RNG.
struct DiagnosticTrace {
    size_t limit=4096, dropped=0, next_direction=0;
    std::vector<std::string> records;
    void add(std::string value) { if(records.size()<limit) records.push_back(std::move(value)); else ++dropped; }
    std::string json() const {
        std::ostringstream out;out<<"{\"dropped\":"<<dropped<<",\"records\":[";
        for(size_t i=0;i<records.size();++i) {if(i) out<<',';out<<records[i];}
        return out.str()+"]}";
    }
};
struct DiagnosticUnit {
    Stats &stats;
    DiagnosticTrace *trace;
    bool enabled;
    std::string module,source,scope;
    size_t id=0;
    double work_seconds=0;
    std::set<std::string> reasons;
    std::map<std::string,unsigned> stages;
    Clock::time_point started=Clock::now();
    DiagnosticUnit(Stats &s,DiagnosticTrace *t,bool e,std::string m,std::string src,std::string sc)
        :stats(s),trace(t),enabled(e),module(std::move(m)),source(std::move(src)),scope(std::move(sc)) {
        if(trace) id=trace->next_direction++;
    }
    void event(const std::string &unit,const std::string &code,const std::string &stage,
               double seconds=0,const std::string &fields="",bool reason=false) {
        if(!enabled) return;
        if(unit=="phase" || unit=="target") work_seconds+=seconds;
        const auto key="diag_"+module+"_"+unit+"_"+code;
        ++stats.counts[key];stats.times[key]+=seconds;
        ++stats.counts["diag_source_"+source+"_"+unit+"_"+code];
        stats.times["diag_source_"+source+"_"+unit+"_"+code]+=seconds;
        if(reason) reasons.insert(code);
        ++stages[stage];
        if(trace) trace->add("{\"unit\":"+quote(unit)+",\"code\":"+quote(code)+",\"stage\":"+quote(stage)+
            ",\"module\":"+quote(module)+",\"direction_id\":"+std::to_string(id)+",\"source\":"+quote(source)+
            ",\"scope\":"+quote(scope)+",\"seconds\":"+std::to_string(seconds)+fields+"}");
    }
    void context(const Formula &f,const Assignment &base,const Assignment &direction,
                 const std::string &scope_query,const std::string &equations) {
        if(!trace || !enabled) return;
        std::ostringstream delta;delta<<'[';
        for(size_t i=0;i<direction.size();++i) {if(i) delta<<',';delta<<quote(direction[i].get_str());}delta<<']';
        trace->add("{\"unit\":\"context\",\"direction_id\":"+std::to_string(id)+",\"module\":"+quote(module)+
            ",\"source\":"+quote(source)+",\"scope\":"+quote(scope)+",\"base\":"+f.model_json(base)+
            ",\"direction\":"+delta.str()+",\"scope_query\":"+quote(scope_query)+",\"equations\":"+quote(equations)+"}");
    }
    void finish(const std::string &fallback) {
        std::string status=reasons.empty()?fallback:reasons.size()==1?*reasons.begin():"MIXED_REASONS";
        std::ostringstream extra;extra<<",\"reasons\":[";bool comma=false;
        for(const auto &r:reasons) {if(comma) extra<<',';extra<<quote(r);comma=true;}
        extra<<"],\"stage_counts\":{";comma=false;
        for(const auto &[s,n]:stages) {if(comma) extra<<',';extra<<quote(s)<<':'<<n;comma=true;}
        extra<<'}';
        event("direction",status,"terminal",work_seconds,extra.str());
    }
};
inline std::string diagnostic_budget(const Budget &budget,bool witness,bool constructed,bool validated) {
    const Budget *expired=nullptr;
    for(auto p=&budget;p;p=p->parent) if(p->done()) expired=p;
    return ",\"budget_level\":"+quote(expired?expired->label:budget.label)+
        ",\"budget_elapsed\":"+std::to_string(budget.elapsed())+
        ",\"witness_found\":"+(witness?"true":"false")+",\"model_constructed\":"+(constructed?"true":"false")+
        ",\"validation_complete\":"+(validated?"true":"false");
}
}
