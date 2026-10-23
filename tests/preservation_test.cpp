#include "observation_preservation.hpp"
#include "output_refiner_api.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
using namespace lia;
int main() {try {
    auto path=std::filesystem::temp_directory_path()/"lia-observation-preservation.smt2";
    std::ofstream(path)<<"(declare-const x Int)(assert (>= x 0))";
    Formula f(path.string());Coverage empty(f);auto raw=f.evaluate({0}),changed=f.evaluate({1});
    if(empty.gain(raw).covered!=empty.gain(changed).covered || preserves_new_observations(f,empty,raw,changed))
        throw std::runtime_error("equal current gain must not permit losing a first observation");
    if(!preserves_new_observations(f,empty,raw,raw)) throw std::runtime_error("raw is always a preservation fallback");
    Coverage seen(f);seen.accept(raw);
    if(!preserves_new_observations(f,seen,raw,changed)) throw std::runtime_error("already seen observations need not be retained by this model");
    auto unobserved=raw;unobserved.visited[f.variables[0]]=0;
    if(preserves_new_observations(f,empty,raw,unobserved)) throw std::runtime_error("unvisited is not observed zero");
    if(output_refiner::independent_seed(1,2)!=output_refiner::independent_seed(1,2) ||
       output_refiner::independent_seed(1,2)==output_refiner::independent_seed(1,3)) throw std::runtime_error("independent deterministic stream");
    std::cout<<"observation preservation tests passed\n";return 0;
} catch(const std::exception &e){std::cerr<<e.what()<<'\n';return 1;}}
