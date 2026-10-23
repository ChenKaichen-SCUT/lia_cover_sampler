#include "core.hpp"
#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>

using namespace lia;
namespace fs = std::filesystem;
struct Options {
    std::string input, output = ".", mode = "benchmark", variant = "full", audit;
    unsigned n = 1000, seed = 0;
    double seconds = 900;
};
void signal_handler(int) { interrupted = 1; }
Options parse(int argc, char **argv) {
    Options a;
    for (int i = 1; i < argc; ++i) {
        std::string k = argv[i];
        if (k == "-h" || k == "--help") {
            std::cout << "lia_cover_sampler -i FILE -o DIR [-n 1000] [-t 900] [-s 0] [--mode "
                         "benchmark|debug|trace] [--variant full|random|no-cache|global-only] "
                         "[--audit-values FILE]\n";
            std::exit(0);
        }
        if (i + 1 >= argc)
            throw std::invalid_argument("missing option value");
        std::string v = argv[++i];
        if (k == "-i")
            a.input = v;
        else if (k == "-o")
            a.output = v;
        else if (k == "-n")
            a.n = std::stoul(v);
        else if (k == "-t")
            a.seconds = std::stod(v);
        else if (k == "-s")
            a.seed = std::stoul(v);
        else if (k == "--mode")
            a.mode = v;
        else if (k == "--variant")
            a.variant = v;
        else if (k == "--audit-values")
            a.audit = v;
        else
            throw std::invalid_argument("unknown option " + k);
    }
    if (a.input.empty() || !std::isfinite(a.seconds) || a.seconds <= 0)
        throw std::invalid_argument("input and positive finite time required");
    if (a.mode != "benchmark" && a.mode != "debug" && a.mode != "trace")
        throw std::invalid_argument("invalid mode");
    if (a.variant != "full" && a.variant != "random" && a.variant != "no-cache" && a.variant != "global-only")
        throw std::invalid_argument("invalid variant");
    return a;
}
int main(int argc, char **argv) {
    Budget budget;
    Options opt;
    Assignment current;
    std::unique_ptr<Formula> f;
    std::unique_ptr<Global> global;
    std::string last_context = "{}";
    Stats stats;
    try {
        opt = parse(argc, argv);
        budget.seconds = opt.seconds;
        std::signal(SIGINT, signal_handler);
        std::signal(SIGTERM, signal_handler);
        {
            Timer t(stats, "parse");
            f = std::make_unique<Formula>(opt.input);
        }
        Coverage coverage(*f);
        if (!opt.audit.empty()) {
            std::ifstream in(opt.audit);
            if (!in)
                throw std::runtime_error("cannot read audit values");
            std::string line;
            while (std::getline(in, line)) {
                Assignment m;
                std::istringstream row(line);
                std::string x;
                while (row >> x) {
                    if (x == "true")
                        m.emplace_back(1);
                    else if (x == "false")
                        m.emplace_back(0);
                    else
                        m.emplace_back(x);
                }
                if (!f->validate(m))
                    throw std::runtime_error("audit assignment violates formula");
                auto o = f->evaluate(m, true);
                coverage.accept(o);
                std::cout << coverage.json(&o) << "\n";
            }
            return 0;
        }
        fs::create_directories(opt.output);
        if (fs::exists(fs::path(opt.output) / "summary.json") ||
            fs::exists(fs::path(opt.output) / "models.jsonl"))
            throw std::runtime_error("output directory already contains a run");
        std::ofstream typed(fs::path(opt.output) / "models.jsonl"), samples, trace;
        std::ofstream emission(fs::path(opt.output) / "emission.csv");
        if (!typed)
            throw std::runtime_error("cannot open model output");
        if (f->legacy_compatible)
            samples.open(fs::path(opt.output) / (fs::path(opt.input).filename().string() + ".samples"));
        if (opt.mode != "benchmark")
            trace.open(fs::path(opt.output) / "trace.jsonl");
        auto log = [&](std::string s) {
            last_context = s;
            if (trace.is_open()) {
                trace << s << '\n';
                trace.flush();
            }
        };
        std::ofstream(fs::path(opt.output) / "format.json")
            << "{\"legacy_compatible\":" << (f->legacy_compatible ? "true" : "false")
            << ",\"legacy_reason\":" << quote(f->legacy_reason)
            << ",\"metric_compatible\":" << (f->metric_compatible ? "true" : "false")
            << ",\"metric_reason\":" << quote(f->metric_reason) << "}\n";
        std::set<std::string> emitted;
        std::vector<Assignment> seed_bank;
        unsigned count = 0;
        double last_checkpoint = 0;
        auto checkpoint = [&]() {
            if (budget.elapsed() - last_checkpoint < 1)
                return;
            auto temporary = fs::path(opt.output) / "progress.tmp";
            {
                std::ofstream out(temporary);
                out << "{\"status\":\"running_checkpoint\",\"samples\":" << count
                    << ",\"elapsed\":" << budget.elapsed() << ",\"covered\":" << coverage.covered()
                    << ",\"total\":" << f->total_bits() << ",\"statistics\":" << stats.json()
                    << ",\"source_sha256\":" << quote(LIA_SOURCE_SHA) << "}\n";
                if (!out)
                    throw std::runtime_error("checkpoint write failed");
            }
            fs::rename(temporary, fs::path(opt.output) / "progress.json");
            last_checkpoint = budget.elapsed();
        };
        ProgressFeedback round_feedback;
        auto accept = [&](const Assignment &m, const std::string &category) -> AcceptResult {
            if (count >= opt.n || budget.done())
                return {};
            auto key = f->key(m);
            if (emitted.count(key)) {
                ++stats.counts["duplicate_output_assignment"];
                return {};
            }
            {
                Timer t(stats, "validation");
                if (!f->validate(m)) {
                    current = m;
                    throw std::logic_error("output model violates original formula");
                }
                if (global && opt.mode == "debug")
                    global->validate_conflicts(m);
            }
            Observation o;
            {
                Timer t(stats, "coverage_scoring");
                o = f->evaluate(m);
            }
            auto g = coverage.gain(o);
            if (budget.done()) return {};
            Timer t(stats, "write");
            double time = budget.elapsed();
            std::ostringstream line;
            line << "{\"index\":" << count << ",\"elapsed\":" << std::setprecision(10) << time
                 << ",\"category\":" << quote(category) << ",\"new_coverage\":" << g.covered
                 << ",\"new_observations\":" << g.observed
                 << ",\"covered\":" << coverage.covered() + g.covered << ",\"total\":" << f->total_bits()
                 << ",\"assignment\":" << f->model_json(m) << "}\n";
            typed << line.str();
            typed.flush();
            if (!typed)
                throw std::runtime_error("model output write failed");
            if (samples.is_open()) {
                std::ostringstream legacy;
                legacy << count << ": ";
                for (size_t i = 0; i < m.size(); ++i)
                    legacy << f->nodes[f->variables[i]].expr.decl().name().str() << ":" << m[i] << ";";
                legacy << '\n';
                samples << legacy.str();
                samples.flush();
                if (!samples)
                    throw std::runtime_error("legacy output write failed");
            }
            emission << count << "," << std::setprecision(10) << budget.elapsed() << '\n';
            emission.flush();
            if (!emission)
                throw std::runtime_error("timestamp write failed");
            coverage.accept(o);
            emitted.insert(key);
            if (seed_bank.size() == 16)
                seed_bank.erase(seed_bank.begin());
            seed_bank.push_back(m);
            ++count;
            ++stats.counts["output_" + category];
            AcceptResult result{true, g.covered, g.observed};
            round_feedback.add(result);
            return result;
        };
        std::string status = "sample_limit";
        z3::solver initial(f->ctx);
        initial.add(f->root);
        initial.set("random_seed", opt.seed);
        if (opt.n) {
            Timer t(stats, "initial_solve");
            unsigned timeout = 100;
            while (!budget.done()) {
                initial.set("timeout", budget.milliseconds(timeout));
                ++stats.counts["initial_calls"];
                auto res = initial.check();
                if (res == z3::sat) {
                    current = f->complete(initial.get_model());
                    status = "running";
                    break;
                }
                if (res == z3::unsat) {
                    status = "unsat";
                    break;
                }
                status = "initial_unknown";
                ++stats.counts["initial_unknown"];
                log("{\"kind\":\"initial_unknown\",\"reason\":" + quote(initial.reason_unknown()) + "}");
                timeout = std::min(10000U, timeout * 2);
            }
            if (status == "sample_limit")
                status = "time_limit_without_model";
        }
        std::mt19937_64 rng(opt.seed);
        if (status == "running") {
            accept(current, "initial");
            global = std::make_unique<Global>(*f, opt.seed);
            global->use_cache = opt.variant != "no-cache";
            if (trace.is_open())
                global->trace = log;
            std::vector<Assignment> cached_directions;
            std::string cached_signature;
            unsigned round = 0, stalled = 0;
            double recent_local_gain = 0, recent_local_cost = 0;
            while (count < opt.n && !budget.done()) {
                ++round;
                round_feedback = {};
                std::vector<Target> targets;
                {
                    Timer target_timer(stats, "active_targets");
                    targets = active_targets(*f, coverage, rng, 48);
                }
                bool progress = false;
                bool do_local = opt.variant != "global-only" && round % 5 != 0 &&
                                (stalled < 3 || round % 3 == 0) &&
                                (recent_local_cost < 0.1 || recent_local_gain >= 1 || round % 3 == 0);
                if (do_local) {
                    auto batch_start = Clock::now();
                    Observation seed_observation;
                    {
                        Timer t(stats, "coverage_scoring");
                        seed_observation = f->evaluate(current);
                    }
                    std::unique_ptr<Region> r;
                    {
                        Timer t(stats, "region");
                        r = std::make_unique<Region>(*f, current, seed_observation, budget);
                    }
                    if (r->supported) {
                        if (opt.mode == "debug" && !budget.done()) {
                            z3::solver proof(f->ctx);
                            proof.add(r->expression() && !f->root);
                            proof.set("timeout", budget.milliseconds(200));
                            auto pr = proof.check();
                            if (pr == z3::sat)
                                throw std::logic_error("region implication is false");
                            ++stats.counts[pr == z3::unsat ? "region_proved" : "region_proof_unknown"];
                        }
                        auto sig = r->signature();
                        if (sig != cached_signature || round % 4 == 1 || cached_directions.empty()) {
                            cached_directions = directions(*r, rng, stats);
                            cached_signature = sig;
                        } else
                            ++stats.counts["direction_cache_hit"];
                        std::optional<Assignment> best;
                        Gain best_gain;
                        size_t tried = 0;
                        auto working_directions = cached_directions;
                        for (const auto &seed : seed_bank) {
                            Assignment d(current.size());
                            Z divisor = 0;
                            for (size_t i = 0; i < d.size(); ++i) {
                                d[i] = seed[i] - current[i];
                                divisor = gcd(divisor, d[i]);
                            }
                            if (divisor == 0 || !r->kernel(d))
                                continue;
                            for (auto &v : d)
                                v /= divisor;
                            if (std::find(working_directions.begin(), working_directions.end(), d) ==
                                working_directions.end()) {
                                working_directions.push_back(d);
                                ++stats.counts["checked_model_difference_direction"];
                            }
                        }
                        std::shuffle(working_directions.begin(), working_directions.end(), rng);
                        for (auto &d : working_directions) {
                            if (budget.done() || tried >= 32 ||
                                std::chrono::duration<double>(Clock::now() - batch_start).count() > 0.25)
                                break;
                            Interval range;
                            {
                                Timer t(stats, "interval");
                                range = r->interval(current, d);
                            }
                            if (range.lo && range.hi && *range.lo == 0 && *range.hi == 0) {
                                ++stats.counts["interval_only_current_point"];
                                continue;
                            }
                            for (auto target : targets) {
                                if (budget.done() || tried >= 32)
                                    break;
                                if (f->nodes[target.node].boolean)
                                    continue;
                                Affine a;
                                try {
                                    a = r->linear(target.node, false);
                                } catch (const std::domain_error &) {
                                    continue;
                                }
                                Z alpha = a.dot(d), beta = a.at(current);
                                if (alpha == 0) {
                                    ++stats.counts["target_constant_along_direction"];
                                    continue;
                                }
                                auto [L, U] = range.finite(alpha, target.bit);
                                std::optional<Z> z;
                                {
                                    Timer t(stats, "mod_hit");
                                    if (opt.variant == "random")
                                        z = L + random_below(Z(U - L + 1), rng);
                                    else {
                                        Z hits =
                                            count_bit_hits(alpha, beta, target.bit, target.desired, L, U);
                                        if (hits > 0)
                                            z = find_bit_hit(alpha, beta, target.bit, target.desired, L, U,
                                                             random_below(hits, rng));
                                    }
                                }
                                if (!z) {
                                    ++stats.counts["target_numerically_unreachable_in_interval"];
                                    continue;
                                }
                                bool arithmetic_hit = bit(Z(beta + alpha * *z), target.bit) == target.desired;
                                if (opt.variant != "random" && !arithmetic_hit)
                                    throw std::logic_error("modular witness mismatch");
                                Assignment candidate = current;
                                for (size_t i = 0; i < d.size(); ++i)
                                    candidate[i] += d[i] * *z;
                                ++tried;
                                ++stats.counts["local_candidates"];
                                Observation observation;
                                {
                                    Timer t(stats, "coverage_scoring");
                                    observation = f->evaluate(candidate);
                                }
                                if (observation.values[0] != 1) {
                                    current = candidate;
                                    throw std::logic_error("local candidate violates original formula");
                                }
                                auto gain = coverage.gain(observation);
                                bool reached = observation.visited[target.node];
                                if (arithmetic_hit && !reached)
                                    ++stats.counts["numeric_hit_unobserved"];
                                if (!gain.covered)
                                    ++stats.counts["legal_candidate_no_coverage_gain"];
                                bool duplicate = emitted.count(f->key(candidate));
                                if (duplicate)
                                    ++stats.counts["duplicate_candidate_assignment"];
                                if (trace.is_open()) {
                                    std::ostringstream s;
                                    s << "{\"kind\":\"local\",\"round\":" << round
                                      << ",\"seed\":" << f->model_json(current)
                                      << ",\"target\":" << quote(target.key())
                                      << ",\"alpha\":" << quote(alpha.get_str())
                                      << ",\"beta\":" << quote(beta.get_str())
                                      << ",\"L\":" << (range.lo ? quote(range.lo->get_str()) : "null")
                                      << ",\"U\":" << (range.hi ? quote(range.hi->get_str()) : "null")
                                      << ",\"z\":" << quote(z->get_str())
                                      << ",\"arithmetic_bit_hit\":" << (arithmetic_hit ? "true" : "false")
                                      << ",\"observation_reached\":" << (reached ? "true" : "false")
                                      << ",\"actual_new_coverage\":" << gain.covered
                                      << ",\"direction_source\":\"exact_kernel_or_coordinate_combination\","
                                         "\"Ed_zero\":true,\"direction\":[";
                                    for (size_t i = 0; i < d.size(); ++i) {
                                        if (i)
                                            s << ',';
                                        s << quote(d[i].get_str());
                                    }
                                    s << "],\"region\":" << quote(r->expression().to_string()) << "}";
                                    log(s.str());
                                }
                                if (!duplicate && (!best || gain.covered > best_gain.covered ||
                                                   (gain.covered == best_gain.covered &&
                                                    gain.observed > best_gain.observed))) {
                                    best = candidate;
                                    best_gain = gain;
                                }
                            }
                        }
                        if (best) {
                            if (best_gain.covered || best_gain.observed || stalled >= 2) {
                                current = *best;
                                accept(current, best_gain.covered    ? "local_gain"
                                                : best_gain.observed ? "local_observation"
                                                                     : "local_fill");
                                progress = round_feedback.coverage > 0;
                            } else {
                                if (seed_bank.size() == 16)
                                    seed_bank.erase(seed_bank.begin());
                                seed_bank.push_back(*best);
                            }
                        }
                        recent_local_gain = 0.7 * recent_local_gain + 0.3 * best_gain.covered;
                        recent_local_cost =
                            0.7 * recent_local_cost +
                            0.3 * std::chrono::duration<double>(Clock::now() - batch_start).count();
                        if (!progress && recent_local_cost > 0.1 && recent_local_gain < 1)
                            ++stats.counts["local_analysis_cost_dominates"];
                    } else {
                        ++stats.counts["local_unsupported"];
                        log("{\"kind\":\"local_unsupported\",\"reason\":" + quote(r->reason) + "}");
                    }
                }
                if (count >= opt.n || budget.done())
                    break;
                if (!progress || !do_local) {
                    auto res = f->metric_compatible ? global->query(targets, budget, stats)
                                                    : GlobalResult{{}, "metric_incompatible", "", {}};
                    if (res.model) {
                        if (!f->validate(*res.model)) {
                            current = *res.model;
                            throw std::logic_error("global model invalid");
                        }
                        auto o = f->evaluate(*res.model);
                        auto g = coverage.gain(o);
                        current = *res.model;
                        if (g.covered || g.observed || stalled >= 2) {
                            auto added = accept(current, g.covered    ? "global_gain"
                                                         : g.observed ? "global_observation"
                                                                      : "global_fill");
                            (void)added;
                            progress = round_feedback.coverage > 0;
                        } else {
                            if (seed_bank.size() == 16)
                                seed_bank.erase(seed_bank.begin());
                            seed_bank.push_back(current);
                        }
                    }
                    if (!progress && stalled >= 2 && !budget.done()) {
                        auto e = global->explore(current, budget, stats);
                        if (e.model) {
                            current = *e.model;
                            accept(current, "exploration");
                        } else if (e.status == "exploration_exhausted") {
                            status = e.status;
                            break;
                        } else if (!seed_bank.empty())
                            current = seed_bank[rng() % seed_bank.size()];
                    }
                }
                progress = round_feedback.coverage > 0;
                if (progress) ++stats.counts["coverage_progress_rounds"];
                stalled = round_feedback.next_stalled(stalled);
                checkpoint();
            }
            if (status == "running")
                status = count >= opt.n ? "sample_limit" : interrupted ? "interrupted" : "time_limit";
        }
        if (interrupted)
            status = "interrupted";
        std::ofstream summary(fs::path(opt.output) / "summary.json");
        summary << "{\"status\":" << quote(status) << ",\"samples\":" << count << ",\"requested\":" << opt.n
                << ",\"elapsed\":" << budget.elapsed() << ",\"seed\":" << opt.seed
                << ",\"variant\":" << quote(opt.variant) << ",\"mode\":" << quote(opt.mode)
                << ",\"metric_compatible\":" << (f->metric_compatible ? "true" : "false")
                << ",\"covered\":" << coverage.covered() << ",\"total\":" << f->total_bits()
                << ",\"z3\":" << quote(Z3_get_full_version()) << ",\"version\":" << quote(LIA_VERSION)
                << ",\"source_sha256\":" << quote(LIA_SOURCE_SHA) << ",\"statistics\":" << stats.json()
                << "}\n";
        std::cout << status << ": " << count << " samples, " << coverage.covered() << "/" << f->total_bits()
                  << " covered bits, " << budget.elapsed() << " s\n";
        return interrupted ? 130 : 0;
    } catch (const std::exception &e) {
        std::cerr << "error: " << e.what() << '\n';
        if (!opt.input.empty()) {
            fs::path out = fs::path(opt.output) / "replay";
            fs::create_directories(out);
            fs::copy_file(opt.input, out / "input.smt2", fs::copy_options::overwrite_existing);
            std::ofstream config(out / "failure.json");
            config << "{\"error\":" << quote(e.what()) << ",\"seed\":" << opt.seed
                   << ",\"variant\":" << quote(opt.variant) << ",\"seconds\":" << opt.seconds
                   << ",\"samples\":" << opt.n << ",\"z3\":" << quote(Z3_get_full_version())
                   << ",\"version\":" << quote(LIA_VERSION) << ",\"source_sha256\":" << quote(LIA_SOURCE_SHA)
                   << ",\"context\":" << last_context << "}\n";
            if (f && current.size() == f->variables.size()) {
                std::ofstream values(out / "values.txt");
                for (auto &v : current)
                    values << v << ' ';
                values << '\n';
                std::ofstream(out / "model.json") << f->model_json(current) << '\n';
            }
            if (global) {
                std::ofstream(out / "query.smt2") << global->query_smt2();
                if (global->invalid_exploration_model) {
                    std::ofstream(out / "exploration_candidate.json") << f->model_json(*global->invalid_exploration_model);
                    std::ofstream(out / "exploration_query.smt2") << global->exploration_query_smt2();
                }
            }
            if (fs::exists(fs::path(opt.output)/"config.json"))
                fs::copy_file(fs::path(opt.output)/"config.json",out/"config.json",fs::copy_options::overwrite_existing);
        }
        return 2;
    }
}
