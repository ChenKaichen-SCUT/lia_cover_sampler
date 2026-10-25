#pragma once
#include "core.hpp"

namespace lia {
struct ChoiceCompileLimits {
    double seconds = 5.;
    size_t max_operations = 2000000, max_dag_nodes = 100000;
    unsigned max_coefficient_bits = 4096, max_eliminations = 2048;
    unsigned max_row_terms = 4, max_references = 8, max_term_growth = 8;
};
struct ChoiceEncoding {
    std::vector<unsigned> variables;
    std::vector<Z> first, second;
    std::optional<z3::expr> choice;
    size_t source_clause = 0;
};
// The context and original Formula must outlive this object. Original ASTs are
// never replaced. All reconstruction expressions use final solving variables.
struct CompiledProblem {
    Formula &original_formula;
    z3::expr compiled_formula;
    std::vector<z3::expr> reconstruction_map, solving_variables;
    std::vector<ChoiceEncoding> choices;
    std::vector<std::string> rewrite_evidence;
    std::set<unsigned> eliminated;
    Stats statistics;
    std::string stop_reason = "complete";
    ChoiceCompileLimits limits;
    explicit CompiledProblem(Formula &, bool enabled, Budget &, ChoiceCompileLimits = {});
    Assignment restore(z3::model) const;
    z3::model lift(const Assignment &) const;
    z3::expr block_original(const Assignment &) const;
    std::string json() const;
};
}
