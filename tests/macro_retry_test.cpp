#include "macro_choice.hpp"
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
    auto p = std::filesystem::temp_directory_path() / ("lia-retry-" + std::to_string(n++) + ".smt2");
    std::ofstream(p) << s;
    return p.string();
}
int main() {
    try {
        Formula f(input("(declare-const x Int)(assert (or (= x 0)(= x 1)(= x 2)(= x 3)(= x 4)))"));
        Budget b;
        b.seconds = 10;
        ChoiceCatalog cat(f, b);
        cat.commit({0});
        MacroChoiceGenerator macro(f, cat, 0);
        Stats stats;
        unsigned chosen = 0;
        std::vector<unsigned> budgets;
        std::vector<std::pair<unsigned, unsigned>> order;
        macro.timed_query_override = [&](unsigned ms) {
            budgets.push_back(ms);
            GlobalResult r;
            r.raw_status = ms < 200 ? "unknown" : "sat";
            r.detail = ms < 200 ? "canceled" : "";
            r.check_seconds = ms / 1000.;
            if (ms >= 200)
                r.model = Assignment{cat.groups[0].alternatives[chosen].assignments.at(0)};
            return std::optional(r);
        };
        for (unsigned i = 0; i < 3; ++i) {
            auto pick = macro.select({0});
            check(bool(pick), "selection exists");
            order.push_back(*pick);
            chosen = pick->second;
            auto q = macro.query_choices({*pick}, b, stats);
            check(!cat.groups[0].alternatives[chosen].visits, "failed/internal attempt never formal visit");
            if (i == 2)
                check(q.model && q.code == SolveStatus::Sat, "mock longer budget delivers legal model");
        }
        check(order[0] != order[1] && order[2] == order[0] && budgets == std::vector<unsigned>{50, 50, 200},
              "two new then oldest retry, continuous budget");
        // Constant timeouts cannot monopolize selection or retry one level indefinitely.
        ChoiceCatalog cat2(f, b);
        MacroChoiceGenerator retry(f, cat2, 0);
        std::map<unsigned, std::vector<unsigned>> per;
        retry.timed_query_override = [](unsigned ms) {
            GlobalResult r;
            r.raw_status = "unknown";
            r.detail = "canceled";
            r.check_seconds = ms / 1000.;
            return std::optional(r);
        };
        for (unsigned i = 0; i < 40; ++i) {
            auto pick = retry.select({0});
            if (!pick) {
                retry.fallback_opportunity();
                continue;
            }
            auto q = retry.query_choices({*pick}, b, stats);
            per[pick->second].push_back(q.actual_timeout_ms);
        }
        check(per.size() == 4, "all fresh alternatives get opportunities");
        for (auto &[a, ms] : per)
            check(ms == std::vector<unsigned>{50, 200, 1000, 2000}, "exact four full levels maximum");
        for (auto &a : cat2.groups[0].alternatives)
            check(!a.impossible, "timeout never proof");
        ChoiceCatalog unknown_cat(f, b);
        MacroChoiceGenerator unknown(f, unknown_cat, 0);
        unknown.timed_query_override = [](unsigned) {
            GlobalResult r;
            r.raw_status = "unknown";
            r.detail = "incomplete";
            return std::optional(r);
        };
        for (unsigned i = 0; i < 30; ++i) {
            auto pick = unknown.select({0});
            if (pick)
                unknown.query_choices({*pick}, b, stats);
            else
                unknown.fallback_opportunity();
        }
        for (auto &a : unknown_cat.groups[0].alternatives)
            check(a.attempt.attempts <= 2 && a.attempt.retry_level == 0,
                  "unknown bounded retry without time escalation");
        Formula simple(input("(declare-const x Int)(assert (>= x 0))"));
        ChoiceCatalog empty(simple, b);
        check(empty.groups.empty(), "empty catalog");
        OnlineCoverage online(simple);
        online.restore({}, {0});
        Global p(simple, 0), q(simple, 0);
        p.exploration_budget.fixed_levels = q.exploration_budget.fixed_levels = true;
        std::vector<unsigned> left, right;
        auto mock = [](std::vector<unsigned> &v, unsigned ms) {
            v.push_back(ms);
            GlobalResult r;
            r.raw_status = "unknown";
            r.detail = "canceled";
            r.check_seconds = ms / 1000.;
            return std::optional(r);
        };
        p.exploration_check = [&](unsigned ms) { return mock(left, ms); };
        q.exploration_check = [&](unsigned ms) { return mock(right, ms); };
        std::unique_ptr<MacroChoiceGenerator> none;
        if (!empty.groups.empty())
            none = std::make_unique<MacroChoiceGenerator>(simple, empty, 0);
        check(!none, "no macro engine for empty catalog");
        Budget bp;
        bp.seconds = 10;
        Budget bq;
        bq.seconds = 10;
        plain_broad(p, online, {0}, bp, stats, {}, 4);
        if (none)
            none->generate(true, q, online, {0}, bq, stats, {}, 4);
        else
            plain_broad(q, online, {0}, bq, stats, {}, 4);
        check(left == right && left == std::vector<unsigned>{50, 200, 1000, 2000},
              "same no-structure query decisions with simulated costs");
        std::cout << "macro retry tests passed\n";
        return 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
