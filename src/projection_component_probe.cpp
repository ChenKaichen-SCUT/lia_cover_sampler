#include "feedback_projection.hpp"
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
using namespace lia;
void stop_component(int) { interrupted = 1; }
void sync_file(std::ofstream &f) {
    f.flush();
    if (!f)
        throw std::runtime_error("journal write");
}
int main(int argc, char **argv) {
    try {
        if (argc != 8)
            throw std::invalid_argument(
                "component F plain_extra|projection_fixed SECONDS SEED OUT PREFIX SEED_VALUES");
        std::string mode = argv[2];
        if (mode != "plain_extra" && mode != "projection_fixed")
            throw std::invalid_argument("mode");
        Budget session;
        session.seconds = std::stod(argv[3]);
        unsigned seed = std::stoul(argv[4]);
        std::signal(SIGTERM, stop_component);
        std::signal(SIGINT, stop_component);
        std::filesystem::path dir(argv[5]);
        if (!std::filesystem::create_directory(dir))
            throw std::invalid_argument("new output directory required");
        std::ofstream initial(dir / "initial.json"), queries(dir / "queries.jsonl"),
            candidates(dir / "candidates.jsonl");
        for (auto s : {&initial, &queries, &candidates})
            *s << std::setprecision(17);
        Formula f(argv[1]);
        auto prefix = read_residual_values(f, argv[6]);
        auto seeds = read_residual_values(f, argv[7]);
        if (seeds.size() != 1)
            throw std::invalid_argument("one seed");
        auto m = seeds[0];
        OnlineCoverage online(f);
        bool added = online.restore(prefix, m);
        std::deque<Assignment> history;
        std::set<std::string> seen;
        for (auto &x : prefix)
            if (seen.insert(f.key(x)).second) {
                history.push_back(x);
                if (history.size() > 4)
                    history.pop_front();
            }
        if (seen.insert(f.key(m)).second) {
            history.push_back(m);
            if (history.size() > 4)
                history.pop_front();
        }
        initial << "{\"source_sha256\":" << quote(LIA_SOURCE_SHA) << ",\"mode\":" << quote(mode)
                << ",\"seed\":" << seed << ",\"seconds\":" << session.seconds
                << ",\"seed_added\":" << (added ? "true" : "false") << ",\"seed_model\":" << f.model_json(m)
                << ",\"prefix\":[";
        for (unsigned i = 0; i < prefix.size(); ++i) {
            if (i)
                initial << ',';
            initial << f.model_json(prefix[i]);
        }
        initial << "],\"coverage\":" << online.state().json() << "}\n";
        sync_file(initial);
        double restore_seconds = session.elapsed(), init_seconds = 0;
        std::unique_ptr<Global> plain;
        std::unique_ptr<FeedbackProjection> feedback;
        std::unique_ptr<ProjectionGuidedExplorer> guided;
        unsigned calls = 0, sat = 0, positive = 0, distinct = 0;
        double stage_sum = 0;
        std::vector<double> phase_costs;
        std::string terminal = "budget_exhausted";
        for (unsigned action = 0; action < 16 && session.left() > .065 && !session.done(); ++action) {
            if (guided && !guided->available()) {
                terminal = guided->stop_reason();
                break;
            }
            double started = session.elapsed();
            Budget stage;
            stage.seconds = std::min(.25, session.left() - .025);
            stage.parent = &session;
            Budget work;
            work.start = stage.start;
            work.seconds = stage.seconds - .020;
            work.parent = &stage;
            Stats stats;
            GlobalResult q;
            ProjectionResult projection;
            std::string selection = "null", condition = "null", status = "not_checked";
            if (mode == "projection_fixed") {
                if (!feedback)
                    feedback = std::make_unique<FeedbackProjection>(f, seed);
                auto s = feedback->select(online.state(), history, work);
                selection = s.json(f);
                if (s.complete && !s.variables.empty() && !work.done()) {
                    if (!guided) {
                        auto t = Clock::now();
                        guided =
                            std::make_unique<ProjectionGuidedExplorer>(f, seed, ProjectionOptions{true, 200});
                        init_seconds += std::chrono::duration<double>(Clock::now() - t).count();
                    }
                    if (!work.done()) {
                        projection = guided->query(s.variables, history, work, stats);
                        q = projection.query;
                        condition = projection.condition_json(f);
                        status = q.status;
                    } else
                        status = "initialization_budget";
                } else
                    status = s.reason;
            } else {
                if (!plain) {
                    auto t = Clock::now();
                    plain = std::make_unique<Global>(f, seed);
                    plain->exploration_budget.fixed_levels = true;
                    init_seconds += std::chrono::duration<double>(Clock::now() - t).count();
                }
                if (!work.done()) {
                    q = plain->explore(m, work, stats, 200, true, 200);
                    status = q.status;
                } else
                    status = "initialization_budget";
            }
            calls += q.check_started;
            bool timely = false;
            double completed = -1;
            uint64_t gain = 0, obs = 0;
            if (q.model) {
                ++sat;
                ResidualWindow w;
                auto candidate = online.prepare(*q.model, w, "component", action);
                bool fresh = seen.insert(f.key(*q.model)).second;
                double validated = session.elapsed();
                if (candidate) {
                    gain = candidate->gain.covered;
                    obs = candidate->gain.observed;
                }
                candidates << "{\"action\":" << action << ",\"validated\":" << validated
                           << ",\"formal_duplicate\":" << (!candidate ? "true" : "false")
                           << ",\"internal_duplicate\":" << (candidate && !fresh ? "true" : "false")
                           << ",\"new_to_supply\":" << (fresh ? "true" : "false") << ",\"gain\":" << gain
                           << ",\"observations\":" << obs << ",\"model\":" << f.model_json(*q.model) << "}\n";
                sync_file(candidates);
                completed = session.elapsed();
                timely = completed <= started + stage.seconds && completed <= session.seconds && !interrupted;
                if (timely && fresh) {
                    ++distinct;
                    positive += gain > 0;
                }
            }
            queries << "{\"action\":" << action << ",\"started\":" << started
                    << ",\"allowance\":" << stage.seconds << ",\"status\":" << quote(status)
                    << ",\"query_seconds\":" << q.elapsed_seconds << ",\"evidence\":" << q.evidence_json()
                    << ",\"selection\":" << selection << ",\"condition\":" << condition
                    << ",\"checkpoint_completed\":" << completed
                    << ",\"timely\":" << (timely ? "true" : "false") << ",\"gain\":" << gain
                    << ",\"observations\":" << obs << ",\"stage_before_log_seconds\":" << stage.elapsed()
                    << ",\"statistics\":" << stats.json() << "}\n";
            sync_file(queries);
            phase_costs.push_back(stage.elapsed());
            phase_costs.back() = stage.elapsed();
            stage_sum += phase_costs.back();
            if (action == 15)
                terminal = "action_cap";
        }
        unsigned blocks = guided ? guided->blocked_models() : 0;
        auto cleanup = Clock::now();
        guided.reset();
        feedback.reset();
        plain.reset();
        double cleanup_seconds = std::chrono::duration<double>(Clock::now() - cleanup).count();
        std::ofstream costs(dir / "phase_costs.jsonl");
        costs << std::setprecision(17);
        for (unsigned i = 0; i < phase_costs.size(); ++i)
            costs << "{\"action\":" << i << ",\"seconds\":" << phase_costs[i] << "}\n";
        sync_file(costs);
        std::ofstream summary(dir / "summary.json");
        summary << std::setprecision(17) << "{\"source_sha256\":" << quote(LIA_SOURCE_SHA)
                << ",\"mode\":" << quote(mode) << ",\"seed\":" << seed
                << ",\"restore_seconds\":" << restore_seconds
                << ",\"initialization_seconds\":" << init_seconds << ",\"stage_seconds\":" << stage_sum
                << ",\"cleanup_seconds\":" << cleanup_seconds << ",\"checks\":" << calls << ",\"sat\":" << sat
                << ",\"timely_distinct\":" << distinct << ",\"positive\":" << positive
                << ",\"blocks\":" << blocks << ",\"session_seconds\":" << session.elapsed()
                << ",\"terminal\":" << quote(terminal) << ",\"coverage\":" << online.state().json() << "}\n";
        sync_file(summary);
        return 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
