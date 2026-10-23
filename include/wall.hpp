#pragma once
#include "core.hpp"
#include <thread>
#include <iomanip>

namespace lia {
// Exclusive top-level buckets. Nested algorithm timers remain in Stats.
class WallAccounting {
    std::function<double()> now;
    double last;
    std::string phase = "parsing_and_initialization";
  public:
    std::map<std::string,double> seconds;
    explicit WallAccounting(std::function<double()> clock) : now(std::move(clock)), last(0) {
        for (auto name : {"parsing_and_initialization", "action_wall_seconds", "scheduler_decision_seconds",
                          "idle_sleep_seconds", "checkpoint_and_logging_seconds", "other_wall_seconds"})
            seconds[name]=0;
    }
    void enter(const std::string &next) {
        const double time=now(); seconds[phase]+=time-last; last=time; phase=next;
    }
    void sleep(double duration) {
        auto previous=phase; enter("idle_sleep_seconds");
        if (duration>0) std::this_thread::sleep_for(std::chrono::duration<double>(duration));
        enter(previous);
    }
    std::string json() {
        enter(phase);
        std::ostringstream out; out<<std::setprecision(12)<<"{\"total_wall_seconds\":"<<last;
        for (const auto &[key,value]:seconds) out<<','<<quote(key)<<':'<<value;
        out<<",\"idle_fraction\":"<<(last>0 ? seconds["idle_sleep_seconds"]/last : 0)<<'}';
        return out.str();
    }
};
}
