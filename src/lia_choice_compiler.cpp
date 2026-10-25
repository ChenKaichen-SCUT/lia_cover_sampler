#include "lia_choice_compiler.hpp"
#include <algorithm>
#include <iomanip>
#include <tuple>
#include <unordered_set>

namespace lia {
namespace {
struct CompileStop { std::string reason; };
struct Linear { std::map<unsigned,Z> terms; Z constant=0; };
struct Compiler {
    CompiledProblem &out;
    Formula &f;
    Budget local;
    size_t operations=0;
    std::vector<z3::expr> clauses;
    std::map<unsigned,unsigned> originals;
    std::map<unsigned,z3::expr> symbols;
    std::map<unsigned,std::optional<Linear>> linear_cache;
    std::map<unsigned,z3::expr> linear_owners; // AST ids may be recycled after temporaries die.
    explicit Compiler(CompiledProblem &o,Budget &parent):out(o),f(o.original_formula) {
        local.seconds=o.limits.seconds;local.parent=&parent;
        for(unsigned i=0;i<f.variables.size();++i) {
            auto e=f.nodes[f.variables[i]].expr;
            originals.emplace(e.id(),i);symbols.emplace(e.id(),e);
        }
    }
    void tick(size_t count=1) {
        operations+=count;
        if(operations>out.limits.max_operations) throw CompileStop{"operation_limit"};
        if(local.done()) throw CompileStop{interrupted?"cancelled":"compile_deadline"};
    }
    void flatten(z3::expr e,std::vector<z3::expr> &dst) {
        tick();
        if(e.is_and()) for(unsigned i=0;i<e.num_args();++i) flatten(e.arg(i),dst);
        else dst.push_back(e);
    }
    bool literal(z3::expr e,Z &z) {
        tick();
        if(e.is_int() && e.is_numeral()) {z=Z(Z3_get_numeral_string(f.ctx,e));return true;}
        if(e.is_int() && e.decl().decl_kind()==Z3_OP_UMINUS && e.num_args()==1) {
            if(literal(e.arg(0),z)){z=-z;return true;}
        }
        return false;
    }
    bool assignment(z3::expr e,std::map<unsigned,Z> &values) {
        std::vector<z3::expr> atoms;flatten(e,atoms);
        for(auto atom:atoms) {
            tick();
            if(!atom.is_eq() || atom.num_args()!=2) return false;
            auto a=atom.arg(0),b=atom.arg(1);
            if(!originals.count(a.id())) std::swap(a,b);
            auto it=originals.find(a.id());Z value;
            if(it==originals.end() || !a.is_int() || !literal(b,value)) return false;
            auto [at,inserted]=values.emplace(it->second,value);
            if(!inserted && at->second!=value) return false;
        }
        return !values.empty();
    }
    void add(Linear &a,const Linear &b,const Z &factor) {
        tick(b.terms.size()+1);a.constant+=factor*b.constant;
        for(auto &[v,c]:b.terms) {
            a.terms[v]+=factor*c;
            if(a.terms[v]==0) a.terms.erase(v);
        }
        check_coefficients(a);
    }
    void check_coefficients(const Linear &a) {
        auto check=[&](const Z &v){if(mpz_sizeinbase(v.get_mpz_t(),2)>out.limits.max_coefficient_bits) throw CompileStop{"coefficient_bits"};};
        check(a.constant);for(auto &[_,c]:a.terms)check(c);
    }
    std::optional<Linear> linear(z3::expr e) {
        tick();linear_owners.emplace(e.id(),e);auto found=linear_cache.find(e.id());if(found!=linear_cache.end())return found->second;
        std::optional<Linear> result;
        if(e.is_int()) {
            Z z;
            if(literal(e,z)){Linear r;r.constant=z;result=r;}
            else if(e.is_const() && e.decl().decl_kind()==Z3_OP_UNINTERPRETED) {
                Linear r;r.terms[e.id()]=1;symbols.emplace(e.id(),e);result=r;
            } else {
                auto kind=e.decl().decl_kind();Linear r;bool valid=true;
                if(kind==Z3_OP_ADD || kind==Z3_OP_SUB || kind==Z3_OP_UMINUS) {
                    for(unsigned i=0;i<e.num_args();++i) {
                        auto term=linear(e.arg(i));if(!term){valid=false;break;}
                        add(r,*term,(kind==Z3_OP_UMINUS || (kind==Z3_OP_SUB && i>0))?Z(-1):Z(1));
                    }
                    if(valid)result=r;
                } else if(kind==Z3_OP_MUL) {
                    Z factor=1;std::optional<Linear> variable;
                    for(unsigned i=0;i<e.num_args();++i) {
                        auto term=linear(e.arg(i));if(!term){valid=false;break;}
                        if(term->terms.empty()) factor*=term->constant;
                        else if(variable){valid=false;break;} else variable=term;
                    }
                    if(valid){if(variable)add(r,*variable,factor);else r.constant=factor;result=r;}
                }
            }
        }
        if(result)check_coefficients(*result);
        linear_cache.emplace(e.id(),result);return result;
    }
    // Count DAG references, including recovery expressions: shared subexpressions
    // are not expanded into a tree. Counting the recovery DAG protects chains.
    std::map<unsigned,unsigned> scan(const std::vector<z3::expr>&roots,bool limits=true) {
        std::map<unsigned,unsigned> references;
        std::unordered_set<unsigned> seen;
        std::vector<z3::expr> todo=roots;
        unsigned bits=0;
        while(!todo.empty()) {
            if(limits)tick();
            auto e=todo.back();todo.pop_back();
            if(e.is_const() && e.decl().decl_kind()==Z3_OP_UNINTERPRETED)++references[e.id()];
            if(!seen.insert(e.id()).second)continue;
            if(e.is_int() && e.is_numeral()) {
                Z z(Z3_get_numeral_string(f.ctx,e));bits=std::max(bits,unsigned(mpz_sizeinbase(z.get_mpz_t(),2)));
            }
            for(unsigned i=0;i<e.num_args();++i)todo.push_back(e.arg(i));
        }
        if(limits && (seen.size()>out.limits.max_dag_nodes || seen.size()>2*f.nodes.size()+1024))throw CompileStop{"dag_growth"};
        if(limits && bits>out.limits.max_coefficient_bits)throw CompileStop{"coefficient_bits"};
        return references;
    }
    void substitute_transaction(const z3::expr_vector &from,const z3::expr_vector &to,
                                size_t source,const std::vector<z3::expr>&extra) {
        tick();std::vector<z3::expr> next_clauses,next_recovery;
        for(size_t i=0;i<clauses.size();++i) {
            tick();next_clauses.push_back(i==source?f.ctx.bool_val(true):clauses[i].substitute(from,to).simplify());
        }
        for(auto e:extra)next_clauses.push_back(e);
        for(auto r:out.reconstruction_map){tick();next_recovery.push_back(r.substitute(from,to).simplify());}
        auto roots=next_clauses;roots.insert(roots.end(),next_recovery.begin(),next_recovery.end());
        scan(roots);tick();
        z3::expr_vector conjuncts(f.ctx);for(auto e:next_clauses)if(!e.is_true())conjuncts.push_back(e);
        auto next_formula=z3::mk_and(conjuncts);tick();
        // Publish formula and full recovery together, only after all limits pass.
        out.compiled_formula=next_formula;clauses.swap(next_clauses);
        out.reconstruction_map.swap(next_recovery);
    }
    void encode_choices() {
        struct Proposal {size_t clause;std::map<unsigned,Z>a,b;};
        std::vector<Proposal> proposals;
        for(size_t j=0;j<clauses.size();++j) {
            auto e=clauses[j];tick();if(!e.is_or()||e.num_args()!=2)continue;
            Proposal p{j,{},{}};
            if(!assignment(e.arg(0),p.a)||!assignment(e.arg(1),p.b)||p.a.size()!=p.b.size())continue;
            bool same=true;for(auto &[v,_]:p.a)if(!p.b.count(v))same=false;
            if(same){proposals.push_back(p);++out.statistics.counts["recognized_choice_groups"];}
        }
        for(auto &p:proposals) {
            tick();bool overlap=false;for(auto &[v,_]:p.a)overlap|=out.eliminated.count(v)>0;
            if(overlap){++out.statistics.counts["overlapping_groups_retained"];continue;}
            ChoiceEncoding encoding;encoding.source_clause=p.clause;
            bool distinct=p.a!=p.b;
            if(distinct) {
                // Z3 fresh names can coincide with an explicitly declared name.
                // Check actual identities, not just the fresh-name API promise.
                do {tick();encoding.choice=z3::expr(f.ctx,Z3_mk_fresh_const(f.ctx,"lia_choice",f.ctx.int_sort()));}
                while(originals.count(encoding.choice->id()) || symbols.count(encoding.choice->id()));
                symbols.emplace(encoding.choice->id(),*encoding.choice);
            }
            z3::expr_vector from(f.ctx),to(f.ctx);std::vector<z3::expr> extra;
            for(auto &[v,a]:p.a) {
                tick();Z difference=p.b.at(v)-a;
                encoding.variables.push_back(v);encoding.first.push_back(a);encoding.second.push_back(p.b.at(v));
                from.push_back(f.nodes[f.variables[v]].expr);
                auto expression=f.ctx.int_val(a.get_str().c_str());
                if(distinct && difference!=0)expression=expression+f.ctx.int_val(difference.get_str().c_str())*(*encoding.choice);
                to.push_back(expression.simplify());
            }
            if(distinct)extra={*encoding.choice>=0,*encoding.choice<=1};
            auto source_text=clauses[p.clause].to_string();
            substitute_transaction(from,to,p.clause,extra);
            for(auto v:encoding.variables)out.eliminated.insert(v);
            out.choices.push_back(encoding);
            out.statistics.counts[distinct?"converted_choice_groups":"identical_choices_collapsed"]++;
            out.statistics.counts["choice_eliminated_original_variables"]+=encoding.variables.size();
            out.rewrite_evidence.push_back("{\"kind\":"+quote(distinct?"integer_binary_choice":"identical_alternatives")+
                ",\"source_clause\":"+std::to_string(p.clause)+",\"source\":"+quote(source_text)+"}");
        }
    }
    void eliminate() {
        while(out.statistics.counts["unit_equation_eliminations"]<out.limits.max_eliminations) {
            tick();linear_cache.clear();linear_owners.clear();auto roots=clauses;
            roots.insert(roots.end(),out.reconstruction_map.begin(),out.reconstruction_map.end());
            auto references=scan(roots);
            struct Pick {size_t clause;unsigned variable;Linear row;};
            std::optional<Pick> best;
            std::tuple<size_t,unsigned,size_t,unsigned,size_t> best_cost;
            for(size_t j=0;j<clauses.size();++j) {
                auto e=clauses[j];tick();if(!e.is_eq()||e.num_args()!=2||!e.arg(0).is_int())continue;
                auto a=linear(e.arg(0)),b=linear(e.arg(1));if(!a||!b)continue;add(*a,*b,-1);
                if(a->terms.empty() || a->terms.size()>out.limits.max_row_terms)continue;
                for(auto &[symbol,coefficient]:a->terms) {
                    if(coefficient!=1 && coefficient!=-1)continue;
                    auto v=originals.find(symbol);if(v==originals.end()||out.eliminated.count(v->second))continue;
                    unsigned refs=references[symbol];size_t width=a->terms.size()-1;
                    size_t growth=width? (width-1)*refs:0;
                    if(refs>out.limits.max_references||growth>out.limits.max_term_growth)continue;
                    auto cost=std::make_tuple(width,refs,growth,v->second,j);
                    if(!best||cost<best_cost){best=Pick{j,v->second,*a};best_cost=cost;}
                }
            }
            if(!best)return;
            auto variable=f.nodes[f.variables[best->variable]].expr;
            Z scale=-best->row.terms.at(variable.id());
            Z c=scale*best->row.constant;auto rhs=f.ctx.int_val(c.get_str().c_str());
            for(auto &[id,k]:best->row.terms)if(id!=variable.id()) {
                Z coefficient=scale*k;
                rhs=rhs+f.ctx.int_val(coefficient.get_str().c_str())*symbols.at(id);
            }
            rhs=rhs.simplify();auto source_text=clauses[best->clause].to_string();
            z3::expr_vector from(f.ctx),to(f.ctx);from.push_back(variable);to.push_back(rhs);
            substitute_transaction(from,to,best->clause,{});
            out.eliminated.insert(best->variable);++out.statistics.counts["unit_equation_eliminations"];
            out.rewrite_evidence.push_back("{\"kind\":\"unit_equation\",\"source_clause\":"+
                std::to_string(best->clause)+",\"source\":"+quote(source_text)+",\"variable\":"+
                quote(variable.to_string())+",\"replacement\":"+quote(rhs.to_string())+"}");
        }
        throw CompileStop{"elimination_cap"};
    }
    void run() {
        try{flatten(f.root,clauses);encode_choices();eliminate();}
        catch(const CompileStop &s){out.stop_reason=s.reason;}
        out.statistics.counts["compiler_operations"]=operations;
    }
};
size_t dag(const std::vector<z3::expr>&roots,std::map<unsigned,z3::expr>*variables=nullptr,unsigned *bits=nullptr) {
    std::unordered_set<unsigned> seen;std::vector<z3::expr> todo=roots;
    while(!todo.empty()){
        auto e=todo.back();todo.pop_back();if(!seen.insert(e.id()).second)continue;
        if(variables && e.is_const()&&e.decl().decl_kind()==Z3_OP_UNINTERPRETED)variables->emplace(e.id(),e);
        if(bits && e.is_int()&&e.is_numeral()){Z z(Z3_get_numeral_string(e.ctx(),e));*bits=std::max(*bits,unsigned(mpz_sizeinbase(z.get_mpz_t(),2)));}
        for(unsigned i=0;i<e.num_args();++i)todo.push_back(e.arg(i));
    }
    return seen.size();
}
}
CompiledProblem::CompiledProblem(Formula &f,bool enabled,Budget &budget,ChoiceCompileLimits policy)
    :original_formula(f),compiled_formula(f.root),limits(policy) {
    auto start=Clock::now();
    for(auto n:f.variables)reconstruction_map.push_back(f.nodes[n].expr);
    if(enabled){Compiler compiler(*this,budget);compiler.run();}else stop_reason="disabled";
    auto roots=reconstruction_map;roots.push_back(compiled_formula);std::map<unsigned,z3::expr> vars;unsigned bits=0;
    statistics.counts["combined_dag_nodes"]=dag(roots,&vars,&bits);
    for(auto &[_,v]:vars)solving_variables.push_back(v);
    // No eliminated original symbol may be model-completed during recovery.
    for(auto i:eliminated)if(vars.count(f.nodes[f.variables[i]].expr.id()))throw std::logic_error("eliminated symbol survived reconstruction");
    statistics.counts["original_variables"]=f.variables.size();
    for(auto id:f.variables)++statistics.counts[f.nodes[id].boolean?"original_bool_variables":"original_int_variables"];
    statistics.counts["solving_variables"]=vars.size();statistics.counts["original_dag_nodes"]=f.nodes.size();
    statistics.counts["compiled_dag_nodes"]=dag({compiled_formula});
    statistics.counts["reconstruction_dag_nodes"]=dag(reconstruction_map);statistics.counts["coefficient_max_bits"]=bits;
    statistics.times["compile_seconds"]=std::chrono::duration<double>(Clock::now()-start).count();
}
Assignment CompiledProblem::restore(z3::model m) const {
    // Complete only retained/fresh solving variables first; never an eliminated original.
    for(auto e:solving_variables)(void)m.eval(e,true);
    Assignment out;
    for(size_t i=0;i<reconstruction_map.size();++i){auto v=m.eval(reconstruction_map[i],true);
        if(original_formula.nodes[original_formula.variables[i]].boolean){if(!v.is_true()&&!v.is_false())throw std::logic_error("Boolean recovery");out.emplace_back(v.is_true()?1:0);}
        else {if(!v.is_numeral())throw std::logic_error("non-numeral recovery");out.emplace_back(Z3_get_numeral_string(v.ctx(),v));}}
    return out;
}
z3::model CompiledProblem::lift(const Assignment &a) const {
    auto m=original_formula.as_model(a);
    for(auto &g:choices)if(g.choice){bool first=true,second=true;
        for(size_t i=0;i<g.variables.size();++i){first&=a[g.variables[i]]==g.first[i];second&=a[g.variables[i]]==g.second[i];}
        if(first==second)throw std::logic_error("input outside unique binary choice");
        auto d=g.choice->decl();auto v=original_formula.ctx.int_val(first?0:1);m.add_const_interp(d,v);}
    return m;
}
z3::expr CompiledProblem::block_original(const Assignment &a) const {
    if(a.size()!=reconstruction_map.size())throw std::logic_error("incomplete blocking model");
    z3::expr_vector terms(original_formula.ctx);
    for(size_t i=0;i<a.size();++i)terms.push_back(reconstruction_map[i]!=original_formula.value(i,a[i]));
    return z3::mk_or(terms);
}
std::string CompiledProblem::json() const {
    std::ostringstream o;o<<std::setprecision(17)<<"{\"stop_reason\":"<<quote(stop_reason)<<",\"statistics\":"<<statistics.json()
      <<",\"limits\":{\"seconds\":"<<limits.seconds<<",\"operations\":"<<limits.max_operations<<",\"dag_nodes\":"<<limits.max_dag_nodes
      <<",\"coefficient_bits\":"<<limits.max_coefficient_bits<<",\"eliminations\":"<<limits.max_eliminations
      <<",\"row_terms\":"<<limits.max_row_terms<<",\"references\":"<<limits.max_references<<",\"term_growth\":"<<limits.max_term_growth<<"},\"reconstruction\":[";
    for(size_t i=0;i<reconstruction_map.size();++i){if(i)o<<',';o<<"{\"original\":"<<quote(original_formula.nodes[original_formula.variables[i]].expr.to_string())<<",\"expression\":"<<quote(reconstruction_map[i].to_string())<<"}";}
    o<<"],\"rewrites\":[";for(size_t i=0;i<rewrite_evidence.size();++i){if(i)o<<',';o<<rewrite_evidence[i];}
    return o.str()+"]}";
}
}
