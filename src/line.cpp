#include "line.hpp"
#include <algorithm>

namespace lia {
std::string line_status_name(LineStatus status) {
    return status == LineStatus::Exact ? "exact" : status == LineStatus::Unsupported ? "unsupported" : "budget_exceeded";
}
LineDomain::LineDomain(std::vector<Interval> input) {
    std::erase_if(input, [](const Interval &r) { return r.empty || (r.lo && r.hi && *r.lo > *r.hi); });
    std::sort(input.begin(), input.end(), [](const Interval &a, const Interval &b) {
        if (!a.lo) return bool(b.lo);
        if (!b.lo) return false;
        return *a.lo < *b.lo;
    });
    for (const auto &r : input) {
        if (intervals.empty() || (intervals.back().hi && r.lo && *r.lo > *intervals.back().hi + 1))
            intervals.push_back(r);
        else if (!r.hi) intervals.back().hi.reset();
        else if (intervals.back().hi && *r.hi > *intervals.back().hi) intervals.back().hi = r.hi;
    }
}
LineDomain LineDomain::all() { return LineDomain({Interval{}}); }
LineDomain LineDomain::point(const Z &z) { return LineDomain({Interval{z,z,false}}); }
bool LineDomain::contains(const Z &z) const {
    return std::any_of(intervals.begin(),intervals.end(),[&](const auto &r){return r.contains(z);});
}
LineDomain LineDomain::unite(const LineDomain &other) const {
    auto result=intervals;
    result.insert(result.end(),other.intervals.begin(),other.intervals.end());
    return LineDomain(std::move(result));
}
LineDomain LineDomain::intersect(const LineDomain &other) const {
    std::vector<Interval> result;
    size_t a=0,b=0;
    while (a<intervals.size() && b<other.intervals.size()) {
        const auto &x=intervals[a], &y=other.intervals[b];
        Interval intersection;
        intersection.lo = !x.lo ? y.lo : !y.lo ? x.lo : std::optional<Z>(std::max(*x.lo,*y.lo));
        intersection.hi = !x.hi ? y.hi : !y.hi ? x.hi : std::optional<Z>(std::min(*x.hi,*y.hi));
        if (!intersection.lo || !intersection.hi || *intersection.lo<=*intersection.hi)
            result.push_back(intersection);
        if (!x.hi && !y.hi) break;
        if (!y.hi || (x.hi && *x.hi<*y.hi)) ++a;
        else ++b;
    }
    return LineDomain(std::move(result));
}
LineDomain LineDomain::complement() const {
    std::vector<Interval> result;
    std::optional<Z> start;
    for (const auto &r:intervals) {
        if (r.lo) result.push_back({start,Z(*r.lo-1),false});
        if (!r.hi) return LineDomain(std::move(result));
        start=Z(*r.hi+1);
    }
    result.push_back({start,{},false});
    return LineDomain(std::move(result));
}
LineDomain LineDomain::without(const Z &z) const { return intersect(point(z).complement()); }
std::string LineDomain::json() const {
    std::ostringstream out; out << '[';
    for (size_t i=0;i<intervals.size();++i) {
        if (i) out << ',';
        const auto &r=intervals[i];
        out << '[' << (r.lo ? quote(r.lo->get_str()) : "null") << ','
            << (r.hi ? quote(r.hi->get_str()) : "null") << ']';
    }
    return out.str()+']';
}
const Affine &AffineSyntax::linear(int node, Budget &budget) {
    if (budget.done()) throw LineFailure(LineStatus::BudgetExceeded,"affine analysis deadline");
    if (auto it=cache.find(node);it!=cache.end()) return it->second;
    if (cache.size()>=limits.dag_nodes) throw LineFailure(LineStatus::BudgetExceeded,"affine DAG limit");
    const auto &n=f.nodes[node];
    Affine a;
    if (n.boolean) throw LineFailure(LineStatus::Unsupported,"Boolean arithmetic");
    if (n.variable>=0) a.a[n.variable]=1;
    else switch (n.expr.decl().decl_kind()) {
    case Z3_OP_ANUM: a.c=n.numeral; break;
    case Z3_OP_ADD:
        for (int c:n.children) a.add(linear(c,budget));
        break;
    case Z3_OP_SUB:
        a=linear(n.children[0],budget);
        for (size_t i=1;i<n.children.size();++i) a.add(linear(n.children[i],budget),-1);
        break;
    case Z3_OP_UMINUS: a.add(linear(n.children[0],budget),-1); break;
    case Z3_OP_MUL: {
        Z scalar=1; std::optional<Affine> nonconstant;
        for (int c:n.children) {
            const auto &part=linear(c,budget);
            if (part.a.empty()) scalar*=part.c;
            else {
                if (nonconstant) throw LineFailure(LineStatus::Unsupported,"nonlinear arithmetic");
                nonconstant=part;
            }
        }
        if (nonconstant) a.add(*nonconstant,scalar); else a.c=scalar;
        break;
    }
    case Z3_OP_ITE:
        throw LineFailure(LineStatus::Unsupported,"arithmetic ite requires piecewise affine analysis");
    default: throw LineFailure(LineStatus::Unsupported,"non-affine arithmetic");
    }
    if (mpz_sizeinbase(a.c.get_mpz_t(),2)>limits.integer_bits)
        throw LineFailure(LineStatus::BudgetExceeded,"affine integer bit limit");
    for (const auto &[i,value]:a.a) {
        (void)i;
        if (mpz_sizeinbase(value.get_mpz_t(),2)>limits.integer_bits)
            throw LineFailure(LineStatus::BudgetExceeded,"affine coefficient bit limit");
    }
    if (terms+a.a.size()>limits.affine_terms)
        throw LineFailure(LineStatus::BudgetExceeded,"affine term storage limit");
    terms+=a.a.size();
    return cache.emplace(node,std::move(a)).first->second;
}
StructuralEquations structural_equalities(Formula &f, AffineSyntax &syntax, Budget &budget) {
    StructuralEquations result;
    std::vector<int> pending{0};
    while (!pending.empty()) {
        if (budget.done()) { result.complete=false; result.reason="collection deadline"; break; }
        int id=pending.back(); pending.pop_back();
        const auto &n=f.nodes[id];
        if (n.expr.decl().decl_kind()==Z3_OP_AND)
            pending.insert(pending.end(),n.children.begin(),n.children.end());
        else if (n.expr.decl().decl_kind()==Z3_OP_EQ && n.children.size()==2 &&
                 !f.nodes[n.children[0]].boolean) {
            try {
                Affine a=syntax.linear(n.children[0],budget);
                a.add(syntax.linear(n.children[1],budget),-1);
                result.rows.push_back(std::move(a));
            } catch (const LineFailure &error) {
                if (error.status==LineStatus::BudgetExceeded) {
                    result.complete=false; result.reason=error.what(); break;
                }
                // An ite-dependent equality is not promoted to a global affine relation.
            }
        }
    }
    return result;
}
LineAnalyzer::LineAnalyzer(Formula &formula, const Assignment &base, const Assignment &direction,
                           AffineSyntax &syntax, Budget &budget)
    : f(formula), base(base), direction(direction), syntax(syntax), budget(budget),
      truth_cache(f.nodes.size()), reach_cache(f.nodes.size()) {
    for (size_t i=0;i<f.variables.size();++i)
        if (f.nodes[f.variables[i]].boolean && direction[i]!=0)
            throw std::invalid_argument("LineDomain fixes Boolean assignments");
}
void LineAnalyzer::tick() {
    if (budget.done() || ++processed>syntax.limits.dag_nodes)
        throw LineFailure(LineStatus::BudgetExceeded,"line deadline or DAG limit");
}
LineDomain LineAnalyzer::bounded(LineDomain domain) {
    if (domain.intervals.size()>syntax.limits.intervals)
        throw LineFailure(LineStatus::BudgetExceeded,"interval count limit");
    for (const auto &r:domain.intervals)
        for (const auto &endpoint:{r.lo,r.hi})
            if (endpoint && mpz_sizeinbase(endpoint->get_mpz_t(),2)>syntax.limits.integer_bits)
                throw LineFailure(LineStatus::BudgetExceeded,"line endpoint bit limit");
    return domain;
}
std::pair<Z,Z> LineAnalyzer::numeric(int node) {
    const auto &a=syntax.linear(node,budget);
    Z alpha=a.dot(direction), beta=a.at(base);
    if (mpz_sizeinbase(alpha.get_mpz_t(),2)>syntax.limits.integer_bits ||
        mpz_sizeinbase(beta.get_mpz_t(),2)>syntax.limits.integer_bits)
        throw LineFailure(LineStatus::BudgetExceeded,"substitution integer bit limit");
    return {alpha,beta};
}
LineDomain LineAnalyzer::truth(int id) {
    if (truth_cache[id]) return *truth_cache[id];
    tick();
    const auto &node=f.nodes[id]; const auto &children=node.children;
    if (!node.boolean) throw LineFailure(LineStatus::Unsupported,"non-Boolean truth domain");
    auto kind=node.expr.decl().decl_kind();
    LineDomain result;
    if (node.variable>=0) result=base[node.variable]!=0 ? LineDomain::all() : LineDomain{};
    else if (kind==Z3_OP_TRUE) result=LineDomain::all();
    else if (kind==Z3_OP_FALSE) {}
    else if (kind==Z3_OP_NOT) result=truth(children[0]).complement();
    else if (kind==Z3_OP_AND || kind==Z3_OP_OR) {
        result=kind==Z3_OP_AND ? LineDomain::all() : LineDomain{};
        for (int c:children) {
            auto next=truth(c);
            result=bounded(kind==Z3_OP_AND ? result.intersect(next) : result.unite(next));
        }
    } else if (kind==Z3_OP_IMPLIES)
        result=truth(children[0]).complement().unite(truth(children[1]));
    else if (kind==Z3_OP_XOR) {
        result=truth(children[0]);
        for (size_t i=1;i<children.size();++i) {
            auto next=truth(children[i]);
            result=bounded(result.intersect(next.complement()).unite(result.complement().intersect(next)));
        }
    } else if (kind==Z3_OP_ITE) {
        auto condition=truth(children[0]);
        result=condition.intersect(truth(children[1])).unite(condition.complement().intersect(truth(children[2])));
    } else if (kind==Z3_OP_EQ && f.nodes[children[0]].boolean) {
        auto a=truth(children[0]), b=truth(children[1]);
        result=a.intersect(b).unite(a.complement().intersect(b.complement()));
    } else if (kind==Z3_OP_LE || kind==Z3_OP_LT || kind==Z3_OP_GE || kind==Z3_OP_GT || kind==Z3_OP_EQ) {
        auto [a0,b0]=numeric(children[0]); auto [a1,b1]=numeric(children[1]);
        Z alpha=a0-a1, beta=b0-b1;
        if (kind==Z3_OP_EQ) {
            if (alpha==0) result=beta==0 ? LineDomain::all() : LineDomain{};
            else if (mod(Z(-beta),alpha)==0) result=LineDomain::point(Z(-beta/alpha));
        } else {
            if (kind==Z3_OP_GE || kind==Z3_OP_GT) { alpha=-alpha; beta=-beta; }
            if (kind==Z3_OP_LT || kind==Z3_OP_GT) ++beta;
            Interval interval; interval.constrain(alpha,Z(-beta));
            result=LineDomain({interval});
        }
    } else throw LineFailure(LineStatus::Unsupported,"unsupported Boolean operator in line domain");
    result=bounded(std::move(result)); truth_cache[id]=result; return result;
}
LineDomain LineAnalyzer::reach(int id) {
    if (id==0) return LineDomain::all();
    if (reach_cache[id]) return *reach_cache[id];
    tick(); LineDomain result;
    for (auto [parent,position]:f.nodes[id].parents) {
        auto path=reach(parent);
        const auto &p=f.nodes[parent];
        auto kind=p.expr.decl().decl_kind();
        if (kind==Z3_OP_AND || kind==Z3_OP_OR)
            for (int i=0;i<position;++i) {
                auto preceding=truth(p.children[i]);
                path=bounded(path.intersect(kind==Z3_OP_AND ? preceding : preceding.complement()));
            }
        if (kind==Z3_OP_ITE && position>0) {
            auto condition=truth(p.children[0]);
            path=bounded(path.intersect(position==1 ? condition : condition.complement()));
        }
        result=bounded(result.unite(path));
    }
    reach_cache[id]=result; return result;
}
LineResult LineAnalyzer::feasible() {
    try { return {LineStatus::Exact,truth(0),""}; }
    catch (const LineFailure &error) { return {error.status,{},error.what()}; }
}
LineResult LineAnalyzer::observable(int node) {
    try { return {LineStatus::Exact,bounded(truth(0).intersect(reach(node))),""}; }
    catch (const LineFailure &error) { return {error.status,{},error.what()}; }
}
LineResult LineAnalyzer::boolean_target(Target target) {
    try {
        auto value=truth(target.node);
        auto observable=truth(0).intersect(reach(target.node));
        last_observable_empty=observable.without(0).intervals.empty();
        return {LineStatus::Exact,bounded(observable.intersect(target.desired ? value : value.complement())),""};
    } catch (const LineFailure &error) { return {error.status,{},error.what()}; }
}
std::optional<Z> line_bit_witness(const LineDomain &domain, const Z &alpha, const Z &beta,
                                 unsigned j, bool desired, std::mt19937_64 &rng, bool random) {
    std::vector<std::tuple<Z,Z,Z>> windows;
    Z total=0;
    // Remove the origin BEFORE choosing any period window from unbounded components.
    for (const auto &range:domain.without(0).intervals) {
        auto [lo,hi]=range.finite(alpha,j);
        Z hits=random ? Z(hi-lo+1) : count_bit_hits(alpha,beta,j,desired,lo,hi);
        windows.push_back({lo,hi,hits}); total+=hits;
    }
    if (total==0) return {};
    Z rank=random_below(total,rng);
    for (const auto &[lo,hi,hits]:windows) {
        if (rank<hits) return random ? std::optional<Z>(Z(lo+rank)) :
            find_bit_hit(alpha,beta,j,desired,lo,hi,rank);
        rank-=hits;
    }
    throw std::logic_error("line witness rank mismatch");
}
} // namespace lia
