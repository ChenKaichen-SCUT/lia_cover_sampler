#!/usr/bin/env python3
"""Read audited trajectories; preserve per-instance/seed pairing and explicit promotion gates."""
import argparse,csv,json
from bisect import bisect_right
from collections import Counter,defaultdict
from pathlib import Path
from statistics import mean,median
from run_iteration import ROOT,digest,write
TIMES=(5,10,30,60,120,300,900)
PREFIXES=(2,5,10,50,100,1000)

def csvfile(path,rows):
    if not rows:return
    with path.open('w') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)

def analyze(root,micro=None):
    root=Path(root).resolve();plan=json.loads((root/'plan.json').read_text());done=json.loads((root/'COMPLETE.json').read_text())
    audit=json.loads((root/'AUDIT_COMPLETE.json').read_text())
    assert not done['cancelled'] and done['errors']==0 and audit['all_passed']
    assert digest(root/'plan.json')==done['plan_sha256'] and digest(root/'queue.jsonl')==done['queue_sha256']
    out=root/'analysis';out.mkdir(exist_ok=True);rows=[];curves={};validation=Counter();walls=[];mechanisms=[]
    for name in plan['configs']:
        folder=root/name;manifest=json.loads((folder/'manifest.json').read_text());complete=json.loads((folder/'COMPLETE.json').read_text())
        assert digest(folder/'manifest.json')==complete['manifest_sha256'] and digest(folder/'runs.jsonl')==complete['journal_sha256']
        reports=json.loads((folder/'audit_summary.json').read_text());assert len(reports)==complete['runs']
        for report in reports:
            case=report['instance'];seed=report['seed'];validation.update(report['counts'])
            detail=json.loads((folder/'audit'/case/f'seed-{seed}.json').read_text());curve=detail['curve'];curves[name,case,seed]=curve
            assert len(curve)==report['unique_valid_in_budget']
            previous=0;stamp=0
            for index,p in enumerate(curve,1):
                assert p['k']==index and previous<=p['covered']<=p['total'] and stamp<=p['seconds']<=plan['seconds']
                previous=p['covered'];stamp=p['seconds']
            rows.append(dict(configuration=name,instance=case,seed=seed,samples=len(curve),covered=report['covered'],
                total=report['total'],percent=report['percent'],reached_1000=len(curve)>=1000,
                seconds_1000=curve[999]['seconds'] if len(curve)>=1000 else None,
                prefix1000_percent=100*curve[999]['covered']/report['total'] if len(curve)>=1000 else None))
            sampler=report.get('sampler',{});wall=sampler.get('wall_timing')
            if wall:
                buckets=('parsing_and_initialization','action_wall_seconds','scheduler_decision_seconds','idle_sleep_seconds','checkpoint_and_logging_seconds','other_wall_seconds')
                assert abs(sum(wall[k] for k in buckets)-wall['total_wall_seconds'])<1e-5*max(1,wall['total_wall_seconds'])
                walls.append(dict(configuration=name,instance=case,seed=seed,partial=report.get('statistics_partial',False),**wall))
            mechanisms.append(dict(configuration=name,instance=case,seed=seed,partial=report.get('statistics_partial',False),statistics=sampler.get('statistics',{})))
    points=[]
    for r in rows:
        curve=curves[r['configuration'],r['instance'],r['seed']]
        base={k:r[k] for k in ('configuration','instance','seed')}
        for t in sorted(set([v for v in TIMES if v<=plan['seconds']]+[plan['seconds']])):
            i=bisect_right([p['seconds'] for p in curve],t)
            points.append(dict(**base,metric='time',budget=t,complete=True,percent=100*curve[i-1]['covered']/r['total'] if i else 0,arrival_seconds=t))
        for k in PREFIXES:
            p=curve[k-1] if len(curve)>=k else None
            points.append(dict(**base,metric='prefix',budget=k,complete=p is not None,percent=100*p['covered']/r['total'] if p else None,arrival_seconds=p['seconds'] if p else None))
    lookup={(r['configuration'],r['instance'],r['seed'],r['metric'],r['budget']):r for r in points};pairs=[]
    for r in points:
        if r['configuration']=='control_ab':continue
        ref=lookup.get(('control_ab',r['instance'],r['seed'],r['metric'],r['budget']))
        if not ref:continue
        matched=r['complete'] and ref['complete']
        pairs.append(dict(configuration=r['configuration'],reference='control_ab',instance=r['instance'],seed=r['seed'],metric=r['metric'],budget=r['budget'],
            paired=matched,delta_pp=r['percent']-ref['percent'] if matched else None,
            seconds_delta=r['arrival_seconds']-ref['arrival_seconds'] if matched else None))
    summary=[]
    for name in plan['configs']:
        selected=[r for r in rows if r['configuration']==name]
        summary.append(dict(configuration=name,cases=len(selected),mean_percent=mean(r['percent'] for r in selected),
            samples=sum(r['samples'] for r in selected),reached_1000=sum(r['reached_1000'] for r in selected)))
    paired_summary=[]
    for key in sorted({(p['configuration'],p['metric'],p['budget']) for p in pairs}):
        values=[p['delta_pp'] for p in pairs if (p['configuration'],p['metric'],p['budget'])==key and p['paired']]
        paired_summary.append(dict(configuration=key[0],metric=key[1],budget=key[2],paired=len(values),
            mean_delta_pp=mean(values) if values else None,median_delta_pp=median(values) if values else None,
            wins=sum(v>1e-10 for v in values),ties=sum(abs(v)<=1e-10 for v in values),losses=sum(v< -1e-10 for v in values)))
    resources=json.loads((root/'resources.json').read_text())
    result=dict(stage=plan['stage'],budget_seconds=plan['seconds'],purpose=plan['purpose'],summary=summary,paired=paired_summary,
        validation=dict(validation),sampling_cpu_seconds=resources['cpu_seconds'],sampling_wall_seconds=resources['wall_seconds'],
        audit_cpu_seconds=audit['cpu_seconds'],audit_wall_seconds=audit['wall_seconds'],audit_cache_hits=audit['cache_hits'],
        workers=plan['workers'],evidence={str(p):digest(p) for p in [root/'plan.json',root/'queue.jsonl',root/'AUDIT_COMPLETE.json']},
        definition='One immutable online trajectory: time endpoints and unreordered valid distinct sample prefixes; no best-k selection')
    csvfile(out/'endpoints.csv',rows);csvfile(out/'curves.csv',points);csvfile(out/'paired_by_case_seed.csv',pairs)
    csvfile(out/'paired_summary.csv',paired_summary);csvfile(out/'wall_breakdown.csv',walls);write(out/'mechanisms.json',mechanisms)
    write(out/'analysis.json',result)
    if plan['stage'] in ('screen','confirm'):
        checks={};candidates=[name for name in plan['configs'] if name!='control_ab']
        for name in candidates:
            endpoint=[p for p in pairs if p['configuration']==name and p['metric']=='time' and p['budget']==plan['seconds'] and p['paired']]
            per_case=defaultdict(list)
            for p in endpoint:per_case[p['instance']].append(p['delta_pp'])
            checks[name+'_endpoint_mean_nonnegative']=bool(endpoint) and mean(p['delta_pp'] for p in endpoint)>=0
            checks[name+'_positive_on_two_development_cases']=sum(mean(v)>1e-10 for v in per_case.values())>=2
            checks[name+'_majority_seed_pairs_nonnegative']=sum(p['delta_pp']>=-1e-10 for p in endpoint)>=len(endpoint)*2/3
            for k in (10,50):
                vals=[p['delta_pp'] for p in pairs if p['configuration']==name and p['metric']=='prefix' and p['budget']==k and p['paired']]
                checks[name+f'_prefix_{k}_nonnegative']=bool(vals) and mean(vals)>=0
        if micro:
            data=json.loads(Path(micro).read_text());comparisons=data['paired']
            checks['micro_all_valid']=data['all_valid']
            checks['micro_positive_in_two_modules']=sum(mean(p['efficiency_delta'] for p in comparisons if p['module']==m)>0 for m in ('safe','line','polish'))>=2
        else:checks['micro_evidence_present']=False
        checks['all_new_models_independently_valid']=not any(v for k,v in validation.items() if k!='valid')
        promoted=dict(approved=bool(candidates) and all(checks.values()),next_stage='confirm' if plan['stage']=='screen' else 'final',
            checks=checks,selected=plan['configs'],analysis=str(out/'analysis.json'),analysis_sha256=digest(out/'analysis.json'),
            micro_analysis=str(micro) if micro else None,criteria='Predeclared development gates; never use evaluation cases to tune. Failed gates stop long runs.')
        write(root/'promotion.json',promoted)
    report=['# 第三轮阶段报告','',f'阶段：{plan["stage"]}；每次预算 {plan["seconds"]} 秒；并发 {plan["workers"]}；只使用本阶段 manifest 中的案例与种子。','',
        '| 配置 | 运行数 | 终点平均覆盖 % | 样本数 | 达到 1000 |','|---|---:|---:|---:|---:|']
    for r in summary:report.append(f'| {r["configuration"]} | {r["cases"]} | {r["mean_percent"]:.4f} | {r["samples"]} | {r["reached_1000"]} |')
    report+=['',f'采样 CPU {resources["cpu_seconds"]:.2f} 秒，采样墙钟 {resources["wall_seconds"]:.2f} 秒；独立审计 CPU {audit["cpu_seconds"]:.2f} 秒，审计墙钟 {audit["wall_seconds"]:.2f} 秒。',
        '',f'全部 {validation["valid"]} 个新模型审计通过。逐案例、逐种子差值与未完成前缀见 paired_by_case_seed.csv；顶层互斥计时见 wall_breakdown.csv。',
        '', '短预算和固定状态模块结果不称为 900 秒性能，也不当作完整求解器状态重放。']
    if (root/'promotion.json').exists():
        decision=json.loads((root/'promotion.json').read_text());report+=['','晋级：'+('通过' if decision['approved'] else '未通过，停止进入长预算实验。'),'']
        report += [f'- {k}: {v}' for k,v in decision['checks'].items()]
    (out/'report.md').write_text('\n'.join(report)+'\n');print(json.dumps(result['summary']));return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('--micro-analysis',type=Path)
    a=p.parse_args();analyze(a.root,a.micro_analysis)
if __name__=='__main__':main()
