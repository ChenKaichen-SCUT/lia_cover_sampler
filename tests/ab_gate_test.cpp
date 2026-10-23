#include "core.hpp"
#include "ab_output.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
using namespace lia;
int main() {try {
    auto a=std::filesystem::temp_directory_path()/"gate-original.smt2";
    auto b=std::filesystem::temp_directory_path()/"unrelated-renamed-formula.smt2";
    const std::string text="(declare-const b Bool)(declare-const x Int)(assert (or b (>= x 0)))";
    std::ofstream(a)<<text;std::ofstream(b)<<text;Formula first(a.string()),second(b.string());
    for(const auto &model:std::vector<Assignment>{{0,1},{1,1},{1,-2}}) for(uint64_t covered:{0,1,10}) {
        auto x=output_opportunity_signature(first,first.evaluate(model),covered);
        auto y=output_opportunity_signature(second,second.evaluate(model),covered);
        if(x!=y) throw std::runtime_error("filename changed structural opportunity");
    }
    if(output_opportunity_signature(first,first.evaluate({0,1}),0)==output_opportunity_signature(first,first.evaluate({1,1}),0))
        throw std::runtime_error("gate ignores current observed branch");
    std::cout<<"filename-independent opportunity gate passed\n";return 0;
} catch(const std::exception &e) {std::cerr<<e.what()<<'\n';return 1;}}
