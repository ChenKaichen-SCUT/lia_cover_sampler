#pragma once
#include "conditional_observation.hpp"
#include <array>
#include <deque>
#include <memory>
#include <unordered_set>

namespace lia {
constexpr unsigned native_feature_count=16;
using NativeFeatures=std::array<double,native_feature_count>;
enum class PhaseAction {KeepNative=0,FlipOnce=1};
struct NativePrediction {std::array<double,2> gain{},cost{},utility{};bool finite=true;};
struct NativeChoice {
    PhaseAction action=PhaseAction::KeepNative;
    double probability=1,exploration_draw=-1,action_draw=-1;
    bool cold=true,explore=false;
    NativePrediction prediction;
};
class NativeRidge {
    using Matrix=std::array<NativeFeatures,native_feature_count>;
    struct Sample {NativeFeatures x;unsigned action;double reward,cost;};
    std::array<Matrix,2> gram_{};
    std::array<NativeFeatures,2> rhs_gain_{},rhs_cost_{},weight_gain_{},weight_cost_{};
    std::deque<Sample> window_;
    void adjust(const Sample&,double);
    void fit(unsigned);
public:
    std::array<unsigned,2> feedback_counts{};
    NativeRidge();
    NativePrediction predict(const NativeFeatures&,double max_cost) const;
    void observe(const NativeFeatures&,PhaseAction,double reward,double full_cost);
    NativeChoice choose(const NativeFeatures&,std::mt19937_64&,bool learning,double max_cost) const;
    size_t window_size() const{return window_.size();}
};

struct NativeAtom {
    z3::expr expression;
    unsigned cache_index;
    int original_node=-1,relation=-1;
    bool negated=false,supported=false,in_catalog=false,logged=false;
    std::string reason;
    Affine linear;
    std::vector<int> variables,related_nodes;
    unsigned operations=0,maximum_bits=0;
    NativeAtom(z3::expr e,unsigned i):expression(e),cache_index(i){}
    bool truth(const Assignment&) const;
};
struct NativeDecision {
    bool selected=false,native_truth=false,desired_truth=false;
    unsigned ordinal=0,conflicts=0,coverage_version=0,query=0;
    NativeAtom* atom=nullptr;
    NativeFeatures features{};
    NativeChoice choice;
    std::array<unsigned,2> training_counts{};
    unsigned supported_rank=0;
};
struct NativeAttempt {unsigned ordinal,conflicts;int cache_index;std::string reason;unsigned supported_rank=0;};
// Query-scoped, never reset by a solver backtrack/restart. Identity is the
// cached, strongly referenced AST, not an occurrence of a decision literal.
struct SupportedPosition {
    unsigned target=1,rank=0;bool consumed=false;
    std::unordered_set<unsigned> seen;
    void reset(unsigned k){target=k;rank=0;consumed=false;seen.clear();}
    std::string visit(unsigned id,unsigned ordinal){
        if(consumed)return "opportunity_consumed";
        if(ordinal>32)return "decision_window";
        if(!seen.insert(id).second)return "duplicate_supported_atom";
        ++rank;if(rank<target)return "position_before_target";
        consumed=true;return "selected";
    }
};
class NativePhaseFeedback {
    Formula& f_;
    Budget& budget_;
    z3::solver& solver_;
    std::string mode_;
    std::mt19937_64 rng_;
    std::mt19937_64 position_rng_;
    bool random_position_=false,position_eligible_=false;
    SupportedPosition position_;
    std::unordered_map<unsigned,int> original_,variables_;
    std::unordered_set<unsigned> catalog_;
    std::vector<z3::expr> catalog_refs_;
    std::vector<std::vector<int>> variable_related_;
    std::unordered_map<unsigned,std::unique_ptr<NativeAtom>> cache_;
    std::vector<NativeAtom*> entries_;
    size_t journaled_entries_=0;
    const Coverage* coverage_=nullptr;
    const Assignment* last_model_=nullptr;
    double coverage_ratio_=0,recent_zero_=0,last_cost_=0;
    bool last_timeout_=false,installed_=false;
    unsigned granted_ms_=0;
    Affine parse_linear(const z3::expr&,NativeAtom&,unsigned depth);
    NativeAtom* classify(const z3::expr&);
    NativeFeatures features(NativeAtom&,bool,unsigned,unsigned) const;
    int on_decision(Z3_ast,bool,unsigned,unsigned);
    static int callback(void*,Z3_ast,bool,unsigned,unsigned) noexcept;
public:
    static constexpr unsigned cache_cap=4096,atom_operation_cap=512,depth_cap=64,variable_cap=128,related_cap=128;
    NativeRidge learner;
    NativeDecision decision;
    std::vector<NativeAttempt> attempts;
    std::string hook_log="{}",stop_reason;
    double mechanism_seconds=0,feature_seconds=0,prediction_seconds=0,training_seconds=0;
    double last_check_seconds=0,last_capture_seconds=0,last_hook_seconds=0;
    double quota_seconds;
    NativePhaseFeedback(Formula&,z3::solver&,Budget&,const std::string&,unsigned seed,bool random_position=false);
    ~NativePhaseFeedback();
    void set_catalog(const ConditionalObservations&);
    void prepare(const Coverage&,const Assignment*,unsigned version,unsigned query,unsigned granted,
                 double recent_zero,double last_cost,bool last_timeout);
    z3::check_result check();
    void clear();
    void set_grant(unsigned ms){granted_ms_=ms;}
    bool available();
    void charge(double seconds);
    double observe(const NativeDecision&,double reward,double cost);
    std::string decision_json() const;
    std::string attempts_json() const;
    std::string position_json() const;
    std::string new_atoms_jsonl();
    NativeAtom* inspect(const z3::expr&e){return classify(e);}
    size_t cache_size() const{return cache_.size();}
};
}
