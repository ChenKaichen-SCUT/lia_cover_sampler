#!/usr/bin/env python3
"""Stage-one saved-trajectory diagnosis. No new models or solver searches."""
import argparse,csv,gzip,json,math,os,shutil,statistics,subprocess,tarfile,time
from collections import Counter,defaultdict,deque
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path
from reevaluate_round6_saved_candidates import (FrozenCoverage,no_search,require,dump,
    write_csv,file_hash,object_hash,model_id)
from highdiv_gap_common import add,covered,observed,gain_mask,obs_id,quantile,load_gz,dump_gz
from audit_native_feedback_results import affine,value
from audit_native_delivery_results import MT64
from analyze_highdiv_complementarity_gap import structure
from prepare_highdiv_complementarity_gap import layout_for
import z3

ROOT=Path(__file__).resolve().parents[1]
BASE='7d3d2e7'
SOURCE=ROOT/'results/position_frozen_900s'
OUT=ROOT/'results/position_mechanism'
PHASES=['0-30','30-120','120-300','300-600','600-900']
WINDOW=64

def records(path):
    opener=gzip.open if str(path).endswith('.gz') else open
    with opener(path,'rt') as f:
        for i,line in enumerate(f,1):
            require(line.endswith('\n'),f'incomplete row: {path}:{i}')
            yield json.loads(line)

def csv_rows(path):
    with Path(path).open() as f:return list(csv.DictReader(f))

def phase(t):
    return PHASES[next((i for i,b in enumerate([30,120,300,600]) if t<b),4)]

def zero_bucket(n):return '0' if n==0 else '1-4' if n<5 else '5+'

def state_stratum(r):return (r['phase'],r['covered_bin'],r['grant_ms'],r['prior_zero_bin'])

def marginal(state,obs):
    require(not(obs[0]&obs[1]),'single model provides both values of one bit')
    after=add(state,obs);gain=gain_mask(state,obs)
    first=((obs[0]|obs[1])&~(state[0]|state[1])).bit_count()
    require(observed(after)-observed(state)==first+gain.bit_count(),'first/complement partition')
    return after,gain,first

class History:
    def __init__(self):self.window=deque();self.atoms=set();self.variables=set();self.groups=set()
    def before(self,atom,variables,groups):
        aa={a for a,v,g in self.window};vv=set().union(*(v for a,v,g in self.window));gg=set().union(*(g for a,v,g in self.window))
        return dict(atom_novel64=atom not in aa,variable_novel64=bool(set(variables)-vv),group_novel64=bool(set(groups)-gg),
                    atom_first=atom not in self.atoms,new_variables=len(set(variables)-self.variables),new_groups=len(set(groups)-self.groups),
                    previous64=len(self.window),group_recent_occurrences=sum(bool(set(groups)&g) for a,v,g in self.window))
    def append(self,atom,variables,groups):
        self.window.append((atom,set(variables),set(groups)))
        if len(self.window)>WINDOW:self.window.popleft()
        self.atoms.add(atom);self.variables.update(variables);self.groups.update(groups)

def future_windows(rows):
    for i,r in enumerate(rows):
        for k in [1,5]:
            following=rows[i+1:i+1+k];complete=len(following)==k
            r[f'next{k}_complete']=complete
            r[f'next{k}_gain']=sum(x['gain'] for x in following) if complete else None
            r[f'next{k}_seconds']=sum(x['full_cycle_seconds'] for x in following) if complete else None

def matched_difference(rows,left,right,field,extra_keys=()):
    strata=defaultdict(lambda:[[],[]])
    for r in rows:
        if r[field] is None:continue
        key=state_stratum(r)+tuple(r[k] for k in extra_keys)
        if left(r):strata[key][0].append(r[field])
        elif right(r):strata[key][1].append(r[field])
    common=[(a,b) for a,b in strata.values() if a and b]
    weight=sum(min(len(a),len(b)) for a,b in common)
    delta=sum(min(len(a),len(b))*(statistics.mean(a)-statistics.mean(b)) for a,b in common)/weight if weight else None
    return dict(delta=delta,common_strata=len(common),weight=weight,left_available=sum(len(a) for a,b in strata.values()),right_available=sum(len(b) for a,b in strata.values()))

def distribution(rows):
    n=len(rows);sat=sum(r['status']=='sat' for r in rows);g=[r['gain'] for r in rows];cost=[r['full_cycle_seconds'] for r in rows]
    result=dict(queries=n,sat=sat,unknown=sum(r['status']=='unknown' for r in rows),delivered=sum(r['delivered'] for r in rows),
        positive=sum(v>0 for v in g),zero=sum(v==0 for v in g),gain=sum(g),mean_gain=statistics.mean(g) if n else None,
        positive_rate=sum(v>0 for v in g)/n if n else None,sat_rate=sat/n if n else None,unknown_rate=sum(r['status']=='unknown' for r in rows)/n if n else None,
        first_observations=sum(r['first_observations'] for r in rows),total_seconds=sum(cost),mean_seconds=statistics.mean(cost) if n else None,
        median_seconds=quantile(cost,.5),p90_seconds=quantile(cost,.9),p99_seconds=quantile(cost,.99),check_seconds=sum(r['check_seconds'] for r in rows),
        hook_seconds=sum(r['hook_seconds'] for r in rows),preparation_seconds=sum(r['preparation_seconds'] for r in rows),
        gain_per_full_second=sum(g)/sum(cost) if sum(cost)>0 else None,
        phase_retained=sum(r['phase_retained'] is True for r in rows),phase_evaluable=sum(r['phase_retained'] is not None for r in rows))
    for field in ['changed_variables','changed_groups','changed_equation_neighborhoods','conflicts_at_selection','next1_gain','next5_gain','next5_seconds']:
        v=[r[field] for r in rows if r[field] is not None];result['mean_'+field]=statistics.mean(v) if v else None;result['n_'+field]=len(v)
    return result

def structure_task(args):
    out,formula,representative=args;out=Path(out)
    with no_search() as attempts:
        metric=FrozenCoverage((out/'inputs'/formula).read_text());layout=layout_for(metric)
        old=json.loads((SOURCE/'audit'/representative/'layout.json').read_text());require(layout==old,'original AST layout changed')
        meta=structure(out/'inputs'/formula,layout)
        names=[str(v.decl().name()) for v in metric.decls]
        groups=sorted(meta['global_equation_groups']);groupof={v:i for i,g in enumerate(groups) for v in g}
        eq_by_var={v:[] for v in names}
        for eq in meta['equations']:
            if eq['global_necessary']:
                for v in eq['variables']:eq_by_var[v].append(eq['node'])
        masks={v:0 for v in names}
        for node,dep in zip(layout,meta['dependencies']):
            mask=((1<<node['width'])-1)<<node['offset']
            for v in dep:masks[v]|=mask
        info=dict(formula=formula,formula_sha256=file_hash(out/'inputs'/formula),total=metric.total,variables=names,groups=groups,
            group_ids=[object_hash(dict(formula=file_hash(out/'inputs'/formula),members=g)) for g in groups],groupof=groupof,
            equation_neighborhoods=eq_by_var,variable_observation_masks={v:hex(m) for v,m in masks.items()},
            group_count=len(groups),largest_group=max(map(len,groups)),groups_are_not_proven_independent=True,
            necessary_equations=sum(e['global_necessary'] for e in meta['equations']),conditional_equations=sum(not e['global_necessary'] for e in meta['equations']),search_calls=len(attempts))
        dest=out/'structure'/formula;dest.mkdir(parents=True,exist_ok=True);dump(dest/'structure.json',info);dump(dest/'layout.json',layout)
    return {k:info[k] for k in ['formula','group_count','largest_group','necessary_equations','conditional_equations','total']}

def check_source(out):
    proof=json.loads((out/'input_manifest.json').read_text())
    for p,h in proof['files'].items():require(file_hash(ROOT/p)==h,'source evidence changed: '+p)

def prepare(out):
    require(not(out/'plan.json').exists(),'new output directory required')
    out.mkdir(parents=True,exist_ok=True)
    frozen=subprocess.check_output(['git','show',BASE+':results/position_frozen_900s/evidence_manifest.json'],cwd=ROOT)
    require(object_hash(json.loads(frozen))==object_hash(json.loads((SOURCE/'evidence_manifest.json').read_text())),'source manifest differs from frozen commit')
    manifest=json.loads(frozen);archive=json.loads((SOURCE/'archive_manifest.json').read_text());plan=json.loads((SOURCE/'plan.json').read_text());files={};jobs=[]
    for name in ['plan.json','audit_summary.json','archive_manifest.json','evidence_manifest.json']:
        p=SOURCE/name
        if name!='evidence_manifest.json':require(file_hash(p)==manifest['files'][name],'source root evidence hash')
        files[str(p.relative_to(ROOT))]=file_hash(p)
    for entry in archive['runs']:
        name=entry['run'];run=SOURCE/'runs'/name
        if not run.exists():
            import tempfile
            with tempfile.TemporaryFile() as tmp:
                for part in entry['parts']:
                    p=SOURCE/part['file'];require(file_hash(p)==part['sha256'],'archive part hash');files[str(p.relative_to(ROOT))]=file_hash(p)
                    with p.open('rb') as f:shutil.copyfileobj(f,tmp)
                tmp.seek(0)
                with tarfile.open(fileobj=tmp,mode='r:gz') as tar:
                    for member in tar:
                        require(member.name.startswith('runs/'+name+'/') or member.name=='runs/'+name,'archive path scope')
                        require(not(member.issym() or member.islnk()) and '..' not in Path(member.name).parts,'unsafe archive entry')
                        tar.extract(member,out/'restored')
            run=out/'restored/runs'/name
        for relative,h in entry['content_sha256'].items():
            p=run/Path(relative).relative_to('runs/'+name);require(file_hash(p)==h,'raw archive mismatch: '+str(p));files[str(p.relative_to(ROOT))]=h
        for p in (SOURCE/'audit'/name).iterdir():
            if p.is_file():
                h=manifest['files'][str(p.relative_to(SOURCE))];require(file_hash(p)==h,'old audit hash');files[str(p.relative_to(ROOT))]=h
        spec=json.loads((run/'spec.json').read_text());jobs.append(dict(run=name,raw=str(run/'raw'),**{k:spec[k] for k in ['formula','seed','configuration']}))
    (out/'inputs').mkdir()
    for item in plan['inputs']:
        p=SOURCE/'inputs'/item['name'];require(file_hash(p)==item['sha256'],'formula hash');files[str(p.relative_to(ROOT))]=item['sha256'];shutil.copyfile(p,out/'inputs'/p.name)
    jobs.sort(key=lambda j:j['run']);require(len(jobs)==28,'wrong historical matrix')
    dump(out/'input_manifest.json',dict(base_commit=BASE,files=files,old_audit_reused=True))
    dump(out/'plan.json',dict(base_commit=BASE,stage='read_only_mechanism_diagnosis',jobs=jobs,workers=15,phases=PHASES,novelty_window=64,covered_bin_width=250,
        plateau_seconds=30,followup_queries=[1,5],gain_bins=['0','1-2','3-9','10-99','100+'],search=False,new_algorithm=False,
        analysis_code_sha256={name:file_hash(ROOT/name) for name in ['scripts/diagnose_position_mechanism.py','scripts/audit_position_mechanism.py','tests/test_position_mechanism.py']},
        protocol_sha256=file_hash(ROOT/'docs/原生干预位置机制诊断冻结协议.md')))

def run_one(args):
    out,job=args;out=Path(out);began=time.perf_counter();name=job['run'];raw=Path(job['raw']);audit=SOURCE/'audit'/name;dest=out/'runs'/name;dest.mkdir(parents=True,exist_ok=True)
    common={k:job[k] for k in ['run','formula','seed','configuration']};meta=json.loads((out/'structure'/job['formula']/'structure.json').read_text());groupof=meta['groupof'];masks={v:int(h,16) for v,h in meta['variable_observation_masks'].items()}
    qs=list(records(raw/'queries.jsonl'));ps=list(records(raw/'positions.jsonl'));costs=list(records(raw/'costs.jsonl'));ds={r['query']:r for r in records(raw/'delivery_commits.jsonl')};atomrows=list(records(raw/'atoms.jsonl'))
    saved_iter=records(audit/'models.jsonl.gz');candidate_iter=records(raw/'candidates.jsonl');next_saved=next(saved_iter,None);next_candidate=next(candidate_iter,None)
    history=History();state=(0,0);previous=None;previous_id=None;last_positive=0.;positive_seen=False;zero_queries=0;zero_models=0;rows=[];atom_table=[];witnesses=[];beforefirst=[];gap_rows=[]
    with no_search() as searches:
        metric=FrozenCoverage((out/'inputs'/job['formula']).read_text());decls={str(e.decl().name()):e for e in metric.decls};varids={e.get_id():i for i,e in enumerate(metric.decls) if e.is_int()};atoms={};parsed={}
        for a in atomrows:
            if not a['supported']:continue
            expr=z3.parse_smt2_string('(assert '+a['expression']+')',decls=decls)[0];e=expr
            while z3.is_not(e):e=e.arg(0)
            left,right=affine(e.arg(0),varids),affine(e.arg(1),varids)
            indices=sorted(i for i in left[1].keys()|right[1].keys() if left[1].get(i,0)!=right[1].get(i,0))
            require(indices==a['variables'],'atom dependency index mismatch')
            variables=[meta['variables'][i] for i in indices];groups=sorted({groupof[v] for v in variables})
            stable=object_hash(dict(formula=meta['formula_sha256'],expression=expr.sexpr()))
            entry=dict(cache_index=a['cache_index'],atom_id=stable,expression=expr.sexpr(),original_node=a['original_node'],variables=variables,groups=groups,
                variable_set_id=object_hash(dict(formula=meta['formula_sha256'],variables=variables)),group_set_id=object_hash(dict(formula=meta['formula_sha256'],groups=groups)),
                equation_neighborhoods=sorted(set().union(*(set(meta['equation_neighborhoods'][v]) for v in variables))))
            atoms[a['cache_index']]=entry;parsed[a['cache_index']]=expr;atom_table.append(common|entry)
        action_rng=MT64(job['seed']^0xd7846ba219);position_rng=MT64(job['seed']^0x8a5cd789635d2dff);generation_version=0
        for i,(q,p,cost) in enumerate(zip(qs,ps,costs)):
            require(q['query']==p['query']==cost['query']==i and q['decision']==p['decision'],'query association')
            require(q['generation_version_before']==generation_version,'prequery model version')
            d=q['decision'];selected=d['selected'];pos=p['position'];e=q['evidence'];cycle=cost['cycle_end']-cost['cycle_start'];require(cycle>=0,'negative cycle')
            require(e['actual_check_seconds']<=cycle+1e-6,'check outside full cycle')
            covered_before=covered(state).bit_count();obs_before=observed(state);prestate=obs_id(state)
            if pos['eligible']:
                target=1 if job['configuration']=='first_immediate' else (position_rng.next()>>62)+1;require(target==pos['target'],'K RNG replay')
            else:target=None
            if not generation_version:require(not selected and not pos['eligible'],'intervention before first model')
            if selected:
                draw=action_rng.next()/2**64;require(abs(draw-d['action_draw'])<1e-15 and d['action']==('FlipOnce' if draw>=.5 else 'KeepNative'),'action RNG')
                a=atoms[d['cache_index']];novel=history.before(a['atom_id'],a['variables'],a['groups'])
                require(pos['reached'] and pos.get('selected_rank',target)==target,'actual rank mismatch')
            else:a=None;novel={k:None for k in ['atom_novel64','variable_novel64','group_novel64','atom_first','new_variables','new_groups','previous64','group_recent_occurrences']}
            candidate=None;saved=None;delivered=False;gain=first=0;gmask=0;changed=None;changedgroups=None;changedeq=None;relatedgain=None;truth=None;mid=None;delivery_time=None;index=None
            if next_candidate is not None and next_candidate['query']==i:
                candidate=next_candidate;next_candidate=next(candidate_iter,None);require(candidate['valid'] and e['raw_status']=='sat','candidate validity log')
                values={v['name']:int(v['value']) if v['sort']=='Int' else v['value'] for v in candidate['model']};require(set(values)==set(meta['variables']),'incomplete original assignment')
                mid=model_id(candidate['model'])
                if previous is not None:
                    changed=sorted(v for v in values if values[v]!=previous[v]);changedgroups=sorted({groupof[v] for v in changed});changedeq=sorted(set().union(*(set(meta['equation_neighborhoods'][v]) for v in changed)))
                if selected:truth=bool(value(parsed[d['cache_index']],values))
                previous=values;prior_id=previous_id;previous_id=mid
                if q['generated']:
                    saved=next_saved;require(saved is not None and saved['query']==i and saved['model_id']==mid,'saved observation identity');next_saved=next(saved_iter,None)
                    require(saved['model']==candidate['model'],'audit/raw full assignment differs');generation_version+=1
                delivery=ds.get(i)
                if delivery and delivery['timely'] and not delivery['cancelled'] and delivery['delivery_complete_time']<=900:
                    require(saved is not None,'delivery without verified model');delivered=True;delivery_time=delivery['delivery_complete_time'];index=delivery['formal_output_index'];obs=(int(saved['zero'],16),int(saved['one'],16))
                    after,gmask,first=marginal(state,obs);gain=gmask.bit_count();require(gain==delivery['gain']==saved['gain'] and observed(after)-obs_before==delivery['observations'],'full observation replay')
                    require(covered(after).bit_count()==delivery['covered'],'covered prefix replay');state=after
                    if changed is not None:
                        changedmask=0
                        for v in changed:changedmask|=masks[v]
                        relatedgain=(gmask&changedmask).bit_count()
                    gap=delivery_time-last_positive
                    if gain:
                        gap_rows.append(common|dict(query=i,index=index,model_id=mid,completion=delivery_time,gain=gain,first_positive=not positive_seen,preceding_no_gain_seconds=gap,
                            break_30s=positive_seen and gap>=30,preceding_zero_models=zero_models,changed_variables=len(changed) if changed is not None else None,changed_groups=len(changedgroups) if changedgroups is not None else None,
                            action=d.get('action'),K=target,atom_id=a['atom_id'] if a else None,base_state_id=prestate,new_covered_mask=hex(gmask)))
                        last_positive=delivery_time;positive_seen=True;zero_models=0
                    else:zero_models+=1
                    witnesses.append(dict(query=i,index=index,model_id=mid,reference_model_id=prior_id,base_state_id=prestate,changed_variables=changed,changed_groups=changedgroups,changed_equation_neighborhoods=changedeq,new_covered_mask=hex(gmask),gain=gain,first_observations=first))
            else:require(e['raw_status']!='sat','SAT without saved complete model')
            row=common|dict(query=i,K=target,selected=selected,action=d.get('action') if selected else None,eligible=pos['eligible'],not_selected_reason=None if selected else pos['reason'],
                phase=phase(cost['cycle_start']),cycle_start=cost['cycle_start'],cycle_end=cost['cycle_end'],full_cycle_seconds=cycle,
                generation_version_before=q['generation_version_before'],covered_before=covered_before,covered_bin=covered_before//250,observations_before=obs_before,base_state_id=prestate,
                prior_zero_queries=zero_queries,prior_zero_bin=zero_bucket(zero_queries),status=e['raw_status'],raw_reason=e['raw_reason'],control_classification=e['control_classification'],
                grant_ms=e['granted_timeout_ms'],requested_ms=e['requested_timeout_ms'],retry_before=e['retry_level_before'],retry_after=e['retry_level_after'],check_seconds=e['actual_check_seconds'],
                preparation_seconds=q['preparation_seconds'],hook_seconds=q['hook'].get('hook_seconds',0),completion_seconds=e['completion_seconds'],validation_seconds=e['validation_seconds'],
                generated=q['generated'],delivered=delivered,delivery_index=index,delivery_time=delivery_time,model_id=mid,gain=gain,positive=int(gain>0),first_observations=first,
                zero_gain=gain==0,changed_variables=len(changed) if changed is not None else None,changed_groups=len(changedgroups) if changedgroups is not None else None,
                changed_equation_neighborhoods=len(changedeq) if changedeq is not None else None,gain_related_changed_variables=relatedgain,
                atom_id=a['atom_id'] if a else None,variable_set_id=a['variable_set_id'] if a else None,group_set_id=a['group_set_id'] if a else None,
                atom_variables=a['variables'] if a else [],atom_groups=a['groups'] if a else [],ordinal=d.get('ordinal'),conflicts_at_selection=d.get('conflicts'),
                total_query_conflicts=None,backtracks=None,learned_clauses=None,native_truth=d.get('native_truth'),desired_truth=d.get('desired_truth'),final_atom_truth=truth,
                phase_retained=(truth==d['desired_truth']) if truth is not None else None,**novel)
            rows.append(row)
            if selected:history.append(a['atom_id'],a['variables'],a['groups'])
            if q['generation_version_before']==0:beforefirst.append({k:row[k] for k in ['query','status','raw_reason','requested_ms','grant_ms','retry_before','retry_after','full_cycle_seconds','check_seconds','cycle_start','cycle_end','model_id','delivery_time','selected']})
            zero_queries=0 if gain else zero_queries+1
        require(len(qs)==len(ps)==len(costs)==len(rows) and next_saved is None and next_candidate is None,'unassociated records')
        prior=json.loads((audit/'summary.json').read_text());require(prior['covered']==covered(state).bit_count() and prior['delivered']==sum(r['delivered'] for r in rows),'final replay')
        require(sum(r['gain'] for r in rows)==prior['covered'] and not searches,'telescoping/search')
        future_windows(rows)
        write_csv(dest/'queries.csv',rows);dump_gz(dest/'queries.jsonl.gz',rows);write_csv(dest/'atoms.csv',atom_table);dump_gz(dest/'model_differences.jsonl.gz',witnesses);write_csv(dest/'positive_events.csv',gap_rows);dump(dest/'before_first.json',beforefirst)
        groups=defaultdict(list)
        for r in rows:
            if not r['selected']:continue
            for level,key in [('all',(r['K'],r['action'])),('phase',(r['phase'],r['K'],r['action'])),('state',(*state_stratum(r),r['K'],r['action']))]:groups[level,key].append(r)
        table=[]
        # Explicit empty 4x2 cells remain NA, never silently omitted.
        for k in ([1] if job['configuration']=='first_immediate' else [1,2,3,4]):
            for action in ['KeepNative','FlipOnce']:groups['all',(k,action)]
        for (level,key),rr in sorted(groups.items()):table.append(common|dict(level=level,stratum=json.dumps(key[:-2]),K=key[-2],action=key[-1],**distribution(rr)))
        write_csv(dest/'cells.csv',table)
        stage=[]
        for ph in PHASES:
            rr=[r for r in rows if r['phase']==ph];ss=[r for r in rr if r['selected']]
            stage.append(common|dict(phase=ph,unique_atoms=len({r['atom_id'] for r in ss}),unique_variables=len(set().union(*(set(r['atom_variables']) for r in ss))),
                unique_groups=len(set().union(*(set(r['atom_groups']) for r in ss))),new_variables=sum(r['new_variables'] for r in ss),new_groups=sum(r['new_groups'] for r in ss),
                atom_novel64_rate=statistics.mean(r['atom_novel64'] for r in ss) if ss else None,variable_novel64_rate=statistics.mean(r['variable_novel64'] for r in ss) if ss else None,
                group_novel64_rate=statistics.mean(r['group_novel64'] for r in ss) if ss else None,**distribution(rr)))
        write_csv(dest/'stages.csv',stage)
        selected=[r for r in rows if r['selected']];gains=[r['gain'] for r in rows if r['delivered']];after10=[r['gain'] for r in rows if r['delivered'] and r['delivery_index']>10]
        categories=[]
        for tag,data in [('all',gains),('after10',after10)]:
            for label,lo,hi in [('0',0,0),('1-2',1,2),('3-9',3,9),('10-99',10,99),('100+',100,10**20)]:
                values=[g for g in data if lo<=g<=hi];categories.append(common|dict(scope=tag,bin=label,models=len(values),gain=sum(values)))
        write_csv(dest/'gain_bins.csv',categories)
        ids=[r['atom_id'] for r in selected];freq=Counter(ids);totalgain=sum(gains)
        summary=common|dict(queries=len(rows),selected=len(selected),eligible=sum(r['eligible'] for r in rows),not_reached=sum(r['eligible'] and not r['selected'] for r in rows),
            models=sum(r['delivered'] for r in rows),covered=totalgain,unique_atoms=len(freq),effective_atoms=math.exp(-sum(n/len(ids)*math.log(n/len(ids)) for n in freq.values())) if ids else 0,
            unique_variables=len(history.variables),unique_groups=len(history.groups),exact_atom_repeat_rate=1-len(freq)/len(ids) if ids else None,
            atom_novel64_rate=statistics.mean(r['atom_novel64'] for r in selected),variable_novel64_rate=statistics.mean(r['variable_novel64'] for r in selected),group_novel64_rate=statistics.mean(r['group_novel64'] for r in selected),
            first_model_id=beforefirst[-1]['model_id'],first_model_seconds=beforefirst[-1]['delivery_time'],unknown_before_first=sum(r['status']=='unknown' for r in beforefirst),
            total_cycle_seconds=sum(r['full_cycle_seconds'] for r in rows),initialization_and_gaps_seconds=900-sum(r['full_cycle_seconds'] for r in rows),
            top10_gain=sum(sorted(gains,reverse=True)[:10]),top10_share=sum(sorted(gains,reverse=True)[:10])/totalgain,
            after10_gain=sum(after10),after10_top10_share=sum(sorted(after10,reverse=True)[:10])/sum(after10) if sum(after10) else None,
            breaks_30s=sum(r['break_30s'] for r in gap_rows),longest_no_growth_seconds=max([900-last_positive]+[r['preceding_no_gain_seconds'] for r in gap_rows]),
            final_censored_plateau_seconds=900-last_positive,search_calls=0,old_manual_audit_reused=True,elapsed=time.perf_counter()-began)
        dump(dest/'summary.json',summary)
    return summary

def contrast_rows(rows):
    results=[];common={k:rows[0][k] for k in ['run','formula','seed','configuration']};selected=[r for r in rows if r['selected']]
    for action in ['KeepNative','FlipOnce']:
        aa=[r for r in selected if r['action']==action]
        for k in [2,3,4]:
            for field in ['gain','positive','full_cycle_seconds','next5_gain']:
                result=matched_difference(aa,lambda r:r['K']==k,lambda r:r['K']==1,field)
                results.append(common|dict(contrast='K'+str(k)+'-K1',action=action,field=field,**result))
        for field in ['gain','positive','full_cycle_seconds','next5_gain']:
            rr=[r for r in results if r['action']==action and r['field']==field and r['contrast'] in ['K2-K1','K3-K1','K4-K1']]
            results.append(common|dict(contrast='late_mean-K1',action=action,field=field,
                delta=statistics.mean(r['delta'] for r in rr) if all(r['delta'] is not None for r in rr) else None,
                common_strata=sum(r['common_strata'] for r in rr),weight=min(r['weight'] for r in rr),
                left_available=sum(r['left_available'] for r in rr),right_available=rr[0]['right_available']))
        for novelty in ['atom_novel64','variable_novel64','group_novel64']:
            for field in ['gain','positive','full_cycle_seconds']:
                result=matched_difference(aa,lambda r:r[novelty],lambda r:not r[novelty],field,('K',))
                results.append(common|dict(contrast=novelty,action=action,field=field,**result))
    for label in ['K2-K1','K3-K1','K4-K1','late_mean-K1']:
        for field in ['gain','positive','full_cycle_seconds','next5_gain']:
            rr={r['action']:r for r in results if r['contrast']==label and r['field']==field}
            f,k=rr['FlipOnce'],rr['KeepNative']
            results.append(common|dict(contrast=label,action='Flip-minus-Keep',field=field,
                delta=f['delta']-k['delta'] if f['delta'] is not None and k['delta'] is not None else None,
                common_strata=min(f['common_strata'],k['common_strata']),weight=min(f['weight'],k['weight']),left_available=f['left_available'],right_available=k['left_available']))
    return results

def summarize(out):
    plan=json.loads((out/'plan.json').read_text());summaries=[];cells=[];stages=[];contrasts=[];first=[];itt=[];transition=[]
    for job in plan['jobs']:
        dest=out/'runs'/job['run'];s=json.loads((dest/'summary.json').read_text());summaries.append(s)
        rows=load_gz(dest/'queries.jsonl.gz') if (dest/'queries.jsonl.gz').exists() else None
        # Typed rows are retained independently of CSV string conversion.
        if rows is None:raise ValueError('missing typed query table')
        contrasts.extend(contrast_rows(rows));cells.extend(csv_rows(dest/'cells.csv'));stages.extend(csv_rows(dest/'stages.csv'))
        for k in [1,2,3,4]:
            rr=[r for r in rows if r['eligible'] and r['K']==k]
            if rr:itt.append({key:s[key] for key in ['run','formula','seed','configuration']}|dict(K=k,not_reached=sum(not r['selected'] for r in rr),**distribution(rr)))
        for label,condition in [('zero',lambda r:r['gain']==0),('gain1-2',lambda r:1<=r['gain']<=2),('gain3-99',lambda r:3<=r['gain']<100),('gain100+',lambda r:r['gain']>=100)]:
            rr=[r for r in rows if r['changed_variables'] is not None and condition(r)]
            transition.append({key:s[key] for key in ['run','formula','seed','configuration']}|dict(gain_class=label,**distribution(rr)))
    for formula in sorted({r['formula'] for r in summaries}):
        for seed in range(3,10):
            pair={r['configuration']:r for r in summaries if r['formula']==formula and r['seed']==seed};f,p=pair['first_immediate'],pair['position_immediate']
            logs=[json.loads((out/'runs'/r['run']/'before_first.json').read_text()) for r in [f,p]]
            fields=['status','requested_ms','grant_ms','retry_before','retry_after']
            sequences=[[{k:r[k] for k in fields} for r in log] for log in logs]
            first.append(dict(formula=formula,seed=seed,first_run=f['run'],position_run=p['run'],same_first_model=f['first_model_id']==p['first_model_id'],
                first_model_id=f['first_model_id'],position_model_id=p['first_model_id'],first_seconds=f['first_model_seconds'],position_seconds=p['first_model_seconds'],
                difference_seconds=p['first_model_seconds']-f['first_model_seconds'],first_unknowns=f['unknown_before_first'],position_unknowns=p['unknown_before_first'],same_pre_first_status_budget_sequence=sequences[0]==sequences[1],
                intervention_before_first=False,causal_attribution_to_position=False))
    write_csv(out/'sessions.csv',summaries);write_csv(out/'cells.csv',cells);write_csv(out/'stages.csv',stages);write_csv(out/'within_session_contrasts.csv',contrasts);write_csv(out/'first_model_pairs.csv',first);write_csv(out/'assigned_K_itt.csv',itt);write_csv(out/'transition_classes.csv',transition)
    aggregate=[];groups=defaultdict(list)
    for r in contrasts:
        if r['configuration']=='position_immediate':groups[r['formula'],r['contrast'],r['action'],r['field']].append(r)
    for (formula,contrast,action,field),rs in sorted(groups.items()):
        vv=[r['delta'] for r in rs if r['delta'] is not None]
        aggregate.append(dict(formula=formula,contrast=contrast,action=action,field=field,seeds=len(vv),missing=7-len(vv),mean=statistics.mean(vv) if vv else None,median=quantile(vv,.5),wins=sum(v>0 for v in vv),ties=sum(v==0 for v in vv),losses=sum(v<0 for v in vv),seed_differences=json.dumps({r['seed']:r['delta'] for r in rs}),minimum_common_weight=min((r['weight'] for r in rs),default=0)))
    write_csv(out/'seed_contrasts.csv',aggregate)
    # Position mean cells give each session equal weight, never weight by speed.
    cellgroups=defaultdict(list)
    for r in cells:
        if r['level']=='all':cellgroups[r['formula'],r['configuration'],r['K'],r['action']].append(r)
    mean_cells=[]
    for (formula,config,k,action),rs in sorted(cellgroups.items()):
        item=dict(formula=formula,configuration=config,K=k,action=action,seeds=len(rs),queries=sum(int(r['queries']) for r in rs))
        for field in ['mean_gain','positive_rate','sat_rate','unknown_rate','mean_seconds','median_seconds','p90_seconds','mean_changed_variables','mean_changed_groups','mean_conflicts_at_selection','mean_next5_gain']:
            vv=[float(r[field]) for r in rs if r[field]!=''];item[field]=statistics.mean(vv) if vv else None
        mean_cells.append(item)
    write_csv(out/'equal_seed_cells.csv',mean_cells)
    dump(out/'summary.json',dict(passed=True,sessions=len(summaries),queries=sum(r['queries'] for r in summaries),models=sum(r['models'] for r in summaries),search_calls=0,
        selected=sum(r['selected'] for r in summaries),not_reached=sum(r['not_reached'] for r in summaries),first_model_same=sum(r['same_first_model'] for r in first),first_sequence_same=sum(r['same_pre_first_status_budget_sequence'] for r in first),new_experiment_started=False))

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=OUT);p.add_argument('--prepare',action='store_true');p.add_argument('--run',action='store_true');p.add_argument('--jobs',type=int,default=15);a=p.parse_args();out=a.out.resolve();require(1<=a.jobs<=15,'worker limit')
    if a.prepare:prepare(out)
    if not a.run:return
    require(not(out/'summary.json').exists(),'do not overwrite completed diagnosis');check_source(out);plan=json.loads((out/'plan.json').read_text());started=time.perf_counter()
    jobs=plan['jobs'];unique={}
    for j in jobs:unique.setdefault(j['formula'],j['run'])
    with ProcessPoolExecutor(max_workers=min(a.jobs,2)) as pool:info=list(pool.map(structure_task,[(str(out),f,n) for f,n in unique.items()]))
    dump(out/'structure_summary.json',info)
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:
        futures={pool.submit(run_one,(str(out),j)):j['run'] for j in jobs};completed=0
        for future in as_completed(futures):
            result=future.result();completed+=1;dump(out/'progress.json',dict(status='diagnosis',completed=completed,total=28,workers=a.jobs));print(json.dumps(dict(run=result['run'],complete=completed,queries=result['queries'])),flush=True)
    summarize(out);check_source(out);dump(out/'execution.json',dict(workers=a.jobs,seconds=time.perf_counter()-started,source_unchanged=True,source_code_sha256=file_hash(__file__),git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),search_calls=0))
    dump(out/'progress.json',dict(status='analysis_complete_pending_independent_review',completed=28,total=28,workers=a.jobs))

if __name__=='__main__':main()
