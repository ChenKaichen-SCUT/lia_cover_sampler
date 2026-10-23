#include "core.hpp"
#include "scheduler.hpp"
#include "search.hpp"
#include "wall.hpp"
#include <thread>
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
    bool adaptive = true, new_generation = true, work_conserving = true;
    std::string profile = "full", disable;
    SearchOptions search;
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
        else if (k == "--profile") a.profile = v;
        else if (k == "--disable") a.disable = v;
        else if (k == "--direction-cap") a.search.direction_cap = std::stoul(v);
        else if (k == "--candidate-cap") a.search.candidate_cap = std::stoul(v);
        else if (k == "--attempt-cap") a.search.attempt_cap = std::stoul(v);
        else if (k == "--polish-step-candidates") a.search.polish_step_candidates = std::stoul(v);
        else if (k == "--polish-flat-steps") a.search.polish_flat_steps = std::stoul(v);
        else if (k == "--polish-steps") a.search.polish_steps = std::stoul(v);
        else if (k == "--polish-ms") a.search.polish_seconds = std::stod(v) / 1000;
        else if (k == "--scheduler") {
            if (v != "legacy" && v != "adaptive") throw std::invalid_argument("invalid scheduler");
            a.adaptive = v == "adaptive";
        }
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
    if (a.profile != "schedule" && a.profile != "directions" && a.profile != "polish" &&
        a.profile != "line" && a.profile != "full") throw std::invalid_argument("invalid profile");
    a.new_generation = a.profile != "schedule";
    if (!a.new_generation) a.search.quotas = a.search.fair = a.search.matching = a.search.polish = false;
    if (a.profile == "directions") a.search.polish = false;
    a.search.line = a.profile == "line" || a.profile == "full";
    a.search.repair = a.profile == "full";
    a.search.random = a.variant == "random";
    std::istringstream disabled(a.disable); std::string name;
    while (std::getline(disabled,name,',')) {
        if (name == "quotas") a.search.quotas = false;
        else if (name == "fair") a.search.fair = false;
        else if (name == "matching") a.search.matching = false;
        else if (name == "polish") a.search.polish = false;
        else if (name == "line") a.search.line = false;
        else if (name == "repair") a.search.repair = false;
        else if (name == "scheduler") a.adaptive = false;
        else if (name == "work_conserving") a.work_conserving = false;
        else if (name == "streaming") a.search.streaming = false;
        else if (name == "directional_targets") a.search.directional_targets = false;
        else if (name == "polish_split") a.search.polish_split = false;
        else if (name == "rotation_fix") a.search.rotation_fix = false;
        else if (!name.empty()) throw std::invalid_argument("unknown disabled feature " + name);
    }
    if (a.variant == "global-only") a.search.polish = a.search.line = false;
    if (!a.search.direction_cap || a.search.direction_cap > 256 || !a.search.candidate_cap ||
        a.search.candidate_cap > 4096 || !a.search.attempt_cap || a.search.attempt_cap > 16384 ||
        !a.search.polish_step_candidates || a.search.polish_step_candidates>4096 ||
        !a.search.polish_flat_steps || a.search.polish_flat_steps>128 ||
        a.search.polish_steps > 128 || !std::isfinite(a.search.polish_seconds) ||
        a.search.polish_seconds < 0 || a.search.polish_seconds > 5)
        throw std::invalid_argument("search budget outside supported bounds");
    return a;
}
int main(int argc, char **argv) {
    Budget budget;
    WallAccounting wall([&] { return budget.elapsed(); });
    Options opt;
    Assignment current;
    std::unique_ptr<Formula> f;
    std::unique_ptr<Global> global;
    std::unique_ptr<Search> search;
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
        std::ofstream(fs::path(opt.output)/"config.json") << "{\"profile\":" << quote(opt.profile)
            << ",\"scheduler\":" << quote(opt.adaptive ? "adaptive" : "legacy")
            << ",\"work_conserving\":" << opt.work_conserving
            << ",\"quotas\":" << opt.search.quotas << ",\"fair\":" << opt.search.fair
            << ",\"matching\":" << opt.search.matching << ",\"polish\":" << opt.search.polish
            << ",\"streaming\":" << opt.search.streaming << ",\"directional_targets\":" << opt.search.directional_targets
            << ",\"line\":" << opt.search.line << ",\"repair\":" << opt.search.repair
            << ",\"direction_cap\":" << opt.search.direction_cap << ",\"candidate_cap\":" << opt.search.candidate_cap
            << ",\"attempt_cap\":" << opt.search.attempt_cap << ",\"polish_steps\":" << opt.search.polish_steps
            << ",\"polish_split\":" << opt.search.polish_split << ",\"rotation_fix\":" << opt.search.rotation_fix
            << ",\"polish_step_candidates\":" << opt.search.polish_step_candidates
            << ",\"polish_flat_steps\":" << opt.search.polish_flat_steps
            << ",\"polish_seconds\":" << opt.search.polish_seconds
            << ",\"line_interval_limit\":" << opt.search.line_limits.intervals
            << ",\"line_dag_limit\":" << opt.search.line_limits.dag_nodes
            << ",\"line_affine_term_limit\":" << opt.search.line_limits.affine_terms
            << ",\"line_integer_bits\":" << opt.search.line_limits.integer_bits
            << ",\"repair_max_calls\":4,\"repair_max_expansions\":2}\n";
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
            wall.enter("checkpoint_and_logging_seconds");
            auto temporary = fs::path(opt.output) / "progress.tmp";
            {
                std::ofstream out(temporary);
                out << "{\"status\":\"running_checkpoint\",\"samples\":" << count
                    << ",\"elapsed\":" << budget.elapsed() << ",\"covered\":" << coverage.covered()
                    << ",\"total\":" << f->total_bits() << ",\"statistics\":" << stats.json()
                    << ",\"wall_timing\":" << wall.json()
                    << ",\"source_sha256\":" << quote(LIA_SOURCE_SHA) << "}\n";
                if (!out)
                    throw std::runtime_error("checkpoint write failed");
            }
            fs::rename(temporary, fs::path(opt.output) / "progress.json");
            last_checkpoint = budget.elapsed();
            wall.enter("other_wall_seconds");
        };
        std::mt19937_64 rng(opt.seed);
        ProgressFeedback round_feedback;
        Budget *active_budget = &budget;
        std::string parent_seed_id = "none", seed_origin, seed_id;
        PolishResult refinement;
        unsigned polish_flat = 0;
        double polish_cooldown = 0;
        std::array<double,4> recent_quality{};
        auto already_emitted = [&](const std::string &key) { return emitted.count(key) != 0; };
        auto commit = [&](const Assignment &m, const std::string &category) -> AcceptResult {
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
                 << ",\"category\":" << quote(category)
                 << ",\"seed_origin\":" << quote(seed_origin)
                 << ",\"seed_id\":" << quote(seed_id) << ",\"parent_seed_id\":" << quote(parent_seed_id)
                 << ",\"gain_before_refinement\":" << refinement.before.covered
                 << ",\"gain_after_refinement\":" << refinement.after.covered
                 << ",\"refinement_seconds\":" << refinement.seconds
                 << ",\"seed_gain_retained\":" << refinement.retained
                 << ",\"refinement_added\":" << refinement.added
                 << ",\"seed_gain_lost\":" << refinement.lost
                 << ",\"final_emitted_gain\":" << g.covered
                 << ",\"refinement_modules\":[";
            for (size_t i=0;i<refinement.modules.size();++i) {
                if (i) line << ',';
                line << quote(refinement.modules[i]);
            }
            line << "],\"new_coverage\":" << g.covered
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
        auto accept = [&](const Assignment &seed, const std::string &category) -> AcceptResult {
            if (budget.done() || count>=opt.n) return {};
            seed_id = assignment_id(*f, seed);
            seed_origin = category.rfind("line",0)==0 ? "local_line" : category.rfind("local",0)==0 ? "local_safe" :
                          category.rfind("global",0)==0 ? "target" : category;
            refinement = {};
            refinement.model = seed;
            refinement.before = refinement.after = coverage.gain(f->evaluate(seed));
            refinement.retained = refinement.before.covered;
            if (search && opt.search.polish && category != "initial" &&
                budget.elapsed() >= polish_cooldown && active_budget->left() > .002) {
                Budget polish_budget; polish_budget.parent = active_budget;
                unsigned origin=seed_origin=="local_safe" ? 0 : seed_origin=="local_line" ? 1 :
                                seed_origin=="target" ? 2 : 3;
                double quality=recent_quality[origin];
                // High-quality seeds need less refinement effort; low-quality seeds
                // receive the full bounded allowance. Zero outputs never imply infinite Q.
                double effort=quality>0 ? std::clamp(quality/(1+refinement.before.covered),.25,1.0) : 1.0;
                polish_budget.seconds = std::min(active_budget->left(), opt.search.polish_seconds*effort);
                stats.counts["polish_quality_guided_calls"]+=quality>0;
                refinement = search->polish_candidate(seed,coverage,polish_budget,rng,stats,already_emitted);
                if (refinement.after.covered > refinement.before.covered) polish_flat = 0;
                else if (++polish_flat >= 3) polish_cooldown = budget.elapsed() + std::min(1.0, .02*polish_flat);
            }
            current = refinement.model;
            return commit(current,category);
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
        if (status == "running") {
            accept(current, "initial");
            global = std::make_unique<Global>(*f, opt.seed);
            global->use_cache = opt.variant != "no-cache";
            if (opt.new_generation) {
                Timer setup(stats, "target_manager_setup");
                search = std::make_unique<Search>(*f);
                search->options = opt.search;
                search->targets.rotation_fix = opt.search.rotation_fix;
                search->targets.globally_unreachable = [&](Target target) { return global->unreachable(target); };
            }
            if (trace.is_open())
                global->trace = log;
            std::vector<Assignment> cached_directions;
            std::string cached_signature;
            Scheduler scheduler;
            scheduler.work_conserving = opt.work_conserving;
            unsigned round = 0, stalled = 0;
            bool exploration_exhausted=false;
            unsigned exhausted_at_count=0;
            double recent_local_gain = 0, recent_local_cost = 0;
            while (count < opt.n && !budget.done()) {
                wall.enter("scheduler_decision_seconds");
                ++round;
                round_feedback = {};
                std::optional<ActionSelection> selection;
                if (opt.adaptive) {
                    // An UNSAT blocked context cannot execute useful work again until
                    // another action supplies new output. Never loop on identical rebuilds.
                    if(exploration_exhausted && count>exhausted_at_count) {
                        global->rebuild_exploration(stats);exploration_exhausted=false;
                        ++stats.counts["exploration_context_reactivated"];
                    }
                    scheduler.meters[3].cooldown_until = std::max(scheduler.meters[3].cooldown_until,
                                                               global->exploration_budget.cooldown_until);
                    selection = scheduler.choose(budget.elapsed(), budget.left(),
                        {opt.variant != "global-only", search && opt.search.line && search->line_supported,
                         f->metric_compatible, !opt.work_conserving || !exploration_exhausted},
                        global->target_budget.requested_timeout_ms, global->exploration_budget.requested_timeout_ms);
                    stats.counts["soft_cooldown_bypasses"]=scheduler.soft_cooldown_bypasses;
                    stats.counts["all_actions_soft_cooled"]=scheduler.all_actions_soft_cooled;
                    stats.counts["hard_unavailable_events"]=scheduler.hard_unavailable_events;
                    stats.counts["actions_selected_after_cooldown"]=scheduler.actions_selected_after_cooldown;
                    if (!selection) {
                        ++stats.counts["idle_reason_"+scheduler.no_action_reason];
                        wall.sleep(std::min(budget.left(),.001));
                        checkpoint();
                        continue;
                    }
                }
                wall.enter("action_wall_seconds");
                Budget slice;
                slice.parent = &budget;
                if (selection) slice.seconds = std::min(budget.left(), selection->seconds);
                Budget &work_budget = selection ? slice : budget;
                active_budget = &work_budget;
                parent_seed_id = assignment_id(*f,current);
                if (search) search->begin_action();
                const auto before_attempts = stats.counts["candidate_attempts"];
                const auto before_unique = stats.counts["unique_candidates_scored"];
                const unsigned before_count = count;
                const auto before_candidates = stats.counts["local_candidates"];
                const auto before_duplicates = stats.counts["duplicate_candidate_assignment"];
                std::string action_status = "no_gain";
                unsigned solver_candidates = 0, solver_unique = 0;
                std::vector<Target> targets;
                {
                    Timer target_timer(stats, "active_targets");
                    targets = search && opt.search.matching ?
                        search->targets.select(coverage,rng,48,work_budget) : active_targets(*f, coverage, rng, 48);
                }
                bool progress = false;
                bool do_local = opt.variant != "global-only" && round % 5 != 0 &&
                                (stalled < 3 || round % 3 == 0) &&
                                (recent_local_cost < 0.1 || recent_local_gain >= 1 || round % 3 == 0);
                if (selection) do_local = selection->kind == ActionKind::LocalSafe;
                bool do_line = search && opt.search.line && search->line_supported &&
                    (selection ? selection->kind == ActionKind::LocalLine : do_local && round%3==0);
                if ((do_local || do_line) && search) {
                    Budget generation; generation.parent = &work_budget;
                    generation.seconds = work_budget.left() * (opt.search.polish ? .65 : 1.0);
                    auto batch = do_line ? search->line(current,coverage,generation,rng,stats,seed_bank,already_emitted) :
                                           search->safe(current,coverage,generation,rng,stats,seed_bank,already_emitted);
                    action_status = batch.status;
                    if (batch.model) {
                        current = *batch.model;
                        if (batch.gain.covered || batch.gain.observed || stalled>=2)
                            accept(current,std::string(do_line ? "line_" : "local_")+
                                (batch.gain.covered ? "gain" : batch.gain.observed ? "observation" : "fill"));
                        else {
                            if (seed_bank.size()==16) seed_bank.erase(seed_bank.begin());
                            seed_bank.push_back(current);
                        }
                    }
                    progress = round_feedback.coverage > 0;
                }
                if (do_local && !search) {
                    auto batch_start = Clock::now();
                    Observation seed_observation;
                    {
                        Timer t(stats, "coverage_scoring");
                        seed_observation = f->evaluate(current);
                    }
                    std::unique_ptr<Region> r;
                    {
                        Timer t(stats, "region");
                        r = std::make_unique<Region>(*f, current, seed_observation, work_budget);
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
                            if (work_budget.done() || tried >= 32 ||
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
                                if (work_budget.done() || tried >= 32)
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
                if (count < opt.n && !budget.done() && (selection ? selection->kind == ActionKind::TargetSolve : (!progress || !do_local))) {
                    auto feedback=[&](const GlobalResult &res) {
                        if (search) search->targets.feedback(res.attempted,res,
                            res.model ? coverage.gain(f->evaluate(*res.model)).covered : 0,budget.elapsed());
                    };
                    auto res = !f->metric_compatible ? GlobalResult{{}, "metric_incompatible", "", {}} :
                        opt.search.repair ? global->repair(targets,work_budget,stats,
                            [&](const Assignment &m){return coverage.gain(f->evaluate(m));},feedback) :
                        global->query(targets,work_budget,stats);
                    action_status = res.status;
                    if (!opt.search.repair) feedback(res);
                    if (res.model) {
                        ++solver_candidates;
                        solver_unique += !emitted.count(f->key(*res.model));
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
                }
                if (count < opt.n && !budget.done() && (selection ? selection->kind == ActionKind::Explore : (!progress && stalled >= 2))) {
                        auto e = global->explore(current, work_budget, stats, 0xffffffffU,
                            selection && selection->bypassed_soft_cooldown);
                        action_status = e.status;
                        if (e.model) {
                            ++solver_candidates;
                            solver_unique += !emitted.count(f->key(*e.model));
                            current = *e.model;
                            accept(current, "exploration");
                        } else if (e.status == "exploration_exhausted") {
                            if (!selection) { status = e.status; break; }
                            if(opt.work_conserving) {
                                exploration_exhausted=true;exhausted_at_count=count;
                                ++stats.counts["exploration_context_hard_unavailable"];
                            } else {
                                global->rebuild_exploration(stats);
                                scheduler.meters[3].cooldown_until = budget.elapsed() + 1;
                            }
                        } else if (!seed_bank.empty())
                            current = seed_bank[rng() % seed_bank.size()];
                }
                wall.enter("scheduler_decision_seconds");
                if (selection) {
                    ActionResult result;
                    result.kind = selection->kind;
                    result.elapsed_seconds = slice.elapsed();
                    result.emitted_new_coverage = round_feedback.coverage;
                    result.emitted_new_observations = round_feedback.observations;
                    result.emitted_samples = count - before_count;
                    result.generated_candidates = (search ? stats.counts["candidate_attempts"] - before_attempts :
                        stats.counts["local_candidates"] - before_candidates) + solver_candidates;
                    result.unique_valid_candidates = (search ? stats.counts["unique_candidates_scored"] - before_unique :
                        stats.counts["local_candidates"] - before_candidates -
                        (stats.counts["duplicate_candidate_assignment"] - before_duplicates)) + solver_unique;
                    result.status = result.emitted_new_coverage ? "gain" : result.emitted_samples ? "zero_gain_output" : action_status;
                    scheduler.feedback(*selection, result, budget.elapsed());
                    recent_quality[static_cast<unsigned>(result.kind)]=
                        scheduler.meters[static_cast<unsigned>(result.kind)].quality();
                    auto name = action_name(result.kind);
                    stats.action_times[name] += result.elapsed_seconds;
                    ++stats.counts["action_" + name + "_calls"];
                    ++stats.counts["action_" + name + "_status_" + result.status];
                    stats.counts["action_" + name + "_gain"] += result.emitted_new_coverage;
                    stats.counts["action_" + name + "_samples"] += result.emitted_samples;
                    stats.counts["action_" + name + "_candidates"] += result.generated_candidates;
                    stats.counts["action_" + name + "_unique_valid_candidates"] += result.unique_valid_candidates;
                    if (result.emitted_new_coverage) scheduler.refresh(budget.elapsed());
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
        wall.enter("checkpoint_and_logging_seconds");
        std::ofstream summary(fs::path(opt.output) / "summary.json");
        summary << "{\"status\":" << quote(status) << ",\"samples\":" << count << ",\"requested\":" << opt.n
                << ",\"elapsed\":" << budget.elapsed() << ",\"seed\":" << opt.seed
                << ",\"variant\":" << quote(opt.variant) << ",\"mode\":" << quote(opt.mode)
                << ",\"metric_compatible\":" << (f->metric_compatible ? "true" : "false")
                << ",\"covered\":" << coverage.covered() << ",\"total\":" << f->total_bits()
                << ",\"z3\":" << quote(Z3_get_full_version()) << ",\"version\":" << quote(LIA_VERSION)
                << ",\"source_sha256\":" << quote(LIA_SOURCE_SHA) << ",\"wall_timing\":" << wall.json()
                << ",\"statistics\":" << stats.json()
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
            if (search && !search->last_failure.empty()) {
                std::ofstream(out / "search_context.json") << search->last_failure << '\n';
                std::ofstream(out / "candidate.json") << f->model_json(search->last_candidate) << '\n';
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
