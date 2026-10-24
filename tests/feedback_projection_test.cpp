#include "feedback_projection.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
using namespace lia;
void check(bool b, const char *s) {
    if (!b)
        throw std::runtime_error(s);
}
std::string file(const std::string &s) {
    static unsigned i = 0;
    auto p = std::filesystem::temp_directory_path() / ("lia-projection-" + std::to_string(i++) + ".smt2");
    std::ofstream(p) << s;
    return p.string();
}
int main() {
    try {
        {
            Formula f(file("(declare-const x Int)(declare-const y Int)(assert (and (= (+ x y) 10)(or (= x "
                           "0)(= x 10))))"));
            OnlineCoverage online(f);
            online.restore({}, {0, 10});
            FeedbackProjection feedback(f, 0);
            Budget b;
            b.seconds = 10;
            Stats stats;
            auto empty = Coverage(f);
            auto no = feedback.select(empty, {{0, 10}}, b);
            check(no.variables.empty() && no.missing_bits == 0, "unobserved not fake zero");
            auto selected = feedback.select(online.state(), {{0, 10}, {0, 10}}, b);
            check(!selected.variables.empty() && selected.variables.size() <= 3 && selected.missing_bits,
                  "real one-sided gaps");
            for (auto &s : selected.assessed)
                check(s.score == s.gap_weight * 2 && s.stable_transitions == 1, "stable gap scoring");
            ProjectionGuidedExplorer guided(f, 0);
            auto q = guided.query({0}, {{0, 10}, {0, 10}}, b, stats);
            check(q.forbidden.size() == 1 && q.query.model && *q.query.model == Assignment{10, 0},
                  "dedup projections, equality coordination, other variable free");
            ResidualWindow w;
            auto c = online.prepare(*q.query.model, w, "projection", 0);
            check(c && c->gain.covered > 0 && online.state().covered() == 0, "internal state frozen");
            auto unsat = guided.query({0}, {{0, 10}, {10, 0}}, b, stats);
            check(unsat.query.code == SolveStatus::Unsat && !unsat.query.global_target_proof,
                  "only local projection UNSAT");
            check(guided.query({0}, {{0, 10}}, b, stats).query.model.has_value(),
                  "push pop restores full F context");
            Global ordinary(f, 0);
            check(ordinary.conflicts.cores.empty() && ordinary.explore({0, 10}, b, stats).model.has_value(),
                  "ordinary solver unpolluted");
            auto plain = online.prepare({10, 0}, w, "plain", 0);
            auto best = plain;
            std::optional<ResidualCandidate> failed;
            if (failed && (!best || projection_better(*failed, *best)))
                best = failed;
            check(best && best->origin == "plain", "failed guided query retains ordinary witness");
            check(!projection_better(*c, *plain), "tie retains ordinary");
            auto worse = *c;
            worse.gain.covered = 0;
            check(!projection_better(worse, *plain), "lower gain cannot win");
            check(!online.commit(*c, 1.01, 1.) && online.version() == 0, "cannot backdate output");
            check(online.commit(*c, .9, 1.) && online.outputs() == 1, "actual timestamp commit");
        }
        {
            Formula f(file("(declare-const b Bool)(declare-const x Int)(declare-const y Int)(assert (or b "
                           "(and (= (+ x y) 0)(= (+ x y) x))))"));
            OnlineCoverage s(f);
            s.restore({}, {1, 0, 0});
            Budget b;
            b.seconds = 10;
            FeedbackProjection fb(f, 2);
            auto a = fb.select(s.state(), {{1, 0, 0}}, b);
            check(a.unobserved_nodes > 0, "short circuit unobserved distinct");
            bool sawbool = false, sawint = false;
            for (auto &x : a.assessed) {
                sawbool |= x.variable == 0;
                sawint |= x.variable != 0;
                check(x.related_nodes <= FeedbackProjection::node_cap, "bounded shared AST dependencies");
            }
            check(sawbool && sawint, "Boolean and integer scoring");
            Budget expired;
            expired.seconds = 0;
            auto bad = fb.select(s.state(), {{1, 0, 0}}, expired);
            check(!bad.complete && bad.variables.empty(), "analysis abort fallback");
        }
        {
            Formula f(
                file("(declare-const x Int)(declare-const y Int)(assert (and (= x 0)(>= y 0)(<= y 10)))"));
            Budget b;
            b.seconds = 10;
            Stats st;
            ProjectionGuidedExplorer q(f, 0);
            check(q.query({0}, {{0, 0}, {0, 5}}, b, st).query.code == SolveStatus::Unsat,
                  "different models same fixed projection");
            auto mutableq = q.query({1}, {{0, 0}}, b, st);
            check(mutableq.query.model.has_value(), "mutable projection remains available");
            OnlineCoverage s(f);
            s.restore({{0, -0}, {0, 1}, {0, 2}, {0, 4}, {0, 8}}, {0, 10});
            ResidualWindow w;
            auto c = s.prepare({0, 3}, w, "projection", 0);
            check(c && c->gain.covered == 0, "projection change not coverage guarantee");
        }
        {
            Formula f(file("(declare-const x Int)(assert (>= x 0))"));
            Budget b;
            b.seconds = 10;
            Stats st;
            ProjectionGuidedExplorer q(f, 0);
            q.check_override = [](unsigned ms) {
                GlobalResult r;
                r.raw_status = "unknown";
                r.detail = "canceled";
                r.check_seconds = ms / 1000.;
                return std::optional(r);
            };
            auto a = q.query({0}, {{0}}, b, st);
            check(a.query.actual_timeout_ms == 50 && a.query.exit_decision.inferred &&
                      !a.query.global_target_proof,
                  "50ms cancellation evidence, no proof");
            q.check_override = [](unsigned) {
                GlobalResult r;
                r.raw_status = "unknown";
                r.detail = "incomplete";
                return std::optional(r);
            };
            check(q.query({0}, {{0}}, b, st).query.code == SolveStatus::Unknown, "unknown preserved");
            interrupted = 1;
            check(!q.query({0}, {{0}}, b, st).query.check_started, "external cancellation stop");
            interrupted = 0;
            Budget expired;
            expired.seconds = 0;
            check(!q.query({0}, {{0}}, expired, st).query.check_started, "no check past budget");
        }
        {
            ProjectionTrigger t;
            ResidualCandidate zero;
            zero.gain = {0, 1};
            std::optional<ResidualCandidate> candidate = zero;
            check(t.observe({}) == "none", "no trigger from plain failure");
            check(t.observe(candidate) == "initial_complement", "first valid complement");
            t.attempted();
            for (int i = 0; i < 3; ++i)
                check(t.observe(candidate) == "none", "four plain opportunities cooldown");
            check(t.observe(candidate) == "four_zero_candidates", "zero run trigger");
            t.attempted();
            check(t.observe({}) == "none", "failure does not monopolize guide");
            ProjectionExtraBudget cap(10);
            check(cap.allowance(10) == .05, "50ms phase");
            cap.charge(.99);
            check(cap.allowance(10) == 0, "quota exhausted keeps ordinary");
            check(ProjectionExtraBudget(10).allowance(.06) == 0, "final delivery reserved");
        }
        std::cout << "projection tests passed\n";
        return 0;
    } catch (const std::exception &e) {
        interrupted = 0;
        std::cerr << e.what() << '\n';
        return 1;
    }
}
