#!/usr/bin/env python3
"""Independent original-Manual evaluator audit; no solver search, every zero retained."""
import argparse,json,gzip,csv
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,require,file_hash,dump,write_csv,model_id,model_key
from analyze_early_coverage import patterns
ROOT=Path(__file__).resolve().parents[1]

def lines(p):return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
def audit_job(args):
    base,record,source=args;out=Path(base);directory=out/record['output'];job=record
    for name,expected in record['hashes'].items():require(file_hash(directory/name)==expected,'changed raw evidence')
    require(record['exitcode']==0 and not record['watchdog'],'process failure: retained in runs, cannot pass audit')
    initial=json.loads((directory/'initial.json').read_text());summary=json.loads((directory/'summary.json').read_text());require(initial['source_sha256']==summary['source_sha256']==source,'binary source mismatch')
    rounds=lines(directory/'rounds.jsonl');models=lines(directory/'models.jsonl');receipts={r['id']:r for r in lines(directory/'commits.jsonl')};checkpoints=lines(directory/'checkpoints.jsonl');cat=json.loads((directory/'catalog.json').read_text())
    cby=defaultdict(list)
    for c in checkpoints:cby[c['round']].append(c)
    mby=defaultdict(list)
    for m in models:mby[m['round']].append(m)
    with no_search():
        metric=FrozenCoverage(Path(job['formula']).read_text());state=metric.empty.copy();seen=set();curve=[];assessments=[];last=0;previous_pattern=None;seen_patterns=set();validation_models=0
        if initial['mechanism']:
            for m in initial['prefix']+[initial['initial_model']]:
                if model_key(m) not in seen:state=metric.apply(state,m);seen.add(model_key(m))
            require(metric.export(state)==initial['coverage'],'snapshot state differential')
        basecovered=metric.count(state)
        def commit(m,expected_round,expected_gain=None):
            nonlocal state,last,previous_pattern,validation_models
            r=receipts[m['id']];require(r['timely']==(r['completion']<=r['deadline']<=job['seconds']),'receipt deadline')
            require(m['round']==expected_round,'model round mismatch')
            new=metric.apply(state,m['model']);validation_models+=1;gain=metric.count(new)-metric.count(state)
            require(gain==m['gain'],'formal marginal differential')
            if expected_gain is not None:require(gain==expected_gain,'round gain differential')
            if not r['timely']:return False
            require(r['completion']>=last,'output chronological');require(model_key(m['model']) not in seen,'formal duplicate');seen.add(model_key(m['model']));last=r['completion'];state=new
            pat=patterns(cat['groups'],{x['name']:int(x['value']) for x in m['model']}) if cat else ()
            changed=previous_pattern is not None and pat!=previous_pattern;novel=pat not in seen_patterns and bool(cat and cat['groups']);previous_pattern=pat;seen_patterns.add(pat)
            curve.append(dict(index=len(curve)+1,seconds=last,gain=gain,covered=metric.count(state),total=metric.total,origin=m['origin'],model_hash=model_id(m['model']),mode_changed=changed if cat else None,new_mode=novel if cat else None,mode_hash=str(pat) if cat else None))
            return True
        if not initial['mechanism'] and mby[-1]:
            require(len(mby[-1])==1,'initial count');commit(mby[-1][0],-1);require(metric.export(state)==initial['coverage'],'initial state differential')
        for r in rounds:
            require(r['covered_before']==metric.count(state),'frozen round base');require(r['broad_calls']<=2 and r['micro_calls']<=3,'query caps');require(r['macro_calls']+r['fallback_calls']==r['broad_calls'],'broad accounting')
            before=metric.count(state);writes={w['checkpoint']:w for w in r['checkpoint_writes']};timely=[]
            for c in cby[r['round']]:
                w=writes[c['checkpoint']];require(c['version']==r['version_before'],'checkpoint version');require(c['validated']<=w['completion'],'checkpoint actual finish')
                new=metric.apply(state,c['model']);validation_models+=1;gain=metric.count(new)-before;require(gain==c['gain'],'checkpoint coverage differential');require(model_key(c['model']) not in seen,'checkpoint formal duplicate')
                valid=w['completion']<=min(w['module_deadline'],w['deadline'],job['seconds'])
                assessments.append(dict(round=r['round'],module=c['module'],origin=c['origin'],query=c['query'],gain=gain,timely=valid,completion=w['completion'],model_hash=model_id(c['model'])))
                if valid:timely.append(c)
                if c['origin']=='macro_choice':
                    query=next(q for q in r['broad_queries'] if q['query']==c['query']);values={x['name']:int(x['value']) for x in c['model']}
                    for g,a in query['choices']:require(all(values[v['name']]==int(v['value']) for v in cat['groups'][g]['alternatives'][a]['assignments']),'macro whole alternative witness')
            for a in r['broad_assessments']:
                new=metric.apply(state,a['model']);validation_models+=1;require(metric.count(new)-before==a['gain'],'all broad candidate differential')
            require(len(mby[r['round']])<=1,'one output per round')
            committed=False
            for m in mby[r['round']]:
                matches=[c for c in timely if model_key(c['model'])==model_key(m['model'])];require(matches,'formal output lacks timely checkpoint')
                best=max((c['gain'],c['observations']) for c in timely);require((m['gain'],m['observations'])==best,'did not select best retained timely candidate')
                committed=commit(m,r['round'],r['gain'] if r['committed'] else None)
            require(committed==r['committed'],'commit receipt state');require(metric.count(state)==r['covered_after'] and metric.count(state)-before==r['gain'],'round marginal telescope')
        require(metric.export(state)==summary['coverage'],'final complete state differential');require(len(curve)==summary['formal_count'],'output count');require(sum(r['gain'] for r in rounds)==summary['gain'],'marginal sum')
        return dict(id=job['id'],instance=job['instance'],state=job['state'],mode=job['mode'],seed=job['seed'],split=job['split'],kind=job['kind'],status='passed',validated_models=validation_models,base_covered=basecovered,curve=curve,checkpoints=assessments,total=metric.total,final_covered=metric.count(state),gain=summary['gain'],summary=summary)

def main():
    p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('--jobs',type=int,default=9);a=p.parse_args();plan=json.loads((a.input/'plan.json').read_text());records=lines(a.input/'runs.jsonl');complete=json.loads((a.input/'COMPLETE.json').read_text())
    require(file_hash(a.input/'plan.json')==complete['plan_sha256'] and file_hash(a.input/'runs.jsonl')==complete['journal_sha256'],'batch bindings')
    require(len(records)==len(plan['tasks']),'missing tasks')
    for path,sha in plan['hashes'].items():require(file_hash(path)==sha,'source changed: '+path)
    dest=a.input/'audit';dest.mkdir(exist_ok=False);summaries=[]
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:
        for result in pool.map(audit_job,[(str(a.input),r,plan['source_sha256']) for r in records]):
            file=dest/(result['id'].replace('/','__')+'.json');dump(file,result);summaries.append({k:v for k,v in result.items() if k not in ('curve','checkpoints','summary')})
    write_csv(a.input/'audit_summary.csv',summaries);dump(a.input/'AUDIT.json',dict(sessions=len(summaries),validated_models=sum(r['validated_models'] for r in summaries),all_passed=True,search_calls=0))
if __name__=='__main__':main()
