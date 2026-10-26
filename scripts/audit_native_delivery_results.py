#!/usr/bin/env python3
"""Independent original-AST audit of separate generation and delivery histories."""
import argparse,bisect,json,time,math
from collections import Counter
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,require,dump,write_csv,model_id,file_hash
from audit_bofill_results import jsonlines,fingerprint
from prepare_highdiv_complementarity_gap import layout_for,pack_state,unpack_state
from highdiv_gap_common import add,covered,observed,gain_mask,obs_id,dump_gz

class MT64:
    """Independent standard mt19937_64 replay of the two project RNG streams."""
    mask=(1<<64)-1
    def __init__(self,seed):
        self.x=[seed&self.mask]
        for i in range(1,312):self.x.append((6364136223846793005*(self.x[-1]^(self.x[-1]>>62))+i)&self.mask)
        self.index=312
    def next(self):
        if self.index==312:
            for i in range(312):
                y=(self.x[i]&0xffffffff80000000)|(self.x[(i+1)%312]&0x7fffffff)
                self.x[i]=self.x[(i+156)%312]^(y>>1)^(0xb5026f5aa96619e9 if y&1 else 0)
            self.index=0
        y=self.x[self.index];self.index+=1;y^=(y>>29)&0x5555555555555555;y^=(y<<17)&0x71d67fffeda60000;y^=(y<<37)&0xfff7eee000000000;y^=y>>43
        return y&self.mask

def one_run(task):
    out,name=task;out=Path(out);run=out/'runs'/name;raw=run/'raw';dest=out/'audit'/name;dest.mkdir(parents=True,exist_ok=True);began=time.perf_counter()
    spec=json.loads((run/'spec.json').read_text());seconds=spec['seconds'];formula=out/'inputs'/spec['formula'];plan=json.loads((out/'plan.json').read_text())
    require(file_hash(formula)==next(i['sha256'] for i in plan['inputs'] if i['name']==spec['formula']),'formula hash')
    def read(name):
        rows,issues=jsonlines(raw/name);require(not issues,f'{name}: {issues}');return rows
    cs=read('candidates.jsonl');gc=read('generation_commits.jsonl');gr=read('generation_receipts.jsonl');ds=read('deliveries.jsonl');dc=read('delivery_commits.jsonl');dr=read('delivery_receipts.jsonl');bs=read('batches.jsonl');qs=read('queries.jsonl');ps=read('positions.jsonl');blocks=read('blocks.jsonl');costs=read('costs.jsonl');tail=read('undelivered.jsonl');atoms=read('atoms.jsonl')
    summary=json.loads((raw/'summary.json').read_text());initial=json.loads((raw/'initial.json').read_text());require(len(cs)==len(gc)==len(gr),'generation records mismatch');require(len(ds)==len(dc)==len(dr),'delivery records mismatch');require(len(qs)==len(ps)==len(costs)==summary['queries'],'query records mismatch')
    require(initial['delivery_reserve']==.1 and initial['query_reserve']==.025 and not initial['learning'],'frozen parameters')
    require(initial['batch_capacity']==32 and initial['wait_trigger']==.5 and initial['batch_maintenance_cap']==.020 and initial['delivery_management_quota_fraction']==.05,'delivery parameters')
    require(initial['position_range']==[1,2,3,4] and initial['decision_window']==32 and initial['selected_limit']==1 and initial['flip_probability']==.5,'position parameters')
    if plan.get('kind') in ['exposure','screen','long']:require(initial['test_query_limit']==2**32-1,'test override in performance run')
    generation=[];delivery=[];exclusions=[];cache={};counts=Counter();positions=[];waits=[]
    with no_search() as searches:
        metric=FrozenCoverage(formula.read_text());layout=layout_for(metric);gs=metric.empty.copy();seen=set();gpacked=(0,0);hash_cache={}
        def fnv(state):
            if state not in hash_cache:hash_cache[state]=fingerprint(metric.export(unpack_state(metric,layout,state))['states'])
            return hash_cache[state]
        last_time=-1;generation_prefixes=[(0,0)]
        for c,g,receipt in zip(cs,gc,gr):
            idx=c['candidate_generation_index'];require(c['query']==g['query']==receipt['query'] and idx==g['candidate_generation_index']==receipt['candidate_generation_index'],'generation identity')
            require(c['validated_seconds']<=c['observation_completed_time']<=g['candidate_ready_time'] and g['candidate_ready_time']>=last_time,'generation chronology');last_time=g['candidate_ready_time']
            mid=model_id(c['model']);single=metric.apply(metric.empty,c['model']);o=pack_state(metric,layout,single);duplicate=mid in seen
            require(duplicate==c['duplicate']==g['duplicate'],'generation dedup');timely=not duplicate and not g['cancelled'] and g['candidate_ready_time']<=seconds;require(timely==g['timely'],'generation deadline')
            if timely:
                require(idx==len(generation)+1==g['generation_version'],'generation version');before=gs;gs=metric.apply(gs,c['model']);new=pack_state(metric,layout,gs)
                require(g['gain']==metric.count(gs)-metric.count(before) and g['observations']==observed(new)-observed(gpacked),'generation full-state marginal')
                require(g['covered']==covered(new).bit_count() and g['state_fnv1a64']==fnv(new),'generation full state mismatch');seen.add(mid);gpacked=new
                r=dict(index=idx,query=c['query'],completion=g['candidate_ready_time'],model_id=mid,observation_id=obs_id(o),zero=hex(o[0]),one=hex(o[1]),model=c['model'],obs=o,gain=g['gain'],new_observations=g['observations'],covered=g['covered'],observations=observed(new),state_id=obs_id(new))
                generation.append(r);cache[idx]=r;generation_prefixes.append(gpacked)
            else:exclusions.append(dict(track='generation',query=c['query'],index=idx,reason='duplicate' if duplicate else 'cancelled' if g['cancelled'] else 'late'))
        require([b['candidate_generation_index'] for b in blocks]==[r['index'] for r in generation],'blocking order differs from generation order')
        for b,r in zip(blocks,generation):require(b['query']==r['query'] and b['complete_time']>=r['completion'],'invalid generation blocking')
        dstate=metric.empty.copy();dpacked=(0,0);dseen=set();last_time=-1
        for row,c,receipt in zip(ds,dc,dr):
            idx=row['formal_output_index'];source=row['candidate_generation_index'];require(source in cache,'output before validated candidate');r=cache[source]
            require(idx==c['formal_output_index']==receipt['formal_output_index'] and source==c['candidate_generation_index']==receipt['candidate_generation_index'],'delivery receipt identity')
            require(model_id(row['model'])==r['model_id'] and row['query']==c['query']==r['query'],'delivery linked to wrong generating query')
            require(r['completion']==row['candidate_ready_time']==c['candidate_ready_time']<=c['model_write_complete']<=c['delivery_complete_time'],'candidate arrival/delivery order')
            require(receipt['model_write_complete']==c['model_write_complete'],'write receipt mismatch');require(c['delivery_complete_time']>=last_time,'formal order');last_time=c['delivery_complete_time']
            timely=c['delivery_complete_time']<=seconds and not c['cancelled'];require(c['timely']==timely,'formal deadline')
            require(row['generation_version']==bisect.bisect_right([x['completion'] for x in generation],c['model_write_complete']),'future or stale generation version')
            if timely:
                require(r['model_id'] not in dseen and idx==len(delivery)+1==c['delivery_version'],'formal duplicate/version');dseen.add(r['model_id']);before=dstate;dstate=metric.apply(dstate,r['model']);new=pack_state(metric,layout,dstate)
                gain=metric.count(dstate)-metric.count(before);newobs=observed(new)-observed(dpacked)
                require(gain==row['gain']==c['gain'] and newobs==row['observations']==c['observations'],'formal score mismatch');require(c['covered']==covered(new).bit_count() and c['state_fnv1a64']==fnv(new)==row['state_fnv1a64'],'formal full state')
                available=generation_prefixes[row['generation_version']]
                require(not(new[0]&~available[0] or new[1]&~available[1]),'delivered not subset of generated')
                wait=c['delivery_complete_time']-r['completion'];waits.append(wait);dpacked=new
                delivery.append(dict(index=idx,generation_index=source,query=r['query'],model_id=r['model_id'],completion=c['delivery_complete_time'],ready=r['completion'],wait=wait,gain=gain,new_observations=newobs,covered=c['covered'],observations=observed(new),generated_when_delivered=row['generation_version'],batch_id=row['batch_id'],state_id=obs_id(new)))
            else:exclusions.append(dict(track='delivery',index=idx,reason='cancelled' if c['cancelled'] else 'late'))
        require(metric.export(gs)==summary['generation_coverage'] and metric.export(dstate)==summary['coverage'],'final complete state mismatch')
        require(sum(r['gain'] for r in generation)==summary['generation_marginal_sum']==covered(gpacked).bit_count(),'generation telescoping');require(sum(r['gain'] for r in delivery)==summary['delivery_marginal_sum']==covered(dpacked).bit_count(),'delivery telescoping')
        require(sum(r['new_observations'] for r in generation)==summary['generation_observation_sum'] and sum(r['new_observations'] for r in delivery)==summary['delivery_observation_sum'],'observation telescoping')
        require(summary['generated_count']==len(generation) and summary['formal_count']==len(delivery),'counts')
        missing={r['index'] for r in generation}-{r['generation_index'] for r in delivery};require(missing=={r['candidate_generation_index'] for r in tail} and len(tail)==summary['undelivered_count'],'tail lost or counted')
        # Replay the actual bounded selection, including zero-gain candidates.
        state=(0,0);used=set();batch_rows=[];formal_by_source={r['generation_index']:r for r in delivery}
        for i,b in enumerate(bs):
            require(b['batch_id']==i and len(b['members'])<=32,'batch identity/capacity');members=b['members'];require(len(set(members))==len(members) and not(set(members)&used),'duplicate batch membership')
            require(all(cache[n]['completion']<=b['start'] for n in members),'future batch member');require(b['before_fnv']==fnv(state),'batch before state')
            available={r['index'] for r in generation if r['completion']<=b['start']}-used;require(set(members)==available,'dropped/unlogged pending candidates')
            expected=state
            for n in members:expected=add(expected,cache[n]['obs'])
            require(b['expected_fnv']==fnv(expected),'batch membership union');pending=sorted(members)
            if b['reason']=='capacity':require(len(members)==32,'capacity trigger early')
            if b['reason']=='wait_threshold':require(b['start']-cache[pending[0]]['completion']>=.5,'wait trigger early')
            delayed_queries=[q['query'] for q in qs if q['check_call_start']<=cache[pending[0]]['completion']+.5<q['check_call_finish']] if pending else []
            for choice in b['choices']:
                n=choice['candidate_generation_index'];require(n in pending and n in formal_by_source,'choice not delivered')
                scores={j:(gain_mask(state,cache[j]['obs']).bit_count(),observed(add(state,cache[j]['obs']))-observed(state),-j) for j in pending}
                wanted=pending[0] if choice['fifo'] else max(pending,key=lambda j:scores[j]);require(n==wanted,'not FIFO/true-current-marginal greedy')
                require(scores[n][:2]==(choice['gain'],choice['observations']),'stale batch score');require(choice['delivery_complete_time']==formal_by_source[n]['completion'],'batch time is not actual receipt')
                state=add(state,cache[n]['obs']);pending.remove(n);used.add(n)
            require(b['complete']==(not pending) and b['after_fnv']==fnv(state),'batch final state');require(not b['complete'] or state==expected,'batch changed full observation set')
            batch_rows.append(dict(batch_id=i,members=len(members),delivered=len(b['choices']),reason=b['reason'],fallback=b['fallback'],start=b['start'],finished=b['finished'],maintenance=b['maintenance_seconds'],trigger_delay=b['trigger_delay'],delay_explanation='active_query' if delayed_queries else 'capacity_or_preparation_or_logging',overlapping_queries=json.dumps(delayed_queries),complete=b['complete']))
        require(state==dpacked,'batch replay not equal to formal trajectory')
        if not tail:require(gpacked==dpacked,'drained generation/delivery full states differ')
        if generation:require(bs[0]['members']==[1] and bs[0]['reason']=='first_model','first model waited')
        # Replay both RNG streams without consulting delivery history.
        action_rng=MT64(spec['seed']^0xd7846ba219);position_rng=MT64(spec['seed']^0x8a5cd789635d2dff);at={a['cache_index']:a for a in atoms};gv=0
        for i,(q,p,cost) in enumerate(zip(qs,ps,costs)):
            require(q['query']==p['query']==cost['query']==i and q['generation_version_before']==gv,'query generation chronology');require(q['decision']==p['decision'],'decision association')
            h=p['hook'];d=p['decision'];pos=p['position'];selected=d['selected'];require(h.get('selected',0)==int(selected) and h.get('flips',0)<=1 and h.get('decisions_examined',0)<=32,'native opportunity/window')
            if not gv:require(not selected and not h and not pos['eligible'],'intervened before first')
            if pos['eligible']:
                target=1 if spec['configuration'].startswith('first_') else (position_rng.next()>>62)+1;require(pos['target']==target,'position RNG consumed by delivery or incorrect target')
                counts['target_'+str(target)]+=1;counts['position_eligible']+=1
                seen_atoms=set();rank=0
                for a in p['attempts']:
                    if a['reason'] not in ['selected','position_before_target','duplicate_supported_atom']:continue
                    atom=at[a['cache_index']];require(atom['supported'],'unsupported positional count')
                    if a['cache_index'] not in seen_atoms:seen_atoms.add(a['cache_index']);rank+=1
                    if spec['configuration'].startswith('position_'):require(rank==a['supported_rank'],'repeated atom counted as new rank')
                    if a['reason']=='selected':require(rank==target,'selected wrong supported rank')
                if not selected:
                    counts['target_not_reached']+=1
                    counts['not_selected_reason_'+pos['reason']]+=1
            if selected:
                require(d['coverage_version']==gv and d['probability']==.5 and not d['prediction_enabled'] and d['training_counts']==[0,0],'learning/propensity/history mismatch')
                draw=action_rng.next()/2**64;require(abs(draw-d['action_draw'])<1e-15,'action RNG stream changed');flip=draw>=.5;require(d['action']==('FlipOnce' if flip else 'KeepNative'),'wrong random action')
                require(d['desired_truth']==(d['native_truth']!=flip) and h['flips']==int(flip),'phase action mismatch');counts[d['action']]+=1;counts['reached_'+str(pos['target'])]+=1
            counts[q['evidence']['raw_status']]+=1
            if q['generated']:gv+=1;require(cache[gv]['query']==i,'candidate linked to refresh query')
            require(gv==q['generation_version'],'query generated version');require(q['evidence']['granted_timeout_ms']<=q['evidence']['requested_timeout_ms'],'extra query budget')
            positions.append(dict(query=i,generation_version_before=p['generation_version'],eligible=pos['eligible'],target=pos['target'],reached=pos['reached'],reason=pos['reason'],selected=selected,ordinal=d.get('ordinal'),cache_index=d.get('cache_index'),action=d.get('action'),action_probability=d.get('probability'),raw_status=q['evidence']['raw_status'],grant_ms=q['evidence']['granted_timeout_ms'],check_seconds=q['evidence']['actual_check_seconds']))
        require(summary['training_seconds']==0 and not searches,'unexpected search/learning in audit')
        common={k:spec[k] for k in ['formula','seed','configuration','seconds','group']};result=common|dict(run=name,passed=True,generated=len(generation),delivered=len(delivery),generated_covered=covered(gpacked).bit_count(),covered=covered(dpacked).bit_count(),total=metric.total,tail=len(tail),tail_covered_gap=(covered(gpacked)&~covered(dpacked)).bit_count(),tail_observation_gap=observed(gpacked)-observed(dpacked),counts=dict(counts),exclusions=exclusions,management_seconds=summary['delivery_management_seconds'],mechanism_seconds=summary['mechanism_seconds'],audit_seconds=time.perf_counter()-began,search_calls=0)
        dump(dest/'summary.json',result);dump(dest/'coverage_final.json',metric.export(dstate));dump(dest/'generation_final.json',metric.export(gs));dump(dest/'layout.json',layout)
        dump_gz(dest/'models.jsonl.gz',[{k:v for k,v in r.items() if k!='obs'} for r in generation]);write_csv(dest/'generation.csv',[{k:v for k,v in r.items() if k not in ['obs','model','zero','one']} for r in generation]);write_csv(dest/'delivery.csv',delivery);write_csv(dest/'batches.csv',batch_rows);write_csv(dest/'positions.csv',positions)
        dump(dest/'source_hashes.json',{str(p.relative_to(out)):file_hash(p) for p in sorted(raw.glob('*')) if p.is_file()})
        return result

def main():
    p=argparse.ArgumentParser();p.add_argument('out',type=Path);p.add_argument('--jobs',type=int,default=15);a=p.parse_args();names=[p.name for p in sorted((a.out/'runs').iterdir()) if p.is_dir()];require(1<=a.jobs<=15,'worker limit')
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:results=list(pool.map(one_run,[(str(a.out),n) for n in names]))
    dump(a.out/'audit_summary.json',dict(passed=True,runs=results,search_calls=0));print(json.dumps(dict(passed=True,runs=len(results))))
if __name__=='__main__':main()
