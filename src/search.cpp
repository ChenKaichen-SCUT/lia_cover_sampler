#include "search.hpp"
#include <algorithm>
#include <array>
#include <bit>
#include <numeric>

namespace lia {
bool DirectionSystem::kernel(const Assignment &d) const {
    if (d.size() != width) return false;
    for (auto i : booleans) if (d[i] != 0) return false;
    for (const auto &row : equations) if (row.dot(d) != 0) return false;
    return true;
}
std::string DirectionSystem::signature() const {
    std::ostringstream out;
    out << scope << ':';
    for (const auto &row : equations) {
        for (const auto &[i, a] : row.a) out << i << ':' << a << ';';
        out << '|';
    }
    for (int i : booleans) out << 'b' << i;
    return out.str();
}
std::string assignment_id(const Formula &f, const Assignment &model) {
    uint64_t hash = 1469598103934665603ULL;
    for (unsigned char c : f.key(model)) { hash ^= c; hash *= 1099511628211ULL; }
    std::ostringstream out; out << std::hex << hash; return out.str();
}
std::vector<Direction> quota_directions(const DirectionSystem &s, const std::vector<Assignment> &history,
    const Assignment &base, Budget &budget, std::mt19937_64 &rng, Stats &stats, unsigned cap, bool quotas) {
    Timer timer(stats, "directions");
    std::array<std::vector<Assignment>, 4> groups;
    std::set<std::vector<Z>> seen;
    auto add = [&](Assignment d, unsigned group) {
        Z divisor = 0;
        for (const auto &v : d) divisor = gcd(divisor, v);
        if (divisor == 0) return;
        for (auto &v : d) v /= divisor;
        for (const auto &v : d) if (v != 0) {
            if (v < 0) for (auto &x : d) x = -x;
            break;
        }
        if (!s.kernel(d)) throw std::logic_error("direction outside declared equality scope");
        if (seen.insert(d).second) groups[group].push_back(std::move(d));
    };
    std::set<int> used;
    for (const auto &row : s.equations)
        for (const auto &[i, coefficient] : row.a) { (void)coefficient; used.insert(i); }
    std::vector<int> variables;
    for (size_t i = 0; i < s.width; ++i) if (!s.booleans.count(i)) variables.push_back(i);
    std::shuffle(variables.begin(), variables.end(), rng);
    for (int i : variables) if (!used.count(i) && groups[0].size() < cap && !budget.done()) {
        Assignment d(s.width); d[i] = 1; add(std::move(d), 0);
    }
    std::map<int, std::map<int, Q>> pivots;
    uint64_t operations = 0;
    bool exact_rank = true;
    for (const auto &row : s.equations) {
        std::map<int, Q> a;
        for (const auto &[i, v] : row.a) a[i] = Q(v);
        while (!a.empty()) {
            if (budget.done() || operations > 200000) { exact_rank = false; break; }
            int pivot = a.begin()->first;
            auto found = pivots.find(pivot);
            if (found == pivots.end()) {
                Q scale = a.begin()->second;
                for (auto &[i, value] : a) { (void)i; value /= scale; }
                pivots.emplace(pivot, std::move(a));
                break;
            }
            Q scale = a.begin()->second;
            for (const auto &[i, value] : found->second) {
                a[i] -= scale * value; ++operations;
                if (a[i] == 0) a.erase(i);
                else if (mpz_sizeinbase(a[i].get_num_mpz_t(), 2) > 4096 ||
                         mpz_sizeinbase(a[i].get_den_mpz_t(), 2) > 4096) {
                    exact_rank = false; break;
                }
            }
            if (!exact_rank) break;
        }
        if (!exact_rank) break;
    }
    stats.counts[s.scope + "_equations_last"] = s.equations.size();
    stats.counts[s.scope + "_integer_variables_last"] = variables.size();
    stats.counts[s.scope + "_rank_known_last"] = exact_rank;
    if (exact_rank) {
        ++stats.counts[s.scope + "_rank_exact"];
        stats.counts[s.scope + "_rank_last"] = pivots.size();
        stats.counts[s.scope + "_free_dimension_last"] = variables.size() - pivots.size();
    } else ++stats.counts[s.scope + "_rank_unknown"];
    auto back_substitute = [&](std::vector<Q> v) -> std::optional<Assignment> {
        for (auto it = pivots.rbegin(); it != pivots.rend(); ++it) {
            if (budget.done()) return {};
            Q value = 0;
            for (const auto &[i, a] : it->second) if (i != it->first) value += a * v[i];
            v[it->first] = -value;
        }
        Z denominator = 1;
        for (const auto &v_i : v) {
            denominator = lcm(denominator, v_i.get_den());
            if (mpz_sizeinbase(denominator.get_mpz_t(), 2) > 4096) return {};
        }
        Assignment result(s.width);
        for (size_t i = 0; i < v.size(); ++i) {
            result[i] = v[i].get_num() * (denominator / v[i].get_den());
            if (mpz_sizeinbase(result[i].get_mpz_t(), 2) > 8192) return {};
        }
        return result;
    };
    std::vector<int> free;
    if (exact_rank) {
        for (int i : variables) if (!pivots.count(i)) free.push_back(i);
        // Equation-associated free variables have their own quota.
        for (int i : free) if (used.count(i) && groups[1].size() < cap && !budget.done()) {
            std::vector<Q> v(s.width); v[i] = 1;
            if (auto d = back_substitute(std::move(v))) add(*d, 1);
        }
        // Draw from ALL free variables, not a previously truncated basis.
        for (unsigned k = 0; free.size() > 1 && k < cap && !budget.done(); ++k) {
            std::vector<Q> v(s.width);
            std::shuffle(free.begin(), free.end(), rng);
            unsigned support = std::min<size_t>(free.size(), 2 + rng() % 7);
            for (unsigned j = 0; j < support; ++j) v[free[j]] = (rng() & 1) ? 1 : -1;
            if (auto d = back_substitute(std::move(v))) add(*d, 2);
        }
    }
    for (const auto &m : history) if (!budget.done()) {
        Assignment d(s.width);
        for (size_t i = 0; i < s.width; ++i) d[i] = m[i] - base[i];
        if (s.kernel(d)) add(std::move(d), 3);
        else ++stats.counts["history_direction_wrong_scope"];
    }
    const char *sources[] = {"coordinate", "equation_kernel", "multi_free", "history"};
    std::vector<Direction> result;
    std::array<size_t, 4> positions{};
    auto signature = s.signature();
    if (!quotas) {
        for (unsigned i=0;i<4 && result.size()<cap;++i)
            for (const auto &delta:groups[i]) {
                if (result.size()>=cap || budget.done()) break;
                result.push_back({delta,sources[i],s.scope,signature});
                ++stats.counts["direction_"+std::string(sources[i])+"_selected"];
            }
        return result;
    }
    // Round-robin reserves capacity for each source and transfers unused quota.
    while (result.size() < cap && !budget.done()) {
        bool added = false;
        for (unsigned i = 0; i < 4 && result.size() < cap; ++i)
            if (positions[i] < groups[i].size()) {
                result.push_back({groups[i][positions[i]++], sources[i], s.scope, signature});
                ++stats.counts["direction_" + std::string(sources[i]) + "_selected"];
                added = true;
            }
        if (!added) break;
    }
    if (result.empty()) {
        if (!exact_rank || budget.done()) ++stats.counts["direction_budget_exhausted"];
        else if (pivots.size() == variables.size()) ++stats.counts["direction_nullspace_zero"];
        else ++stats.counts["direction_generator_empty"];
    }
    return result;
}
TargetManager::TargetManager(Formula &formula)
    : f(formula), dependencies(f.nodes.size()), variable_observers(f.variables.size()) {
    // State belongs to this immutable formula/metric pair; never shared between inputs.
    uint64_t hash = 1469598103934665603ULL;
    for (unsigned char c : f.root.to_string()) { hash ^= c; hash *= 1099511628211ULL; }
    formula_metric_identity = std::to_string(hash) + ":original-wire64-lazy-v1";
}
bool TargetManager::dependency(int node, Budget &budget) {
    if (dependencies[node]) return true;
    if (budget.done()) return false;
    const auto &n = f.nodes[node];
    std::vector<int> result;
    if (n.variable >= 0) result.push_back(n.variable);
    for (int c : n.children) {
        if (!dependency(c, budget)) return false;
        std::vector<int> merged;
        std::set_union(result.begin(), result.end(), dependencies[c]->begin(), dependencies[c]->end(),
                       std::back_inserter(merged));
        result = std::move(merged);
    }
    for (int i : result) variable_observers[i].push_back(node);
    dependencies[node] = std::move(result);
    return true;
}
bool TargetManager::eligible(Target t, const Coverage &coverage, double now) {
    auto state = coverage.states[t.node];
    uint64_t mask = uint64_t(1) << t.bit;
    if ((t.desired ? state.one : state.zero) & mask) return false;
    auto &record = records[t];
    if (globally_unreachable && globally_unreachable(t)) record.globally_unreachable = true;
    return !record.globally_unreachable && now >= record.cooldown_until;
}
std::vector<Target> TargetManager::select(const Coverage &coverage, std::mt19937_64 &rng, size_t cap,
                                         Budget &budget, const Assignment *direction) {
    std::vector<std::pair<double, Target>> pool;
    std::set<Target> seen;
    size_t n = f.nodes.size();
    for (size_t attempt = 0; attempt < cap * 12 && !budget.done(); ++attempt) {
        size_t position = cursor++;
        int node = attempt % 4 == 0 ? position % n : rng() % n;
        const auto &description = f.nodes[node];
        if (description.expr.is_numeral() || description.expr.is_true() || description.expr.is_false()) continue;
        unsigned bit_index = description.boolean ? 0 :
            attempt % 4 == 0 ? (position / n) % 64 : attempt % 3 == 0 ? 63 : rng() % 64;
        auto s = coverage.states[node];
        uint64_t mask = uint64_t(1) << bit_index;
        if (s.zero & s.one & mask) continue;
        bool desired = s.zero & mask ? true : s.one & mask ? false : bool(rng() & 1);
        Target t{node, bit_index, desired};
        if (!seen.insert(t).second || !eligible(t, coverage, budget.total_elapsed())) continue;
        double score = ((s.zero | s.one) & mask) ? 4 : 1;
        if (direction && dependency(node, budget)) {
            bool affected = false;
            for (int i : *dependencies[node]) affected |= (*direction)[i] != 0;
            // An unvisited node may become reachable even when its value is constant.
            if (affected) score += 8;
            else if ((s.zero | s.one) == 0) score += 2;
            else score -= 4;
        }
        const auto &record = records[t];
        score += record.witnessed ? 1 : 0;
        score += std::min(2.0, double(record.incidental_gain) / (1 + record.solve_seconds * 100));
        score -= std::min(3.0, record.solve_seconds);
        pool.push_back({score, t});
    }
    std::stable_sort(pool.begin(), pool.end(), [](const auto &a, const auto &b) { return a.first > b.first; });
    std::vector<Target> result;
    // Keep a small rotating/random tail, instead of permanently selecting only high scores.
    size_t greedy = cap > 4 ? cap * 7 / 8 : cap;
    for (size_t i = 0; i < std::min(greedy, pool.size()); ++i) result.push_back(pool[i].second);
    if (pool.size() > greedy) {
        std::shuffle(pool.begin() + greedy, pool.end(), rng);
        for (size_t i = greedy; i < pool.size() && result.size() < cap; ++i) result.push_back(pool[i].second);
    }
    return result;
}
void TargetManager::feedback(const std::vector<Target> &goals, const GlobalResult &result,
                             uint64_t gain, double now) {
    std::optional<Observation> observed;
    if (result.model) observed = f.evaluate(*result.model);
    for (auto target : goals) {
        auto &record = records[target];
        record.solve_seconds += result.elapsed_seconds / std::max<size_t>(1, goals.size());
        if (result.code == SolveStatus::Timeout) {
            ++record.timeouts;
            record.cooldown_until = now + std::min(2.0, .05 * record.timeouts);
        }
        if (result.model) {
            if (observed->visited[target.node] && bit(observed->values[target.node], target.bit) == target.desired)
                record.witnessed = true;
            record.incidental_gain += gain;
        }
    }
}
static bool better(Gain a, Gain b) {
    return a.covered > b.covered || (a.covered == b.covered && a.observed > b.observed);
}
static std::vector<Interval> without_zero(Interval interval) {
    std::vector<Interval> result;
    Interval negative = interval, positive = interval;
    negative.constrain(1, -1);
    positive.constrain(-1, -1);
    if (!negative.empty) result.push_back(negative);
    if (!positive.empty) result.push_back(positive);
    return result;
}
CandidateBatch Search::safe(const Assignment &seed, const Coverage &coverage, Budget &budget,
    std::mt19937_64 &rng, Stats &stats, const std::vector<Assignment> &history,
    const std::function<bool(const std::string &)> &already_emitted) {
    CandidateBatch result;
    auto observation = f.evaluate(seed);
    Region region(f, seed, observation, budget);
    if (!region.supported) { result.status = "unsupported"; return result; }
    std::vector<Direction> dirs;
    if (options.quotas) {
        DirectionSystem system;
        system.width = seed.size(); system.equations = region.eq;
        for (auto [i, v] : region.boolean) { (void)v; system.booleans.insert(i); }
        dirs = quota_directions(system, history, seed, budget, rng, stats, options.direction_cap);
    } else {
        for (auto &d : directions(region, rng, stats, options.direction_cap))
            dirs.push_back({d, "legacy", "safe_region", region.signature()});
    }
    std::vector<std::vector<Target>> goals;
    std::vector<std::vector<Interval>> ranges;
    for (const auto &direction : dirs) {
        if (budget.done()) break;
        goals.push_back(options.matching ? targets.select(coverage, rng, 32, budget, &direction.delta) :
                                          active_targets(f, coverage, rng, 32));
        ranges.push_back(without_zero(region.interval(seed, direction.delta)));
        if (ranges.back().empty()) ++stats.counts["interval_only_current_point"];
    }
    std::set<std::string> scored{f.key(seed)};
    result.direction_attempts.resize(goals.size());
    auto attempt = [&](size_t i, size_t target_index) {
        if (budget.done() || result.attempts >= options.attempt_cap || result.scored >= options.candidate_cap ||
            target_index >= goals[i].size() || ranges[i].empty()) return;
        ++result.attempts;
        ++result.direction_attempts[i];
        ++stats.counts["candidate_attempts"];
        ++stats.counts["direction_" + dirs[i].source + "_attempts"];
        Target target = goals[i][target_index];
        if (f.nodes[target.node].boolean) return;
        Affine affine;
        try { affine = region.linear(target.node, false); }
        catch (const std::domain_error &) { return; }
        Z alpha = affine.dot(dirs[i].delta), beta = affine.at(seed);
        if (alpha == 0 && bit(beta, target.bit) != target.desired) {
            ++stats.counts["target_constant_wrong_bit"]; return;
        }
        std::vector<std::tuple<Z, Z, Z>> windows;
        Z all_hits = 0;
        for (const auto &range : ranges[i]) {
            auto [lo, hi] = range.finite(alpha, target.bit);
            Z hits = options.random ? Z(hi - lo + 1) :
                     count_bit_hits(alpha, beta, target.bit, target.desired, lo, hi);
            windows.push_back({lo, hi, hits}); all_hits += hits;
        }
        if (all_hits == 0) { ++stats.counts["target_numerically_unreachable_in_interval"]; return; }
        Z rank = random_below(all_hits, rng);
        std::optional<Z> z;
        for (const auto &[lo, hi, hits] : windows) {
            if (rank < hits) {
                z = options.random ? std::optional<Z>(Z(lo + rank)) :
                    find_bit_hit(alpha, beta, target.bit, target.desired, lo, hi, rank);
                break;
            }
            rank -= hits;
        }
        if (!z || *z == 0) throw std::logic_error("invalid nonzero modular witness");
        Assignment candidate = seed;
        for (size_t j = 0; j < seed.size(); ++j) candidate[j] += *z * dirs[i].delta[j];
        auto key = f.key(candidate);
        if (!scored.insert(key).second || already_emitted(key) || !action_scored.insert(key).second) {
            ++result.duplicates; ++stats.counts["duplicate_candidates"]; return;
        }
        last_candidate = candidate;
        {
            Timer timer(stats, "validation");
            if (!f.validate(candidate)) {
                last_failure = "{\"scope\":\"safe_region\",\"seed\":" + f.model_json(seed) +
                    ",\"candidate\":" + f.model_json(candidate) + ",\"target\":" + quote(target.key()) +
                    ",\"region\":" + quote(region.expression().to_string()) +
                    ",\"snapshot\":" + coverage.json() + "}";
                throw std::logic_error("safe candidate violates original formula");
            }
        }
        ++result.scored; ++stats.counts["unique_candidates_scored"];
        auto observed = f.evaluate(candidate);
        auto gain = coverage.gain(observed);
        if (!observed.visited[target.node]) ++stats.counts["numeric_hit_unobserved"];
        if (!result.model || better(gain, result.gain)) {
            result.model = candidate; result.gain = gain; result.direction_source = dirs[i].source;
        }
        stats.counts["direction_" + dirs[i].source + "_candidate_gain"] += gain.covered;
    };
    if (options.fair) {
        for (unsigned round = 0; round < 32 && !budget.done() &&
             result.attempts < options.attempt_cap && result.scored < options.candidate_cap; ++round)
            for (size_t i = 0; i < goals.size(); ++i) attempt(i, round);
    } else {
        for (size_t i = 0; i < goals.size(); ++i)
            for (size_t j = 0; j < goals[i].size(); ++j) attempt(i, j);
    }
    result.status = result.model ? "candidate" : budget.done() ? "budget_exhausted" : "no_candidate";
    return result;
}
CandidateBatch Search::line(const Assignment &seed, const Coverage &coverage, Budget &budget,
    std::mt19937_64 &rng, Stats &stats, const std::vector<Assignment> &history,
    const std::function<bool(const std::string &)> &already_emitted) {
    CandidateBatch result;
    Timer timer(stats,"line_analysis");
    if (!syntax) syntax=std::make_unique<AffineSyntax>(f,options.line_limits);
    if (!structural || !structural->complete) structural=structural_equalities(f,*syntax,budget);
    stats.counts["structural_collection_complete"]=structural->complete;
    DirectionSystem system;
    system.scope="structural_line"; system.width=seed.size(); system.equations=structural->rows;
    for (size_t i=0;i<seed.size();++i) if (f.nodes[f.variables[i]].boolean) system.booleans.insert(i);
    auto dirs=quota_directions(system,history,seed,budget,rng,stats,options.direction_cap,options.quotas);
    std::vector<std::unique_ptr<LineAnalyzer>> analyzers;
    std::vector<std::vector<Target>> goals;
    std::vector<LineDomain> domains;
    for (const auto &direction:dirs) {
        if (budget.done()) break;
        auto analyzer=std::make_unique<LineAnalyzer>(f,seed,direction.delta,*syntax,budget);
        auto feasible=analyzer->feasible();
        ++stats.counts["line_domain_"+line_status_name(feasible.status)];
        if (feasible.status!=LineStatus::Exact) {
            if (feasible.status==LineStatus::Unsupported) line_supported=false;
            result.status=line_status_name(feasible.status); return result;
        }
        stats.counts["line_nonzero_components"]+=feasible.domain.without(0).intervals.size();
        domains.push_back(feasible.domain);
        goals.push_back(options.matching ? targets.select(coverage,rng,32,budget,&direction.delta) :
                                          active_targets(f,coverage,rng,32));
        analyzers.push_back(std::move(analyzer));
    }
    auto seed_observation=f.evaluate(seed);
    Region safe_region(f,seed,seed_observation,budget);
    std::set<std::string> scored{f.key(seed)};
    result.direction_attempts.resize(goals.size());
    auto attempt=[&](size_t i,size_t j) {
        if (budget.done() || result.attempts>=options.attempt_cap || result.scored>=options.candidate_cap ||
            j>=goals[i].size()) return;
        ++result.attempts; ++result.direction_attempts[i]; ++stats.counts["candidate_attempts"];
        ++stats.counts["direction_"+dirs[i].source+"_attempts"];
        auto target=goals[i][j];
        auto domain=f.nodes[target.node].boolean ? analyzers[i]->boolean_target(target) :
                                                   analyzers[i]->observable(target.node);
        if (domain.status!=LineStatus::Exact) {
            ++stats.counts["line_target_"+line_status_name(domain.status)]; return;
        }
        std::optional<Z> z;
        try {
            if (f.nodes[target.node].boolean) z=line_bit_witness(domain.domain,0,0,0,false,rng);
            else {
                auto [alpha,beta]=analyzers[i]->numeric(target.node);
                if (alpha==0 && bit(beta,target.bit)==target.desired)
                    ++stats.counts["line_constant_reach_opportunities"];
                z=line_bit_witness(domain.domain,alpha,beta,target.bit,target.desired,rng,options.random);
            }
        } catch (const LineFailure &error) {
            ++stats.counts["line_target_"+line_status_name(error.status)]; return;
        }
        if (!z) { ++stats.counts["line_target_no_witness"]; return; }
        Assignment candidate=seed;
        for (size_t k=0;k<seed.size();++k) candidate[k]+=*z*dirs[i].delta[k];
        auto key=f.key(candidate);
        if (!scored.insert(key).second || already_emitted(key) || !action_scored.insert(key).second) {
            ++result.duplicates; ++stats.counts["duplicate_candidates"]; return;
        }
        last_candidate=candidate;
        auto observed=f.evaluate(candidate);
        if (!f.validate(candidate) || (!options.random &&
            (!observed.visited[target.node] || bit(observed.values[target.node],target.bit)!=target.desired))) {
            std::ostringstream direction; direction << '[';
            for (size_t k=0;k<seed.size();++k) { if(k) direction<<','; direction<<quote(dirs[i].delta[k].get_str()); }
            last_failure="{\"scope\":\"structural_line\",\"seed\":"+f.model_json(seed)+
                ",\"candidate\":"+f.model_json(candidate)+",\"direction\":"+direction.str()+"]"+
                ",\"domain\":"+domain.domain.json()+",\"target\":"+quote(target.key())+
                ",\"snapshot\":"+coverage.json()+"}";
            throw std::logic_error("line candidate violates formula or observation target");
        }
        ++result.scored; ++stats.counts["unique_candidates_scored"];
        if (safe_region.supported && !safe_region.contains(candidate)) ++stats.counts["line_cross_branch_candidates"];
        auto gain=coverage.gain(observed);
        if (!result.model || better(gain,result.gain)) {
            result.model=candidate; result.gain=gain; result.direction_source=dirs[i].source;
        }
        stats.counts["direction_"+dirs[i].source+"_candidate_gain"]+=gain.covered;
    };
    if(options.fair) {
        for(unsigned j=0;j<32 && !budget.done() && result.attempts<options.attempt_cap &&
            result.scored<options.candidate_cap;++j)
            for(size_t i=0;i<goals.size();++i) attempt(i,j);
    } else for(size_t i=0;i<goals.size();++i) for(size_t j=0;j<goals[i].size();++j) attempt(i,j);
    if (result.model && safe_region.supported && !safe_region.contains(*result.model))
        ++stats.counts["line_cross_branch_moves"];
    result.status=result.model ? "candidate" : budget.done() ? "budget_exhausted" : "no_candidate";
    return result;
}
std::vector<uint64_t> new_coverage_bits(const Formula &f, const Coverage &coverage, const Observation &o) {
    std::vector<uint64_t> result(f.nodes.size());
    for (size_t i = 0; i < result.size(); ++i) if (o.visited[i]) {
        Z low = mod(o.values[i], pow2(64));
        uint64_t mask = f.nodes[i].boolean ? 1 : ~uint64_t(0), value = low.get_ui() & mask;
        auto state = coverage.states[i];
        result[i] = ((state.one | value) & (state.zero | (~value & mask))) & ~(state.one & state.zero);
    }
    return result;
}
PolishResult Search::polish_candidate(const Assignment &seed, const Coverage &committed,
    Budget &budget, std::mt19937_64 &rng, Stats &stats,
    const std::function<bool(const std::string &)> &already_emitted) {
    const auto begin = Clock::now();
    PolishResult result;
    result.model = seed;
    const auto before_observation = f.evaluate(seed);
    result.before = result.after = committed.gain(before_observation);
    auto original_bits = new_coverage_bits(f, committed, before_observation);
    const unsigned candidate_limit=options.candidate_cap;
    const auto starting_scored=stats.counts["unique_candidates_scored"];
    // No accept() is possible here: the committed coverage is read-only.
    for (unsigned move = 0; move < options.polish_steps && !budget.done(); ++move) {
        auto used=stats.counts["unique_candidates_scored"]-starting_scored;
        if (used>=candidate_limit) break;
        options.candidate_cap=candidate_limit-used;
        auto proposal = safe(result.model, committed, budget, rng, stats, {}, already_emitted);
        options.candidate_cap=candidate_limit;
        std::string module="local_safe";
        if ((!proposal.model || !better(proposal.gain,result.after)) &&
            options.line && line_supported && !budget.done() &&
            stats.counts["unique_candidates_scored"]-starting_scored<candidate_limit) {
            options.candidate_cap=candidate_limit-(stats.counts["unique_candidates_scored"]-starting_scored);
            proposal=line(result.model,committed,budget,rng,stats,{},already_emitted);
            options.candidate_cap=candidate_limit;
            module="local_line";
        }
        if (!proposal.model || !better(proposal.gain, result.after)) break;
        result.model = *proposal.model;
        result.after = proposal.gain;
        ++result.moves;
        if (std::find(result.modules.begin(),result.modules.end(),module)==result.modules.end())
            result.modules.push_back(module);
        // safe() rebuilt Region, branch conditions, intercepts and intervals from this model.
    }
    auto final_bits = new_coverage_bits(f, committed, f.evaluate(result.model));
    for (size_t i = 0; i < final_bits.size(); ++i) {
        result.retained += std::popcount(original_bits[i] & final_bits[i]);
        result.added += std::popcount(final_bits[i] & ~original_bits[i]);
        result.lost += std::popcount(original_bits[i] & ~final_bits[i]);
    }
    result.seconds = std::chrono::duration<double>(Clock::now() - begin).count();
    ++stats.counts["polish_calls"];
    stats.counts["polish_moves"] += result.moves;
    stats.counts["polish_added_bits"] += result.added;
    stats.counts["polish_lost_bits"] += result.lost;
    stats.refinement_seconds += result.seconds;
    return result;
}
} // namespace lia
