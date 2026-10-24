#pragma once
#include "core.hpp"
namespace lia {
struct PlateauSegment {
    double start = 0, end = 0;
    uint64_t start_index = 0, end_index = 0, zero_outputs = 0, failures = 0, duplicates = 0, new_modes = 0,
             recovery_gain = 0;
    bool censored = false, internal_known = true;
    std::string recovery_origin;
    std::map<std::string, double> costs;
    std::string json() const;
};
class PlateauTracker {
    PlateauSegment current;
    double last_time = 0;

  public:
    std::vector<PlateauSegment> segments;
    uint64_t longest_zero_run = 0, consecutive_zero = 0, last_positive_index = 0;
    double longest_no_growth = 0, last_positive_time = 0;
    bool finished = false;
    explicit PlateauTracker(bool internal_known = true) { current.internal_known = internal_known; }
    void internal(uint64_t failed, uint64_t duplicates, const std::string &module, double seconds);
    void output(double time, uint64_t index, uint64_t gain, bool new_mode, const std::string &origin);
    void finish(double time, uint64_t index);
    std::string json() const;
};
} // namespace lia
