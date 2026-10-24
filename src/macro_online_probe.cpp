#include "macro_choice.hpp"
#include "plateau_tracker.hpp"
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
using namespace lia;
namespace {
void stop(int) { interrupted = 1; }
void flush(std::ofstream &out) {
    out.flush();
    if (!out)
        throw std::runtime_error("journal write failed");
}
std::string rows(const std::vector<std::string> &xs) {
    std::string r = "[";
    for (size_t i = 0; i < xs.size(); ++i) {
        if (i)
            r += ',';
        r += xs[i];
    }
    return r + "]";
}
} // namespace
int main(int argc, char **argv) {
    try {
        if (argc != 6 && argc != 8)
            throw std::invalid_argument("macro_online_probe F micro_plain|micro_macro SECONDS RNG OUT "
                                        "[PREFIX_VALUES SEED_VALUES (one broad mechanism action)]");
        const std::string mode = argv[2];
        if (mode != "micro_plain" && mode != "micro_macro")
            throw std::invalid_argument("mode");
        double seconds = std::stod(argv[3]);
        if (!std::isfinite(seconds) || seconds <= 0)
            throw std::invalid_argument("budget");
        unsigned rng = std::stoul(argv[4]);
        bool mechanism = argc == 8;
        Budget session;
        session.seconds = seconds;
        std::signal(SIGTERM, stop);
        std::signal(SIGINT, stop);
        std::filesystem::path dir(argv[5]);
        if (!std::filesystem::create_directory(dir))
            throw std::runtime_error("new output directory required");
        std::ofstream initial(dir / "initial.json"), models(dir / "models.jsonl"),
            receipts(dir / "commits.jsonl"), checkpoints(dir / "checkpoints.jsonl"),
            rounds(dir / "rounds.jsonl"), events(dir / "events.jsonl");
        for (auto p : {&initial, &models, &receipts, &checkpoints, &rounds, &events}) {
            *p << std::setprecision(17);
            if (!*p)
                throw std::runtime_error("journal open");
        }
        Formula f(argv[1]);
        double formula_seconds = session.elapsed();
        OnlineCoverage online(f);
        Assignment current;
        std::vector<Assignment> prefix;
        Stats setup_stats;
        std::string initial_status = "not_started";
        double seed_start = session.elapsed();
        if (mechanism) {
            prefix = read_residual_values(f, argv[6]);
            auto ss = read_residual_values(f, argv[7]);
            if (ss.size() != 1)
                throw std::runtime_error("one seed");
            current = ss[0];
            initial_status = "saved_snapshot";
            online.restore(prefix, current);
        } else if (!session.done()) {
            z3::solver solver(f.ctx, z3::solver::simple());
            solver.add(f.root);
            solver.set("random_seed", rng);
            solver.set("timeout", session.milliseconds(30000));
            auto q = solver.check();
            if (q == z3::sat) {
                current = f.complete(solver.get_model());
                if (!f.validate(current))
                    throw std::logic_error("invalid initial model");
                initial_status = "sat";
            } else
                initial_status = q == z3::unsat ? "unsat" : solver.reason_unknown();
        }
        double seed_seconds = session.elapsed() - seed_start;
        uint64_t formal_count = 0, marginal_sum = 0;
        double initial_completion = -1;
        if (!mechanism && !current.empty() && !session.done()) {
            auto observation = f.evaluate(current);
            Coverage empty(f);
            auto gain = empty.gain(observation);
            models << "{\"id\":\"initial\",\"round\":-1,\"index\":1,\"gain\":" << gain.covered
                   << ",\"observations\":" << gain.observed
                   << ",\"origin\":\"initial\",\"model\":" << f.model_json(current) << "}\n";
            flush(models);
            initial_completion = session.elapsed();
            bool timely = initial_completion <= seconds;
            receipts << "{\"id\":\"initial\",\"completion\":" << initial_completion
                     << ",\"deadline\":" << seconds << ",\"timely\":" << (timely ? "true" : "false") << "}\n";
            flush(receipts);
            if (timely) {
                online.restore({}, current);
                formal_count = 1;
            } else
                current.clear();
        } else if (!mechanism)
            current.clear();
        initial << "{\"mode\":" << quote(mode) << ",\"seed\":" << rng
                << ",\"source_sha256\":" << quote(LIA_SOURCE_SHA) << ",\"seconds\":" << seconds
                << ",\"mechanism\":" << (mechanism ? "true" : "false")
                << ",\"initial_status\":" << quote(initial_status)
                << ",\"formula_seconds\":" << formula_seconds << ",\"seed_seconds\":" << seed_seconds
                << ",\"initial_completion\":" << initial_completion
                << ",\"initial_model\":" << (current.empty() ? "null" : f.model_json(current))
                << ",\"prefix\":[";
        for (size_t i = 0; i < prefix.size(); ++i) {
            if (i)
                initial << ',';
            initial << f.model_json(prefix[i]);
        }
        initial << "],\"coverage\":" << online.state().json() << "}\n";
        flush(initial);
        std::unique_ptr<ResidualGenerator> micro;
        std::unique_ptr<ChoiceCatalog> catalog;
        std::unique_ptr<MacroChoiceGenerator> macro;
        double engine_start = session.elapsed();
        if (!current.empty() && !session.done()) {
            micro = std::make_unique<ResidualGenerator>(f, rng);
            micro->options.exploration_fallback = false;
            if (mode == "micro_macro" && !session.done()) {
                Budget cb;
                cb.seconds = std::min(.1, session.left());
                cb.parent = &session;
                catalog = std::make_unique<ChoiceCatalog>(f, cb);
                if (!session.done() && !catalog->groups.empty())
                    macro = std::make_unique<MacroChoiceGenerator>(f, *catalog, rng ^ 0x9e3779b9U);
                catalog->commit(current);
                for (auto &m : prefix)
                    if (f.key(m) != f.key(current))
                        catalog->commit(m);
            }
        }
        const double engine_seconds = session.elapsed() - engine_start,
                     initialization_seconds = session.elapsed();
        std::ofstream catfile(dir / "catalog.json");
        catfile << (catalog ? catalog->json() : "null") << '\n';
        flush(catfile);
        PlateauTracker plateau;
        std::set<std::string> formal_modes;
        if (catalog && !current.empty())
            formal_modes.insert(catalog->signature(current));
        if (formal_count)
            plateau.output(initial_completion, 1, 0, catalog && !catalog->groups.empty(), "initial");
        unsigned round_index = 0, stalled = 0, rebuilds = 0, stall_events = 0;
        std::string terminal = current.empty() ? "no_initial_model" : "budget_exhausted";
        double stalled_at = -1;
        uint64_t stalled_index = 0;
        std::string error;
        try {
            while (micro && !session.done() && session.left() > .005 && (!mechanism || round_index == 0) &&
                   round_index < 10000) {
                Budget round;
                round.seconds = mechanism ? session.left() : std::min(1., session.left());
                round.parent = &session;
                double started = std::chrono::duration<double>(round.start - session.start).count();
                double deadline = std::min(seconds, started + round.seconds);
                uint64_t before = online.state().covered(), version = online.version();
                Stats stats;
                std::vector<std::string> writes;
                unsigned checkpoint_index = 0;
                std::string module;
                double module_deadline = deadline;
                auto checkpoint = [&](const ResidualCandidate &c) {
                    unsigned id = checkpoint_index++;
                    checkpoints << "{\"round\":" << round_index << ",\"checkpoint\":" << id
                                << ",\"module\":" << quote(module) << ",\"query\":" << c.query
                                << ",\"origin\":" << quote(c.origin) << ",\"version\":" << c.coverage_version
                                << ",\"gain\":" << c.gain.covered << ",\"observations\":" << c.gain.observed
                                << ",\"validated\":" << session.elapsed()
                                << ",\"model\":" << f.model_json(c.model) << "}\n";
                    flush(checkpoints);
                    double completed = session.elapsed();
                    std::ostringstream w;
                    w << std::setprecision(17) << "{\"checkpoint\":" << id << ",\"completion\":" << completed
                      << ",\"deadline\":" << deadline << ",\"module_deadline\":" << module_deadline
                      << ",\"module\":" << quote(module) << '}';
                    writes.push_back(w.str());
                    return completed;
                };
                ResidualRound mr;
                double micro_seconds = 0, broad_seconds = 0;
                std::optional<ResidualCandidate> best;
                if (!mechanism) {
                    module = "micro";
                    Budget b;
                    b.seconds = round.seconds * .4;
                    b.parent = &round;
                    double module_start = std::chrono::duration<double>(b.start - session.start).count();
                    module_deadline = std::min(deadline, module_start + b.seconds);
                    mr = micro->generate(true, online, current, b, stats,
                                         [&](const auto &c) { return checkpoint(c) - module_start; });
                    micro_seconds = b.elapsed();
                    best = mr.best;
                }
                BroadResult br;
                if (!round.done()) {
                    module = "broad";
                    Budget b;
                    b.seconds = mechanism ? round.left() * .8 : round.seconds * .4;
                    b.parent = &round;
                    double module_start = std::chrono::duration<double>(b.start - session.start).count();
                    module_deadline = std::min(deadline, module_start + b.seconds);
                    if (macro)
                        br = macro->generate(true, micro->global, online, current, b, stats,
                                             [&](const auto &c) { return checkpoint(c) - module_start; });
                    else
                        br = plain_broad(micro->global, online, current, b, stats,
                                         [&](const auto &c) { return checkpoint(c) - module_start; });
                    broad_seconds = b.elapsed();
                    if (br.best && (!best || br.best->gain.covered > best->gain.covered ||
                                    (br.best->gain.covered == best->gain.covered &&
                                     br.best->gain.observed > best->gain.observed)))
                        best = br.best;
                }
                plateau.internal(mr.target_calls - mr.assessments.size() - mr.duplicates, mr.duplicates,
                                 "micro", micro_seconds);
                plateau.internal(br.calls - br.assessments.size() - br.duplicates, br.duplicates, "broad",
                                 broad_seconds);
                bool committed = false, changed = false, new_mode = false;
                double completion = -1;
                std::string id, origin;
                if (best && !round.done()) {
                    ResidualWindow empty;
                    empty.coverage_version = version;
                    auto checked = online.prepare(best->model, empty, best->origin, best->query);
                    if (checked && !round.done()) {
                        id = "round-" + std::to_string(round_index);
                        origin = checked->origin;
                        if (catalog) {
                            auto signature = catalog->signature(checked->model);
                            changed = signature != catalog->signature(current);
                            new_mode = !formal_modes.count(signature);
                        }
                        models << "{\"id\":" << quote(id) << ",\"round\":" << round_index
                               << ",\"index\":" << formal_count + 1 << ",\"gain\":" << checked->gain.covered
                               << ",\"observations\":" << checked->gain.observed
                               << ",\"origin\":" << quote(origin)
                               << ",\"model\":" << f.model_json(checked->model) << "}\n";
                        flush(models);
                        completion = session.elapsed();
                        bool timely = completion <= deadline;
                        receipts << "{\"id\":" << quote(id) << ",\"completion\":" << completion
                                 << ",\"deadline\":" << deadline
                                 << ",\"timely\":" << (timely ? "true" : "false") << "}\n";
                        flush(receipts);
                        if (timely) {
                            committed = online.commit(*checked, completion, deadline);
                            if (!committed)
                                throw std::logic_error("commit");
                            ++formal_count;
                            marginal_sum += checked->gain.covered;
                            current = checked->model;
                            if (catalog) {
                                formal_modes.insert(catalog->signature(current));
                                catalog->commit(current);
                            }
                            plateau.output(completion, formal_count, checked->gain.covered, new_mode, origin);
                        }
                    }
                }
                uint64_t gain = online.state().covered() - before;
                stalled = gain ? 0 : stalled + 1;
                if (gain && stalled_at >= 0) {
                    events << "{\"kind\":\"recovery\",\"time\":" << completion
                           << ",\"since_stall\":" << completion - stalled_at
                           << ",\"new_models\":" << formal_count - stalled_index
                           << ",\"mode_changed\":" << (changed ? "true" : "false") << ",\"gain\":" << gain
                           << ",\"origin\":" << quote(origin) << "}\n";
                    flush(events);
                    stalled_at = -1;
                }
                if (stalled && stalled % 8 == 0) {
                    ++stall_events;
                    double handling_start = session.elapsed();
                    if (stalled_at < 0) {
                        stalled_at = handling_start;
                        stalled_index = formal_count;
                    }
                    bool rebuilt = false;
                    // The same bounded treatment in both arms. No unlimited rebuilds.
                    if (rebuilds < 2 && round.left() > .005) {
                        micro->global.rebuild_exploration(stats);
                        ++rebuilds;
                        rebuilt = true;
                    }
                    events << "{\"kind\":\"stagnation\",\"time\":" << handling_start
                           << ",\"stalled_rounds\":" << stalled
                           << ",\"rebuilt\":" << (rebuilt ? "true" : "false")
                           << ",\"handling_seconds\":" << session.elapsed() - handling_start << "}\n";
                    flush(events);
                }
                if (marginal_sum != online.state().covered() - online.initial_covered())
                    throw std::logic_error("telescoping");
                rounds << "{\"round\":" << round_index << ",\"started\":" << started
                       << ",\"deadline\":" << deadline << ",\"budget\":" << round.seconds
                       << ",\"version_before\":" << version << ",\"version_after\":" << online.version()
                       << ",\"covered_before\":" << before
                       << ",\"covered_after\":" << online.state().covered() << ",\"gain\":" << gain
                       << ",\"committed\":" << (committed ? "true" : "false")
                       << ",\"mode_changed\":" << (catalog ? (changed ? "true" : "false") : "null")
                       << ",\"micro_seconds\":" << micro_seconds << ",\"broad_seconds\":" << broad_seconds
                       << ",\"full_seconds\":" << round.elapsed() << ",\"micro_calls\":" << mr.target_calls
                       << ",\"broad_calls\":" << br.calls << ",\"macro_calls\":" << br.macro_calls
                       << ",\"fallback_calls\":" << br.fallback_calls
                       << ",\"micro_queries\":" << rows(mr.queries)
                       << ",\"broad_queries\":" << rows(br.queries)
                       << ",\"micro_assessments\":" << rows(mr.assessments)
                       << ",\"broad_assessments\":" << rows(br.assessments)
                       << ",\"checkpoint_writes\":" << rows(writes) << ",\"micro_best_gain\":"
                       << (mr.best ? std::to_string(mr.best->gain.covered) : "null")
                       << ",\"broad_best_gain\":"
                       << (br.best ? std::to_string(br.best->gain.covered) : "null")
                       << ",\"first_broad_positive_seconds\":" << br.first_positive_seconds
                       << ",\"duplicates\":" << mr.duplicates + br.duplicates
                       << ",\"late_candidates\":" << mr.late + br.late << ",\"statistics\":" << stats.json()
                       << "}\n";
                flush(rounds);
                ++round_index;
            }
            if (mechanism)
                terminal = "single_broad_action";
            else if (interrupted)
                terminal = "cancelled";
            else if (round_index >= 10000)
                terminal = "safety_round_cap";
        } catch (const std::exception &e) {
            terminal = "exception";
            error = e.what();
        }
        plateau.finish(std::min(seconds, session.elapsed()), formal_count);
        std::ofstream pf(dir / "plateau.json");
        pf << plateau.json() << '\n';
        flush(pf);
        std::ofstream cf(dir / "catalog_final.json");
        cf << (catalog ? catalog->json() : "null") << '\n';
        flush(cf);
        std::ofstream summary(dir / "summary.json");
        summary << std::setprecision(17) << "{\"mode\":" << quote(mode) << ",\"rng_seed\":" << rng
                << ",\"source_sha256\":" << quote(LIA_SOURCE_SHA) << ",\"budget_seconds\":" << seconds
                << ",\"initialization_seconds\":" << initialization_seconds
                << ",\"engine_seconds\":" << engine_seconds << ",\"session_seconds\":" << session.elapsed()
                << ",\"rounds\":" << round_index << ",\"formal_count\":" << formal_count
                << ",\"gain\":" << marginal_sum << ",\"initial_covered\":" << online.initial_covered()
                << ",\"coverage\":" << online.state().json() << ",\"stall_events\":" << stall_events
                << ",\"unrecovered_stall\":" << (stalled_at >= 0 ? "true" : "false")
                << ",\"rebuilds\":" << rebuilds << ",\"terminal\":" << quote(terminal)
                << ",\"error\":" << quote(error) << "}\n";
        flush(summary);
        return error.empty() ? 0 : 1;
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
