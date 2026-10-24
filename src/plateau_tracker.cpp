#include "plateau_tracker.hpp"
#include <cmath>
#include <iomanip>
namespace lia {
std::string PlateauSegment::json() const {
    std::ostringstream o;
    o << std::setprecision(17) << "{\"start\":" << start << ",\"end\":" << end
      << ",\"start_index\":" << start_index << ",\"end_index\":" << end_index
      << ",\"zero_outputs\":" << zero_outputs
      << ",\"failures\":" << (internal_known ? std::to_string(failures) : "null")
      << ",\"duplicates\":" << (internal_known ? std::to_string(duplicates) : "null")
      << ",\"new_modes\":" << new_modes << ",\"recovery_gain\":" << recovery_gain
      << ",\"right_censored\":" << (censored ? "true" : "false")
      << ",\"recovery_origin\":" << quote(recovery_origin) << ",\"costs\":{";
    bool first = true;
    for (auto &[k, v] : costs) {
        if (!first)
            o << ',';
        first = false;
        o << quote(k) << ':' << v;
    }
    return o.str() + "}}";
}
void PlateauTracker::internal(uint64_t fail, uint64_t dup, const std::string &module, double seconds) {
    if (finished || seconds < 0 || !std::isfinite(seconds))
        throw std::logic_error("invalid plateau event");
    current.failures += fail;
    current.duplicates += dup;
    current.costs[module] += seconds;
}
void PlateauTracker::output(double time, uint64_t index, uint64_t gain, bool new_mode,
                            const std::string &origin) {
    if (finished || !std::isfinite(time) || time < last_time)
        throw std::logic_error("invalid plateau chronology");
    last_time = time;
    current.new_modes += new_mode;
    if (!gain) {
        ++current.zero_outputs;
        ++consecutive_zero;
        longest_zero_run = std::max(longest_zero_run, consecutive_zero);
        return;
    }
    current.end = time;
    current.end_index = index;
    current.recovery_gain = gain;
    current.recovery_origin = origin;
    longest_no_growth = std::max(longest_no_growth, time - current.start);
    segments.push_back(current);
    bool known = current.internal_known;
    current = PlateauSegment{};
    current.internal_known = known;
    current.start = time;
    current.start_index = index;
    consecutive_zero = 0;
    last_positive_time = time;
    last_positive_index = index;
}
void PlateauTracker::finish(double time, uint64_t index) {
    if (finished || time < last_time)
        throw std::logic_error("invalid plateau finish");
    current.end = time;
    current.end_index = index;
    current.censored = true;
    segments.push_back(current);
    longest_no_growth = std::max(longest_no_growth, time - current.start);
    finished = true;
}
std::string PlateauTracker::json() const {
    std::ostringstream o;
    o << std::setprecision(17) << "{\"longest_zero_run\":" << longest_zero_run
      << ",\"consecutive_zero\":" << consecutive_zero << ",\"last_positive_index\":" << last_positive_index
      << ",\"last_positive_time\":" << last_positive_time << ",\"longest_no_growth\":" << longest_no_growth
      << ",\"segments\":[";
    for (size_t i = 0; i < segments.size(); ++i) {
        if (i)
            o << ',';
        o << segments[i].json();
    }
    return o.str() + "]}";
}
} // namespace lia
