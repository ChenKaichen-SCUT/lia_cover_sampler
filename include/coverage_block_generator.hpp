#pragma once
#include "core.hpp"

namespace lia {
struct BlockOptions {
    unsigned max_queries=6, max_pack=3, pool_size=24;
    unsigned dependency_operations=2048, equality_variables=8;
    unsigned max_expansions=3, release_per_core=2, max_block=64;
    double reserve_fraction=.2;
};
struct BlockRelease { unsigned variable, query; std::string reason; };
struct BlockQuery {
    std::string status, detail;
    unsigned block_size=0, timeout_ms=0;
    bool restricted=false, exclusion_core=false, global_proof=false;
    double seconds=0, finished=0;
    std::vector<Target> targets, core_targets;
    std::vector<unsigned> fixed_core;
};
struct BlockResult {
    std::optional<Assignment> model;
    Gain gain;
    std::string status="no_target", fallback_reason;
    Target primary{-1,0,false};
    bool first_observation=false, dependency_complete=true;
    unsigned initial_block=0, final_block=0, variables=0, expansions=0;
    unsigned validated_candidates=0, duplicate_candidates=0, late_candidates=0, branch_changes=0;
    double first_positive_seconds=-1, checkpoint_seconds=-1, full_seconds=0, reserve_seconds=0;
    std::vector<BlockRelease> releases;
    std::vector<BlockQuery> queries;
    std::string json(const Formula &f) const;
};

// Both modes share selection, exact Global target encoding, bounded packing,
// original-formula validation and immutable deadline checkpoints. Only block
// mode enables dependency-based fixings and conflict-guided release.
class CoverageBlockGenerator {
    Formula &f;
    Global global;
  public:
    BlockOptions options;
    explicit CoverageBlockGenerator(Formula &formula,unsigned seed,unsigned resource_limit=0)
        : f(formula),global(formula,seed,resource_limit) {}
    // Deterministic regression seams, empty in all measured runs.
    std::function<std::optional<GlobalResult>(const std::vector<Target>&,unsigned)> query_override;
    std::function<void(const char *)> boundary;
    BlockResult generate(const Assignment &seed,const Coverage &coverage,Budget &budget,
        uint64_t target_seed,bool use_blocks,Stats &stats,
        const std::function<bool(const std::string &)> &already_emitted,
        const std::function<double(const Assignment &,Gain,double)> &checkpoint={},
        const std::vector<Target> &explicit_plan={});
    size_t global_conflicts() const {return global.conflicts.cores.size();}
};
}
