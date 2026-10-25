#!/usr/bin/env python3
"""Frozen evaluator and independent online-learning replay; never searches."""
import argparse,json,math,time,hashlib
from collections import Counter,deque
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from audit_bofill_results import audit_run,jsonlines
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,require,dump,write_csv,model_id,file_hash,object_hash
import z3

def close(a,b,label,tol=1e-8):
    require(math.isfinite(float(a)) and math.isfinite(float(b)) and abs(a-b)<=tol*(1+abs(b)),label)

def value(e,assignment):
    if z3.is_int_value(e):return e.as_long()
    if z3.is_true(e):return True
    if z3.is_false(e):return False
    if z3.is_const(e):
        require(str(e.decl().name()) in assignment,'unsupported auxiliary in saved supported atom')
        return assignment[str(e.decl().name())]
    args=[value(c,assignment) for c in e.children()];k=e.decl().kind()
    if k==z3.Z3_OP_ADD:return sum(args)
    if k==z3.Z3_OP_SUB:return args[0]-sum(args[1:])
    if k==z3.Z3_OP_UMINUS:return -args[0]
    if k==z3.Z3_OP_MUL:return math.prod(args)
    if k==z3.Z3_OP_EQ:return args[0]==args[1]
    if k==z3.Z3_OP_LE:return args[0]<=args[1]
    if k==z3.Z3_OP_LT:return args[0]<args[1]
    if k==z3.Z3_OP_GE:return args[0]>=args[1]
    if k==z3.Z3_OP_GT:return args[0]>args[1]
    if k==z3.Z3_OP_NOT:return not args[0]
    raise ValueError('unsupported recorded expression')

def affine(e,variables):
    if z3.is_int_value(e):return e.as_long(),{}
    if e.get_id() in variables:return 0,{variables[e.get_id()]:1}
    require(e.is_int() and not z3.is_const(e),'unidentified variable in supported atom')
    parts=[affine(c,variables) for c in e.children()];kind=e.decl().kind()
    if kind==z3.Z3_OP_MUL:
        require(len(parts)==2 and (not parts[0][1] or not parts[1][1]),'nonlinear saved atom')
        scalar,term=(parts[0][0],parts[1]) if not parts[0][1] else (parts[1][0],parts[0])
        return scalar*term[0],{k:scalar*v for k,v in term[1].items() if scalar*v}
    require(kind in (z3.Z3_OP_ADD,z3.Z3_OP_SUB,z3.Z3_OP_UMINUS),'unsupported saved affine term')
    constant=0;coefficients={}
    for i,(c,vs) in enumerate(parts):
        sign=-1 if kind==z3.Z3_OP_UMINUS or kind==z3.Z3_OP_SUB and i else 1
        constant+=sign*c
        for k,v in vs.items():coefficients[k]=coefficients.get(k,0)+sign*v
    return constant,{k:v for k,v in coefficients.items() if v}

def atom_structure(metric,atom,expression,catalog,related_by_var):
    stripped=expression;negated=False
    while z3.is_not(stripped):stripped=stripped.arg(0);negated=not negated
    require(atom['negated']==negated and atom['relation']==stripped.decl().kind(),'atom relation metadata')
    require(atom['in_catalog']==(stripped.sexpr() in catalog),'old catalog membership mislabeled')
    variables={e.get_id():i for i,e in enumerate(metric.decls) if e.is_int()}
    lhs=affine(stripped.arg(0),variables);rhs=affine(stripped.arg(1),variables)
    coefficients={k:lhs[1].get(k,0)-rhs[1].get(k,0) for k in lhs[1].keys()|rhs[1].keys()}
    coefficients={k:v for k,v in coefficients.items() if v};indices=sorted(coefficients)
    require(atom['variables']==indices,'wrong original variable dependency')
    bits=max(abs(v).bit_length() for v in [lhs[0]-rhs[0],*coefficients.values()])
    require(bits==atom['maximum_bits']<=4096,'coefficient feature or cap')
    related=set();work=0
    for v in indices:
        for n in related_by_var[v]:
            if work>=512 or len(related)>=128:break
            work+=1;related.add(n)
    require(atom['related_nodes']==sorted(related),'wrong bounded original AST dependency')

class RidgeReplay:
    """Rebuild normal equations from the retained data, not C++ rank updates."""
    def __init__(self):
        self.window=deque();self.counts=[0,0];self.weights=[[[0.]*16 for _ in range(2)] for _ in range(2)]
    def update(self,x,action,reward,cost):
        self.window.append((x,action,reward,cost));self.counts[action]+=1
        if len(self.window)>256:self.window.popleft()
        for a in range(2):
            data=[s for s in self.window if s[1]==a]
            gram=[[float(i==j)+math.fsum(s[0][i]*s[0][j] for s in data) for j in range(16)] for i in range(16)]
            L=[[0.]*16 for _ in range(16)]
            for i in range(16):
                for j in range(i+1):
                    v=gram[i][j]-math.fsum(L[i][k]*L[j][k] for k in range(j))
                    L[i][j]=math.sqrt(v) if i==j else v/L[j][j]
            for target in range(2):
                rhs=[math.fsum(s[0][i]*s[2+target] for s in data) for i in range(16)];y=[0.]*16;w=[0.]*16
                for i in range(16):y[i]=(rhs[i]-math.fsum(L[i][j]*y[j] for j in range(i)))/L[i][i]
                for i in reversed(range(16)):w[i]=(y[i]-math.fsum(L[j][i]*w[j] for j in range(i+1,16)))/L[i][i]
                self.weights[a][target]=w
    def predict(self,x,seconds):
        gains=[];costs=[]
        for a in range(2):
            gains.append(min(1,max(0,math.fsum(w*v for w,v in zip(self.weights[a][0],x)))))
            costs.append(min(seconds,max(.001,math.fsum(w*v for w,v in zip(self.weights[a][1],x)))))
        return gains,costs,[g/c for g,c in zip(gains,costs)]

def one_run(arg):
    baseline=audit_run(arg);out,name=arg;out=Path(out);raw=out/'runs'/name/'raw';dest=out/'audit'/name
    q,issues=jsonlines(raw/'queries.jsonl');c,ii=jsonlines(raw/'candidates.jsonl');issues+=ii
    cs={r['query']:r for r in c};ms,_=jsonlines(raw/'models.jsonl');ms={r['query']:r for r in ms}
    receipts,_=jsonlines(raw/'commits.jsonl');receipts={r['query']:r for r in receipts}
    feedback,ii=jsonlines(raw/'feedback.jsonl');issues+=ii;fb={r['query']:r for r in feedback}
    costrows,ii=jsonlines(raw/'costs.jsonl');issues+=ii;costs={r['query']:r for r in costrows}
    atom_rows,ii=jsonlines(raw/'atoms.jsonl');issues+=ii;atoms={r['cache_index']:r for r in atom_rows}
    summary=json.loads((raw/'summary.json').read_text());initial=json.loads((raw/'initial.json').read_text())
    require(len(cs)==len(c) and len(fb)==len(feedback) and len(costs)==len(costrows)==len(q),'duplicate/missing query feedback/cost identity')
    require(len(atoms)==len(atom_rows)<=4096,'cache size/identity')
    mode=baseline['configuration'];seconds=baseline['seconds'];learner=RidgeReplay();rows=[];candidate_rows=[];counter=Counter();recent=deque(maxlen=16);last_model=None
    source_hash=file_hash(raw/'queries.jsonl');formula_hash=file_hash(out/'inputs'/baseline['formula'])
    common=dict(formula=baseline['formula'],formula_sha256=formula_hash,configuration=mode,seed=baseline['seed'],session=name,query_source_sha256=source_hash)
    with no_search() as attempts:
        metric=FrozenCoverage((out/'inputs'/baseline['formula']).read_text());state=metric.empty.copy();version=0;seen=set()
        decls={str(e.decl().name()):e for e in metric.decls};parsed={}
        catalog=set()
        if (raw/'summaries.json').exists():
            for row in json.loads((raw/'summaries.json').read_text())['atoms']:
                require(metric.nodes[row['node']].sexpr()==row['expression'],'old summary AST identity')
                e=z3.simplify(metric.nodes[row['node']])
                while z3.is_not(e):e=e.arg(0)
                catalog.add(e.sexpr())
        node_ids={e.get_id():i for i,e in enumerate(metric.nodes)};parents=[[] for e in metric.nodes]
        walked=set()
        def visit_parent_order(i):
            if i in walked:return
            walked.add(i)
            for child in metric.nodes[i].children():
                j=node_ids[child.get_id()];visit_parent_order(j);parents[j].append(i)
        visit_parent_order(0)
        related_by_var=[]
        for e in metric.decls:
            queue=[node_ids[e.get_id()]];visited=set();related=[]
            for n in queue:
                if n in visited:continue
                visited.add(n);related.append(n)
                for parent in parents[n]:
                    if len(queue)<64:queue.append(parent)
            related_by_var.append(related)
        for index,a in atoms.items():
            require(a['operations']<=513,'unbounded structural work')
            if not a['supported']:continue
            require(len(a['variables'])<=128 and len(a['related_nodes'])<=128,'bounded dependency work')
            expressions=z3.parse_smt2_string('(assert '+a['expression']+')',decls=decls,ctx=metric.root.ctx);require(len(expressions)==1,'atom parsing');parsed[index]=expressions[0]
            atom_structure(metric,a,parsed[index],catalog,related_by_var)
            if a['original_node']>=0:require(parsed[index].sexpr()==metric.nodes[a['original_node']].sexpr(),'invented original AST mapping')
            a['stable_identity']=object_hash(dict(formula=formula_hash,expression=parsed[index].sexpr(),original_node=a['original_node']))
        for index,query in enumerate(q):
            i=query['query'];require(i==index and query['coverage_version']==version,'query/coverage chronology')
            require(costs[i]['start']==query['cycle_start'] and costs[i]['end']>=query['finished'],'complete cost interval')
            close(costs[i]['full_cycle_seconds'],costs[i]['end']-costs[i]['start'],'interval duration')
            if not i:require(costs[i]['start']==0,'initialization omitted from first cost')
            if i:
                close(costs[i-1]['end'],costs[i]['start'],'cost gap/overlap')
                if i-1 in fb and fb[i-1]['trained']:
                    old=fb[i-1];require(old['label_finalized_at']<=query['start'],'future cost used')
                    previous=q[i-1]['decision'];learner.update(previous['features'],int(previous['action']=='FlipOnce'),old['reward'],old['full_cycle_seconds'])
            d=query['decision'];h=query['hook'];selected=d['selected']
            for attempt in query['attempts']:
                if attempt['cache_index']>=0:
                    seen_atom=atoms[attempt['cache_index']]
                    counter['supported_encounters']+=seen_atom['supported']
                    counter['outside_catalog_encounters']+=seen_atom['supported'] and not seen_atom['in_catalog']
            counter['native_decisions_examined']+=h.get('decisions_examined',0)
            require(h.get('selected',0)==int(selected) and h.get('selected',0)<=1 and h.get('flips',0)<=1,'native single-point cap')
            require(h.get('decisions_examined',0)<=32,'native decision window')
            if not version:require(not selected and not h,'first model intervention')
            if mode=='plain':require(not selected,'plain action')
            record=dict(common,query=i,coverage_version=version,selected=selected,action=None,in_catalog=None,stable_atom_identity=None,
                        raw_status=query['evidence']['raw_status'],timely=query['emitted'],formal_gain=query['gain'],full_cycle_seconds=costs[i]['full_cycle_seconds'],
                        check_seconds=query['evidence']['actual_check_seconds'],hook_seconds=query['hook_seconds_nested'],grant_ms=query['evidence']['granted_timeout_ms'],
                        cold_start=None,exploration=None,probability=None,final_realized=None,model_sha256=None,predicted_selected_gain=None,
                        predicted_selected_cost=None,observed_reward=None,gain_prediction_error=None,cost_prediction_error=None,unselected_reward=None,predicted_utility_gap=None)
            if selected:
                a=atoms[d['cache_index']];require(a['supported'],'selected unsupported object');require(1<=d['ordinal']<=32,'decision ordinal')
                require(d['training_counts']==learner.counts,'feedback used before available or cross-session contamination')
                require(d['desired_truth']==(d['native_truth']!=(d['action']=='FlipOnce')),'wrong phase action')
                require(h['flips']==int(d['action']=='FlipOnce'),'logged action differs from native flip')
                if mode=='catalog_random_one':require(a['in_catalog'],'catalog scope violated')
                selected_attempt=[x for x in query['attempts'] if x['reason']=='selected']
                require(len(selected_attempt)==1 and selected_attempt[0]['ordinal']==d['ordinal'],'multiple or missing selected callback')
                require(query['attempts'][-1]['reason']=='selected','callback after used slot')
                for earlier in query['attempts'][:-1]:
                    if earlier['cache_index']>=0:
                        candidate=atoms[earlier['cache_index']]
                        require(not(candidate['supported'] and (mode!='catalog_random_one' or candidate['in_catalog'])),'did not use first eligible object')
                x=d['features'];require(len(x)==16 and all(math.isfinite(v) for v in x),'invalid feature vector')
                single=unseen=total=0
                for n in a['related_nodes']:
                    one,zero,kind=state[metric.nodes[n].get_id()];mask=(1<<64)-1 if kind=='int' else 1;total+=64 if kind=='int' else 1
                    single+=((int(one)^int(zero))&mask).bit_count();unseen+=(~(int(one)|int(zero))&mask).bit_count()
                old_truth=bool(value(parsed[d['cache_index']],last_model)) if last_model is not None else None
                expected=[1,float(a['relation']==z3.Z3_OP_EQ),min(1,math.log2(1+len(a['variables']))/8),min(1,a['maximum_bits']/64),
                    1 if d['native_truth'] else -1,d['ordinal']/32,min(1,math.log2(1+d['conflicts'])/8),
                    (1 if old_truth else -1) if old_truth is not None else 0,float(old_truth is not None),metric.count(state)/metric.total if metric.total else 0,
                    sum(recent)/len(recent) if recent else 0,single/total if total else 0,unseen/total if total else 0,
                    min(1,costs[i-1]['full_cycle_seconds']/2) if i else 0,
                    float(q[i-1]['evidence']['control_classification'] in ('explicit_local_timeout','suspected_local_timeout')) if i else 0,
                    query['evidence']['granted_timeout_ms']/2000]
                for j,(actual,wanted) in enumerate(zip(x,expected)):close(actual,wanted,'feature '+str(j))
                action=int(d['action']=='FlipOnce')
                if mode=='native_feedback_one':
                    predictions=learner.predict(x,seconds)
                    for key,values in zip(('predicted_gain','predicted_cost','predicted_utility'),predictions):
                        for got,want in zip(d[key],values):close(got,want,'decision-time prediction',1e-7)
                    cold=min(learner.counts)<8;require(d['cold_start']==cold,'cold start wrong')
                    # Independently reconstructed predictions have just been checked.
                    # Choice must use the values actually available online, including
                    # floating-point ties near an expired window's zero coefficients.
                    best=int(d['predictions_finite'] and d['predicted_utility'][1]>d['predicted_utility'][0])
                    close(d['probability'],.5 if cold else .9 if action==best else .1,'action propensity')
                    if cold:require(action==int(d['action_draw']>=.5),'cold random action')
                    elif d['exploration']:require(d['exploration_draw']<.2 and action==int(d['action_draw']>=.5),'exploration draw')
                    else:require(d['exploration_draw']>=.2 and action==best,'exploitation prediction')
                    record.update(predicted_selected_gain=predictions[0][action],predicted_selected_cost=predictions[1][action],predicted_utility_gap=predictions[2][1]-predictions[2][0])
                else:
                    require(not d['prediction_enabled'] and d['probability']==.5 and action==int(d['action_draw']>=.5),'random control changed')
                record.update(action=d['action'],in_catalog=a['in_catalog'],stable_atom_identity=a['stable_identity'],cold_start=d['cold_start'],exploration=d['exploration'],probability=d['probability'])
                counter['selected']+=1;counter[d['action']]+=1;counter['outside_catalog']+=not a['in_catalog'];counter['cold']+=mode=='native_feedback_one' and d['cold_start'];counter['exploration']+=d['exploration'];counter['exploitation']+=mode=='native_feedback_one' and not d['cold_start'] and not d['exploration']
            require(selected==(i in fb),'missing or fabricated action feedback')
            candidate=cs.get(i);trial=state;candidate_gain=None
            if candidate:
                require(query['evidence']['raw_status']=='sat' and candidate['valid'] and candidate['coverage_version']==version,'candidate association')
                trial=metric.apply(state,candidate['model']);candidate_gain=metric.count(trial)-metric.count(state);identity=model_id(candidate['model'])
                require(query['duplicate']==(identity in seen),'candidate duplicate classification')
                values={v['name']:int(v['value']) if v['sort']=='Int' else v['value'] for v in candidate['model']}
                record['model_sha256']=identity
                if selected:
                    truth=bool(value(parsed[d['cache_index']],values));require(query['final_truth']==truth,'final atom truth')
                    require(fb[i]['final_truth']==truth,'feedback final truth');record['final_realized']=truth==d['desired_truth'];counter['realized']+=record['final_realized'];counter['unrealized']+=not record['final_realized']
                if query['emitted']:
                    receipt=receipts[i];require(receipt['timely'] and receipt['completion']==query['completion']<=seconds,'delivery cutoff')
                    require(ms[i]['model']==candidate['model'] and ms[i]['validated_seconds']==candidate['validated_seconds']<=receipt['completion'],'model/timestamp identity')
                    require(candidate_gain==query['gain'] and identity not in seen,'formal reward or duplicate mismatch');state=trial;version+=1;last_model=values;seen.add(identity)
                else:require(query['gain']==0,'uncommitted candidate gained formal coverage')
                candidate_rows.append(dict(common,query=i,model_sha256=identity,candidate_gain=candidate_gain,timely=query['emitted'],validated_seconds=candidate['validated_seconds']))
            else:
                require(query['evidence']['raw_status']!='sat','SAT missing complete candidate')
                require(query['final_truth'] is None and not query['emitted'] and query['gain']==0,'missing candidate assigned truth/reward')
                if selected:counter['no_model']+=1;require(fb[i]['final_truth'] is None,'Unknown final truth fabricated')
            if selected:
                observed=query['gain']/metric.total if metric.total else 0
                require(fb[i]['action']==d['action'] and fb[i]['gain']==query['gain'],'action relabeled by result')
                close(fb[i]['reward'],observed,'feedback reward');close(fb[i]['full_cycle_seconds'],costs[i]['full_cycle_seconds'],'feedback complete cost')
                require(fb[i]['unselected_action_reward'] is None and fb[i]['unselected_action_cost'] is None,'counterfactual labels')
                record['observed_reward']=observed
                if mode=='native_feedback_one':
                    record['gain_prediction_error']=observed-record['predicted_selected_gain'];record['cost_prediction_error']=costs[i]['full_cycle_seconds']-record['predicted_selected_cost']
                else:require(not fb[i]['trained'],'random control trained')
            recent.append(query['observations']==0);rows.append(record)
        require(version==baseline['distinct'] and metric.export(state)==summary['coverage'],'final evaluator state')
        require(not attempts,'audit searched')
    close(sum(c['full_cycle_seconds'] for c in costrows)+summary['final_tail_seconds'],summary['elapsed'],'complete session cost partition')
    require(sum(r['selected'] for r in rows)==len(feedback),'feedback duplicated')
    write_csv(dest/'decision_audit.csv',rows);write_csv(dest/'candidate_audit.csv',candidate_rows);write_csv(dest/'atom_audit.csv',[dict(common,**a) for a in atoms.values()])
    result=dict(baseline,native_counters=dict(counter),saved_candidates=len(c),native_issues=issues,mechanism_seconds=summary['mechanism_seconds'],mechanism_stop=summary['mechanism_stop'],
                feature_seconds=summary['feature_seconds'],prediction_seconds=summary['prediction_seconds'],training_seconds=summary['training_seconds'],cycle_cost_sum=summary['cycle_cost_sum'],tail_cost=summary['final_tail_seconds'],
                search_attempts=0,feedback_audit='chosen-action labels; full-window ridge replay; predecision state features; full costs')
    dump(dest/'native_summary.json',result);return result

def main():
    p=argparse.ArgumentParser();p.add_argument('out');p.add_argument('--jobs',type=int,default=15);a=p.parse_args();out=Path(a.out).resolve()
    if(out/'archive_manifest.json').exists():
        from archive_bofill_results import restore
        restore(out)
    args=[(str(out),d.name) for d in sorted((out/'runs').iterdir()) if d.is_dir()]
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:rows=list(pool.map(one_run,args))
    require(all(not r['issues'] and not r['native_issues'] for r in rows),'incomplete saved evidence')
    dump(out/'audit_summary.json',dict(passed=True,runs=rows,method='frozen original evaluator + independent chosen-action learning replay; no SMT search'))
    print('Native feedback audit passed:',len(rows),'sessions',flush=True)
if __name__=='__main__':main()
