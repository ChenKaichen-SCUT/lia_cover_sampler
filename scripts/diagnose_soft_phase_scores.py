#!/usr/bin/env python3
"""Frozen-candidate-only diagnosis. Does not import any sampler or call search.

Bitmaps index original AST preorder nodes at 64-bit word offsets. Every concrete
observation comes from the frozen ManualSatisfiesMetric. No hypothetical models.
"""
import argparse,csv,gzip,hashlib,json,math,statistics,sys,tarfile,time,shutil
from collections import Counter,defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from contextlib import contextmanager
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,require,file_hash,object_hash,model_id,dump,write_csv
from audit_soft_phase_results import atom_value
ROOT=Path(__file__).resolve().parents[1]
BASE='56cc9ef'
NA=None

def read_csv(path):
    with Path(path).open() as f:return list(csv.DictReader(f))

def line_records(path):
    rows=[];issues=[]
    if not path.exists():return [],[dict(file=str(path),issue='missing')]
    for i,line in enumerate(path.read_bytes().splitlines(keepends=True),1):
        if not line.endswith(b'\n'):issues.append(dict(file=str(path),line=i,issue='cutoff_tail'));continue
        try:r=json.loads(line)
        except (ValueError,UnicodeError):issues.append(dict(file=str(path),line=i,issue='corrupt_json'));continue
        r['_line']=i;r['_sha']=hashlib.sha256(line).hexdigest();rows.append(r)
    return rows,issues

def pack(zero,one):
    return (int.from_bytes(b''.join(int(v).to_bytes(8,'little') for v in zero),'little'),int.from_bytes(b''.join(int(v).to_bytes(8,'little') for v in one),'little'))
def unpack(state,n):
    z=state[0].to_bytes(n*8,'little');o=state[1].to_bytes(n*8,'little')
    return [(int.from_bytes(z[i:i+8],'little'),int.from_bytes(o[i:i+8],'little')) for i in range(0,n*8,8)]
def frozen_pack(metric,state,ids):
    return pack([state[k][1] for k in ids],[state[k][0] for k in ids])
def bitmap(summary):
    z=o=0
    for node,zero,one in summary['observations']:z|=int(zero)<<(64*node);o|=int(one)<<(64*node)
    return z,o

def guarantee(g,s):return (g[0]&s[1]&~s[0])|(g[1]&s[0]&~s[1])
def score(g,s):return guarantee(g,s).bit_count(),(g[0]&~s[0]).bit_count()+(g[1]&~s[1]).bit_count()
def union_bits(xs):
    result=0
    for x in xs:result|=x
    return result

def gain(s,observation):
    after=(s[0]|observation[0],s[1]|observation[1]);d=(after[0]&after[1])&~(s[0]&s[1])
    new=(observation[0]&~s[0]).bit_count()+(observation[1]&~s[1]).bit_count()
    first=((observation[0]|observation[1])&~(s[0]|s[1])).bit_count()
    require(new==first+d.bit_count(),'single-model first/complement split mismatch')
    return after,d,new,first

def explain(d,sets,timely):
    e=union_bits(sets);require(e&~d==0,'H/E is not a subset of concrete D')
    return dict(explained=e.bit_count() if timely else 0,unexplained=(d&~e).bit_count() if timely else 0,
                explanation_ratio=e.bit_count()/d.bit_count() if timely and d else None,
                overlapping_count=sum(x.bit_count() for x in sets)-e.bit_count(),candidate_explained=e.bit_count(),candidate_gain=d.bit_count()),e

def category(a,b):
    if a[1]==b[1]==0:return 'no_new_observation'
    if a[0]!=b[0]:return 'covered_difference'
    if a[1]!=b[1]:return 'observation_only_difference'
    return 'full_tie_positive_observation'

def prefix_stage(n):return 'first' if n<=1 else '2-10' if n<=10 else '11-50' if n<=50 else '51-100' if n<=100 else '101+'
def time_stage(t):return '0-10' if t<10 else '10-30' if t<30 else '30-60'
def cost_bucket(t):return '<50ms' if t<.05 else '50-200ms' if t<.2 else '200-1000ms' if t<1 else '>=1000ms'
def ratios(a,b):return a/b if b else None

def fingerprint(s,n):
    h=14695981039346656037
    for z,o in unpack(s,n):
        for v in (z,o):
            for b in v.to_bytes(8,'little'):h=((h^b)*1099511628211)&((1<<64)-1)
    return str(h)

def decode_events(hook):
    """Only missing non-adoption events can be truncated; never infer absence."""
    events=[];used=set();groups=set();count=0;known=Counter()
    for event in hook.get('events',[]):
        atom=event['atom_id'];group=event['group']
        reason=('already_used' if atom in used or group in groups else 'override_cap' if count>=8 else
                'conflict_cap' if event['conflicts']>=64 else 'same_native' if event['native_truth']==event['preferred_truth'] else 'applied')
        require((reason=='applied')==event['applied'],'saved event inconsistent with native gate ordering')
        if event['applied']:used.add(atom);groups.add(group);count+=1
        known[reason]+=1;events.append(dict(event,reason=reason))
    require(count==hook.get('applied',0),'applied event missing/truncated')
    missing=hook.get('opportunities',0)-len(events);require(missing>=0,'more events than opportunities')
    remainder={k:hook.get(k,0)-known[k] for k in ('same_native','already_used','override_cap','conflict_cap')}
    require(all(v>=0 for v in remainder.values()) and sum(remainder.values())==missing,'unattributable event count mismatch')
    return events,remainder,missing==0

def stable_atom(formula_hash,index,sexpr):
    return object_hash(dict(formula=formula_hash,original_preorder=index,original_expression=sexpr))

def availability(atom_scores,summary_union,state):
    scores=[x for sides in atom_scores.values() for x in sides]
    return dict(positive_L_directions=sum(l>0 for l,n in scores),only_N_directions=sum(l==0 and n>0 for l,n in scores),
        tied_atoms=sum(a==b for a,b in atom_scores.values()),zero_N_atoms=sum(a[1]==b[1]==0 for a,b in atom_scores.values()),
        maximum_L=max((l for l,n in scores),default=0),maximum_N=max((n for l,n in scores),default=0),
        L_distribution=json.dumps(dict(sorted(Counter(l for l,n in scores).items()))),N_distribution=json.dumps(dict(sorted(Counter(n for l,n in scores).items()))),
        describable_missing_bits=guarantee(summary_union,state).bit_count())

@contextmanager
def gz_rows(path):
    with gzip.open(path,'wt',newline='') as f:
        writer=None
        def emit(row):
            nonlocal writer
            if writer is None:writer=csv.DictWriter(f,fieldnames=list(row));writer.writeheader()
            writer.writerow(row)
        yield emit

def correlations(points):
    n=len(points)
    if n<10:return dict(n=n,pearson=None,spearman=None,reason='fewer_than_10_points')
    x,y=zip(*points)
    def corr(a,b):
        ma=statistics.mean(a);mb=statistics.mean(b);num=sum((u-ma)*(v-mb) for u,v in zip(a,b));den=math.sqrt(sum((u-ma)**2 for u in a)*sum((v-mb)**2 for v in b))
        return num/den if den else None
    def rank(a):
        sorted_pairs=sorted(enumerate(a),key=lambda p:p[1]);r=[0.]*len(a);i=0
        while i<len(a):
            j=i+1
            while j<len(a) and sorted_pairs[j][1]==sorted_pairs[i][1]:j+=1
            for k in range(i,j):r[sorted_pairs[k][0]]=(i+j-1)/2+1
            i=j
        return r
    p=corr(x,y);return dict(n=n,pearson=p,spearman=corr(rank(x),rank(y)),reason='descriptive_only' if p is not None else 'constant_variable')

def prepare_inputs(out):
    """Read frozen archives; all extraction/copies stay under the new output."""
    sources={};jobs=[]
    for stage in ('screen','exposure'):
        old=ROOT/f'results/soft_phase_{stage}';dest=out/'inputs'/stage;dest.mkdir(parents=True,exist_ok=False)
        for p in old.rglob('*'):
            if p.is_file() and 'runs' not in p.relative_to(old).parts:sources[str(p.relative_to(ROOT))]=file_hash(p)
        for name in ('plan.json','archive_manifest.json','process_results.json','audit_summary.json'):
            shutil.copyfile(old/name,dest/name)
        shutil.copytree(old/'inputs',dest/'inputs');shutil.copytree(old/'audit',dest/'prior_audit')
        manifest=json.loads((dest/'archive_manifest.json').read_text())
        for r in manifest['runs']:
            require(len(r['parts'])==1,'unexpected split archive; explicit merge required')
            part=r['parts'][0];archive=old/part['file'];require(file_hash(archive)==part['sha256'],'archive hash mismatch')
            with tarfile.open(archive,'r:gz') as t:
                for m in t.getmembers():
                    target=(dest/m.name).resolve();require(target.is_relative_to(dest.resolve()) and not m.issym() and not m.islnk(),'unsafe archive member')
                    require(m.name=='runs/'+r['run'] or m.name.startswith('runs/'+r['run']+'/'),'archive run identity mismatch')
                t.extractall(dest,filter='data')
            for name,h in r['content_sha256'].items():require(file_hash(dest/name)==h,'extracted raw hash mismatch')
            jobs.append((str(out),stage,r['run']))
    for name in ('src/soft_phase_sampler.cpp','src/conditional_observation.cpp','dependencies/patches/z3_soft_phase.patch','docs/覆盖软偏好实验协议.md','docs/覆盖软偏好实现与实验报告.md','scripts/audit_soft_phase_results.py','scripts/reevaluate_round6_saved_candidates.py'):
        sources[name]=file_hash(ROOT/name)
    sources['/tmp/lia-highdiv-frozen/HighDiv/my_scripts/calc_metric.py']=file_hash('/tmp/lia-highdiv-frozen/HighDiv/my_scripts/calc_metric.py')
    dump(out/'source_manifest.json',dict(base_commit=BASE,files=sources,method='immutable archive input; extraction and outputs isolated',source_bundle_sha256=object_hash(sources)))
    return jobs

def run_session(args):
    out,stage,run=args;out=Path(out);inputs=out/'inputs'/stage;raw=inputs/'runs'/run/'raw';dest=out/stage/run;dest.mkdir(parents=True,exist_ok=False)
    spec=json.loads((raw.parent/'spec.json').read_text());plan=json.loads((inputs/'plan.json').read_text());process=json.loads((raw.parent/'process_result.json').read_text())
    formula=inputs/'inputs'/spec['formula'];fh=file_hash(formula);require(fh==next(x['sha256'] for x in plan['inputs'] if x['name']==spec['formula']),'formula hash mismatch')
    sources={p.name:file_hash(p) for p in sorted(raw.iterdir()) if p.is_file()};bundle=object_hash(sources)
    common=dict(stage=stage,formula=spec['formula'],formula_sha256=fh,configuration=spec['configuration'],seed=spec['seed'],session=run,source_bundle_sha256=bundle,
                query=None,coverage_version=None,atom_key=None,direction=None,query_record_sha256=None,catalog_sha256=sources.get('summaries.json'))
    qs,issues=line_records(raw/'queries.jsonl');cs,ii=line_records(raw/'candidates.jsonl');issues+=ii;ms,ii=line_records(raw/'models.jsonl');issues+=ii;receipts,ii=line_records(raw/'commits.jsonl');issues+=ii
    def indexed(rows,key):
        result={r[key]:r for r in rows};require(len(result)==len(rows),'duplicate identity');return result
    candidates=indexed(cs,'query');models=indexed(ms,'query');commits=indexed(receipts,'query')
    catalog=json.loads((raw/'summaries.json').read_text()) if (raw/'summaries.json').exists() else None
    summary=json.loads((raw/'summary.json').read_text()) if (raw/'summary.json').exists() else None
    prior_queries={int(r['query']):r for r in read_csv(inputs/'prior_audit'/run/'phase_queries.csv')}
    prior_realization={(int(r['query']),int(r['node'])):r for r in read_csv(inputs/'prior_audit'/run/'phase_realization.csv')}
    query_rows=[];event_rows=[];explanations=[];states=[];atom_rows=[];class_stats=defaultdict(Counter);class_query_ids=defaultdict(set);applied_history=defaultdict(list)
    initial_time=None;first_obs=post_obs=post_first=post_covered=0;last_finish=0.;commit_times={0:0.};seen=set();state=(0,0);version=0;coverage_sum=0;status_counter=Counter();checks=0;all_candidates_seen=set()
    began=time.perf_counter()
    with no_search() as forbidden, gz_rows(dest/'phase_opportunities.csv.gz') as opportunity, gzip.open(dest/'bitmap_evidence.jsonl.gz','wt') as bitfile:
        metric=FrozenCoverage(formula.read_text());ids=[n.get_id() for n in metric.nodes];node_count=len(ids);atoms={};summary_union=(0,0)
        for a in (catalog or {}).get('atoms',[]):
            n=a['node'];expr=metric.nodes[n].sexpr();require(''.join(expr.split())==''.join(a['expression'].split()),'original atom expression identity mismatch')
            key=stable_atom(fh,n,expr);sides=[bitmap(g) for g in a['sides']];require(a['sides'][0]['truth'] is False and a['sides'][1]['truth'] is True,'summary direction labels')
            atoms[n]=dict(a,key=key,sides_bitmap=sides)
            summary_union=(summary_union[0]|sides[0][0]|sides[1][0],summary_union[1]|sides[0][1]|sides[1][1])
            atom_rows.append(dict(common,atom_key=key,node=n,original_ast_id_local=a['ast_id'],group_node=a['group'],original_expression=expr,
                                  original_expression_sha256=hashlib.sha256(expr.encode()).hexdigest(),normalized_original_expression=expr,
                                  normalization='canonical original S-expression; internal normalized expression not logged',internal_normalized_expression=None,
                                  zero_observation_bits=sides[0][0].bit_count()+sides[0][1].bit_count(),one_observation_bits=sides[1][0].bit_count()+sides[1][1].bit_count()))
        def new_state():
            scores={n:tuple(score(g,state) for g in a['sides_bitmap']) for n,a in atoms.items()}
            row=dict(common,coverage_version=version,state_start_seconds=commit_times[version],state_end_seconds=None,state_duration_seconds=None,
                     state_fnv1a64=fingerprint(state,node_count),covered=(state[0]&state[1]).bit_count(),catalog_present=catalog is not None,
                     catalog_available_online=bool(catalog is not None and version>0),catalog_complete=(catalog or {}).get('complete'),catalog_atoms=len(atoms),
                     catalog_summary_nodes=(catalog or {}).get('summary_nodes'),catalog_reasons=json.dumps((catalog or {}).get('reasons',{})),
                     formal_state_stage=prefix_stage(version),query_count=0,proposed=0,applied=0,delivered_gain=0,catalog_outside_gain=0)
            if catalog is not None:row.update(availability(scores,summary_union,state))
            else:row.update({k:None for k in availability({},(0,0),state)})
            states.append(row);return scores,row
        scores,available=new_state()
        for q in qs:
            qid=q['query'];require(qid==len(query_rows),'noncontiguous query IDs')
            require(q['coverage_version']==version,'query coverage version differs from timely trajectory')
            require(last_finish<=q['start']<=q['finished'],'query ordering')
            cycle=q['finished']-last_finish;span=q['finished']-q['start'];pre=q['start']-last_finish;last_finish=q['finished']
            h=q['hook'];events,remainder,complete=decode_events(h);adopted=[e for e in events if e['applied']]
            prefs={p['node']:p for p in q['preferences']};require(len(prefs)==len(q['preferences']),'duplicate preference')
            by_id={p['ast_id']:p for p in prefs.values()};mapping={m['atom_id']:m for m in h.get('mapping',[])}
            require(len(prefs)==h.get('installed',0),'installed table count differs')
            if prefs:require(h['epoch']==qid and h['active'],'query epoch/install lifecycle')
            by_events=defaultdict(list)
            for e in events:
                require(e['atom_id'] in by_id,'native event has no installed original atom');p=by_id[e['atom_id']]
                require(e['group']==p['group'] and e['preferred_truth']==p['truth'],'event origin/sign mismatch');by_events[p['node']].append(e)
            for n,p in prefs.items():
                require(n in atoms and p['ast_id']==atoms[n]['ast_id'] and p['group']==atoms[n]['group'],'preference/source catalog identity')
                require(p['coverage_version']==version,'preference coverage version stale')
                a,b=scores[n]
                require((p['zero_covered'],p['zero_observed'])==a and (p['one_covered'],p['one_observed'])==b,'static score replay mismatch')
                require(p['lower_bound']==scores[n][p['truth']][0],'preferred L mismatch')
            c=candidates.get(qid);m=models.get(qid);receipt=commits.get(qid);candidate_hash=None;truths={};d=0;observation=None;after=state;newobs=first=0;timely=False
            if c:
                all_candidates_seen.add(qid);require(q['evidence']['raw_status']=='sat' and c['valid'] and q['valid'],'non-SAT/invalid saved candidate');require(c['coverage_version']==version,'candidate state version differs')
                candidate_hash=model_id(c['model']);single=metric.apply(metric.empty,c['model']);observation=frozen_pack(metric,single,ids)
                require(observation[0]&observation[1]==0,'one deterministic model observed both bit values');after,d,newobs,first=gain(state,observation)
                vals={v['name']:int(v['value']) if v['sort']=='Int' else v['value'] for v in c['model']}
                truths={n:bool(atom_value(metric.nodes[n],vals)) for n in atoms}
                for n,a in atoms.items():
                    g=a['sides_bitmap'][truths[n]];require(not(g[0]&~observation[0]) and not(g[1]&~observation[1]),'conditional guaranteed observation false');checks+=1
                if m:
                    require(m['model']==c['model'],'candidate/formal full assignment differs')
                    require(m['gain']==d.bit_count() and m['observations']==newobs and m['covered']==(after[0]&after[1]).bit_count(),'model marginal differs')
                    require(m['state_fnv1a64']==fingerprint(after,node_count),'formal full-state fingerprint differs')
                if receipt:
                    require(m is not None and receipt['index']==m['index'],'orphan receipt')
                    timely=bool(receipt['timely'] and receipt['completion']<=spec['seconds'])
                    require(c['validated_seconds']<=receipt['completion']<=q['finished'],'candidate/commit times inconsistent')
                require(bool(q['emitted'])==timely,'emission/receipt inconsistent')
                if timely:require(candidate_hash not in seen and m['index']==version+1,'duplicate/noncontiguous formal model')
            else:
                require(not q['emitted'],'output has no saved candidate')
                if q['evidence']['raw_status']=='sat':issues.append(dict(query=qid,issue='SAT_without_saved_model'))
            qb=dict(common,query=qid,coverage_version=version,query_record_sha256=q['_sha'])
            applied_sets=[];proposed_sets=[];all_catalog_H=guarantee(summary_union,state);query_applied_scores=[];query_proposed_scores=[];realized_adopted=0
            applied_set_details=[]
            for n,a in atoms.items():
                zero,one=scores[n];kind=category(zero,one);p=prefs.get(n);es=by_events[n];mp=mapping.get(a['ast_id']);chosen=p['truth'] if p else None
                online=version>0;eligible=online and kind!='no_new_observation' and (spec['configuration']=='random_soft' or kind!='full_tie_positive_observation')
                require(bool(p)==eligible,'mode-specific installation policy differs from frozen code')
                if p:
                    if spec['configuration']=='coverage_soft':require(chosen==(one>zero),'coverage ranking differs')
                    query_proposed_scores.append(scores[n][chosen][0])
                    if c and truths[n]==chosen:proposed_sets.append(guarantee(a['sides_bitmap'][chosen],state))
                stats=class_stats[kind] if online else None
                if stats is not None:
                    stats['atom_query_opportunities']+=1;stats['proposed']+=bool(p);stats['mapped_at_capture']+=bool(mp and mp['mapped']);stats['saved_native_opportunities']+=len(es);stats['applied']+=sum(e['applied'] for e in es)
                    if p:class_query_ids[(kind,'proposed')].add(qid)
                    if any(e['applied'] for e in es):class_query_ids[(kind,'applied')].add(qid)
                for direction in (False,True):
                    proposed=bool(p and chosen==direction);here=[e for e in es if e['preferred_truth']==direction];actual=any(e['applied'] for e in here)
                    encountered=bool(here) if here or (proposed and complete) else None
                    own=scores[n][direction];opp=scores[n][not direction]
                    opportunity(dict(qb,atom_key=a['key'],direction=direction,node=n,group_node=a['group'],original_ast_id_local=a['ast_id'],
                        normalized_ast_id_local=mp['normalized_id'] if mp else None,internal_var_local=mp['internal_var'] if mp else None,negated=mp['negated'] if mp else None,
                        catalog_available_online=online,summary_exists=True,opportunity_class=kind,L=own[0],N=own[1],opposite_summary_score=json.dumps(dict(L=opp[0],N=opp[1])),
                        opposite_generated_model_gain=None,opposite_query_cost=None,tied=zero==one,covered_tied_N_differs=zero[0]==one[0] and zero[1]!=one[1],
                        mode_pair_eligible=eligible,proposed=proposed,installed=proposed,mapped_at_capture=mp['mapped'] if proposed else None,
                        native_encountered=encountered,saved_native_opportunities=len(here),events_complete=complete,
                        same_native_observed=any(e['reason']=='same_native' for e in here),applied=actual,
                        skipped_cap_observed=any(e['reason'] in ('override_cap','conflict_cap') for e in here),
                        final_direction_realized=(truths[n]==direction) if c else None,candidate_sha256=candidate_hash,timely=timely,
                        native_deltas_saved=json.dumps([dict(decision=e['decision'],delta_L=own[0]-scores[n][e['native_truth']][0],delta_N=own[1]-scores[n][e['native_truth']][1]) for e in here])))
                for e in es:
                    if not e['applied']:continue
                    direction=e['preferred_truth'];l,nscore=scores[n][direction];realized=(truths[n]==direction) if c else None;H=guarantee(a['sides_bitmap'][direction],state)
                    require(H.bit_count()==l,'H cardinality differs from L')
                    if realized:
                        require(H&~d==0,'realized applied H outside concrete gain');applied_sets.append(H);realized_adopted+=1
                    query_applied_scores.append(l)
                    old=applied_history[(a['key'],direction)];prev=old[-1] if old else None
                    event=dict(qb,atom_key=a['key'],direction=direction,node=a['node'],group_node=a['group'],original_ast_id_local=e['atom_id'],normalized_ast_id_local=e['normalized_id'],internal_var_local=e['internal_var'],negated=e['negated'],opportunity_class=kind,
                        native_truth=e['native_truth'],L=l,N=nscore,opposite_summary_score=json.dumps(dict(L=scores[a['node']][not direction][0],N=scores[a['node']][not direction][1])),
                        opposite_generated_model_gain=None,opposite_query_cost=None,native_L=scores[a['node']][e['native_truth']][0],native_N=scores[a['node']][e['native_truth']][1],
                        delta_native_L=l-scores[a['node']][e['native_truth']][0],delta_native_N=nscore-scores[a['node']][e['native_truth']][1],
                        raw_status=q['evidence']['raw_status'],final_realized=realized,candidate_sha256=candidate_hash,timely=timely,candidate_gain=d.bit_count() if c else None,delivered_gain=d.bit_count() if timely else 0,
                        guaranteed_bits=l,H_sha256=object_hash(hex(H)),associated_query_cycle_seconds=cycle,associated_recorded_query_seconds=span,check_seconds=q['evidence']['actual_check_seconds'],granted_timeout_ms=q['evidence']['granted_timeout_ms'],
                        conflicts_at_event=e['conflicts'],query_total_conflicts=None,event_timestamp=None,phase_owned_cost=None,high_cost_cycle=cycle>=.2,
                        previous_direction_query=prev['query'] if prev else None,repeated_same_version=bool(prev and prev['coverage_version']==version),consecutive_query_repeat=bool(prev and prev['query']+1==qid),score_unchanged_from_previous=bool(prev and (prev['L'],prev['N'])==(l,nscore)))
                    old.append(event);event_rows.append(event);applied_set_details.append(dict(atom_key=a['key'],direction=direction,L=l,H=hex(H),realized=realized))
                    if stats is not None:stats['applied_candidate_known']+=realized is not None;stats['applied_final_realized']+=realized is True;stats['applied_final_unrealized']+=realized is False
                    prior=prior_realization.get((qid,a['node']))
                    if c:require(prior is not None and prior['actual_override']=='True' and (prior['realized']=='True')==realized,'previous phase realization audit differs')
            main,E=explain(d,applied_sets,timely);prop,P=explain(d,proposed_sets,timely)
            formal_gain=d.bit_count() if timely else 0;catalog_outside=(d&~all_catalog_H).bit_count() if timely else 0
            explanations.append(dict(qb,candidate_sha256=candidate_hash,candidate_known=c is not None,timely=timely,D_bits=formal_gain,candidate_D_bits=d.bit_count() if c else None,
                E_applied_realized_bits=main['explained'],applied_explanation_ratio=main['explanation_ratio'],outside_applied_summary=main['unexplained'],applied_overlap=main['overlapping_count'],
                E_proposed_realized_bits=prop['explained'],proposed_explanation_ratio=prop['explanation_ratio'],outside_proposed_summary=prop['unexplained'],proposed_overlap=prop['overlapping_count'],
                catalog_described_gain=(d&all_catalog_H).bit_count() if timely else 0,catalog_outside_gain=catalog_outside,ratio_label='已实现干预摘要的覆盖解释比例；不是因果收益'))
            bitfile.write(json.dumps(dict(query=qid,coverage_version=version,state_fnv1a64=available['state_fnv1a64'],candidate_sha256=candidate_hash,candidate_known=c is not None,timely=timely,D=hex(d) if c else None,E_applied=hex(E),E_proposed=hex(P),catalog_H=hex(all_catalog_H),applied=applied_set_details))+'\n')
            row=dict(qb,start=q['start'],finished=q['finished'],recorded_query_seconds=span,cycle_seconds=cycle,pre_query_unseparated_seconds=pre,check_seconds=q['evidence']['actual_check_seconds'],granted_timeout_ms=q['evidence']['granted_timeout_ms'],
                raw_status=q['evidence']['raw_status'],raw_reason=q['evidence']['raw_reason'],control_classification=q['evidence']['control_classification'],budget_clip_reason=q['evidence']['budget_clip_reason'],
                candidate_known=c is not None,candidate_sha256=candidate_hash,candidate_gain=d.bit_count() if c else None,timely=timely,completion=receipt['completion'] if receipt else None,
                delivered_gain=formal_gain,new_observations=newobs if timely else 0,first_side_observations=first if timely else 0,prefix_stage=prefix_stage(version+1),time_stage=time_stage(q['start']),
                catalog_available_online=catalog is not None and version>0,catalog_atoms=len(atoms),positive_L_directions=available['positive_L_directions'],proposed=len(prefs),mapped=sum(m['mapped'] for m in mapping.values()),
                opportunities=h.get('opportunities',0),same_native=h.get('same_native',0),applied=len(adopted),already_used=h.get('already_used',0),override_cap=h.get('override_cap',0),conflict_cap=h.get('conflict_cap',0),
                saved_nonapplied_events=len(events)-len(adopted),unattributable_nonapplied_events=sum(remainder.values()),unattributable_reasons=json.dumps(remainder),events_complete=complete,
                realized_adoptions=realized_adopted,unrealized_adoptions=len(adopted)-realized_adopted if c else None,adoptions_without_candidate=len(adopted) if not c else 0,
                max_applied_L=max(query_applied_scores) if query_applied_scores else None,max_proposed_L=max(query_proposed_scores) if query_proposed_scores else None,
                explained_applied=main['explained'],explained_proposed=prop['explained'],catalog_outside_gain=catalog_outside,high_cost_cycle=cycle>=.2,cost_bucket=cost_bucket(cycle),
                query_conflicts_lower_bound=max((e['conflicts'] for e in events),default=None),query_total_conflicts=None)
            prior=prior_queries[qid]
            for col in ('proposed','mapped','opportunities','same_native','applied','already_used','override_cap','conflict_cap'):require(row[col]==int(prior[col]),'prior aggregate differs: '+col)
            query_rows.append(row);status_counter[row['raw_status']]+=1;available['query_count']+=1;available['proposed']+=len(prefs);available['applied']+=len(adopted);available['delivered_gain']+=formal_gain;available['catalog_outside_gain']+=catalog_outside
            if timely:
                if version==0:first_obs=newobs;initial_time=receipt['completion']
                else:post_obs+=newobs;post_first+=first;post_covered+=formal_gain
                state=after;seen.add(candidate_hash);version+=1;coverage_sum+=formal_gain;commit_times[version]=receipt['completion'];scores,available=new_state()
        require(coverage_sum==(state[0]&state[1]).bit_count(),'gain telescope')
        if summary:
            require(summary['formal_count']==version and summary['coverage']['covered']==coverage_sum and summary['coverage']['total']==metric.total,'final counts')
            require(summary['coverage']['states']==[[str(z),str(o)] for z,o in unpack(state,node_count)],'final complete state differs')
        require(not forbidden,'offline analysis called search')
        for qid in candidates.keys()-all_candidates_seen:issues.append(dict(query=qid,issue='candidate_without_complete_query_record'))
    for i,r in enumerate(states):
        r['state_end_seconds']=states[i+1]['state_start_seconds'] if i+1<len(states) else spec['seconds'];r['state_duration_seconds']=r['state_end_seconds']-r['state_start_seconds']
    zero_intervals=[];revivals=[]
    for r in states:
        if not r['catalog_available_online']:continue
        zero=r['positive_L_directions']==0
        if zero and (not zero_intervals or zero_intervals[-1]['end_seconds']!=r['state_start_seconds']):zero_intervals.append(dict(start_version=r['coverage_version'],start_seconds=r['state_start_seconds'],end_seconds=r['state_end_seconds'],end_version=r['coverage_version']))
        elif zero:zero_intervals[-1].update(end_seconds=r['state_end_seconds'],end_version=r['coverage_version'])
        elif zero_intervals and zero_intervals[-1]['end_seconds']==r['state_start_seconds']:revivals.append(r['coverage_version'])
    repeats=[]
    for (key,direction),es in applied_history.items():
        consecutive=maximum=1;unknown_chain=maximum_unknown=0;previous=None
        for e in es:
            consecutive=consecutive+1 if previous and e['query']==previous['query']+1 else 1;maximum=max(maximum,consecutive)
            unknown_chain=unknown_chain+1 if previous and e['raw_status']=='unknown' and previous['raw_status']=='unknown' and (e['coverage_version'],e['L'],e['N'])==(previous['coverage_version'],previous['L'],previous['N']) else int(e['raw_status']=='unknown')
            maximum_unknown=max(maximum_unknown,unknown_chain);previous=e
        repeats.append(dict(common,atom_key=key,direction=direction,queries=len(es),coverage_versions=len({e['coverage_version'] for e in es}),same_version_repeat_count=sum(e['repeated_same_version'] for e in es),max_consecutive_queries=maximum,
            max_unchanged_unknown_occurrence_run=maximum_unknown,known_final_models=sum(e['final_realized'] is not None for e in es),final_realized=sum(e['final_realized'] is True for e in es),final_unrealized=sum(e['final_realized'] is False for e in es),
            no_candidate=sum(e['final_realized'] is None for e in es),L_values=json.dumps([e['L'] for e in es]),N_values=json.dumps([e['N'] for e in es]),query_ids=json.dumps([e['query'] for e in es]),
            associated_unique_query_cost=sum(e['associated_query_cycle_seconds'] for e in es),associated_delivered_gain=sum(e['delivered_gain'] for e in es),high_cost_queries=sum(e['high_cost_cycle'] for e in es),cost_warning='associated unique queries within this direction; do not sum across directions'))
    stage_rows=[]
    for dimension in ('prefix_stage','time_stage'):
        for label in dict.fromkeys(q[dimension] for q in query_rows):
            subset=[q for q in query_rows if q[dimension]==label]
            stage_rows.append(dict(common,dimension=dimension,stage_label=label,queries=len(subset),candidate_known=sum(q['candidate_known'] for q in subset),timely=sum(q['timely'] for q in subset),
                **{key:sum(q[key] for q in subset) for key in ('cycle_seconds','check_seconds','delivered_gain','new_observations','first_side_observations','applied','explained_applied','explained_proposed','catalog_outside_gain')},
                unknown=sum(q['raw_status']=='unknown' for q in subset),no_applied_queries=sum(not q['applied'] for q in subset)))
    class_rows=[]
    for kind,counts in class_stats.items():
        ids_applied=class_query_ids[(kind,'applied')];related=[q for q in query_rows if q['query'] in ids_applied]
        class_rows.append(dict(common,opportunity_class=kind,**counts,unique_applied_queries=len(ids_applied),associated_unique_query_cost=sum(q['cycle_seconds'] for q in related),associated_delivered_gain=sum(q['delivered_gain'] for q in related),cost_warning='classes may share a query; non-additive'))
    corr=[]
    for x in ('max_applied_L','max_proposed_L'):
        for y in ('cycle_seconds','delivered_gain'):
            pts=[(q[x],q[y]) for q in query_rows if q[x] is not None];corr.append(dict(common,x=x,y=y,**correlations(pts)))
    elapsed=summary['elapsed'] if summary else process['process_wall_seconds'];tail=elapsed-last_finish
    require(tail>=-1e-6,'negative unallocated tail')
    high=[q for q in query_rows if q['high_cost_cycle']];adopted_queries=[q for q in query_rows if q['applied']];no_adopt=[q for q in query_rows if not q['applied']]
    result=dict(common,seconds=spec['seconds'],queries=len(qs),formal_models=version,total_bits=metric.total,covered=coverage_sum,first_model_seconds=initial_time,
        first_model_observations=first_obs,post_first_observations=post_obs,post_first_only_first_side=post_first,post_first_covered=post_covered,
        all_observations_per_full_budget=(first_obs+post_obs)/spec['seconds'],post_first_observations_per_full_budget=post_obs/spec['seconds'],
        catalog_present=catalog is not None,catalog_atoms=len(atoms),catalog_complete=(catalog or {}).get('complete'),catalog_reasons=(catalog or {}).get('reasons'),
        first_all_L_zero=zero_intervals[0]['start_seconds'] if zero_intervals else None,zero_L_intervals=zero_intervals,L_revival_versions=revivals,
        all_applied=sum(q['applied'] for q in query_rows),applied_known_final=sum(e['final_realized'] is not None for e in event_rows),applied_realized=sum(e['final_realized'] is True for e in event_rows),
        applied_unrealized=sum(e['final_realized'] is False for e in event_rows),applied_without_candidate=sum(e['final_realized'] is None for e in event_rows),
        explained_applied=sum(q['explained_applied'] for q in query_rows),explained_proposed=sum(q['explained_proposed'] for q in query_rows),catalog_outside_gain=sum(q['catalog_outside_gain'] for q in query_rows),
        applied_explanation_ratio=ratios(sum(q['explained_applied'] for q in query_rows),coverage_sum),proposed_explanation_ratio=ratios(sum(q['explained_proposed'] for q in query_rows),coverage_sum),
        high_cost_queries=len(high),high_cost_queries_without_adoption=sum(not q['applied'] for q in high),high_cost_cycle_seconds=sum(q['cycle_seconds'] for q in high),
        applied_query_cycle_seconds=sum(q['cycle_seconds'] for q in adopted_queries),no_applied_query_cycle_seconds=sum(q['cycle_seconds'] for q in no_adopt),
        total_cycle_seconds=sum(q['cycle_seconds'] for q in query_rows),unallocated_tail_seconds=tail,reported_session_elapsed=elapsed,process_wall_seconds=process['process_wall_seconds'],
        status_counts=dict(status_counter),unattributable_nonapplied_events=sum(q['unattributable_nonapplied_events'] for q in query_rows),subset_checks=checks,issues=issues,forbidden_search_attempts=[],analysis_seconds=time.perf_counter()-began)
    for name,rows in [('query_outcomes',query_rows),('applied_phase_events',event_rows),('summary_explanation',explanations),('score_availability',states),('repeated_direction_costs',repeats),('stage_summary',stage_rows),('opportunity_classes',class_rows),('correlations',corr),('atom_catalog',atom_rows)]:write_csv(dest/(name+'.csv'),rows,tuple(common))
    dump(dest/'session_summary.json',result);dump(dest/'source_hashes.json',sources)
    return result

def merge_csv(paths,destination):
    fields=[]
    for p in paths:
        with p.open() as f:
            for field in csv.DictReader(f).fieldnames or []:
                if field not in fields:fields.append(field)
    with destination.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for p in paths:
            with p.open() as source:writer.writerows(csv.DictReader(source))

def concurrency(out,stage):
    inputs=out/'inputs'/stage;plan=json.loads((inputs/'plan.json').read_text());process=json.loads((inputs/'process_results.json').read_text());top={r['cpu']:(r['package'],r['physical_core']) for r in plan['cpu_topology']}
    for p in process:p['end_ns']=p['start_monotonic_ns']+int(p['process_wall_seconds']*1e9)
    boundaries=sorted({p[k] for p in process for k in ('start_monotonic_ns','end_ns')});begin=boundaries[0];rows=[]
    for i,(a,b) in enumerate(zip(boundaries,boundaries[1:])):
        active=[p for p in process if p['start_monotonic_ns']<=a<p['end_ns']];physical=Counter(top[p['core']] for p in active)
        rows.append(dict(stage=stage,interval=i,start_monotonic_ns=a,end_monotonic_ns=b,start_seconds=(a-begin)/1e9,end_seconds=(b-begin)/1e9,
                         active_processes=len(active),active_groups=json.dumps(sorted({p['group'] for p in active})),shared_SMT_physical_cores=sum(n>1 for n in physical.values()),
                         active_sessions=json.dumps([dict(group=p['group'],formula=p['formula'],seed=p['seed'],mode=p['configuration'],cpu=p['core'],physical_core=top[p['core']]) for p in active]),source_sha256=file_hash(inputs/'process_results.json')))
    write_csv(out/stage/'concurrency_intervals.csv',rows)
    first=[]
    for p in process:
        run=f"{p['group']:02d}_{p['configuration']}";s=json.loads((out/stage/run/'session_summary.json').read_text());t=s['first_model_seconds'];absolute=p['start_monotonic_ns']+int(t*1e9) if t is not None else None
        active=[q for q in process if absolute is not None and q['start_monotonic_ns']<=absolute<q['end_ns']]
        first.append(dict(stage=stage,session=run,formula=p['formula'],seed=p['seed'],configuration=p['configuration'],group=p['group'],cpu=p['core'],physical_core=json.dumps(top[p['core']]),first_model_seconds=t,
                          active_processes_at_first=len(active) if t is not None else None,SMT_other_sessions_at_first=sum(q is not p and top[q['core']]==top[p['core']] for q in active) if t is not None else None,source_sha256=file_hash(inputs/'process_results.json')))
    write_csv(out/stage/'first_model_concurrency.csv',first)

def main():
    a=argparse.ArgumentParser();a.add_argument('--out',default='results/soft_phase_score_diagnosis');a.add_argument('--jobs',type=int,default=15);v=a.parse_args();out=(ROOT/v.out).resolve();out.mkdir(parents=True,exist_ok=True)
    require(not(out/'inputs').exists(),'new diagnosis directory required (never overwrite prior computation)')
    protocol=json.loads((out/'protocol.json').read_text());require(file_hash(ROOT/protocol['protocol_path'])==protocol['protocol_sha256'],'frozen protocol changed')
    dump(out/'execution_plan.json',dict(base=BASE,protocol_sha256=protocol['protocol_sha256'],analyzer_sha256=file_hash(__file__),jobs=v.jobs,started_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),search='forbidden'))
    jobs=prepare_inputs(out);results=[]
    with ProcessPoolExecutor(max_workers=v.jobs) as pool:
        for s in pool.map(run_session,jobs):
            results.append(s);print(s['stage'],s['session'],'queries',s['queries'],'replay passed',flush=True)
    for stage in ('screen','exposure'):
        directory=out/stage;data=[s for s in results if s['stage']==stage];write_csv(directory/'session_summary.csv',[{k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in s.items()} for s in data])
        for name in ('query_outcomes','applied_phase_events','summary_explanation','score_availability','repeated_direction_costs','stage_summary','opportunity_classes','correlations','atom_catalog'):
            merge_csv([directory/s['session']/(name+'.csv') for s in data],directory/(name+'.csv'))
        concurrency(out,stage)
    dump(out/'analysis_summary.json',dict(passed=True,search_calls=0,sessions=results,completed_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())))
    manifest=json.loads((out/'source_manifest.json').read_text())
    for name,expected in manifest['files'].items():require(file_hash(Path(name) if name.startswith('/') else ROOT/name)==expected,'historical evidence changed')
    print('Offline diagnosis completed; history hashes unchanged.',flush=True)
if __name__=='__main__':main()
