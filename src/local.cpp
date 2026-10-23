#include "core.hpp"
#include <algorithm>
#include <numeric>

namespace lia {
void Affine::add(const Affine &o, const Z &scale) {
    c += o.c * scale;
    for (auto &[i, v] : o.a) {
        a[i] += v * scale;
        if (a[i] == 0)
            a.erase(i);
    }
}
Z Affine::at(const Assignment &m) const { return c + dot(m); }
Z Affine::dot(const Assignment &d) const {
    Z v = 0;
    for (auto &[i, k] : a)
        v += k * d[i];
    return v;
}
Region::Region(Formula &formula, const Assignment &m, const Observation &o, Budget &b)
    : f(formula), model(m), values(o), budget(b) {
    try {
        require(0, true);
    } catch (const std::domain_error &e) {
        supported = false;
        reason = e.what();
    }
    for (size_t i = 0; i < f.variables.size(); ++i)
        if (f.nodes[f.variables[i]].boolean)
            boolean[i] = m[i] != 0;
    if (supported && !contains(m))
        throw std::logic_error("region does not contain seed");
}
Affine Region::linear(int id, bool permit_conditions) {
    if (budget.done())
        throw std::domain_error("region budget exhausted");
    if (auto it = affine_cache.find(id); it != affine_cache.end())
        return it->second;
    auto &n = f.nodes[id];
    auto &ch = n.children;
    Affine v;
    if (n.boolean)
        throw std::domain_error("Boolean arithmetic");
    if (n.variable >= 0)
        v.a[n.variable] = 1;
    else
        switch (n.expr.decl().decl_kind()) {
        case Z3_OP_ANUM:
            v.c = n.numeral;
            break;
        case Z3_OP_ADD:
            for (int c : ch)
                v.add(linear(c, permit_conditions));
            break;
        case Z3_OP_SUB:
            v = linear(ch[0], permit_conditions);
            for (size_t i = 1; i < ch.size(); ++i)
                v.add(linear(ch[i], permit_conditions), -1);
            break;
        case Z3_OP_UMINUS:
            v.add(linear(ch[0], permit_conditions), -1);
            break;
        case Z3_OP_MUL: {
            Z scalar = 1;
            std::optional<Affine> term;
            for (int c : ch) {
                auto a = linear(c, permit_conditions);
                if (a.a.empty())
                    scalar *= a.c;
                else {
                    if (term)
                        throw std::domain_error("nonlinear multiplication");
                    term = a;
                }
            }
            if (term)
                v.add(*term, scalar);
            else
                v.c = scalar;
            break;
        }
        case Z3_OP_ITE: {
            bool truth = f.as_model(model).eval(f.nodes[ch[0]].expr, true).is_true();
            if (!permit_conditions) {
                auto it = required.find(ch[0]);
                if (it == required.end() || it->second != truth)
                    throw std::domain_error("unfixed observation ite");
            } else
                require(ch[0], truth);
            v = linear(ch[truth ? 1 : 2], permit_conditions);
            break;
        }
        default:
            throw std::domain_error("unsupported local arithmetic: " + n.expr.decl().name().str());
        }
    affine_cache.emplace(id, v);
    return v;
}
void Region::require(int id, bool truth) {
    if (budget.done())
        throw std::domain_error("region budget exhausted");
    if (auto it = required.find(id); it != required.end()) {
        if (it->second != truth)
            throw std::logic_error("inconsistent branch conditions");
        return;
    }
    required[id] = truth;
    auto &n = f.nodes[id];
    auto &c = n.children;
    auto k = n.expr.decl().decl_kind();
    auto actual = [&](int a) {
        return values.visited[a] ? values.values[a] != 0
                                 : f.as_model(model).eval(f.nodes[a].expr, true).is_true();
    };
    if (n.variable >= 0) {
        boolean[n.variable] = truth;
        return;
    }
    switch (k) {
    case Z3_OP_TRUE:
    case Z3_OP_FALSE:
        if ((k == Z3_OP_TRUE) != truth)
            throw std::logic_error("false region literal");
        return;
    case Z3_OP_NOT:
        require(c[0], !truth);
        return;
    case Z3_OP_AND:
    case Z3_OP_OR:
        if ((k == Z3_OP_AND) == truth) {
            for (int a : c)
                require(a, truth);
        } else {
            bool found = false;
            for (int a : c)
                if (actual(a) == truth) {
                    require(a, truth);
                    found = true;
                    break;
                }
            if (!found)
                throw std::logic_error("missing Boolean branch");
        }
        return;
    case Z3_OP_IMPLIES:
        if (!truth) {
            require(c[0], true);
            require(c[1], false);
        } else if (!actual(c[0]))
            require(c[0], false);
        else
            require(c[1], true);
        return;
    case Z3_OP_XOR:
        for (int a : c)
            require(a, actual(a));
        return;
    case Z3_OP_ITE: {
        bool t = actual(c[0]);
        require(c[0], t);
        require(c[t ? 1 : 2], truth);
        return;
    }
    case Z3_OP_EQ:
        if (f.nodes[c[0]].boolean) {
            for (int a : c)
                require(a, actual(a));
            return;
        }
        break;
    case Z3_OP_DISTINCT: {
        bool found = false;
        for (size_t i = 0; i < c.size(); ++i)
            for (size_t j = i + 1; j < c.size(); ++j) {
                if (f.nodes[c[i]].boolean) {
                    for (int a : c)
                        require(a, actual(a));
                    return;
                }
                auto a = linear(c[i]);
                a.add(linear(c[j]), -1);
                Z at = a.at(model);
                if (truth) {
                    if (at == 0)
                        throw std::logic_error("distinct seed error");
                    if (at > 0) {
                        Affine neg;
                        neg.add(a, -1);
                        a = neg;
                    }
                    a.c += 1;
                    le.push_back(a);
                } else if (at == 0) {
                    eq.push_back(a);
                    found = true;
                    break;
                }
            }
        if (!truth && !found)
            throw std::logic_error("distinct false seed error");
        return;
    }
    case Z3_OP_LE:
    case Z3_OP_LT:
    case Z3_OP_GE:
    case Z3_OP_GT:
        break;
    default:
        throw std::domain_error("unsupported local Boolean: " + n.expr.decl().name().str());
    }
    auto a = linear(c[0]);
    a.add(linear(c[1]), -1);
    if (k == Z3_OP_EQ) {
        if (truth) {
            eq.push_back(a);
            return;
        }
        if (a.at(model) > 0) {
            Affine neg;
            neg.add(a, -1);
            a = neg;
        }
        a.c += 1;
        le.push_back(a);
        return;
    }
    bool reverse = (k == Z3_OP_GE || k == Z3_OP_GT);
    bool strict = (k == Z3_OP_LT || k == Z3_OP_GT);
    if (!truth) {
        reverse = !reverse;
        strict = !strict;
    }
    if (reverse) {
        Affine neg;
        neg.add(a, -1);
        a = neg;
    }
    if (strict)
        a.c += 1;
    le.push_back(a);
}
bool Region::contains(const Assignment &m) const {
    for (auto &r : eq)
        if (r.at(m) != 0)
            return false;
    for (auto &r : le)
        if (r.at(m) > 0)
            return false;
    for (auto [i, v] : boolean)
        if ((m[i] != 0) != v)
            return false;
    return true;
}
bool Region::kernel(const Assignment &d) const {
    for (auto &r : eq)
        if (r.dot(d) != 0)
            return false;
    for (auto [i, v] : boolean) {
        (void)v;
        if (d[i] != 0)
            return false;
    }
    return true;
}
Interval Region::interval(const Assignment &m, const Assignment &d) const {
    if (!kernel(d))
        throw std::logic_error("direction violates full E*d=0");
    Interval out;
    for (auto &r : le)
        out.constrain(r.dot(d), -r.at(m));
    if (!out.contains(0))
        throw std::logic_error("feasible interval excludes zero");
    return out;
}
z3::expr Region::expression() const {
    auto expr = [&](const Affine &r) {
        z3::expr v = f.ctx.int_val(r.c.get_str().c_str());
        for (auto &[i, k] : r.a)
            v = v + f.ctx.int_val(k.get_str().c_str()) * f.nodes[f.variables[i]].expr;
        return v;
    };
    z3::expr_vector terms(f.ctx);
    for (auto &r : eq)
        terms.push_back(expr(r) == 0);
    for (auto &r : le)
        terms.push_back(expr(r) <= 0);
    for (auto [i, v] : boolean)
        terms.push_back(f.nodes[f.variables[i]].expr == f.ctx.bool_val(v));
    return z3::mk_and(terms);
}
std::string Region::signature() const {
    std::ostringstream s;
    for (auto &r : eq) {
        for (auto &[i, k] : r.a)
            s << i << ':' << k << ';';
        s << '|';
    }
    for (auto [i, v] : boolean) {
        (void)v;
        s << 'b' << i;
    }
    return s.str();
}
std::vector<Assignment> directions(const Region &r, std::mt19937_64 &rng, Stats &stats, size_t cap) {
    Timer t(stats, "directions");
    auto start = Clock::now();
    size_t n = r.model.size();
    std::vector<Assignment> out;
    auto add = [&](Assignment d) {
        Z g = 0;
        for (auto &a : d)
            g = gcd(g, a);
        if (g == 0)
            return;
        for (auto &a : d)
            a /= g;
        if (!r.kernel(d))
            throw std::logic_error("direction violates full E*d=0");
        if (std::find(out.begin(), out.end(), d) == out.end())
            out.push_back(std::move(d));
    };
    std::vector<int> vars;
    for (size_t i = 0; i < n; ++i)
        if (!r.boolean.count(i))
            vars.push_back(i);
    std::shuffle(vars.begin(), vars.end(), rng);
    std::set<int> used;
    for (auto &row : r.eq)
        for (auto &[i, v] : row.a) {
            (void)v;
            used.insert(i);
        }
    for (int i : vars)
        if (!used.count(i) && out.size() < cap) {
            Assignment d(n);
            d[i] = 1;
            add(d);
        }
    // Sparse exact echelon form. Bounded work; exceeding a bound discards this source.
    std::map<int, std::map<int, Q>> pivots;
    size_t operations = 0;
    bool bounded = true;
    for (auto &row : r.eq) {
        std::map<int, Q> a;
        for (auto &[i, v] : row.a)
            a[i] = Q(v);
        while (!a.empty()) {
            if (r.budget.done() || operations > 300000 ||
                std::chrono::duration<double>(Clock::now() - start).count() > 0.15) {
                bounded = false;
                break;
            }
            int p = a.begin()->first;
            auto it = pivots.find(p);
            if (it == pivots.end()) {
                Q scale = a.begin()->second;
                for (auto &[i, v] : a) {
                    (void)i;
                    v /= scale;
                }
                pivots.emplace(p, a);
                break;
            }
            Q scale = a.begin()->second;
            for (auto &[i, v] : it->second) {
                a[i] -= scale * v;
                ++operations;
                if (a[i] == 0)
                    a.erase(i);
                else if (mpz_sizeinbase(a[i].get_num_mpz_t(), 2) > 4096) {
                    bounded = false;
                    break;
                }
            }
            if (!bounded)
                break;
        }
        if (!bounded)
            break;
    }
    if (bounded)
        for (int free : vars) {
            if (out.size() >= cap || r.budget.done())
                break;
            if (pivots.count(free))
                continue;
            std::vector<Q> v(n);
            v[free] = 1;
            for (auto it = pivots.rbegin(); it != pivots.rend(); ++it) {
                Q x = 0;
                for (auto &[i, k] : it->second)
                    if (i != it->first)
                        x += k * v[i];
                v[it->first] = -x;
            }
            Z den = 1;
            for (auto &q : v)
                den = lcm(den, q.get_den());
            if (mpz_sizeinbase(den.get_mpz_t(), 2) > 8192) {
                ++stats.counts["direction_coefficient_budget"];
                continue;
            }
            Assignment d(n);
            for (size_t i = 0; i < n; ++i)
                d[i] = v[i].get_num() * (den / v[i].get_den());
            add(d);
        }
    else
        ++stats.counts["direction_elimination_budget"];
    size_t base = out.size();
    for (size_t k = 0; base > 1 && k < 4; ++k) {
        Assignment d = out[rng() % base];
        auto &b = out[rng() % base];
        int sign = (rng() & 1) ? 1 : -1;
        for (size_t i = 0; i < n; ++i)
            d[i] += sign * b[i];
        add(d);
    }
    if (out.empty())
        ++stats.counts["no_nonzero_direction"];
    return out;
}
} // namespace lia
