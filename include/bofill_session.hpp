#pragma once
#include "lia_choice_compiler.hpp"
#include <memory>

namespace lia {
// Solver ownership is separate from committed coverage. Resetting a solver never
// rolls back a model, and the reconstruction context outlives every solver.
class BofillSolverSession {
    Formula &f_;
    Budget &budget_;
    unsigned seed_;
    CompiledProblem *active_;
    std::unique_ptr<CompiledProblem> original_;
    std::unique_ptr<z3::solver> solver_;
public:
    static constexpr double first_model_limit_seconds=10.;
    bool fallback=false;
    BofillSolverSession(CompiledProblem &compiled,Budget &budget,unsigned seed)
        :f_(compiled.original_formula),budget_(budget),seed_(seed),active_(&compiled) {
        solver_=std::make_unique<z3::solver>(f_.ctx,z3::solver::simple{});
        solver_->add(compiled.compiled_formula);solver_->set("random_seed",seed_);
    }
    z3::solver &solver(){return *solver_;}
    CompiledProblem &problem(){return *active_;}
    bool should_fallback(bool enabled,size_t formal_count) const {
        return enabled && !fallback && active_->mode!=CompileMode::Plain && formal_count==0 &&
               budget_.elapsed()>=first_model_limit_seconds && !budget_.done();
    }
    double action_left(bool enabled,size_t formal_count) const {
        if(enabled && !fallback && active_->mode!=CompileMode::Plain && formal_count==0)
            return std::min(budget_.left(),std::max(0.,first_model_limit_seconds-budget_.elapsed()));
        return budget_.left();
    }
    // Also safe after formal output: callers retain coverage and identities, and
    // every committed full original assignment is blocked in the replacement F.
    // If any allocation fails, the caller terminates; it never reuses an unsafe
    // half-built solver. This operation cannot replenish the parent deadline.
    void reset_to_original(const std::vector<Assignment> &committed) {
        if(fallback)throw std::logic_error("duplicate original fallback");
        solver_.reset();
        original_=std::make_unique<CompiledProblem>(f_,CompileMode::Plain,budget_);
        auto next=std::make_unique<z3::solver>(f_.ctx,z3::solver::simple{});
        next->add(f_.root);next->set("random_seed",seed_);
        for(const auto &a:committed)next->add(original_->block_original(a));
        active_=original_.get();solver_=std::move(next);fallback=true;
    }
};
}
