#include "macro_choice.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
using namespace lia;
void check(bool x, const char *why) {
    if (!x)
        throw std::runtime_error(why);
}
std::string file(std::string s) {
    static unsigned n = 0;
    auto p = std::filesystem::temp_directory_path() / ("lia-macro-" + std::to_string(++n) + ".smt2");
    std::ofstream(p) << s;
    return p.string();
}
int main() {
    try {
        {
            Formula f(file("(declare-const x Int)(declare-const y Int)(assert (and (= (+ x y) 10)(or (= x "
                           "0)(= x 10))))"));
            Budget init;
            init.seconds = 10;
            ChoiceCatalog catalog(f, init);
            catalog.commit({0, 10});
            MacroChoiceGenerator g(f, catalog, 0);
            Global global(f, 0);
            OnlineCoverage online(f);
            online.restore({}, {0, 10});
            Stats stats;
            Budget b;
            b.seconds = 2;
            auto r = g.generate(true, global, online, {0, 10}, b, stats, {});
            check(r.best && r.best->model == Assignment{10, 0} && r.best->gain.covered > 0,
                  "single choice and equality coordination");
            check(online.outputs() == 0 && catalog.groups[0].alternatives[1].visits == 0,
                  "internal candidate not formal");
            auto gain = r.best->gain.covered;
            check(online.commit(*r.best, b.elapsed(), 2) && online.state().covered() == gain,
                  "marginal identity");
            ResidualWindow duplicate_window;
            duplicate_window.coverage_version = online.version();
            check(!online.prepare(r.best->model, duplicate_window, "dup", 0), "formal dedup");
        }
        {
            Formula f(file("(declare-const x Int)(declare-const y Int)(assert (or (and (= x 0)(= y 0))(and "
                           "(= x 7)(= y 7))))"));
            Budget b;
            b.seconds = 10;
            ChoiceCatalog cat(f, b);
            cat.commit({0, 0});
            MacroChoiceGenerator g(f, cat, 1);
            Stats s;
            auto r = g.query_choices({{0, 1}}, b, s);
            check(r.model && *r.model == Assignment{7, 7} && f.validate(*r.model), "grouped assignment");
        }
        {
            Formula f(file("(declare-const x Int)(declare-const y Int)(assert (and (= x y)(or (= x 0)(= x "
                           "1))(or (= y 0)(= y 1))))"));
            Budget b;
            b.seconds = 10;
            ChoiceCatalog cat(f, b);
            MacroChoiceGenerator g(f, cat, 2);
            Stats s;
            auto r = g.query_choices({{0, 0}, {1, 1}}, b, s);
            check(r.code == SolveStatus::Unsat && !r.global_target_proof, "combo conflict scope");
            for (auto &group : cat.groups)
                for (auto &a : group.alternatives)
                    check(!a.impossible, "combo cannot cache singleton");
            check(g.query_choices({{0, 0}}, b, s).model.has_value() &&
                      g.query_choices({{1, 1}}, b, s).model.has_value(),
                  "push/pop no contamination");
        }
        {
            Formula f(file("(declare-const x Int)(assert (and (= x 0)(or (= x 0)(= x 1))))"));
            Budget b;
            b.seconds = 10;
            ChoiceCatalog cat(f, b);
            MacroChoiceGenerator g(f, cat, 1);
            Stats s;
            auto r = g.query_choices({{0, 1}}, b, s);
            check(r.code == SolveStatus::Unsat && cat.groups[0].alternatives[1].impossible,
                  "single unrestricted proof");
        }
        {
            Formula f(file("(declare-const x Int)(declare-const y Int)(assert (or (= x 0)(= y 0)))"));
            Budget b;
            b.seconds = 10;
            ChoiceCatalog cat(f, b);
            MacroChoiceGenerator g(f, cat, 0);
            Stats s;
            g.query_override = [](unsigned) {
                GlobalResult r;
                r.model = Assignment{0, 0};
                r.status = "sat";
                r.code = SolveStatus::Sat;
                return std::optional(r);
            };
            auto r = g.query_choices({{0, 1}}, b, s);
            check(r.model && cat.satisfied(cat.groups[0], *r.model).size() == 2,
                  "actual set not requested label");
            g.query_override = [](unsigned) {
                GlobalResult r;
                r.status = "unknown";
                r.code = SolveStatus::Unknown;
                return std::optional(r);
            };
            g.query_choices({{0, 1}}, b, s);
            check(!cat.groups[0].alternatives[1].impossible, "unknown no proof");
        }
        {
            Formula f(file("(declare-const b Bool)(declare-const x Int)(assert (and (= b true)(>= x (- "
                           "184467440737095516160)) (<= x 184467440737095516160)))"));
            Budget b;
            b.seconds = 10;
            ChoiceCatalog cat(f, b);
            check(cat.groups.empty(), "no structure");
            Global global(f, 3);
            global.exploration_check = [](unsigned) {
                GlobalResult q;
                q.model = Assignment{1, -2};
                q.code = SolveStatus::Sat;
                q.status = "sat";
                return std::optional(q);
            };
            OnlineCoverage online(f);
            online.restore({{1, -1}, {1, 0}}, {1, 1});
            Stats s;
            auto r = plain_broad(global, online, {1, 1}, b, s, {}, 2);
            check(r.best && r.best->model.size() == 2 && r.best->model[0] == 1 && f.validate(r.best->model),
                  "fallback complete bool and integer");
            check(r.best->gain.covered == 0, "legal zero coverage retained accurately");
        }
        {
            Formula f(file("(declare-const x Int)(declare-const y Int)(assert (and (or (= x 0)(= x 1))(or (= "
                           "y 0)(= y 1))))"));
            Budget init;
            init.seconds = 10;
            ChoiceCatalog cat(f, init);
            cat.commit({0, 0});
            Global global(f, 0);
            OnlineCoverage online(f);
            online.restore({}, {0, 0});
            MacroChoiceGenerator g(f, cat, 0);
            Stats s;
            Budget b;
            b.seconds = 2;
            g.query_override = [&](unsigned n) {
                GlobalResult r;
                if (!n) {
                    r.model = Assignment{1, 1};
                    r.code = SolveStatus::Sat;
                    r.status = "sat";
                } else {
                    b.start -= std::chrono::seconds(3);
                    r.code = SolveStatus::Timeout;
                    r.status = "timeout";
                }
                return std::optional(r);
            };
            auto r = g.generate(true, global, online, {0, 0}, b, s, {});
            check(r.best && r.best->model == Assignment{1, 1} && r.calls == 2,
                  "retain timely witness after later overrun");
            check(!online.commit(*r.best, b.elapsed(), 2), "cannot backdate output");
            MacroChoiceGenerator late(f, cat, 0);
            late.query_override = [](unsigned) {
                GlobalResult r;
                r.model = Assignment{1, 1};
                r.code = SolveStatus::Sat;
                r.status = "sat";
                return std::optional(r);
            };
            Budget c;
            c.seconds = 2;
            auto rejected = late.generate(true, global, online, {0, 0}, c, s, [&](const auto &) {
                c.start -= std::chrono::seconds(3);
                return c.elapsed();
            });
            check(!rejected.best && rejected.late, "late checkpoint rejected");
        }
        std::cout << "macro tests passed\n";
        return 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
