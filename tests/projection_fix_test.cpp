#include "feedback_projection.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
using namespace lia;
void check(bool b, const char *s) {
    if (!b)
        throw std::runtime_error(s);
}
std::string input(const std::string &s) {
    static unsigned n = 0;
    auto p = std::filesystem::temp_directory_path() / ("lia-projection-fix-" + std::to_string(n++) + ".smt2");
    std::ofstream(p) << s;
    return p;
}
int main() {
    try {
        {
            Formula f(input(
                "(declare-const x Int)(declare-const y Int)(assert (and (>= x 0)(<= x 2)(>= y 0)(<= y 2)))"));
            ProjectionGuidedExplorer g(f, 0, {true, 200});
            Budget b;
            b.seconds = 10;
            Stats st;
            auto first = g.query({0}, {{0, 0}}, b, st);
            check(bool(first.query.model) && first.block_added && first.blocks_after == 1,
                  "first full assignment blocked");
            // Exclude x=1,2 temporarily: only x=0 remains, proving the old x!=0 projection was popped.
            auto second = g.query({0}, {{1, 0}, {2, 0}}, b, st);
            check(second.query.model && second.query.model->at(0) == 0, "temporary projection removed");
            std::set<std::string> seen{f.key(*first.query.model), f.key(*second.query.model)};
            // Reuse x!=0: all SIX pairs must remain available except the single exact old assignment.
            unsigned same_projection = 0;
            for (unsigned i = 0; i < 8; ++i) {
                auto q = g.query({0}, {{0, 0}}, b, st);
                if (q.query.code == SolveStatus::Unsat) {
                    check(!q.query.global_target_proof, "blocked local UNSAT not proof");
                    break;
                }
                check(q.query.model && seen.insert(f.key(*q.query.model)).second,
                      "persistent no-good after repeated pop");
                same_projection += q.query.model->at(0) == first.query.model->at(0);
            }
            check(seen.size() == 7 && same_projection == 2, "full no-good must not block projected class");
        }
        {
            Formula f(input("(declare-const x Int)(assert (and (>= x 0)(<= x 1000)))"));
            ProjectionGuidedExplorer g(f, 0, {true, 200});
            Budget b;
            b.seconds = 20;
            Stats st;
            std::set<std::string> seen;
            for (unsigned i = 0; i < 256; ++i) {
                auto q = g.query({0}, {{0}}, b, st);
                check(q.query.model && q.block_added && seen.insert(f.key(*q.query.model)).second,
                      "256 distinct retained blocks");
            }
            check(!g.available() && g.blocked_models() == 256, "bounded resident blocks");
            auto stop = g.query({0}, {{0}}, b, st);
            check(!stop.query.check_started && !stop.query.model &&
                      stop.stop_reason == "projection_block_cap",
                  "cap no retry no reset");
            Global ordinary(f, 0);
            check(ordinary.explore({0}, b, st).model.has_value(), "ordinary unaffected after cap");
        }
        {
            Formula f(input(
                "(declare-const b Bool)(declare-const x Int)(assert (or b (= x (- 18446744073709551617))))"));
            ProjectionGuidedExplorer g(f, 0, {true, 200});
            Budget b;
            b.seconds = 10;
            Stats st;
            auto q = g.query({0}, {{0, Z("-18446744073709551617")}}, b, st);
            check(q.query.model && q.block_added, "complete Bool/integer exact block");
            g.check_override = [&](unsigned) { return std::optional(q.query); };
            auto repeated = g.query({0}, {{0, Z("-18446744073709551617")}}, b, st);
            check(repeated.internal_duplicate && !repeated.block_added && !g.available(),
                  "defensive repeat guard stops");
        }
        {
            Formula f(input("(declare-const x Int)(assert (>= x 0))"));
            ProjectionGuidedExplorer g(f, 0, {true, 200});
            Stats st;
            Budget root;
            root.seconds = 10;
            Budget stage;
            stage.parent = &root;
            stage.seconds = .25;
            // Deterministic elapsed-initialization fixture, no scheduler-dependent sleep.
            stage.start -= std::chrono::milliseconds(60);
            Budget work;
            work.start = stage.start;
            work.seconds = .230;
            work.parent = &stage;
            auto q = g.query({0}, {{0}}, work, st);
            check(q.query.check_started && q.query.actual_timeout_ms > 50 &&
                      q.query.actual_timeout_ms <= 170 && q.query.model,
                  "60ms charged init still admits actual check");
            g.check_override = [](unsigned ms) {
                GlobalResult q;
                q.raw_status = "unknown";
                q.detail = "canceled";
                q.check_seconds = ms / 1000.;
                return std::optional(q);
            };
            Budget enough;
            enough.seconds = 2;
            auto timeout = g.query({0}, {{0}}, enough, st);
            check(timeout.query.actual_timeout_ms == 200 && timeout.query.exit_decision.inferred &&
                      !timeout.query.global_target_proof,
                  "200ms Unknown timing evidence");
            g.check_override = [](unsigned) {
                GlobalResult q;
                q.raw_status = "unknown";
                q.detail = "incomplete";
                return std::optional(q);
            };
            check(g.query({0}, {{0}}, enough, st).query.code == SolveStatus::Unknown, "unknown preserved");
            Budget clipped;
            clipped.seconds = .025;
            clipped.parent = &enough;
            auto limited = g.query({0}, {{0}}, clipped, st);
            check(limited.query.actual_timeout_ms <= 25 && limited.query.budget_clip_reason != "none",
                  "parent/action clipped grant");
            interrupted = 1;
            check(!g.query({0}, {{0}}, enough, st).query.check_started, "external stop no check");
            interrupted = 0;
            Budget empty;
            empty.seconds = 0;
            check(!g.query({0}, {{0}}, empty, st).query.model, "exhausted budget no generation");
            Global control(f, 0);
            control.exploration_budget.fixed_levels = true;
            auto c = control.explore({0}, enough, st, 200, true, 200);
            check(c.requested_timeout_ms == 200 && c.actual_timeout_ms == 200,
                  "extra ordinary actual equal timeout request");
            ProjectionExtraBudget quota(10, .25, .04);
            check(quota.allowance(10) == .25, "250ms phase");
            quota.charge(.96);
            check(quota.allowance(10) == 0, "quota never reset");
        }
        std::cout << "projection fix tests passed\n";
        return 0;
    } catch (const std::exception &e) {
        interrupted = 0;
        std::cerr << e.what() << '\n';
        return 1;
    }
}
