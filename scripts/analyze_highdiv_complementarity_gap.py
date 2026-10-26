#!/usr/bin/env python3
"""Fixed-protocol offline analysis. Consumes observations, never synthesizes models."""
import argparse,csv,gzip,json,time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from highdiv_gap_common import *
from reevaluate_round6_saved_candidates import dump,write_csv,file_hash,no_search,FrozenCoverage,z3
from prepare_highdiv_complementarity_gap import OUT
KS=[2,5,10,50,100];TS=[30,120,300,900]

class Table:
    def __init__(self,path):self.f=gzip.open(path,'wt',newline='');self.writer=None
    def put(self,row):
        if self.writer is None:self.writer=csv.DictWriter(self.f,list(row));self.writer.writeheader()
        self.writer.writerow(row)
    def close(self):self.f.close()

def bit_meta(layout,total):
    result=[None]*total
    for n in layout:
        for j in range(n['width']):result[n['offset']+j]=dict(node=n['node'],bit=j,kind=n['kind'],band='Boolean' if n['width']==1 else '0-7' if j<8 else '8-31' if j<32 else '32-63')
    return result

def witness(rows,first,v,b):
    i=first[v][b]
    return (rows[i]['index'],rows[i]['completion'],rows[i]['model_id']) if i>=0 else (None,None,None)

def stats(values):return dict(count=len(values),median=quantile(values,.5),p90=quantile(values,.90),p95=quantile(values,.95),p99=quantile(values,.99),max=max(values) if values else None)

def structure(formula,layout):
    """Static dependency groups are descriptive, never asserted into a solver."""
    with no_search():
        m=FrozenCoverage(formula.read_text());memo={};ids={n.get_id():i for i,n in enumerate(m.nodes)}
        def deps(n):
            if n.get_id() in memo:return memo[n.get_id()]
            if z3.is_const(n) and n.decl().kind()==z3.Z3_OP_UNINTERPRETED:s={str(n)}
            else:s=set().union(*(deps(c) for c in n.children()))
            memo[n.get_id()]=s;return s
        for n in m.nodes:deps(n)
        def unconditional(n):
            if z3.is_and(n):return sum((unconditional(c) for c in n.children()),[])
            return [n] if z3.is_eq(n) else []
        eqs=[n for n in m.nodes if z3.is_eq(n) and any(z3.is_int(c) for c in n.children())]
        global_ids={n.get_id() for n in unconditional(m.root)}
        def groups(nodes):
            parent={str(v):str(v) for v in m.decls}
            def find(x):
                while parent[x]!=x:parent[x]=parent[parent[x]];x=parent[x]
                return x
            for e in nodes:
                vv=sorted(deps(e))
                for v in vv[1:]:parent[find(v)]=find(vv[0])
            grouped={}
            for v in parent:grouped.setdefault(find(v),[]).append(v)
            return [sorted(v) for _,v in sorted(grouped.items())]
        patterns=[]
        for n in m.nodes:
            if not z3.is_or(n):continue
            alternatives=[]
            for c in n.children():
                terms=c.children() if z3.is_and(c) else [c];pairs=[]
                for t in terms:
                    if not z3.is_eq(t):pairs=[];break
                    a,b=t.children()
                    if z3.is_int_value(a):a,b=b,a
                    if not(z3.is_const(a) and a.decl().kind()==z3.Z3_OP_UNINTERPRETED and z3.is_int_value(b)):pairs=[];break
                    pairs.append([str(a),str(b.as_long())])
                alternatives.append(pairs)
            if any(alternatives):patterns.append(dict(node=ids[n.get_id()],alternatives=alternatives,partial=not all(alternatives)))
        return dict(dependencies=[sorted(memo[n.get_id()]) for n in m.nodes],global_equation_groups=groups([e for e in eqs if e.get_id() in global_ids]),syntactic_equation_groups=groups(eqs),equations=[dict(node=ids[n.get_id()],global_necessary=n.get_id() in global_ids,variables=sorted(deps(n))) for n in eqs],discrete_patterns=patterns)

def changes(meta,reference,model):
    a={v['name']:v['value'] for v in reference['model']};b={v['name']:v['value'] for v in model['model']};changed={k for k in a if a[k]!=b[k]}
    result=dict(reference_model_id=reference['model_id'],model_id=model['model_id'],changed_variables=[dict(name=k,before=a[k],after=b[k]) for k in sorted(changed)])
    for key in ['global_equation_groups','syntactic_equation_groups']:
        result[key]=[dict(group=i,size=len(v),changed=sorted(set(v)&changed)) for i,v in enumerate(meta[key]) if set(v)&changed]
    def matches(p,values):return [i for i,alt in enumerate(p['alternatives']) if alt and all(str(values[k])==v for k,v in alt)]
    result['discrete_pattern_changes']=[dict(node=p['node'],partial=p['partial'],before=matches(p,a),after=matches(p,b)) for p in meta['discrete_patterns'] if matches(p,a)!=matches(p,b)]
    return result

def analyze_formula(task):
    out,formula=task;out=Path(out);began=time.perf_counter();short='random' if 'random' in formula else 'real';dest=out/'analysis'/short;dest.mkdir(parents=True,exist_ok=True)
    jobs=[j for j in json.loads((out/'plan.json').read_text())['jobs'] if j['formula']==formula]
    runs={j['run']:load_run(out/'observations'/j['run']) for j in jobs};H=next(j['run'] for j in jobs if j['mode']=='highdiv');native=[j['run'] for j in jobs if j['mode']=='native_random_one']
    layout=json.loads((out/'observations'/H/'layout.json').read_text());total=sum(n['width'] for n in layout);bm=bit_meta(layout,total)
    for name in runs:require(json.loads((out/'observations'/name/'layout.json').read_text())==layout,'AST layout differs between runs')
    finals={name:prefix(rows,len(rows)) for name,rows in runs.items()};first={name:first_arrivals(rows,total) for name,rows in runs.items()}
    summary=[];gap_table=Table(dest/'bit_set_differences.csv.gz');gap_classes=[]
    for N in native:
        h=covered(finals[H]);n=covered(finals[N]);joined=covered(add(finals[H],finals[N]));sets=dict(highdiv_only=h&~n,native_only=n&~h,intersection=h&n,union_pairing=joined&~(h|n))
        counts=Counter();summary.append(dict(formula=formula,native_run=N,seed=runs[N][0]['seed'],highdiv_run=H,highdiv_seed=0,highdiv_models=len(runs[H]),native_models=len(runs[N]),highdiv_covered=h.bit_count(),native_covered=n.bit_count(),union_covered=joined.bit_count(),**{k:v.bit_count() for k,v in sets.items()}))
        for label,mask in sets.items():
            for b in bits(mask):
                ns='both' if (n>>b)&1 else 'only0' if (finals[N][0]>>b)&1 else 'only1' if (finals[N][1]>>b)&1 else 'unvisited'
                hs='both' if (h>>b)&1 else 'only0' if (finals[H][0]>>b)&1 else 'only1' if (finals[H][1]>>b)&1 else 'unvisited'
                row=dict(native_run=N,highdiv_run=H,category=label,dense_bit=b,**bm[b],native_state=ns,highdiv_state=hs)
                for name,tag in [(N,'native'),(H,'highdiv')]:
                    for v in (0,1):
                        i,t,mid=witness(runs[name],first[name],v,b);row.update({f'{tag}_first{v}_index':i,f'{tag}_first{v}_seconds':t,f'{tag}_first{v}_model_id':mid})
                gap_table.put(row);counts[label,ns,hs,bm[b]['kind'],bm[b]['band']]+=1
        for (cat,ns,hs,kind,band),count in sorted(counts.items()):gap_classes.append(dict(native_run=N,category=cat,native_state=ns,highdiv_state=hs,kind=kind,band=band,bits=count))
    gap_table.close();write_csv(dest/'final_sets.csv',summary);write_csv(dest/'gap_structure.csv',gap_classes)
    # Cross-seed pooling is deliberately separate from all main comparisons.
    combined=union(finals[n] for n in native);hc=covered(finals[H]);cc=covered(combined)
    dump(dest/'cross_seed_information_only.json',dict(label='extra pooled potential, NOT a single 900-second run',runs=native,covered=cc.bit_count(),highdiv_missing=(hc&~cc).bit_count(),native_only=(cc&~hc).bit_count(),new_pairing=(cc&~union((covered(finals[n]),0) for n in native)[0]).bit_count()))
    arrivals=Table(dest/'first_arrivals.csv.gz');arrival_summary=[]
    for N in native:
        for k in KS:
            require(len(runs[H])>=k,'incomplete HighDiv reference prefix');ref=covered(prefix(runs[H],k));ht=runs[H][k-1]['completion'];counts=Counter()
            for b in bits(ref):
                zi,zt,zm=witness(runs[N],first[N],0,b);oi,ot,om=witness(runs[N],first[N],1,b)
                complete=max(zi,oi) if zi is not None and oi is not None else None;ct=max(zt,ot) if complete else None
                ic,tc=arrival_class(complete,ct,k,ht);counts['index',ic]+=1;counts['time',tc]+=1
                arrivals.put(dict(native_run=N,highdiv_prefix=k,highdiv_prefix_seconds=ht,dense_bit=b,**bm[b],first0_index=zi,first0_seconds=zt,first0_model_id=zm,first1_index=oi,first1_seconds=ot,first1_model_id=om,complete_index=complete,complete_seconds=ct,index_class=ic,time_class=tc))
            arrival_summary.append(dict(native_run=N,k=k,highdiv_seconds=ht,highdiv_covered=ref.bit_count(),native_prefix_complete=len(runs[N])>=k,native_prefix_covered=covered(prefix(runs[N],k)).bit_count(),early_by_index=counts['index','early_by_index'],later_by_index=counts['index','later_by_index'],early_by_time=counts['time','early_by_time'],later_by_time=counts['time','later_by_time'],right_censored_900=counts['index','right_censored_900']))
    arrivals.close();write_csv(dest/'arrival_summary.csv',arrival_summary)
    bases=[]
    for name in [H]+native:
        for k in [2,10]:bases.append(dict(run=name,k=k,state=prefix(runs[name],k),id=obs_id(prefix(runs[name],k)),models=[r['model_id'] for r in runs[name][:k]]))
    dump(dest/'reference_states.json',[{k:v for k,v in b.items() if k!='state'}|dict(zero=hex(b['state'][0]),one=hex(b['state'][1])) for b in bases])
    scores=Table(dest/'common_state_model_gains.csv.gz');score_summary=[]
    for base in bases:
        for name,rows in runs.items():
            values=[];obsvalues=[]
            for r in rows:
                gm=gain_mask(base['state'],r['obs']);u=add(base['state'],r['obs']);fresh=(r['obs'][0]|r['obs'][1])&~(base['state'][0]|base['state'][1]);v=gm.bit_count();values.append(v);obsvalues.append(observed(u)-observed(base['state']))
                scores.put(dict(base_run=base['run'],base_k=base['k'],base_id=base['id'],run=name,index=r['index'],completion=r['completion'],model_id=r['model_id'],gain=v,new_observations=obsvalues[-1],first_observed_bits=fresh.bit_count()))
            score_summary.append(dict(base_run=base['run'],base_k=base['k'],base_id=base['id'],run=name,positive=sum(v>0 for v in values),**stats(values),median_new_observations=quantile(obsvalues,.5)))
    scores.close();write_csv(dest/'common_state_summary.csv',score_summary)
    events=[];scans=Table(dest/'highdiv_jump_native_scan.csv.gz');jump_summary=[];base=(0,0)
    for event in runs[H][:10]:
        target=gain_mask(base,event['obs']);jump=target.bit_count()
        if jump:
            events.append((event,base,target))
            for N in native:
                vals=[];hit=[];best=None
                for r in runs[N]:
                    g=gain_mask(base,r['obs']);value=g.bit_count();inter=(g&target).bit_count();uni=(g|target).bit_count();vals.append(value)
                    if value>=jump:hit.append(r)
                    row=dict(highdiv_run=H,event_index=event['index'],event_model_id=event['model_id'],base_id=obs_id(base),highdiv_gain=jump,native_run=N,index=r['index'],completion=r['completion'],model_id=r['model_id'],gain=value,target_intersection=inter,target_missing=(target&~g).bit_count(),other_covered=(g&~target).bit_count(),jaccard=inter/uni if uni else 1,meets_gain=value>=jump)
                    scans.put(row)
                    if best is None or value>best['gain']:best=row
                jump_summary.append(dict(native_run=N,event_index=event['index'],highdiv_gain=jump,highdiv_seconds=event['completion'],**stats(vals),matching_count=len(hit),first_matching_index=hit[0]['index'] if hit else None,first_matching_seconds=hit[0]['completion'] if hit else None,first_matching_model_id=hit[0]['model_id'] if hit else None,best_model_id=best['model_id'],best_index=best['index'],best_seconds=best['completion'],best_target_intersection=best['target_intersection'],best_target_missing=best['target_missing'],best_other_covered=best['other_covered']))
        base=add(base,event['obs'])
    scans.close();write_csv(dest/'highdiv_jump_summary.csv',jump_summary)
    # Exactly the frozen 32 paths, no search-parameter adaptation.
    selections=[];identities=[];selection_cost=[]
    for N in native:
        for cutoff in TS:
            pool=pool_at(runs[N],cutoff);allc=covered(union(r['obs'] for r in pool))
            for fixed in [True,False]:
                start=time.perf_counter();indices,meta=select_path(pool,fixed);elapsed=time.perf_counter()-start
                selection_cost.append(dict(native_run=N,cutoff=cutoff,keep_first=fixed,pool_size=len(pool),seconds=elapsed,**meta))
                for k in KS:
                    state,lb,ub,status=selection_bounds(pool,indices,k,fixed,meta['pair_exact']);hc=covered(prefix(runs[H],k));chosen=[pool[i] for i in indices[:k]];hit=(covered(state)&hc).bit_count();hub=(allc&hc).bit_count()
                    verdict=selection_verdict(lb,ub,allc.bit_count(),hc.bit_count())
                    row=dict(native_run=N,seed=runs[N][0]['seed'],cutoff=cutoff,k=k,keep_first=fixed,pool_size=len(pool),selected_count=len(chosen),pool_has_k=len(pool)>=k,highdiv_count=hc.bit_count(),highdiv_seconds=runs[H][k-1]['completion'],actual_native_prefix_count=covered(prefix(runs[N],k)).bit_count(),lower_bound=lb,upper_bound=ub,status=status,verdict=verdict,highdiv_target_hit=hit,highdiv_target_upper=hub,highdiv_target_missing=hc.bit_count()-hit,other_bits=(covered(state)&~hc).bit_count(),latest_selected_seconds=max((r['completion'] for r in chosen),default=None),future_relative_to_H_prefix=any(r['completion']>runs[H][k-1]['completion'] for r in chosen),state_id=obs_id(state))
                    selections.append(row);identities.append(row|dict(selected_indices=[r['index'] for r in chosen],selected_model_ids=[r['model_id'] for r in chosen],zero=hex(state[0]),one=hex(state[1])))
    write_csv(dest/'selection_bounds.csv',selections);dump_gz(dest/'selected_subsets.jsonl.gz',identities);write_csv(dest/'selection_cost.csv',selection_cost)
    meta=structure(out/'inputs'/formula,layout);dump(dest/'structure.json',meta);details=[];structural=[]
    # Fixed top-three positive jumps, all positive jumps remain in the scan tables.
    for event,base,target in sorted(events,key=lambda v:-v[2].bit_count())[:3]:
        ref=runs[H][event['index']-2]
        models=[event]
        for N in native:
            best=next(s for s in jump_summary if s['native_run']==N and s['event_index']==event['index']);models.append(runs[N][best['best_index']-1])
        for r in models:
            ev,firstnodes=observation_events(layout,base,r['obs']);chg=changes(meta,ref,r);gm=gain_mask(base,r['obs'])
            chg.update(dict(event_index=event['index'],run=r['run'],index=r['index'],completion=r['completion'],base_id=obs_id(base),highdiv_internal_source='unknown',gain=gm.bit_count(),target_overlap=(gm&target).bit_count(),first_visit_nodes=firstnodes,events={k:hex(v) for k,v in ev.items()},gain_mask=hex(gm),repeated_observations=(r['obs'][0]&base[0]).bit_count()+(r['obs'][1]&base[1]).bit_count()))
            details.append(chg)
            for eventkind,mask in ev.items():
                for (kind,band),count in categories(layout,mask).items():structural.append(dict(highdiv_event=event['index'],run=r['run'],index=r['index'],model_id=r['model_id'],base_id=obs_id(base),event=eventkind,kind=kind,band=band,bits=count))
    dump_gz(dest/'representative_witnesses.jsonl.gz',details);write_csv(dest/'representative_structure.csv',structural)
    # Record structure of every concrete selected set, not distances alone.
    subset_struct=[]
    for s in identities:
        for (kind,band),count in categories(layout,int(s['zero'],16)&int(s['one'],16)).items():subset_struct.append({k:s[k] for k in ['native_run','cutoff','k','keep_first','state_id']}|dict(kind=kind,band=band,covered=count))
    write_csv(dest/'selected_structure.csv',subset_struct)
    result=dict(formula=formula,elapsed_seconds=time.perf_counter()-began,runs=list(runs),models=sum(map(len,runs.values())),selection_paths=len(selection_cost),subset_checkpoints=len(selections),search_attempts=0)
    dump(dest/'complete.json',result);return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=OUT);p.add_argument('--workers',type=int,default=2);args=p.parse_args()
    require((args.out/'replay_complete.json').exists(),'complete trajectory replay required');require(1<=args.workers<=15,'worker limit')
    formulas=sorted({j['formula'] for j in json.loads((args.out/'plan.json').read_text())['jobs']})
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        for r in executor.map(analyze_formula,[(str(args.out),f) for f in formulas]):print(json.dumps(r),flush=True)
if __name__=='__main__':main()
