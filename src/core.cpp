#include "core.hpp"
#include <algorithm>
#include <bit>
#include <iomanip>
#include <numeric>

namespace lia {
volatile std::sig_atomic_t interrupted = 0;
std::string quote(const std::string &s) {
    std::ostringstream o;
    o << '"';
    for (unsigned char c : s) {
        if (c == '"' || c == '\\')
            o << '\\' << c;
        else if (c < 32)
            o << "\\u" << std::hex << std::setw(4) << std::setfill('0') << int(c) << std::dec;
        else
            o << c;
    }
    o << '"';
    return o.str();
}
std::string Stats::json() const {
    std::ostringstream o;
    o << "{\"seconds\":{";
    bool first = true;
    for (auto &[k, v] : times) {
        if (!first)
            o << ',';
        first = false;
        o << quote(k) << ':' << v;
    }
    o << "},\"action_seconds\":{";
    first = true;
    for (auto &[k, v] : action_times) {
        if (!first) o << ',';
        first = false;
        o << quote(k) << ':' << v;
    }
    o << "},\"refinement_seconds\":" << refinement_seconds << ",\"counts\":{";
    first = true;
    for (auto &[k, v] : counts) {
        if (!first)
            o << ',';
        first = false;
        o << quote(k) << ':' << v;
    }
    return o.str() + "}}";
}
int Formula::add(z3::expr e) {
    if (auto it = index.find(e.id()); it != index.end())
        return it->second;
    if (!e.is_app() || (!e.is_int() && !e.is_bool()))
        throw std::runtime_error("only quantifier-free Int/Bool formulas supported");
    int n = nodes.size();
    index[e.id()] = n;
    nodes.emplace_back(e);
    auto k = e.decl().decl_kind();
    if (k == Z3_OP_UNINTERPRETED && e.num_args())
        throw std::runtime_error("uninterpreted functions are outside QF_LIA");
    bool compatible;
    if (e.is_bool())
        compatible = k == Z3_OP_AND || k == Z3_OP_OR || k == Z3_OP_NOT || k == Z3_OP_LE || k == Z3_OP_LT ||
                     k == Z3_OP_GT || k == Z3_OP_GE || k == Z3_OP_EQ || k == Z3_OP_TRUE || k == Z3_OP_FALSE ||
                     k == Z3_OP_UNINTERPRETED;
    else
        compatible = k == Z3_OP_ADD || k == Z3_OP_SUB || k == Z3_OP_MUL || k == Z3_OP_UMINUS ||
                     k == Z3_OP_ANUM || k == Z3_OP_ITE || k == Z3_OP_UNINTERPRETED;
    if ((k == Z3_OP_SUB || k == Z3_OP_EQ) && e.num_args() != 2)
        compatible = false;
    if (!compatible) {
        metric_compatible = false;
        metric_reason = "frozen evaluator does not handle " + e.decl().name().str();
    }
    if (e.is_numeral())
        nodes[n].numeral = Z(Z3_get_numeral_string(ctx, e));
    for (unsigned i = 0; i < e.num_args(); ++i) {
        int c = add(e.arg(i));
        nodes[n].children.push_back(c);
        nodes[c].parents.emplace_back(n, i);
    }
    return n;
}
Formula::Formula(const std::string &file) : assertions(ctx), root(ctx) {
    assertions = ctx.parse_file(file.c_str());
    root = z3::mk_and(assertions);
    add(root);
    for (size_t i = 0; i < nodes.size(); ++i)
        if (nodes[i].expr.is_const() && nodes[i].expr.decl().decl_kind() == Z3_OP_UNINTERPRETED)
            variables.push_back(i);
    std::sort(variables.begin(), variables.end(), [&](int a, int b) {
        auto x = nodes[a].expr.decl().name().str(), y = nodes[b].expr.decl().name().str();
        return x == y ? nodes[a].boolean < nodes[b].boolean : x < y;
    });
    std::set<std::string> names;
    for (size_t i = 0; i < variables.size(); ++i) {
        Node &n = nodes[variables[i]];
        n.variable = i;
        auto name = n.expr.decl().name().str();
        if (n.boolean || name.find_first_of(":;\n\r") != std::string::npos || !names.insert(name).second) {
            legacy_compatible = false;
            legacy_reason = "Boolean, delimiter-containing, or sort-overloaded names cannot be represented "
                            "by the original parser";
        }
    }
    if (variables.empty()) {
        legacy_compatible = false;
        legacy_reason = "original parser cannot read an empty assignment";
    }
}
Observation Formula::evaluate(const Assignment &m, bool trace) const {
    if (m.size() != variables.size())
        throw std::runtime_error("incomplete assignment");
    Observation o;
    o.values.resize(nodes.size());
    o.visited.resize(nodes.size());
    std::optional<z3::model> fallback;
    std::function<Z(int)> eval = [&](int id) -> Z {
        if (o.visited[id] && !trace)
            return o.values[id];
        const Node &n = nodes[id];
        const auto &c = n.children;
        Z v;
        if (n.variable >= 0)
            v = m[n.variable];
        else
            switch (n.expr.decl().decl_kind()) {
            case Z3_OP_ANUM:
                v = n.numeral;
                break;
            case Z3_OP_TRUE:
                v = 1;
                break;
            case Z3_OP_FALSE:
                v = 0;
                break;
            case Z3_OP_AND:
                v = 1;
                for (int a : c)
                    if (eval(a) == 0) {
                        v = 0;
                        break;
                    }
                break;
            case Z3_OP_OR:
                v = 0;
                for (int a : c)
                    if (eval(a) != 0) {
                        v = 1;
                        break;
                    }
                break;
            case Z3_OP_NOT:
                v = eval(c[0]) == 0;
                break;
            case Z3_OP_ITE:
                v = eval(c[0]) != 0 ? eval(c[1]) : eval(c[2]);
                break;
            case Z3_OP_ADD:
                v = 0;
                for (int a : c)
                    v += eval(a);
                break;
            case Z3_OP_MUL:
                v = 1;
                for (int a : c)
                    v *= eval(a);
                break;
            case Z3_OP_SUB:
                v = eval(c[0]);
                for (size_t i = 1; i < c.size(); ++i)
                    v -= eval(c[i]);
                break;
            case Z3_OP_UMINUS:
                v = -eval(c[0]);
                break;
            case Z3_OP_EQ:
            case Z3_OP_LE:
            case Z3_OP_LT:
            case Z3_OP_GE:
            case Z3_OP_GT: {
                Z a = eval(c[0]), b = eval(c[1]);
                auto k = n.expr.decl().decl_kind();
                v = k == Z3_OP_EQ   ? a == b
                    : k == Z3_OP_LE ? a <= b
                    : k == Z3_OP_LT ? a < b
                    : k == Z3_OP_GE ? a >= b
                                    : a > b;
                break;
            }
            default:
                // An unsupported evaluator operation is explicitly diagnostic-only.
                // Z3 concrete model evaluation preserves the input, without solving.
                for (int a : c)
                    eval(a);
                if (!fallback)
                    fallback = as_model(m);
                {
                    auto e = fallback->eval(n.expr, true);
                    v = e.is_true() ? Z(1) : e.is_false() ? Z(0) : Z(Z3_get_numeral_string(ctx, e));
                }
                break;
            }
        o.values[id] = v;
        o.visited[id] = 1;
        if (trace)
            o.events.push_back(id);
        return v;
    };
    eval(0);
    return o;
}
z3::expr Formula::value(int var, const Z &v) const {
    auto &c = const_cast<z3::context &>(ctx);
    if (nodes[variables[var]].boolean) {
        if (v != 0 && v != 1)
            throw std::runtime_error("invalid Boolean value");
        return c.bool_val(v != 0);
    }
    return c.int_val(v.get_str().c_str());
}
z3::model Formula::as_model(const Assignment &m) const {
    if (m.size() != variables.size())
        throw std::runtime_error("incomplete assignment");
    z3::model model(const_cast<z3::context &>(ctx));
    for (size_t i = 0; i < m.size(); ++i) {
        auto d = nodes[variables[i]].expr.decl();
        auto v = value(i, m[i]);
        model.add_const_interp(d, v);
    }
    return model;
}
bool Formula::validate(const Assignment &m) const { return as_model(m).eval(root, true).is_true(); }
Assignment Formula::complete(z3::model m) const {
    Assignment a;
    for (int id : variables) {
        auto v = m.eval(nodes[id].expr, true);
        a.push_back(nodes[id].boolean ? Z(v.is_true() ? 1 : 0) : Z(Z3_get_numeral_string(ctx, v)));
    }
    return a;
}
std::string Formula::key(const Assignment &m) const {
    std::string s;
    for (auto &v : m) {
        auto t = v.get_str();
        s += std::to_string(t.size()) + ":" + t;
    }
    return s;
}
std::string Formula::model_json(const Assignment &m) const {
    std::ostringstream o;
    o << '[';
    for (size_t i = 0; i < variables.size(); ++i) {
        if (i)
            o << ',';
        auto &n = nodes[variables[i]];
        o << "{\"name\":" << quote(n.expr.decl().name().str())
          << ",\"sort\":" << quote(n.boolean ? "Bool" : "Int") << ",\"value\":";
        if (n.boolean)
            o << (m[i] != 0 ? "true" : "false");
        else
            o << quote(m[i].get_str());
        o << '}';
    }
    return o.str() + "]";
}
z3::expr Formula::reach(int id) {
    if (id == 0)
        return ctx.bool_val(true);
    if (auto it = reach_cache.find(id); it != reach_cache.end())
        return it->second;
    z3::expr_vector paths(ctx);
    for (auto [p, position] : nodes[id].parents) {
        z3::expr r = reach(p);
        auto k = nodes[p].expr.decl().decl_kind();
        auto &ch = nodes[p].children;
        if (k == Z3_OP_AND || k == Z3_OP_OR)
            for (int i = 0; i < position; ++i)
                r = r && (k == Z3_OP_AND ? nodes[ch[i]].expr : !nodes[ch[i]].expr);
        if (k == Z3_OP_ITE && position > 0)
            r = r && (position == 1 ? nodes[ch[0]].expr : !nodes[ch[0]].expr);
        paths.push_back(r);
    }
    z3::expr r = z3::mk_or(paths).simplify();
    reach_cache.emplace(id, r);
    return r;
}
unsigned long Formula::total_bits() const {
    unsigned long r = 0;
    for (auto &n : nodes)
        r += n.boolean ? 1 : 64;
    return r;
}
static uint64_t low64(const Z &z) {
    Z r;
    mpz_fdiv_r_2exp(r.get_mpz_t(), z.get_mpz_t(), 64);
    return r.get_ui();
}
Gain Coverage::gain(const Observation &o) const {
    Gain g;
    for (size_t i = 0; i < states.size(); ++i)
        if (o.visited[i]) {
            uint64_t mask = f.nodes[i].boolean ? 1 : ~uint64_t(0), v = low64(o.values[i]) & mask;
            auto s = states[i];
            uint64_t one = s.one | v, zero = s.zero | (~v & mask);
            g.covered += std::popcount(one & zero) - std::popcount(s.one & s.zero);
            g.observed += std::popcount(one ^ s.one) + std::popcount(zero ^ s.zero);
        }
    return g;
}
Gain Coverage::accept(const Observation &o) {
    auto g = gain(o);
    for (size_t i = 0; i < states.size(); ++i)
        if (o.visited[i]) {
            uint64_t mask = f.nodes[i].boolean ? 1 : ~uint64_t(0), v = low64(o.values[i]) & mask;
            states[i].one |= v;
            states[i].zero |= ~v & mask;
        }
    return g;
}
unsigned long Coverage::covered() const {
    unsigned long n = 0;
    for (auto s : states)
        n += std::popcount(s.zero & s.one);
    return n;
}
std::string Coverage::json(const Observation *o) const {
    std::ostringstream s;
    s << "{\"covered\":" << covered() << ",\"total\":" << f.total_bits() << ",\"states\":[";
    for (size_t i = 0; i < states.size(); ++i) {
        if (i)
            s << ',';
        s << "[" << quote(std::to_string(states[i].zero)) << "," << quote(std::to_string(states[i].one))
          << "]";
    }
    s << "]";
    if (o) {
        s << ",\"events\":[";
        for (size_t i = 0; i < o->events.size(); ++i) {
            if (i)
                s << ',';
            s << o->events[i];
        }
        s << "],\"visited\":[";
        for (size_t i = 0; i < o->visited.size(); ++i) {
            if (i)
                s << ',';
            s << int(o->visited[i]);
        }
        s << "]";
    }
    return s.str() + "}";
}
std::vector<Target> active_targets(const Formula &f, const Coverage &cov, std::mt19937_64 &rng, size_t cap) {
    std::vector<Target> out;
    // Bounded random probing, then bounded rotating scan: never directions x AST x 64.
    std::set<Target> used;
    size_t attempts = std::max<size_t>(256, cap * 32), n = f.nodes.size();
    for (size_t k = 0; k < attempts && out.size() < cap; ++k) {
        int i = rng() % n;
        auto &node = f.nodes[i];
        if (node.expr.is_numeral() || node.expr.is_true() || node.expr.is_false())
            continue;
        unsigned j = node.boolean ? 0 : ((k % 3 == 0) ? rng() % 8 : rng() % 64);
        auto s = cov.states[i];
        uint64_t mask = uint64_t(1) << j;
        if ((s.zero & s.one) & mask)
            continue;
        bool v = (s.zero & mask) ? true : (s.one & mask) ? false : bool(rng() & 1);
        Target t{i, j, v};
        if (used.insert(t).second)
            out.push_back(t);
    }
    return out;
}
} // namespace lia
