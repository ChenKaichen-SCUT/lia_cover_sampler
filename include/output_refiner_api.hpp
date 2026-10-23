#pragma once
// Deliberately independent of both solver namespaces and generator state types.
#include <gmpxx.h>
#include <array>
#include <functional>
#include <memory>
#include <string>
#include <vector>
namespace output_refiner {
using Values=std::vector<mpz_class>;
using Seen=std::vector<std::array<uint64_t,2>>; // zero, one
struct Proposal {
    Values model;
    uint64_t gain_before=0,gain_after=0;
    bool changed=false,rank_known=false,no_free_direction=false;
    unsigned attempts=0;
};
class Refiner {
    struct Impl;
    std::unique_ptr<Impl> impl;
  public:
    explicit Refiner(const std::string &input);
    ~Refiner();
    Proposal propose(const Values &raw,const Seen &seen,double seconds,uint64_t seed,
        const std::function<bool(const std::string &)> &committed);
    std::string statistics() const;
};
uint64_t independent_seed(uint64_t run_seed,uint64_t raw_index);
void cancel();
}
