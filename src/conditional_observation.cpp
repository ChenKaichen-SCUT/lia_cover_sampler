#include "conditional_observation.hpp"
#include "macro_choice.hpp"
#include <bit>
#include <iomanip>
namespace lia {
namespace {
struct Stop{};
struct Row {std::map<int,Z> terms;Z constant=0;unsigned node;};
uint64_t low(const Z&z){Z r;mpz_fdiv_r_2exp(r.get_mpz_t(),z.get_mpz_t(),64);return r.get_ui();}
}
ConditionalObservations::ConditionalObservations(Formula&f,Budget&parent):f_(f){
    auto start=Clock::now();Budget budget;budget.seconds=1.;budget.parent=&parent;
    auto tick=[&](){if(++operations>operation_cap){++reasons["operation_cap"];throw Stop{};}if(budget.done()){++reasons["preparation_deadline"];throw Stop{};}};
    try{
        ChoiceCatalog catalog(f,budget);operations+=catalog.operations;reasons["catalog_skipped_unsupported_mandatory_or"]=catalog.skipped;if(!catalog.complete){complete=false;++reasons["catalog_incomplete"];}
        std::set<int> mandatory;std::vector<int> pending{0};
        while(!pending.empty()){tick();auto n=pending.back();pending.pop_back();if(!mandatory.insert(n).second)continue;if(f.nodes[n].expr.is_and())for(int c:f.nodes[n].children)pending.push_back(c);}
        std::map<int,std::optional<Row>> cache;
        std::function<std::optional<Row>(int)> linear=[&](int n)->std::optional<Row>{
            tick();if(cache.count(n))return cache.at(n);auto &node=f.nodes[n];auto k=node.expr.decl().decl_kind();std::optional<Row> result;
            if(!node.boolean){
                if(node.variable>=0){Row r;r.terms[node.variable]=1;result=r;}
                else if(node.expr.is_numeral()){Row r;r.constant=node.numeral;result=r;}
                else if(k==Z3_OP_ADD||k==Z3_OP_SUB||k==Z3_OP_UMINUS||k==Z3_OP_MUL){
                    Row r;bool valid=true;Z factor=1;std::optional<Row> variable;
                    for(unsigned i=0;i<node.children.size();++i){auto part=linear(node.children[i]);if(!part){valid=false;break;}
                        if(k==Z3_OP_MUL){if(part->terms.empty())factor*=part->constant;else if(variable){valid=false;break;}else variable=part;}
                        else {Z sign=(k==Z3_OP_UMINUS||(k==Z3_OP_SUB&&i))?-1:1;r.constant+=sign*part->constant;for(auto &[v,c]:part->terms)r.terms[v]+=sign*c;}}
                    if(k==Z3_OP_MUL&&valid){if(variable){r.constant=factor*variable->constant;for(auto &[v,c]:variable->terms)r.terms[v]=factor*c;}else r.constant=factor;}
                    for(auto it=r.terms.begin();it!=r.terms.end();)if(it->second==0)it=r.terms.erase(it);else ++it;
                    if(valid&&r.terms.size()<=64)result=r;
                }
            }
            cache.emplace(n,result);return result;
        };
        std::vector<Row> rows;
        for(int n:mandatory){auto &node=f.nodes[n];if(!node.expr.is_eq()||f.nodes[node.children[0]].boolean)continue;auto a=linear(node.children[0]),b=linear(node.children[1]);if(!a||!b)continue;a->constant-=b->constant;for(auto &[v,c]:b->terms)a->terms[v]-=c;for(auto it=a->terms.begin();it!=a->terms.end();)if(it->second==0)it=a->terms.erase(it);else ++it;a->node=n;rows.push_back(*a);}
        for(auto &g:catalog.groups){
            tick();if(atoms.size()>=group_cap){complete=false;++reasons["group_cap"];break;}
            if(!g.exclusive||g.alternatives.size()!=2){++skipped;++reasons["not_exclusive_binary"];continue;}
            auto &a=g.alternatives[0].assignments,&b=g.alternatives[1].assignments;bool keys=a.size()==b.size();for(auto &[v,z]:a)keys&=b.count(v)>0;
            if(!keys){++skipped;++reasons["variable_sets_differ"];continue;}
            int discriminator=-1;for(auto &[v,z]:a)if(z!=b.at(v)){discriminator=v;break;}
            if(discriminator<0){++skipped;++reasons["same_vector"];continue;}
            int atom=-1;pending={g.alternatives[1].node};
            while(!pending.empty()){tick();int n=pending.back();pending.pop_back();auto &e=f.nodes[n];if(e.expr.is_and()){for(int c:e.children)pending.push_back(c);continue;}
                if(e.expr.is_eq())for(int c:e.children)if(f.nodes[c].variable==discriminator)atom=n;}
            if(atom<0){++skipped;++reasons["no_distinguishing_original_atom"];continue;}
            PhaseAtom pair{unsigned(atom),unsigned(g.node),{}};bool valid=true;unsigned local_nodes=0;
            for(unsigned phase=0;phase<2&&valid;++phase){
                auto &summary=pair.sides[phase];summary.values=phase?b:a;
                bool changed=true;
                while(changed){changed=false;for(auto &row:rows){tick();Z sum=row.constant;int unknown=-1;unsigned count=0;
                    for(auto &[v,c]:row.terms){auto it=summary.values.find(v);if(it==summary.values.end()){unknown=v;++count;}else sum+=c*it->second;}
                    if(!count&&sum!=0){valid=false;++conflicts;++reasons["conditional_equation_conflict"];break;}
                    if(count==1){auto coef=row.terms.at(unknown);if(coef==1||coef==-1){summary.values[unknown]=-sum*coef;summary.equations.push_back(row.node);changed=true;}}}
                    if(!valid)break;
                }
                if(!valid)break;
                std::vector<std::optional<Z>> value(f.nodes.size());std::vector<bool> calculated(f.nodes.size());
                std::function<std::optional<Z>(int)> known=[&](int n)->std::optional<Z>{
                    tick();if(calculated[n])return value[n];calculated[n]=true;auto &node=f.nodes[n];auto &ch=node.children;auto k=node.expr.decl().decl_kind();std::optional<Z> r;
                    // Conditioning is on a legal model of complete F. Every node
                    // on the root's necessary AND spine is therefore true.
                    if(mandatory.count(n))r=Z(1);
                    else if(node.variable>=0){if(summary.values.count(node.variable))r=summary.values.at(node.variable);}
                    else if(node.expr.is_numeral())r=node.numeral;
                    else if(node.expr.is_true()||node.expr.is_false())r=Z(node.expr.is_true()?1:0);
                    else if(k==Z3_OP_AND||k==Z3_OP_OR){bool all=true;Z identity=k==Z3_OP_AND?1:0;for(int c:ch){auto x=known(c);if(!x)all=false;else if((k==Z3_OP_AND&&*x==0)||(k==Z3_OP_OR&&*x!=0)){r=Z(1-identity);break;}}if(!r&&all)r=identity;}
                    else if(k==Z3_OP_ITE){auto c=known(ch[0]);if(c)r=known(ch[*c!=0?1:2]);else{auto x=known(ch[1]),y=known(ch[2]);if(x&&y&&*x==*y)r=x;}}
                    else {std::vector<Z> xs;bool all=true;for(int c:ch){auto x=known(c);if(!x){all=false;break;}xs.push_back(*x);}if(all){
                        if(k==Z3_OP_ADD){Z z=0;for(auto &x:xs)z+=x;r=z;}else if(k==Z3_OP_MUL){Z z=1;for(auto &x:xs)z*=x;r=z;}
                        else if(k==Z3_OP_SUB){Z z=xs.at(0);for(size_t i=1;i<xs.size();++i)z-=xs[i];r=z;}
                        else if(k==Z3_OP_UMINUS)r=-xs.at(0);else if(k==Z3_OP_NOT)r=Z(xs.at(0)==0);
                        else if(k==Z3_OP_EQ)r=Z(xs.at(0)==xs.at(1));else if(k==Z3_OP_LE)r=Z(xs.at(0)<=xs.at(1));else if(k==Z3_OP_LT)r=Z(xs.at(0)<xs.at(1));else if(k==Z3_OP_GE)r=Z(xs.at(0)>=xs.at(1));else if(k==Z3_OP_GT)r=Z(xs.at(0)>xs.at(1));}}
                    value[n]=r;return r;
                };
                std::set<int> visited;
                std::function<void(int)> visit=[&](int n){tick();if(!visited.insert(n).second)return;auto v=known(n);auto &node=f.nodes[n];auto &ch=node.children;auto k=node.expr.decl().decl_kind();
                    if(v){uint64_t mask=node.boolean?1:~uint64_t(0),bits=low(*v)&mask;summary.observations.push_back({unsigned(n),(~bits)&mask,bits});++local_nodes;if(summary_nodes+local_nodes>observation_cap){++reasons["observation_cap"];throw Stop{};}}
                    if(k==Z3_OP_AND||k==Z3_OP_OR){for(int c:ch){visit(c);auto x=known(c);if(!x||(k==Z3_OP_AND&&*x==0)||(k==Z3_OP_OR&&*x!=0))break;}}
                    else if(k==Z3_OP_ITE){visit(ch[0]);auto x=known(ch[0]);if(x)visit(ch[*x!=0?1:2]);}
                    else for(int c:ch)visit(c);
                };visit(0);
            }
            if(valid){summary_nodes+=local_nodes;atoms.push_back(std::move(pair));}else ++skipped;
        }
    }catch(const Stop&){complete=false;}
    seconds=std::chrono::duration<double>(Clock::now()-start).count();
}
Gain ConditionalObservations::score(const ConditionalSummary &g,const Coverage &s)const{
    Gain r;for(auto &o:g.observations){auto v=s.states.at(o.node);r.covered+=std::popcount(o.zero&v.one&~v.zero)+std::popcount(o.one&v.zero&~v.one);r.observed+=std::popcount(o.zero&~v.zero)+std::popcount(o.one&~v.one);}return r;
}
bool ConditionalObservations::contains(const ConditionalSummary&g,const Observation&o)const{
    for(auto &e:g.observations){if(!o.visited[e.node])return false;uint64_t mask=f_.nodes[e.node].boolean?1:~uint64_t(0),v=low(o.values[e.node])&mask;if((e.one&~v)||(e.zero&v)||((e.one|e.zero)&~mask))return false;}return true;
}
std::string ConditionalObservations::json()const{
    std::ostringstream o;o<<std::setprecision(17)<<"{\"complete\":"<<(complete?"true":"false")<<",\"operations\":"<<operations<<",\"seconds\":"<<seconds<<",\"skipped\":"<<skipped<<",\"conflicts\":"<<conflicts<<",\"summary_nodes\":"<<summary_nodes<<",\"atoms\":[";
    for(size_t i=0;i<atoms.size();++i){if(i)o<<',';auto &a=atoms[i];o<<"{\"node\":"<<a.node<<",\"ast_id\":"<<f_.nodes[a.node].expr.id()<<",\"expression\":"<<quote(f_.nodes[a.node].expr.to_string())<<",\"group\":"<<a.group<<",\"sides\":[";
        for(unsigned p=0;p<2;++p){if(p)o<<',';auto &s=a.sides[p];o<<"{\"truth\":"<<(p?"true":"false")<<",\"values\":[";bool comma=false;for(auto &[v,z]:s.values){if(comma)o<<',';comma=true;o<<"["<<v<<','<<quote(z.get_str())<<']';}o<<"],\"equations\":[";for(size_t j=0;j<s.equations.size();++j){if(j)o<<',';o<<s.equations[j];}o<<"],\"observations\":[";for(size_t j=0;j<s.observations.size();++j){if(j)o<<',';auto x=s.observations[j];o<<'['<<x.node<<','<<quote(std::to_string(x.zero))<<','<<quote(std::to_string(x.one))<<']';}o<<"]}";}o<<"]}";}
    o<<"],\"reasons\":{";bool comma=false;for(auto &[k,v]:reasons){if(comma)o<<',';comma=true;o<<quote(k)<<':'<<v;}return o.str()+"}}";
}
}
