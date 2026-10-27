#pragma once
#include "bounded_delivery.hpp"
#include <memory>
#include <climits>

namespace lia {
struct PureNode {Z3_decl_kind op=Z3_OP_UNINTERPRETED;std::vector<int> children;int variable=-1;bool boolean=false;Z numeral;};
// No Z3 API is called by evaluate(). Metadata is compiled before check().
class PureEvaluationPlan {
    const Formula& f_;
    size_t cursor_=0,affine_cursor_=0;
    std::vector<int> affine_cache_,constant_cache_;
    bool affine(int,Budget&,unsigned=0);
public:
    std::vector<PureNode> nodes;
    std::vector<std::string> names;
    std::vector<bool> booleans;
    std::vector<Z3_ast> variables,terms;
    bool ready=false;
    explicit PureEvaluationPlan(const Formula&);
    void prepare(Budget&);
    Observation evaluate(const Assignment&,Budget* =nullptr) const;
    std::string model_json(const Assignment&) const;
};
unsigned arith_coverage_choice(const std::vector<Gain>&,uint64_t seed);
uint64_t arith_stream_seed(unsigned seed,uint64_t tag,unsigned query);
std::string arith_assignment_hash(const Assignment&);
class ArithCoverageBridge {
    Formula&f_;z3::solver&solver_;Budget&session_;const DeliveryState&delivery_;const GenerationState&generation_;
    std::string mode_;unsigned seed_,query_=0,version_=0,probe_states_=0;
    std::unique_ptr<PureEvaluationPlan>plan_;
    bool installed_=false,score_complete_=false,pool_received_=false,fatal_=false;
    std::string reason_="before_first",backend_="{}",pool_models_="[]",pool_scores_="[]";
    Assignment original_,selected_;
    unsigned offered_=0,pool_size_=0;
    Gain predicted_;double expected_covered_=0,expected_observed_=0,scoring_=0,prepare_=0,callback_=0;
    static unsigned callback(void*,unsigned,unsigned,const char*const*,const char*) noexcept;
    unsigned select(unsigned,unsigned,const char*const*,const char*);
    void stop_if_needed();
public:
    double cumulative=0,quota=0,initialization_seconds=0,first_prepare_at=-1,first_install_at=-1,stop_at=-1;
    std::string stop_reason;
    unsigned stop_query=UINT_MAX;
    ArithCoverageBridge(Formula&,z3::solver&,Budget&,const DeliveryState&,const GenerationState&,const std::string&,unsigned);
    ~ArithCoverageBridge();
    void prepare(unsigned query,unsigned granted_ms);
    void capture();
    bool failed() const {return fatal_;}
    void charge(double seconds);
    std::string record(const Assignment*final,bool delivered,Gain actual) const;
    std::string summary() const;
};
}
