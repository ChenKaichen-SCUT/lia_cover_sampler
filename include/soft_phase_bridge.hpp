#pragma once
#include "core.hpp"
namespace lia {
struct SoftPhasePreference {
    z3::expr original_atom;
    bool preferred_truth;
    uint64_t source_group, coverage_version;
};
class SoftPhaseBridge {
    z3::solver &solver_;
    bool installed_=false;
public:
    std::string last_log="{}";
    double install_seconds=0, finish_seconds=0;
    explicit SoftPhaseBridge(z3::solver &s):solver_(s){}
    ~SoftPhaseBridge();
    void replace_preferences(const std::vector<SoftPhasePreference>&,uint64_t epoch);
    void clear_preferences();
    z3::check_result check();
};
}
