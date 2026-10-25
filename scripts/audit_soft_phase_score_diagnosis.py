#!/usr/bin/env python3
"""Second offline pass: replay saved models with the frozen metric.
Uses sparse sets for intervention explanations, independently of analyzer union
arithmetic. Historical files remain read-only. Never runs the original mains.
"""
import argparse,csv,gzip,json,hashlib,time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from collections import Counter
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,require,file_hash,dump,model_id
from audit_bofill_results import fingerprint
from audit_soft_phase_results import atom_value
ROOT=Path(__file__).resolve().parents[1]
def csv_rows(p):
    with p.open() as f:return list(csv.DictReader(f))
def json_rows(p):return [json.loads(x) for x in p.read_text().splitlines() if x]
def bitset_words(number):
    result=set();index=0
    while number:
        word=number&((1<<64)-1)
        while word:
            bit=(word&-word).bit_length()-1;result.add((index,bit));word&=word-1
        index+=1;number>>=64
    return result

def sparse_H(side,state,ids):
    result=set()
    for n,z,o in side['observations']:
        one,zero,_=state[ids[n]];mask=(int(z)&int(one)&~int(zero))|(int(o)&int(zero)&~int(one))
        while mask:bit=(mask&-mask).bit_length()-1;result.add((n,bit));mask&=mask-1
    return result

def one(args):
    root,stage,run=args;root=Path(root);base=root/'inputs'/stage;raw=base/'runs'/run/'raw';derived=root/stage/run
    qs=json_rows(raw/'queries.jsonl');cs={r['query']:r for r in json_rows(raw/'candidates.jsonl')};ms={r['query']:r for r in json_rows(raw/'models.jsonl')};receipts={r['query']:r for r in json_rows(raw/'commits.jsonl')}
    spec=json.loads((raw.parent/'spec.json').read_text());summary=json.loads((raw/'summary.json').read_text());cat=json.loads((raw/'summaries.json').read_text()) if (raw/'summaries.json').exists() else None
    atoms={a['node']:a for a in (cat or {}).get('atoms',[])}
    qr={int(r['query']):r for r in csv_rows(derived/'query_outcomes.csv')};ex={int(r['query']):r for r in csv_rows(derived/'summary_explanation.csv')};av={int(r['coverage_version']):r for r in csv_rows(derived/'score_availability.csv')}
    atom_keys={int(r['node']):r['atom_key'] for r in csv_rows(derived/'atom_catalog.csv')}
    with gzip.open(derived/'bitmap_evidence.jsonl.gz','rt') as f:proof={r['query']:r for r in map(json.loads,f)}
    require(len(qr)==len(qs)==len(proof),'query/proof row count')
    checks=0;verified_scores=0;missing_counterfactual=0
    with no_search() as attempts:
        metric=FrozenCoverage((base/'inputs'/spec['formula']).read_text());ids=[n.get_id() for n in metric.nodes];state=metric.empty.copy();version=0;previous=0.;covered_sum=0;seen=set()
        gs={}
        for n,a in atoms.items():
            sides=[]
            for side in a['sides']:
                z=sum(int(r[1])<<(64*r[0]) for r in side['observations']);o=sum(int(r[2])<<(64*r[0]) for r in side['observations']);require(not(z&o),'summary internally contradicts');sides.append((z,o))
            gs[n]=sides
        verified_versions=set();scores={}
        def verify_state():
            nonlocal verified_scores,scores
            if version in verified_versions:return
            v=av[version];require(v['state_fnv1a64']==fingerprint(metric.export(state)['states']),'version fingerprint')
            verified_versions.add(version)
            if cat is None:require(v['positive_L_directions']=='','plain must not be labeled score-exhausted');return
            zero=int.from_bytes(b''.join(int(state[k][1]).to_bytes(8,'little') for k in ids),'little');one=int.from_bytes(b''.join(int(state[k][0]).to_bytes(8,'little') for k in ids),'little')
            oldcov=zero&one;oldobs=zero.bit_count()+one.bit_count();histL=Counter();histN=Counter();union=set();tied=zeroboth=onlyN=0;combined_H=0;scores={}
            for n,sides in gs.items():
                pair=[]
                for z,o in sides:
                    newz=zero|z;newo=one|o;newcov=(newz&newo)&~oldcov;L=newcov.bit_count();N=newz.bit_count()+newo.bit_count()-oldobs
                    pair.append((L,N));histL[L]+=1;histN[N]+=1;onlyN+=L==0 and N>0;combined_H|=newcov;verified_scores+=1
                scores[n]=pair;tied+=pair[0]==pair[1];zeroboth+=pair[0][1]==pair[1][1]==0
            require(int(v['positive_L_directions'])==sum(c for l,c in histL.items() if l>0),'positive L census')
            require(int(v['only_N_directions'])==onlyN and int(v['tied_atoms'])==tied and int(v['zero_N_atoms'])==zeroboth,'N/tie census')
            require({int(k):v for k,v in json.loads(v['L_distribution']).items()}==histL,'L histogram')
            require({int(k):v for k,v in json.loads(v['N_distribution']).items()}==histN,'N histogram')
            require(int(v['describable_missing_bits'])==combined_H.bit_count(),'directory gap union')
        verify_state()
        for q in qs:
            i=q['query'];r=qr[i];e=ex[i];p=proof[i];require(q['coverage_version']==version==int(r['coverage_version']),'query formal version')
            require(abs(float(r['cycle_seconds'])-(q['finished']-previous))<1e-9,'cycle cost');previous=q['finished']
            c=cs.get(i);m=ms.get(i);receipt=receipts.get(i);timely=bool(receipt and receipt['timely'] and receipt['completion']<=spec['seconds']);D=set();trial=state;truths={}
            require(bool(q['emitted'])==timely,'query/receipt delivery disagreement')
            if receipt:
                require(m is not None and c is not None,'orphan completion receipt')
                require(q['completion']==receipt['completion'],'query/receipt completion timestamp')
                require(c['validated_seconds']==m['validated_seconds']<=receipt['completion']<=q['finished'],'candidate/model completion timeline')
                require(m['index']==receipt['index']==version+1,'formal output index')
            if c:
                require(q['valid'] and c['valid'] and q['evidence']['raw_status']=='sat','candidate validity/status')
                require(q['duplicate']==(model_id(c['model']) in seen),'formal duplicate set mismatch')
                trial=metric.apply(state,c['model']);vals={v['name']:int(v['value']) if v['sort']=='Int' else v['value'] for v in c['model']};truths={n:bool(atom_value(metric.nodes[n],vals)) for n in atoms}
                for n,k in enumerate(ids):
                    mask=(int(trial[k][0])&int(trial[k][1]))&~(int(state[k][0])&int(state[k][1]))
                    while mask:bit=(mask&-mask).bit_length()-1;D.add((n,bit));mask&=mask-1
                require(bitset_words(int(p['D'],16))==D,'recorded concrete D bitmap')
            else:require(p['D'] is None and r['candidate_gain']=='','missing candidate is unknown, not a zero-valued model')
            by_local={a['ast_id']:n for n,a in atoms.items()};adopted=[x for x in q['hook'].get('events',[]) if x['applied']]
            require(len(adopted)==q['hook'].get('applied',0),'lost adopted event')
            E=set();P=set();sum_individual=0
            for event in adopted:
                n=by_local[event['atom_id']];direction=event['preferred_truth'];H=sparse_H(atoms[n]['sides'][direction],state,ids)
                require(len(H)==scores[n][direction][0],'H cardinality vs independently computed L')
                row=next(x for x in p['applied'] if x['atom_key']==atom_keys[n] and x['direction']==direction)
                require(bitset_words(int(row['H'],16))==H,'saved individual H bitmap')
                require(row['realized']==(truths[n]==direction if c else None),'realization unknown handling')
                if c and truths[n]==direction:require(H<=D,'realized H outside D');E|=H;sum_individual+=len(H)
                checks+=1
            for pref in q['preferences']:
                n=pref['node'];direction=pref['truth']
                require(scores[n][0]==(pref['zero_covered'],pref['zero_observed']) and scores[n][1]==(pref['one_covered'],pref['one_observed']),'both-side recorded scores')
                if c and truths[n]==direction:P|=sparse_H(atoms[n]['sides'][direction],state,ids)
            require(E<=P<=D,'intervention/proposal explanation subset')
            require(bitset_words(int(p['E_applied'],16))==E and bitset_words(int(p['E_proposed'],16))==P,'union explanation bitmap')
            require(int(e['E_applied_realized_bits'])==(len(E) if timely else 0) and int(e['D_bits'])==(len(D) if timely else 0),'formal explanation must exclude late models')
            require(int(e['applied_overlap'])==sum_individual-len(E),'overlap accounting')
            require((e['applied_explanation_ratio']=='')==(not timely or not D),'zero-denominator ratio not NA')
            if timely and D:require(abs(float(e['applied_explanation_ratio'])-len(E)/len(D))<1e-12,'explanation ratio')
            if timely:
                require(m is not None and m['gain']==len(D),'formal marginal');covered_sum+=len(D);state=trial;version+=1;verify_state()
                require(q['gain']==m['gain'] and m['model']==c['model'],'query/formal candidate identity and gain')
                seen.add(model_id(c['model']))
        require(version==summary['formal_count'] and metric.export(state)==summary['coverage'] and covered_sum==metric.count(state),'final original evaluator replay')
        require(len(av)==version+1,'all saved coverage versions represented')
        require(not attempts,'auditor attempted SMT search')
    with gzip.open(derived/'phase_opportunities.csv.gz','rt') as f:
        for r in csv.DictReader(f):
            require(r['opposite_generated_model_gain']=='' and r['opposite_query_cost']=='','invented strict counterfactual')
            if not cs.get(int(r['query'])):require(r['final_direction_realized']=='','Unknown final phase treated as false')
            require(r['atom_key']==atom_keys[int(r['node'])],'cross-process atom identity');missing_counterfactual+=1
    session=json.loads((derived/'session_summary.json').read_text());require(abs(sum(float(r['cycle_seconds']) for r in qr.values())+session['unallocated_tail_seconds']-session['reported_session_elapsed'])<1e-7,'complete cost partition')
    return dict(stage=stage,session=run,passed=True,queries=len(qs),formal_models=version,coverage_versions=len(av),applied_H_checks=checks,independent_direction_scores=verified_scores,direction_rows=missing_counterfactual,search_attempts=0)

def main():
    a=argparse.ArgumentParser();a.add_argument('--out',default='results/soft_phase_score_diagnosis');a.add_argument('--jobs',type=int,default=15);v=a.parse_args();out=(ROOT/v.out).resolve()
    summary=json.loads((out/'analysis_summary.json').read_text());args=[(str(out),r['stage'],r['session']) for r in summary['sessions']]
    with ProcessPoolExecutor(max_workers=v.jobs) as pool:results=list(pool.map(one,args))
    manifest=json.loads((out/'source_manifest.json').read_text())
    for path,h in manifest['files'].items():require(file_hash(Path(path) if path.startswith('/') else ROOT/path)==h,'historical source changed')
    dump(out/'independent_audit.json',dict(passed=True,sessions=results,history_unchanged=True,search_calls=0,method='second frozen-evaluator replay; sparse-set H/E checks; full-directory score census via cumulative observation union'))
    print(f'Independent offline audit passed: {len(results)} sessions',flush=True)
if __name__=='__main__':main()
