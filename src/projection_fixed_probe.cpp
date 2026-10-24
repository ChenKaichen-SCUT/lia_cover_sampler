#include "feedback_projection.hpp"
#include "plateau_tracker.hpp"
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
using namespace lia;
namespace {
void stop(int) { interrupted = 1; }
void flush(std::ofstream &s) {
    s.flush();
    if (!s)
        throw std::runtime_error("journal flush");
}
} // namespace
int main(int argc, char **argv) {
    try {
        if (argc != 8)
            throw std::invalid_argument("projection_fixed_probe F plain_only|plain_extra|projection_fixed "
                                        "SECONDS SEED OUT PREFIX_VALUES SEED_VALUES");
        std::string mode = argv[2];
        if (mode != "plain_only" && mode != "plain_extra" && mode != "projection_fixed")
            throw std::invalid_argument("mode");
        double seconds = std::stod(argv[3]);
        if (!std::isfinite(seconds) || seconds <= 0)
            throw std::invalid_argument("seconds");
        unsigned seed = std::stoul(argv[4]);
        Budget session;
        session.seconds = seconds;
        std::signal(SIGINT, stop);
        std::signal(SIGTERM, stop);
        std::filesystem::path dir(argv[5]);
        if (!std::filesystem::create_directory(dir))
            throw std::runtime_error("new output directory required");
        std::ofstream initial(dir / "initial.json"), queries(dir / "queries.jsonl"),
            candidates(dir / "candidates.jsonl"), cycles(dir / "cycles.jsonl"), extras(dir / "extra.jsonl"),
            models(dir / "models.jsonl"), commits(dir / "commits.jsonl");
        for (auto p : {&initial, &queries, &candidates, &cycles, &extras, &models, &commits}) {
            *p << std::setprecision(17);
            if (!*p)
                throw std::runtime_error("open journal");
        }
        Formula f(argv[1]);
        double formula_seconds = session.elapsed();
        auto prefix = read_residual_values(f, argv[6]);
        auto seeds = read_residual_values(f, argv[7]);
        if (seeds.size() != 1)
            throw std::invalid_argument("one seed");
        Assignment current = seeds[0];
        OnlineCoverage online(f);
        bool seed_added = online.restore(prefix, current);
        double restored = session.elapsed();
        std::deque<Assignment> history;
        std::set<std::string> initial_keys;
        for (auto &m : prefix)
            if (initial_keys.insert(f.key(m)).second) {
                history.push_back(m);
                if (history.size() > 4)
                    history.pop_front();
            }
        if (initial_keys.insert(f.key(current)).second) {
            history.push_back(current);
            if (history.size() > 4)
                history.pop_front();
        }
        auto engine_start = Clock::now();
        Global global(f, seed);
        global.exploration_budget.fixed_levels = true;
        double engine_seconds = std::chrono::duration<double>(Clock::now() - engine_start).count();
        initial << "{\"source_sha256\":" << quote(LIA_SOURCE_SHA) << ",\"mode\":" << quote(mode)
                << ",\"seed\":" << seed << ",\"seconds\":" << seconds
                << ",\"seed_added\":" << (seed_added ? "true" : "false")
                << ",\"seed_model\":" << f.model_json(current) << ",\"prefix\":[";
        for (unsigned i = 0; i < prefix.size(); ++i) {
            if (i)
                initial << ',';
            initial << f.model_json(prefix[i]);
        }
        initial << "],\"coverage\":" << online.state().json() << "}\n";
        flush(initial);
        std::unique_ptr<FeedbackProjection> feedback;
        std::unique_ptr<ProjectionGuidedExplorer> guided;
        ProjectionTrigger trigger;
        ProjectionExtraBudget extra_budget(seconds, .25, .04);
        PlateauTracker plateau;
        unsigned cycle = 0, plain_calls = 0, plain_sat = 0, extra_calls = 0, extra_sat = 0, triggers = 0,
                 replacements = 0, late_candidates = 0, duplicates = 0;
        uint64_t formal_count = 0, gain_sum = 0, unique_candidates = 0, positive_outputs = 0;
        double plain_seconds = 0, output_seconds = 0, first_positive = -1, init_seconds = session.elapsed(),
               feedback_seconds = 0, guide_init_seconds = 0;
        std::set<std::string> candidate_keys = online.keys();
        std::string terminal = "budget_exhausted";
        while (!session.done() && session.left() > .05 && cycle < 10000) {
            auto cycle_start = session.elapsed();
            auto version = online.version();
            auto covered = online.state().covered();
            Stats stats;
            std::vector<std::string> receipts;
            unsigned cid = 0;
            auto assess = [&](const GlobalResult &q, const std::string &origin,
                              double deadline) -> std::optional<ResidualCandidate> {
                if (!q.model)
                    return {};
                auto t = Clock::now();
                ResidualWindow w;
                w.coverage_version = version;
                auto c = online.prepare(*q.model, w, origin, cid);
                double validated = session.elapsed();
                bool distinct = bool(c);
                bool fresh = distinct && candidate_keys.insert(f.key(*q.model)).second;
                if (fresh && validated <= seconds)
                    ++unique_candidates;
                if (!distinct)
                    ++duplicates;
                std::string id = std::to_string(cycle) + "-" + std::to_string(cid++);
                candidates << "{\"id\":" << quote(id) << ",\"cycle\":" << cycle
                           << ",\"origin\":" << quote(origin) << ",\"version\":" << version
                           << ",\"validated\":" << validated
                           << ",\"formal_duplicate\":" << (distinct ? "false" : "true")
                           << ",\"internal_duplicate\":" << (distinct && !fresh ? "true" : "false")
                           << ",\"new_to_supply\":" << (fresh ? "true" : "false")
                           << ",\"gain\":" << (c ? c->gain.covered : 0)
                           << ",\"observations\":" << (c ? c->gain.observed : 0)
                           << ",\"model\":" << f.model_json(*q.model) << "}\n";
                flush(candidates);
                double completed = session.elapsed();
                bool timely = completed <= deadline && completed <= seconds && !interrupted;
                std::ostringstream receipt;
                receipt << std::setprecision(17) << "{\"id\":" << quote(id) << ",\"completion\":" << completed
                        << ",\"deadline\":" << deadline << ",\"timely\":" << (timely ? "true" : "false")
                        << '}';
                receipts.push_back(receipt.str());
                stats.times[origin + "_assessment_seconds"] +=
                    std::chrono::duration<double>(Clock::now() - t).count();
                if (c && timely && !(origin == "projection" && !fresh)) {
                    c->checkpoint_completed = completed;
                    return c;
                }
                if (c && !timely)
                    ++late_candidates;
                return {};
            };
            auto log_query = [&](const GlobalResult &q, const std::string &origin) {
                queries << "{\"cycle\":" << cycle << ",\"origin\":" << quote(origin)
                        << ",\"version\":" << version << ",\"status\":" << quote(q.status)
                        << ",\"query_seconds\":" << q.elapsed_seconds << ",\"evidence\":" << q.evidence_json()
                        << "}\n";
                flush(queries);
            };
            Budget ordinary;
            ordinary.seconds = std::min(2.075, std::max(0., session.left() - .025));
            ordinary.parent = &session;
            double ordinary_start = session.elapsed();
            Budget work;
            work.start = ordinary.start;
            work.seconds = std::max(0., ordinary.seconds - .025);
            work.parent = &ordinary;
            auto pquery = global.explore(current, work, stats, 0xffffffffU, true);
            ++plain_calls;
            plain_sat += bool(pquery.model);
            log_query(pquery, "plain");
            auto plain = assess(pquery, "plain", std::min(seconds, ordinary_start + ordinary.seconds));
            double pcost = ordinary.elapsed();
            plain_seconds += pcost;
            std::optional<ResidualCandidate> best = plain;
            auto reason = trigger.observe(plain);
            double allowance = extra_budget.allowance(session.left()), ecost = 0;
            bool attempted = false, replaced = false;
            std::string extra_status = "not_triggered";
            if (reason != "none") {
                ++triggers;
                if (mode == "plain_only")
                    trigger.attempted();
                else if (mode == "projection_fixed" && guided && !guided->available())
                    extra_status = guided->stop_reason();
                else if (allowance > 0 && !session.done()) {
                    attempted = true;
                    trigger.attempted();
                    Budget stage;
                    stage.seconds = allowance;
                    stage.parent = &session;
                    double extra_start = session.elapsed();
                    Budget solve;
                    solve.start = stage.start;
                    solve.seconds = std::max(0., allowance - .020);
                    solve.parent = &stage;
                    ProjectionSelection selected;
                    ProjectionResult result;
                    std::string selection = "null", condition = "null";
                    std::optional<ResidualCandidate> extra;
                    if (mode == "projection_fixed") {
                        auto t = Clock::now();
                        if (!feedback)
                            feedback = std::make_unique<FeedbackProjection>(f, seed);
                        if (!solve.done())
                            selected = feedback->select(online.state(), history, solve);
                        else {
                            selected.complete = false;
                            selected.reason = "analysis_budget";
                        }
                        feedback_seconds += std::chrono::duration<double>(Clock::now() - t).count();
                        selection = selected.json(f);
                        if (selected.complete && !selected.variables.empty() && !solve.done()) {
                            if (!guided) {
                                auto start = Clock::now();
                                guided = std::make_unique<ProjectionGuidedExplorer>(
                                    f, seed, ProjectionOptions{true, 200});
                                guide_init_seconds +=
                                    std::chrono::duration<double>(Clock::now() - start).count();
                            }
                            if (!solve.done()) {
                                result = guided->query(selected.variables, history, solve, stats);
                                ++extra_calls;
                                extra_sat += bool(result.query.model);
                                log_query(result.query, "projection");
                                extra = assess(result.query, "projection",
                                               std::min(seconds, extra_start + allowance));
                                condition = result.condition_json(f);
                                extra_status = result.query.status;
                            } else
                                extra_status = "initialization_budget";
                        } else
                            extra_status = selected.reason;
                    } else {
                        auto q = global.explore(current, solve, stats, 200, true, 200);
                        ++extra_calls;
                        extra_sat += bool(q.model);
                        log_query(q, "extra_plain");
                        extra = assess(q, "extra_plain", std::min(seconds, extra_start + allowance));
                        extra_status = q.status;
                    }
                    if (extra && (!best || projection_better(*extra, *best))) {
                        best = extra;
                        replaced = true;
                        ++replacements;
                    }
                    extras << "{\"cycle\":" << cycle << ",\"version\":" << version
                           << ",\"trigger\":" << quote(reason) << ",\"started\":" << extra_start
                           << ",\"allowance\":" << allowance << ",\"status\":" << quote(extra_status)
                           << ",\"selection\":" << selection << ",\"condition\":" << condition
                           << ",\"replaced\":" << (replaced ? "true" : "false")
                           << ",\"plain_gain\":" << (plain ? std::to_string(plain->gain.covered) : "null")
                           << ",\"extra_gain\":" << (extra ? std::to_string(extra->gain.covered) : "null")
                           << "}\n";
                    flush(extras);
                    ecost = stage.elapsed();
                    extra_budget.charge(ecost);
                } else
                    extra_status = "extra_quota_or_delivery_reserve";
            }
            bool committed = false;
            double completion = -1;
            auto output_start = Clock::now();
            if (best && !session.done()) {
                ResidualWindow w;
                w.coverage_version = version;
                auto checked = online.prepare(best->model, w, best->origin, best->query);
                if (checked && !session.done()) {
                    std::string id = std::to_string(cycle);
                    models << "{\"id\":" << quote(id) << ",\"cycle\":" << cycle
                           << ",\"index\":" << formal_count + 1 << ",\"version\":" << version
                           << ",\"origin\":" << quote(checked->origin)
                           << ",\"gain\":" << checked->gain.covered
                           << ",\"observations\":" << checked->gain.observed
                           << ",\"model\":" << f.model_json(checked->model) << "}\n";
                    flush(models);
                    completion = session.elapsed();
                    bool timely = completion <= seconds && !interrupted;
                    commits << "{\"id\":" << quote(id) << ",\"completion\":" << completion
                            << ",\"deadline\":" << seconds << ",\"timely\":" << (timely ? "true" : "false")
                            << "}\n";
                    flush(commits);
                    if (timely) {
                        committed = online.commit(*checked, completion, seconds);
                        if (!committed)
                            throw std::logic_error("formal commit rejected");
                        ++formal_count;
                        gain_sum += checked->gain.covered;
                        positive_outputs += checked->gain.covered > 0;
                        if (checked->gain.covered && first_positive < 0)
                            first_positive = completion;
                        current = checked->model;
                        history.push_back(current);
                        if (history.size() > 4)
                            history.pop_front();
                        plateau.output(completion, formal_count, checked->gain.covered, false,
                                       checked->origin);
                    }
                }
            }
            output_seconds += std::chrono::duration<double>(Clock::now() - output_start).count();
            if (gain_sum != online.state().covered() - online.initial_covered())
                throw std::logic_error("marginal identity");
            cycles << "{\"cycle\":" << cycle << ",\"started\":" << cycle_start
                   << ",\"finished\":" << session.elapsed() << ",\"version_before\":" << version
                   << ",\"version_after\":" << online.version() << ",\"covered_before\":" << covered
                   << ",\"covered_after\":" << online.state().covered()
                   << ",\"committed\":" << (committed ? "true" : "false") << ",\"trigger\":" << quote(reason)
                   << ",\"ordinary_opportunities_since_extra\":" << trigger.plain_opportunities
                   << ",\"zero_plain_candidates\":" << trigger.consecutive_zero
                   << ",\"plain_seconds\":" << pcost
                   << ",\"extra_attempted\":" << (attempted ? "true" : "false")
                   << ",\"extra_status\":" << quote(extra_status) << ",\"extra_seconds\":" << ecost
                   << ",\"extra_spent\":" << extra_budget.spent
                   << ",\"replaced\":" << (replaced ? "true" : "false") << ",\"writes\":[";
            for (unsigned i = 0; i < receipts.size(); ++i) {
                if (i)
                    cycles << ',';
                cycles << receipts[i];
            }
            cycles << "],\"statistics\":" << stats.json() << "}\n";
            flush(cycles);
            ++cycle;
        }
        if (interrupted)
            terminal = "cancelled";
        else if (cycle >= 10000)
            terminal = "safety_cycle_cap";
        unsigned projection_blocks = guided ? guided->blocked_models() : 0;
        std::string projection_stop = guided ? guided->stop_reason() : "not_initialized";
        auto cleanup = Clock::now();
        bool allocated = bool(feedback) || bool(guided);
        guided.reset();
        feedback.reset();
        double cleanup_seconds = std::chrono::duration<double>(Clock::now() - cleanup).count();
        if (allocated)
            extra_budget.charge(cleanup_seconds);
        plateau.finish(std::min(seconds, session.elapsed()), formal_count);
        std::ofstream pf(dir / "plateau.json");
        pf << plateau.json() << '\n';
        flush(pf);
        std::ofstream summary(dir / "summary.json");
        summary << std::setprecision(17) << "{\"source_sha256\":" << quote(LIA_SOURCE_SHA)
                << ",\"mode\":" << quote(mode) << ",\"seed\":" << seed << ",\"budget_seconds\":" << seconds
                << ",\"formula_seconds\":" << formula_seconds
                << ",\"restore_seconds\":" << restored - formula_seconds
                << ",\"ordinary_engine_seconds\":" << engine_seconds
                << ",\"initialization_seconds\":" << init_seconds << ",\"formal_count\":" << formal_count
                << ",\"positive_outputs\":" << positive_outputs << ",\"gain\":" << gain_sum
                << ",\"first_positive_seconds\":" << first_positive
                << ",\"initial_covered\":" << online.initial_covered()
                << ",\"coverage\":" << online.state().json() << ",\"cycles\":" << cycle
                << ",\"plain_calls\":" << plain_calls << ",\"plain_sat\":" << plain_sat
                << ",\"plain_seconds\":" << plain_seconds << ",\"extra_calls\":" << extra_calls
                << ",\"extra_sat\":" << extra_sat << ",\"extra_quota\":" << extra_budget.quota
                << ",\"extra_spent\":" << extra_budget.spent
                << ",\"extra_overrun\":" << (extra_budget.spent > extra_budget.quota ? "true" : "false")
                << ",\"projection_blocks\":" << projection_blocks
                << ",\"projection_stop_reason\":" << quote(projection_stop)
                << ",\"feedback_seconds\":" << feedback_seconds
                << ",\"guided_initialization_seconds\":" << guide_init_seconds
                << ",\"guided_cleanup_seconds\":" << (allocated ? cleanup_seconds : 0)
                << ",\"output_seconds\":" << output_seconds
                << ",\"unique_validated_candidates\":" << unique_candidates << ",\"triggers\":" << triggers
                << ",\"replacements\":" << replacements << ",\"duplicates\":" << duplicates
                << ",\"late_candidates\":" << late_candidates << ",\"session_seconds\":" << session.elapsed()
                << ",\"terminal\":" << quote(terminal) << "}\n";
        flush(summary);
        return 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
