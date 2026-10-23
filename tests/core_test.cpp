#include "core.hpp"
#include "scheduler.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>

using namespace lia;
void check(bool v, const char *m) {
    if (!v)
        throw std::runtime_error(m);
}
std::string fixture(const std::string &text) {
    static unsigned id = 0;
    auto p = std::filesystem::temp_directory_path() / ("lia-core-" + std::to_string(++id) + ".smt2");
    std::ofstream(p) << text;
    return p.string();
}
void region_case(const std::string &text) {
    auto path = fixture(text);
    Formula f(path);
    z3::solver s(f.ctx);
    s.add(f.root);
    check(s.check() == z3::sat, "fixture SAT");
    auto m = f.complete(s.get_model());
    auto o = f.evaluate(m);
    Budget budget;
    budget.seconds = 5;
    Region r(f, m, o, budget);
    check(r.supported, "region supported");
    check(r.contains(m), "region seed");
    z3::solver proof(f.ctx);
    proof.add(r.expression() && !f.root);
    check(proof.check() == z3::unsat, "P implies F");
    std::mt19937_64 rng(10);
    Stats stats;
    auto dirs = directions(r, rng, stats);
    for (auto &d : dirs) {
        check(r.kernel(d), "full kernel");
        auto interval = r.interval(m, d);
        check(interval.contains(0), "origin interval");
        for (int z = -4; z <= 4; ++z)
            if (interval.contains(z)) {
                auto a = m;
                for (size_t i = 0; i < a.size(); ++i)
                    a[i] += z * d[i];
                check(f.validate(a), "direction model valid");
            }
    }
    std::filesystem::remove(path);
}
int main() {
    try {
        Interval interval;
        interval.constrain(-1, 0);
        interval.constrain(1, 5);
        check(interval.lo && *interval.lo == 0 && interval.hi && *interval.hi == 5, "interval [0,5]");
        std::vector<std::string> forms = {
            "(declare-const x Int)(declare-const y Int)(assert (= (+ x (* 2 y)) 10))(assert (and (<= 0 y) "
            "(<= y 5)))",
            "(declare-const x Int)(declare-const y Int)(declare-const z Int)(assert (= (+ x (* 2 y) z) "
            "10))(assert (= (+ (* 3 x) y) 7))(assert (< y 100))",
            "(declare-const x Int)(declare-const y Int)(assert (and (> x -10) (< x 20) (not (= y 2)) (>= y "
            "-7)))",
            "(declare-const x Int)(assert (= x 987654321098765432109876543210))",
            "(declare-const x Int)(declare-const b Bool)(assert (or (and b (< x -1)) (and (not b) (>= x "
            "3))))",
            "(declare-const x Int)(declare-const y Int)(assert (= (ite (> x 0) (+ x 1) (- x)) y))",
            "(declare-const x Int)(declare-const y Int)(declare-const z Int)(assert (distinct x y z))",
            "(declare-const x Int)(declare-const y Int)(declare-const z Int)(assert (not (distinct x y z)))",
            "(declare-const b Bool)(declare-const c Bool)(assert (xor b c))",
            "(declare-const b Bool)(declare-const c Bool)(assert (=> b c))",
            "(declare-const x Int)(assert (not (or (< x 0) (>= x 9))))",
            "(declare-const x Int)(assert (not (and (< x 0) (>= x 9))))"};
        for (auto &text : forms)
            region_case(text);
        {
            Formula f(fixture(forms[0]));
            Assignment m{0, 5}, d{2, -1};
            Budget budget;
            auto o = f.evaluate(m);
            Region r(f, m, o, budget);
            auto i = r.interval(m, d);
            check(i.lo && *i.lo == 0 && i.hi && *i.hi == 5, "specified equality interval");
            check(!r.kernel(Assignment{1, 0}), "reject partial kernel");
        }
        {
            Formula f(fixture("(declare-const x Int)(assert (or (= x 0) (= x 1)))"));
            Budget budget;
            Assignment m{0};
            auto o = f.evaluate(m);
            Region r(f, m, o, budget);
            check(!r.contains(Assignment{1}), "local excludes second model");
            int x = f.variables[0];
            Global global(f, 0);
            Stats stats;
            auto res = global.query({Target{x, 0, true}}, budget, stats);
            check(res.model && (*res.model)[0] == 1, "global reaches other local region");
            check(global.conflicts.cores.empty(), "local failure did not poison global cache");
            Coverage cov(f);
            cov.accept(o);
            auto before = cov.json();
            auto candidate = f.evaluate(Assignment{1});
            cov.gain(candidate);
            check(before == cov.json(), "discarded candidate does not update coverage");
        }
        {
            Formula f(fixture("(declare-const x Int)(declare-const y Int)(declare-const z Int)(assert (and "
                              "(<= 0 x) (<= x 1) (<= 0 y) (<= y 1) (<= 0 z) (<= z 1) (<= (+ x y z) 2)))"));
            Global global(f, 0);
            Budget budget;
            Stats stats;
            global.pack_size = 3;
            std::vector<Target> goals;
            for (int n : f.variables)
                goals.push_back({n, 0, true});
            auto r = global.query(goals, budget, stats);
            check(r.status == "unsat" && r.core.size() == 3, "ternary core");
            check(global.conflicts.cores.size() == 1, "core persisted");
            for (int drop = 0; drop < 3; ++drop) {
                auto p = goals;
                p.erase(p.begin() + drop);
                global.pack_size = 2;
                auto sat = global.query(p, budget, stats);
                check(sat.status == "sat", "pair not blocked by ternary core");
            }
            auto e = global.explore(Assignment{0, 0, 0}, budget, stats);
            check(e.status == "sat", "explore SAT");
            check(global.conflicts.cores.size() == 1, "exploration isolated");
            global.validate_conflicts(Assignment{1, 1, 0});
            global.conflicts.insert({0}); // Deliberately corrupt the cache.
            bool caught = false;
            try {
                global.validate_conflicts(Assignment{1, 0, 0});
            } catch (const std::logic_error &) {
                caught = true;
            }
            check(caught, "detect cache rejecting a known satisfiable combination");
        }
        {
            Formula f(fixture("(declare-const x Int)(assert (> x 0))"));
            Global global(f, 0, 1);
            Budget budget;
            Stats stats;
            auto r = global.query({Target{f.variables[0], 63, true}}, budget, stats);
            check(r.status == "unknown" || r.status == "resource_exhausted" || r.status == "cancelled",
                  ("resource exhaustion is not UNSAT: " + r.status + " " + r.detail).c_str());
            check(global.conflicts.cores.empty(), "unknown does not create conflicts");
            check(global.target_budget.requested_timeout_ms == 50, "resource exhaustion does not increase timeout");
        }
        {
            Formula f(fixture("(declare-const x Int)(assert (= (mod x 3) 0))"));
            Budget budget;
            Assignment m{0};
            auto o = f.evaluate(m);
            Region r(f, m, o, budget);
            check(!r.supported && !f.metric_compatible, "unsupported local/evaluator distinguished");
            check(f.validate(m), "unsupported local does not mean UNSAT");
        }
        {
            Formula f(fixture("(declare-const x Int)(declare-const b Bool)"
                              "(assert (and (or b (= x 0)) (>= (ite b x (- x)) -5)))"));
            for (int b = 0; b <= 1; ++b)
                for (int x = -2; x <= 2; ++x) {
                    Assignment model{b, x};
                    if (!f.validate(model))
                        continue;
                    auto observed = f.evaluate(model);
                    auto zmodel = f.as_model(model);
                    for (size_t i = 0; i < f.nodes.size(); ++i)
                        check(zmodel.eval(f.reach(i), true).is_true() == bool(observed.visited[i]),
                              "Reach agrees with lazy evaluator on shared paths");
                    Budget budget;
                    Region r(f, model, observed, budget);
                    z3::solver proof(f.ctx);
                    proof.add(r.expression() && !f.root);
                    check(proof.check() == z3::unsat, "branch-dependent regions imply F");
                }
        }
        {
            Formula f(fixture("(declare-const x Int)(assert (>= x 0))(assert (<= x 3))"));
            Global global(f, 0);
            Stats stats; Budget budget;
            std::vector<unsigned> calls;
            global.exploration_check = [&](unsigned ms) -> std::optional<GlobalResult> {
                calls.push_back(ms);
                GlobalResult r;
                r.code = ms < 200 ? SolveStatus::Timeout : SolveStatus::Sat;
                if (ms >= 200) r.model = Assignment{1};
                return r;
            };
            for (int i=0;i<3;++i) {
                auto target = global.query({Target{f.variables[0],63,true}}, budget, stats);
                check(!target.model, "impossible target stays impossible");
                auto result = global.explore(Assignment{0}, budget, stats);
                check(bool(result.model) == (i==2), "independent retry reaches SAT");
            }
            check(calls == std::vector<unsigned>({50,100,200}), "exploration actually gets growing allocations");
            check(global.target_budget.requested_timeout_ms == 50, "target budget independent");
            check(global.blocked_models() == 2 && stats.counts["exploration_block_added"] == 2,
                  "retries do not duplicate blocks; new exploration model is blocked");
            auto clipped = global.explore(Assignment{0}, budget, stats, 25);
            check(clipped.actual_timeout_ms == 25, "allocation bounds requested timeout");
            global.exploration_check=[](unsigned) -> std::optional<GlobalResult> {
                GlobalResult bad; bad.code=SolveStatus::Sat; bad.model=Assignment{-1}; return bad;
            };
            bool rejected=false;
            try { global.explore(Assignment{0},budget,stats); }
            catch(const std::logic_error &) { rejected=true; }
            check(rejected && global.invalid_exploration_model==std::optional<Assignment>(Assignment{-1}),
                  "invalid solver witness rejected and preserved for independent replay");
            ProgressFeedback progress;
            Coverage cov(f); cov.accept(f.evaluate(Assignment{0}));
            auto gain = cov.gain(f.evaluate(Assignment{1}));
            check(gain.covered>0, "exploration gain fixture");
            progress.add({true,gain.covered,gain.observed});
            progress.add({});
            check(progress.next_stalled(8)==0, "later failed action cannot erase coverage progress");
            ProgressFeedback duplicate;
            duplicate.add({});
            check(duplicate.next_stalled(8)==9, "no invented duplicate progress");
        }
        {
            Scheduler sched; double now = 0;
            double cheap_early = 0, costly_early = 0, cheap_late = 0;
            for (int i=0;i<800;++i) {
                auto choice = sched.choose(now,100,{true,false,true,false},50,50);
                if (!choice) { now += .01; continue; }
                ActionResult r; r.kind=choice->kind;
                bool cheap = r.kind==ActionKind::LocalSafe;
                r.elapsed_seconds = cheap ? .005 : .08;
                bool late = now>12;
                r.emitted_new_coverage = late ? (cheap ? 5 : 0) : (cheap ? 0 : 10);
                r.emitted_samples = r.emitted_new_coverage ? 1 : 0;
                if (!late) (cheap ? cheap_early : costly_early) += r.elapsed_seconds;
                else if (cheap) cheap_late += r.elapsed_seconds;
                now += r.elapsed_seconds;
                sched.feedback(*choice,r,now);
            }
            check(cheap_early < costly_early*.2,"zero-gain cheap action cannot monopolize actual time");
            check(cheap_late>0.5,"cooled module is retried after reward changes");
            check(sched.probe_seconds <= .25 + .15*sched.total_seconds + .1,"probe accounting uses actual cost");
            Scheduler retry;
            auto selection=retry.choose(0,10,{false,false,false,true},50,1600);
            check(selection && selection->seconds>=1.6,"scheduler honors exploration requested budget");
            Scheduler filler;
            ActionSelection action{ActionKind::LocalSafe,.2,false};
            ActionResult empty_gain; empty_gain.kind=ActionKind::LocalSafe;
            empty_gain.elapsed_seconds=.01; empty_gain.emitted_samples=1;
            for(unsigned i=0;i<100;++i) filler.feedback(action,empty_gain,10);
            check(filler.meters[0].cooldown_until<=10.2 && filler.meters[0].gain==0,
                  "distinct fillers retain throughput without invented coverage");
        }
        std::cout << "core tests passed\n";
        return 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
