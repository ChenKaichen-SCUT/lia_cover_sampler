#!/usr/bin/env python3
"""Read-only recovery and complete original-AST replay of frozen saved models."""
import argparse,csv,json,os,shutil,sys,tarfile,tempfile,time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,require,dump,write_csv,file_hash,model_id,object_hash,z3
from audit_bofill_results import complete_lines,jsonlines,typed_highdiv,fingerprint
from highdiv_gap_common import add,covered,observed,obs_id,dump_gz
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/highdiv_complementarity_gap'

def layout_for(metric):
    layout=[];offset=0
    for i,n in enumerate(metric.nodes):
        width=1 if z3.is_bool(n) else 64
        kind='Boolean' if width==1 else 'integer_constant' if z3.is_int_value(n) else 'integer_variable' if z3.is_const(n) and n.decl().kind()==z3.Z3_OP_UNINTERPRETED else 'arithmetic_expression'
        layout.append(dict(node=i,offset=offset,width=width,kind=kind,expression=n.sexpr()))
        offset+=width
    require(offset==metric.total,'layout denominator')
    return layout

def pack_state(metric,layout,state):
    zero=one=0
    for n,entry in zip(metric.nodes,layout):
        o,z,_=state[n.get_id()];zero|=int(z)<<entry['offset'];one|=int(o)<<entry['offset']
    return zero,one

def unpack_state(metric,layout,state):
    return {n.get_id():((state[1]>>e['offset'])&((1<<e['width'])-1),(state[0]>>e['offset'])&((1<<e['width'])-1),metric.empty[n.get_id()][2]) for n,e in zip(metric.nodes,layout)}

def stage(out):
    """Copy verified files; recover only into the NEW directory if originals absent."""
    original=ROOT/'results/native_frozen_900s';high=ROOT/'results/highdiv_time900'
    out.mkdir(parents=True,exist_ok=True);(out/'inputs').mkdir(exist_ok=True)
    require(not (out/'plan.json').exists(),'new output directory required')
    provenance=[];jobs=[]
    def copy(src,dst,expected=None):
        require(src.is_file(),f'missing evidence {src}');h=file_hash(src)
        require(expected is None or h==expected,f'hash mismatch {src}')
        dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
        provenance.append(dict(source=str(src.relative_to(ROOT)),copy=str(dst.relative_to(out)),sha256=h))
    def recover(src,dst,expected,archive,member):
        if src.exists():copy(src,dst,expected);return
        dst.parent.mkdir(parents=True,exist_ok=True)
        with archive.extractfile(member) as f,dst.open('wb') as g:shutil.copyfileobj(f,g)
        require(file_hash(dst)==expected,'recovery hash mismatch')
        provenance.append(dict(source=str(src.relative_to(ROOT)),copy=str(dst.relative_to(out)),sha256=expected,recovered=True))
    manifest=json.loads((original/'archive_manifest.json').read_text())
    for item in manifest['runs']:
        name=item['run'];dest=out/'runs'/name
        with tempfile.TemporaryFile() as merged:
            for part in item['parts']:
                p=original/part['file'];require(file_hash(p)==part['sha256'],'archive hash mismatch')
                with p.open('rb') as f:shutil.copyfileobj(f,merged)
            merged.seek(0)
            with tarfile.open(fileobj=merged,mode='r:gz') as archive:
                for relative,h in item['content_sha256'].items():
                    # Keep the whole source run, including failed queries and costs.
                    recover(original/relative,out/relative,h,archive,relative)
        spec=json.loads((dest/'spec.json').read_text());formula=spec['formula']
        if not (out/'inputs'/formula).exists():copy(original/'inputs'/formula,out/'inputs'/formula)
        for f in ['timeline.csv','coverage_final.json','summary.json']:
            p=original/'audit'/name/f
            if p.exists():copy(p,dest/'prior_audit'/f)
        jobs.append(dict(run=name,formula=formula,seed=spec['seed'],mode=spec['configuration'],formula_sha256=file_hash(out/'inputs'/formula)))
    hmanifest=json.loads((high/'archive.json').read_text());require(file_hash(high/hmanifest['archive'])==hmanifest['sha256'],'HighDiv archive hash')
    with tarfile.open(high/hmanifest['archive'],'r:gz') as archive:
        for formula in sorted({j['formula'] for j in jobs}):
            short='random' if 'random' in formula else 'real';name='highdiv_'+short;dest=out/'runs'/name
            for file in [formula+'.samples',formula+'.times.csv','process.json']:
                relative='raw/'+formula+'/seed-0/'+file
                recover(high/relative,dest/file,hmanifest['files'][relative],archive,relative)
            copy(high/'audit'/formula/'seed-0.json',dest/'prior_audit.json')
            jobs.append(dict(run=name,formula=formula,seed=0,mode='highdiv',formula_sha256=file_hash(out/'inputs'/formula)))
    for src in [original/'archive_manifest.json',original/'historical_highdiv.json',original/'plan.json',high/'archive.json',high/'manifest.json',ROOT/'dependencies/highdiv_timing.patch']:
        copy(src,out/'inputs'/src.parent.name/src.name)
    evaluator=Path(os.environ.get('HIGHDIV_ROOT',ROOT.parent/'HighDiv-master'))/'HighDiv/my_scripts/calc_metric.py'
    expected=json.loads((ROOT/'dependencies/source_audit.json').read_text())['HighDiv/my_scripts/calc_metric.py']['sha256']
    require(file_hash(evaluator)==expected,'frozen evaluator hash')
    destination=out/'inputs/frozen_evaluator/HighDiv/my_scripts/calc_metric.py';destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(evaluator,destination)
    dump(out/'inputs/offline_environment.json',dict(python=sys.version,z3=z3.get_version_string(),metric_sha256=expected,search_disabled=True))
    dump(out/'provenance.json',provenance);dump(out/'plan.json',dict(base='ef3e9a7',search_forbidden=True,jobs=jobs,protocol_sha256=file_hash(ROOT/'docs/HighDiv原生随机互补性差距分析协议.md')))
    return jobs

def replay(task):
    out,job=task;out=Path(out);run=out/'runs'/job['run'];dest=out/'observations'/job['run'];dest.mkdir(parents=True,exist_ok=True)
    start=time.perf_counter();issues=[];timeline=[];saved=[]
    with no_search() as attempts:
        formula=out/'inputs'/job['formula'];require(file_hash(formula)==job['formula_sha256'],'formula hash')
        metric=FrozenCoverage(formula.read_text());layout=layout_for(metric);raw=[]
        if job['mode']=='highdiv':
            lines,ii=complete_lines(run/(job['formula']+'.samples'));issues+=ii
            ts,ii=complete_lines(run/(job['formula']+'.times.csv'));issues+=ii
            stamps={}
            for _,text in ts:
                idx,t=text.strip().split(',');require(int(idx) not in stamps,'duplicate time');stamps[int(idx)]=float(t)
            for line,text in lines:
                idx,model=typed_highdiv(text,[(str(e.decl().name()),str(e.sort())) for e in metric.decls])
                raw.append(dict(index=idx+1,source_index=idx,source_line=line,model=model,completion=stamps.get(idx)))
            audit=json.loads((run/'prior_audit.json').read_text());expected=audit['curve']
            require(len(expected)==len(raw),'historical HighDiv prefix count')
        else:
            raw,ii=jsonlines(run/'raw/models.jsonl');issues+=ii;commits,ii=jsonlines(run/'raw/commits.jsonl');issues+=ii
            receipts={c['index']:c for c in commits};require(len(receipts)==len(commits),'duplicate commit')
            require(len(raw)==len(commits),'unmatched model/commit')
            for r in raw:
                receipt=receipts[r['index']];require(r['query']==receipt['query'],'receipt identity')
                r['completion']=receipt['completion'];r['source_index']=r['index'];require(receipt['timely']==(receipt['completion']<=900),'timely flag')
                require(r['validated_seconds']<=r['completion'],'write before validation')
            expected=list(csv.DictReader((run/'prior_audit/timeline.csv').open()))
            audit=json.loads((run/'raw/summary.json').read_text())
            require(len(expected)==len(raw),'native prefix count')
        state=(0,0);seen=set();last=-1;cache_state=None;cache_fnv=None
        for idx,r in enumerate(raw):
            require(r['index']==idx+1,'source order not contiguous');t=r['completion']
            if t is None:issues.append(dict(kind='timestamp_missing',index=r['index']));continue
            require(t>=last,'nonmonotone completion');last=t
            if t>900:issues.append(dict(kind='after_deadline',index=r['index']));continue
            mid=model_id(r['model'])
            if mid in seen:issues.append(dict(kind='duplicate',index=r['index']));continue
            seen.add(mid);single=metric.apply(metric.empty,r['model']);obs=pack_state(metric,layout,single);before=state;state=add(state,obs)
            count=covered(state).bit_count();delta=count-covered(before).bit_count();newobs=observed(state)-observed(before)
            e=expected[idx]
            require(count==int(e['covered']) and metric.total==int(e['total']),f'prefix score mismatch {job["run"]} {idx+1}')
            require(t==float(e['seconds'] if job['mode']=='highdiv' else e['completion']),'timestamp mismatch')
            if job['mode']!='highdiv':
                require(delta==r['gain'] and newobs==r['observations'],'native gain/observations mismatch')
                require(mid==e['model_sha256'],'model identity mismatch')
                if state!=cache_state:
                    cache_fnv=fingerprint(metric.export(unpack_state(metric,layout,state))['states']);cache_state=state
                require(cache_fnv==r['state_fnv1a64']==e['state_fnv1a64'],'full seen0/seen1 prefix mismatch')
            rec=dict(run=job['run'],formula=job['formula'],seed=job['seed'],mode=job['mode'],index=len(saved)+1,source_index=r['source_index'],completion=t,model_id=mid,observation_id=obs_id(obs),zero=hex(obs[0]),one=hex(obs[1]),model=r['model'])
            saved.append(rec);timeline.append({k:rec[k] for k in ['run','index','source_index','completion','model_id','observation_id']}|dict(covered=count,total=metric.total,gain=delta,new_observations=newobs,observations=observed(state),state_id=obs_id(state)))
        dump(dest/'exclusions.json',issues)
        require(not issues,f'evidence exclusions need explicit review: {issues}')
        if job['mode']!='highdiv':require(metric.export(unpack_state(metric,layout,state))==audit['coverage'],'full final state mismatch')
        else:require(covered(state).bit_count()==audit['covered'] and len(saved)==audit['unique_valid_in_budget'],'HighDiv endpoint')
        dump_gz(dest/'models.jsonl.gz',saved);write_csv(dest/'timeline.csv',timeline)
        dump(dest/'layout.json',layout);dump(dest/'exclusions.json',issues)
        summary=job|dict(models=len(saved),total=metric.total,covered=covered(state).bit_count(),observations=observed(state),state_id=obs_id(state),zero=hex(state[0]),one=hex(state[1]),elapsed_seconds=time.perf_counter()-start,search_attempts=attempts,all_prefixes_exact=True,exclusions=len(issues))
        dump(dest/'summary.json',summary)
    return {k:v for k,v in summary.items() if k not in ['zero','one']}

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=OUT);p.add_argument('--workers',type=int,default=10);p.add_argument('--replay-staged',action='store_true');args=p.parse_args()
    require(1<=args.workers<=15,'worker limit')
    jobs=json.loads((args.out/'plan.json').read_text())['jobs'] if args.replay_staged else stage(args.out)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for r in pool.map(replay,[(str(args.out),j) for j in jobs]):print(json.dumps(r),flush=True)
    for e in json.loads((args.out/'provenance.json').read_text()):
        require(file_hash(args.out/e['copy'])==e['sha256'],'staged evidence changed')
        if (ROOT/e['source']).exists():require(file_hash(ROOT/e['source'])==e['sha256'],'historical source changed')
    dump(args.out/'replay_complete.json',dict(runs=len(jobs),search_attempts=0,historical_hashes_unchanged=True))
if __name__=='__main__':main()
