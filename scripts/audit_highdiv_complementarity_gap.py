#!/usr/bin/env python3
"""Independent checks of saved assignments, observation sets, timing and subsets.

Runs only the frozen ManualSatisfiesMetric, with solver search entry points disabled.
"""
import argparse,csv,gzip,json,time
from collections import Counter
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from highdiv_gap_common import *
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,dump,file_hash,write_csv
from prepare_highdiv_complementarity_gap import OUT,ROOT,layout_for,pack_state

def csv_gz(path):
    with gzip.open(path,'rt') as f:yield from csv.DictReader(f)

def audit_run(task):
    out,job=task;out=Path(out);start=time.perf_counter();rows=load_run(out/'observations'/job['run']);expected=list(csv.DictReader((out/'observations'/job['run']/'timeline.csv').open()));identities=set()
    with no_search() as attempts:
        metric=FrozenCoverage((out/'inputs'/job['formula']).read_text());layout=layout_for(metric);state=metric.empty.copy();last=-1
        for r,e in zip(rows,expected):
            require(model_id(r['model'])==r['model_id'],'model hash');require(r['model_id'] not in identities,'duplicate model');identities.add(r['model_id'])
            require(last<=r['completion']<=900,'late or reordered model');last=r['completion']
            single=metric.apply(metric.empty,r['model']);require(pack_state(metric,layout,single)==r['obs'],'single model original-evaluator observations mismatch')
            require(obs_id(r['obs'])==r['observation_id'],'observation identity')
            state=metric.apply(state,r['model']);packed=pack_state(metric,layout,state)
            require(metric.count(state)==int(e['covered']),'full evaluator prefix mismatch');require(obs_id(packed)==e['state_id'],'full prefix observations mismatch')
        summary=json.loads((out/'observations'/job['run']/'summary.json').read_text());require(len(rows)==len(expected)==summary['models'],'prefix count')
        require(metric.total==summary['total'],'denominator')
        if job['mode']=='highdiv':
            proc=json.loads((out/'runs'/job['run']/'process.json').read_text());require(proc['input_sha256']==job['formula_sha256'],'HighDiv input formula mismatch')
        result=dict(run=job['run'],models=len(rows),prefixes=len(expected),complete_assignments=True,formula_validity=True,observations_exact=True,all_prefixes_exact=True,search_attempts=attempts,seconds=time.perf_counter()-start)
        if job['mode']=='native_random_one':
            short='random' if 'random' in job['formula'] else 'real';selected=[s for s in load_gz(out/'analysis'/short/'selected_subsets.jsonl.gz') if s['native_run']==job['run']];pool={r['model_id']:r for r in rows};subsets=[]
            for s in selected:
                require(len(s['selected_model_ids'])<=s['k'],'selection exceeds k');require(len(set(s['selected_model_ids']))==len(s['selected_model_ids']),'duplicate selected identity')
                require(not s['keep_first'] or not s['selected_model_ids'] or s['selected_model_ids'][0]==rows[0]['model_id'],'first model not preserved')
                independent=metric.empty.copy()
                for idx,mid in zip(s['selected_indices'],s['selected_model_ids']):
                    r=pool[mid];require(r['index']==idx and r['seed']==job['seed'],'cross seed identity');require(r['completion']<=s['cutoff'],'future candidate used')
                    independent=metric.apply(independent,r['model'])
                require(metric.count(independent)==s['lower_bound'],'original evaluator subset joint count')
                require(obs_id(pack_state(metric,layout,independent))==s['state_id'],'original evaluator full subset state')
                actual_pool=pool_at(rows,s['cutoff']);upper=covered(prefix(actual_pool,len(actual_pool))).bit_count()
                require(s['lower_bound']<=s['upper_bound']<=upper,'invalid total bounds')
                if s['upper_bound']<upper:
                    require(s['k']==2,'unjustified tighter upper bound')
                    # Independent exhaustive verification of conditional pair optimum.
                    if s['keep_first']:
                        exact=max((covered(add(actual_pool[0]['obs'],p['obs'])).bit_count() for p in actual_pool),default=0)
                    else:
                        require(len(actual_pool)<=32,'non-exhaustive pair claimed exact');exact=max((covered(add(a['obs'],b['obs'])).bit_count() for a in actual_pool for b in actual_pool),default=0)
                    require(exact==s['upper_bound'],'pair upper bound not exact')
                subsets.append({k:s[k] for k in ['native_run','cutoff','k','keep_first','selected_count','lower_bound','upper_bound','state_id']}|dict(original_evaluator_exact=True))
            result['selected_subsets_exact']=len(subsets);write_csv(out/'audit'/('subsets_'+job['run']+'.csv'),subsets)
        result['seconds']=time.perf_counter()-start;dump(out/'audit'/(job['run']+'.json'),result);return result

def audit_tables(out,formula,jobs):
    short='random' if 'random' in formula else 'real';dest=out/'analysis'/short;names=[j['run'] for j in jobs if j['formula']==formula];runs={n:load_run(out/'observations'/n) for n in names};maps={n:{r['index']:r for r in rows} for n,rows in runs.items()}
    H=next(n for n in names if n.startswith('highdiv'));total=json.loads((out/'observations'/H/'summary.json').read_text())['total'];final={n:prefix(rows,len(rows)) for n,rows in runs.items()};arr={n:first_arrivals(rows,total) for n,rows in runs.items()};checked=Counter()
    gapcounts=Counter()
    for r in csv_gz(dest/'bit_set_differences.csv.gz'):
        N=r['native_run'];b=int(r['dense_bit']);h=covered(final[H]);n=covered(final[N]);both=covered(add(final[H],final[N]));masks=dict(highdiv_only=h&~n,native_only=n&~h,intersection=h&n,union_pairing=both&~(h|n));require((masks[r['category']]>>b)&1,'set difference membership')
        for name,tag in [(H,'highdiv'),(N,'native')]:
            for v in (0,1):
                field=f'{tag}_first{v}_index';idx=arr[name][v][b];require((not r[field])==(idx<0),'missing witness')
                if idx>=0:
                    model=runs[name][idx];require(model['index']==int(r[field]) and model['model_id']==r[f'{tag}_first{v}_model_id'],'first witness identity');require((model['obs'][v]>>b)&1,'witness observation')
        checked['gap_bits']+=1
        gapcounts[N,r['category']]+=1
    for r in csv_gz(dest/'first_arrivals.csv.gz'):
        N=r['native_run'];b=int(r['dense_bit']);zi,oi=arr[N][0][b],arr[N][1][b]
        if zi<0 or oi<0:require(not r['complete_index'] and r['index_class']=='right_censored_900','censored treated as observed')
        else:
            idx=max(zi,oi);require(runs[N][idx]['index']==int(r['complete_index']),'first completion index');require(runs[N][idx]['completion']==float(r['complete_seconds']),'first completion time')
        checked['arrivals']+=1
    bases={(r['run'],r['k']):(int(r['zero'],16),int(r['one'],16)) for r in json.loads((dest/'reference_states.json').read_text())}
    for r in csv_gz(dest/'common_state_model_gains.csv.gz'):
        base=bases[r['base_run'],int(r['base_k'])];m=maps[r['run']][int(r['index'])];require(gain_mask(base,m['obs']).bit_count()==int(r['gain']),'common state gain');require(obs_id(base)==r['base_id'] and m['model_id']==r['model_id'],'common state binding');checked['common_scores']+=1
    for s in load_gz(dest/'selected_subsets.jsonl.gz'):
        hc=covered(prefix(runs[H],s['k']));actual=(int(s['zero'],16)&int(s['one'],16));pc=covered(prefix(pool_at(runs[s['native_run']],s['cutoff']),10**9))
        require((actual&hc).bit_count()==s['highdiv_target_hit'],'selected target intersection');require((pc&hc).bit_count()==s['highdiv_target_upper'],'target upper bound');checked['subset_target_bounds']+=1
    for r in csv.DictReader((dest/'final_sets.csv').open()):
        n=final[r['native_run']];h=final[H];hc,nc=covered(h),covered(n);uc=covered(add(h,n))
        require(uc.bit_count()==int(r['union_covered']),'union count');require((hc|nc).bit_count()+int(r['union_pairing'])==uc.bit_count(),'first-observation pairing');require(int(r['intersection'])+int(r['highdiv_only'])==hc.bit_count(),'H partition');require(int(r['intersection'])+int(r['native_only'])==nc.bit_count(),'N partition')
        for label in ['highdiv_only','native_only','intersection','union_pairing']:require(gapcounts[r['native_run'],label]==int(r[label]),'gap table incomplete')
    if (dest/'selection_certificates.csv').exists():
        selections=load_gz(dest/'selected_subsets.jsonl.gz')
        for c in csv.DictReader((dest/'selection_certificates.csv').open()):
            eligible=[s for s in selections if s['native_run']==c['native_run'] and s['cutoff']==int(c['cutoff']) and s['k']==int(c['k']) and (c['keep_first']=='False' or s['keep_first'])]
            require(max(s['lower_bound'] for s in eligible)==int(c['certified_lower_bound']),'certificate lower bound')
            require(any(s['state_id']==c['witness_state_id'] and s['lower_bound']==int(c['certified_lower_bound']) for s in eligible),'certificate witness')
            pc=covered(prefix(pool_at(runs[c['native_run']],int(c['cutoff'])),10**9)).bit_count()
            require(c['verdict']==selection_verdict(int(c['certified_lower_bound']),int(c['upper_bound']),pc,int(c['highdiv_count'])),'certificate interpretation')
            checked['certificates']+=1
    if (dest/'actual_transitions.csv.gz').exists():
        states={n:(0,0) for n in runs}
        for r in csv_gz(dest/'actual_transitions.csv.gz'):
            name=r['run'];idx=int(r['index']);m=maps[name][idx];state=states[name]
            require(gain_mask(state,m['obs']).bit_count()==int(r['covered_gain']),'transition gain');require(obs_id(state)==r['base_id'],'transition base')
            if idx>1:
                prev=maps[name][idx-1];before={v['name']:v['value'] for v in prev['model']};changed=[v['name'] for v in m['model'] if v['value']!=before[v['name']]]
                require(changed==json.loads(r['changed_names']),'changed variable names');require(len(changed)==int(r['changed_variables']),'changed variable count')
            states[name]=add(state,m['obs']);checked['transitions']+=1
    return dict(formula=formula,checks=dict(checked),passed=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=OUT);p.add_argument('--workers',type=int,default=10);p.add_argument('--tables-only',action='store_true');a=p.parse_args();require(1<=a.workers<=15,'worker limit');(a.out/'audit').mkdir(exist_ok=True)
    plan=json.loads((a.out/'plan.json').read_text());started=time.perf_counter()
    results=[]
    if not a.tables_only:
        with ProcessPoolExecutor(max_workers=a.workers) as pool:
            results=list(pool.map(audit_run,[(str(a.out),j) for j in plan['jobs']]))
    tables=[audit_tables(a.out,f,plan['jobs']) for f in sorted({j['formula'] for j in plan['jobs']})]
    count=0
    for e in json.loads((a.out/'provenance.json').read_text()):
        require(file_hash(a.out/e['copy'])==e['sha256'],'staged source changed')
        if (ROOT/e['source']).exists():require(file_hash(ROOT/e['source'])==e['sha256'],'historical source changed')
        count+=1
    result=dict(passed=True,run_checks=results,table_checks=tables,historical_hash_checks=count,search_calls=0,seconds=time.perf_counter()-started)
    dump(a.out/('audit/supplemental_tables.json' if a.tables_only else 'audit/summary.json'),result);print(json.dumps(result),flush=True)
if __name__=='__main__':
    main()
