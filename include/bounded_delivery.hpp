#pragma once
#include "core.hpp"
#include <memory>
#include <optional>

namespace lia {
enum class PositionPolicy { First, RandomSupportedRank };
enum class DeliveryPolicy { Immediate, BoundedGreedyBatch };
uint64_t state_fingerprint(const Coverage&);
Gain mask_gain(const Coverage&,const std::vector<State>&);
void accept_masks(Coverage&,const std::vector<State>&);
bool same_observations(const Coverage&,const Coverage&);
bool observation_subset(const Coverage&,const Coverage&);
struct SavedCandidate {
    unsigned index=0,query=0;
    double validated=0,ready=0;
    Assignment assignment;
    std::string key,serialized;
    std::vector<State> observations;
};
using Candidate=std::shared_ptr<const SavedCandidate>;
struct GenerationState {
    Coverage coverage;
    std::set<std::string> identities;
    std::optional<Assignment> last_generated_model;
    unsigned version=0;
    unsigned long marginal_sum=0,observation_sum=0;
    explicit GenerationState(const Formula&f):coverage(f){}
};
struct DeliveryState {
    Coverage coverage;
    std::set<std::string> identities;
    unsigned version=0;
    unsigned long marginal_sum=0,observation_sum=0;
    explicit DeliveryState(const Formula&f):coverage(f){}
};
struct DeliveryChoice {unsigned candidate=0;Gain gain;double completed=0,waiting=0;bool fifo=false;double maintenance_at_selection=0;};
struct BatchRecord {
    unsigned id=0;
    std::string reason,fallback;
    double start=0,finished=0,maintenance=0,trigger_delay=0;
    uint64_t before=0,expected=0,after=0;
    bool complete=false;
    std::vector<unsigned> members;
    std::vector<DeliveryChoice> choices;
};
class BoundedDelivery {
    GenerationState& generation_;
    DeliveryState& delivery_;
    DeliveryPolicy policy_;
    std::function<double()> now_;
    std::function<bool()> stopped_;
    std::function<std::optional<double>(const Candidate&,unsigned,const Gain&)> emit_;
    std::function<void(const BatchRecord&)> journal_;
    double quota_;
    unsigned next_batch_=0;
    bool immediate_=false,failed_=false;
    std::vector<Candidate> pending_;
public:
    static constexpr unsigned capacity=32;
    static constexpr double wait_trigger=.5,batch_cap=.020,quota_fraction=.05;
    double maintenance_seconds=0;
    double quota_stop_at=-1,quota_stop_cost=0;
    unsigned fifo_batches=0,quota_fallbacks=0;
    BoundedDelivery(GenerationState&,DeliveryState&,DeliveryPolicy,double total_seconds,
                    std::function<double()>,std::function<bool()>,
                    std::function<std::optional<double>(const Candidate&,unsigned,const Gain&)>,std::function<void(const BatchRecord&)>);
    void offer(Candidate);
    void refresh_if_due(bool ending=false);
    void flush(const std::string& reason,bool force_fifo=false);
    bool failed() const{return failed_;}
    const std::vector<Candidate>& pending() const{return pending_;}
};
}
