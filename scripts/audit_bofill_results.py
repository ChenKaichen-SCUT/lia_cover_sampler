#!/usr/bin/env python3
"""Original-AST audit of completed traces. All solver search entry points disabled."""
import argparse, csv, json, math, time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from reevaluate_round6_saved_candidates import FrozenCoverage, no_search, model_id, require, file_hash, dump, write_csv

def fingerprint(states):
    h=14695981039346656037
    for zero,one in states:
        for v in (int(zero),int(one)):
            for b in v.to_bytes(8,'little'):h=((h^b)*1099511628211)&((1<<64)-1)
    return str(h)

def complete_lines(path):
    if not path.exists():return [],[dict(kind='missing_file',file=path.name)]
    rows=[];issues=[]
    with path.open('rb') as f:
        for i,line in enumerate(f,1):
            if not line.endswith(b'\n'):issues.append(dict(kind='cutoff_tail',file=path.name,line=i,bytes=len(line)));continue
            rows.append((i,line.decode()))
    return rows,issues

def jsonlines(path):
    lines,issues=complete_lines(path);rows=[]
    for n,line in lines:
        try:rows.append(json.loads(line))
        except (ValueError,UnicodeError):issues.append(dict(kind='corrupt_json',file=path.name,line=n))
    return rows,issues

def typed_highdiv(text,decls):
    index,body=text.split(':',1);tokens={}
    for part in body.strip().split(';'):
        if not part:continue
        name,value=part.split(':',1);require(name not in tokens,'duplicate HighDiv variable');tokens[name]=value.strip()
    require(set(tokens)=={name for name,sort in decls},'incomplete HighDiv original model')
    model=[]
    for name,sort in sorted(decls):
        v=tokens[name]
        if sort=='Bool':require(v in ('true','false'),'non-Boolean value');v=v=='true'
        else:
            if v.startswith('(- ') and v.endswith(')'):v='-'+v[3:-1].strip()
            v=str(int(v))
        model.append(dict(name=name,sort=sort,value=v))
    return int(index),model

def audit_run(args):
    out,run_name=args;out=Path(out);run=out/'runs'/run_name;raw=run/'raw';began=time.perf_counter()
    spec=json.loads((run/'spec.json').read_text());process=json.loads((run/'process_result.json').read_text());seconds=spec['seconds']
    original=out/'inputs'/spec['formula'];plan=json.loads((out/'plan.json').read_text())
    expected=next(i['sha256'] for i in plan['inputs'] if i['name']==spec['formula']);require(file_hash(original)==expected,'input hash changed')
    issues=[];records=[];query_rows=[];summary=None;structure=None
    with no_search() as attempts:
        metric=FrozenCoverage(original.read_text());state=metric.empty.copy();seen=set();timeline=[]
        if spec['configuration']=='HighDiv_release':
            lines,ii=complete_lines(raw/(spec['formula']+'.samples'));issues+=ii
            times,ii=complete_lines(raw/(spec['formula']+'.times.csv'));issues+=ii;receipts={}
            for line,t in times:
                fields=t.strip().split(',');require(len(fields)==3,'HighDiv receipt schema')
                idx,ns,ok=map(int,fields);require(idx not in receipts,'duplicate receipt');receipts[idx]=(ns,ok)
            used=set()
            for line,text in lines:
                idx,model=typed_highdiv(text,[(str(e.decl().name()),str(e.sort())) for e in metric.decls]);rec=receipts.get(idx);used.add(idx)
                records.append(dict(index=idx,model=model,source_line=line,completion=(rec[0]-process['start_monotonic_ns'])/1e9 if rec else None,timely=bool(rec and rec[1] and 0<=(rec[0]-process['start_monotonic_ns'])/1e9<=seconds)))
            for idx in receipts.keys()-used:issues.append(dict(kind='receipt_without_model',index=idx))
        else:
            models,ii=jsonlines(raw/'models.jsonl');issues+=ii;cs,ii=jsonlines(raw/'commits.jsonl');issues+=ii
            receipts={c['query']:c for c in cs};require(len(receipts)==len(cs),'duplicate query receipt')
            for line,m in enumerate(models,1):
                c=receipts.get(m['query']);records.append(dict(m,source_line=line,completion=c['completion'] if c else None,timely=bool(c and c['timely'])))
                if c:require(c['index']==m['index'],'receipt model identity mismatch')
            query_rows,ii=jsonlines(raw/'queries.jsonl');issues+=ii
            if (raw/'summary.json').exists():summary=json.loads((raw/'summary.json').read_text())
            if (raw/'structure.json').exists():structure=json.loads((raw/'structure.json').read_text())
        audit=[];previous=-1.;positive=0;last_positive=0.;longest_plateau=0.;longest_zero=0;zero=0
        for record in records:
            identity=model_id(record['model']);completion=record['completion'];before=metric.count(state)
            # Validate even saved late/duplicate models. They do not update formal state.
            trial=metric.apply(state,record['model']);duplicate=identity in seen
            row=dict(source_line=record['source_line'],source_index=record['index'],model_sha256=identity,completion=completion,duplicate=duplicate,accepted=False,reason='',gain=None)
            if completion is None:row['reason']='missing_timestamp'
            else:
                require(math.isfinite(completion) and completion>=0,'invalid completion time')
                require(completion>=previous,'nonmonotonic completion');previous=completion
                if completion>seconds:row['reason']='late'
                elif not record['timely']:row['reason']='not_committed_or_cancelled'
                elif duplicate:row['reason']='duplicate'
                else:
                    state=trial;seen.add(identity);after=metric.count(state);gain=after-before
                    if gain:positive+=1;longest_plateau=max(longest_plateau,completion-last_positive);last_positive=completion;zero=0
                    else:zero+=1;longest_zero=max(longest_zero,zero)
                    exported=metric.export(state)
                    if spec['configuration']!='HighDiv_release':
                        require(record['gain']==gain and record['covered']==after,'online marginal/coverage mismatch')
                        require(record['state_fnv1a64']==fingerprint(exported['states']),'complete seen0/seen1 fingerprint mismatch')
                        observed=sum((int(s[0]).bit_count()+int(s[1]).bit_count()) for s in exported['states'])
                        prior_observed=timeline[-1]['observed'] if timeline else 0
                        require(record['observations']==observed-prior_observed,'observation marginal mismatch')
                    else:observed=sum((int(s[0]).bit_count()+int(s[1]).bit_count()) for s in exported['states'])
                    row.update(accepted=True,reason='timely_distinct_valid',gain=gain)
                    timeline.append(dict(index=len(timeline)+1,source_index=record['index'],completion=completion,interval=completion-(timeline[-1]['completion'] if timeline else 0),gain=gain,covered=after,total=metric.total,coverage=after/metric.total if metric.total else 0,observed=observed,model_sha256=identity,state_fnv1a64=fingerprint(exported['states'])))
            audit.append(row)
        require(sum(r['gain'] for r in timeline)==metric.count(state),'marginal telescope')
        if summary:
            require(summary['formal_count']==len(timeline),'formal count differs')
            require(summary['coverage']==metric.export(state),'final full coverage states differ')
        longest_plateau=max(longest_plateau,seconds-last_positive)
        result=dict(formula=spec['formula'],seed=spec['seed'],configuration=spec['configuration'],group=spec['group'],run=run_name,seconds=seconds,total=metric.total,covered=metric.count(state),coverage=metric.count(state)/metric.total if metric.total else 0,distinct=len(timeline),saved_models=len(records),positive=positive,first_model_seconds=timeline[0]['completion'] if timeline else None,longest_zero_gain_models=longest_zero,longest_no_growth_seconds=longest_plateau,tail_no_growth_seconds=seconds-last_positive,process_wall_seconds=process['process_wall_seconds'],cleanup_seconds=process['cleanup_after_deadline_seconds'],returncode=process['returncode'],terminal=summary['terminal'] if summary else ('supervised_deadline' if process['termination_sent_seconds'] else 'original_process_exit'),query_counts={k:sum(q['evidence']['raw_status']==k for q in query_rows) for k in ['sat','unsat','unknown']} if query_rows else None,costs=summary['stats'] if summary else None,structure=structure['statistics'] if structure else None,compiler_stop=structure['stop_reason'] if structure else None,unknown_highdiv_internal_duplicate_and_query_costs=spec['configuration']=='HighDiv_release',audit_seconds=time.perf_counter()-began,issues=issues,excluded={k:sum(r['reason']==k for r in audit) for k in ['duplicate','late','missing_timestamp','not_committed_or_cancelled']},forbidden_search_attempts=attempts)
        require(not attempts,'audit attempted search')
        dest=out/'audit'/run_name;dest.mkdir(parents=True,exist_ok=True)
        write_csv(dest/'model_audit.csv',audit,('source_line','model_sha256','completion','reason'))
        write_csv(dest/'timeline.csv',timeline,('index','completion','gain','covered','total'))
        dump(dest/'coverage_final.json',metric.export(state));dump(dest/'summary.json',result)
        dump(dest/'source_hashes.json',{str(p.relative_to(out)):file_hash(p) for p in sorted(run.rglob('*')) if p.is_file()})
        return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('out');parser.add_argument('--jobs',type=int,default=3);a=parser.parse_args();out=Path(a.out).resolve()
    if (out/'archive_manifest.json').exists():
        from archive_bofill_results import restore
        restore(out)
    runs=[p.name for p in sorted((out/'runs').iterdir()) if p.is_dir()]
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:results=list(pool.map(audit_run,[(str(out),r) for r in runs]))
    dump(out/'audit_summary.json',dict(passed=True,runs=results,method='frozen ManualSatisfiesMetric; search entry points blocked; all saved full assignments validated'))
    print(f'Independent audit passed: {len(results)} runs, {sum(r["distinct"] for r in results)} timely distinct models',flush=True)
if __name__=='__main__':main()
