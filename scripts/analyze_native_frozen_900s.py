#!/usr/bin/env python3
"""Offline paired 900-second validation; historical HighDiv is never a paired run."""
import argparse,csv,json,statistics
from pathlib import Path
from reevaluate_round6_saved_candidates import dump,write_csv,require,file_hash

TIMES=(1,5,10,30,60,120,300,600,900)
PREFIXES=(2,5,10,50,100,1000,5000)
def read(path):
    with path.open() as f:return list(csv.DictReader(f))
def mean(xs):return statistics.mean(xs) if xs else None
def median(xs):return statistics.median(xs) if xs else None
def short(name):return 'SMT_random' if 'random' in name else 'SMT_real'
def fmt(x):return '未完成/未知' if x is None else f'{x:.6g}' if isinstance(x,float) else str(x)
def table(headers,rows):return '\n'.join(['| '+' | '.join(headers)+' |','|'+'---|'*len(headers),*['| '+' | '.join(map(fmt,r))+' |' for r in rows]])

def paired(rows,dimension):
    index={(r['formula'],r['seed'],r['configuration'],r[dimension]):r for r in rows};result=[]
    for r in rows:
        if r['configuration']!='native_random_one':continue
        b=index[(r['formula'],r['seed'],'plain',r[dimension])];complete=r['complete'] and b['complete'];delta=r['covered']-b['covered'] if complete else None
        result.append(dict(formula=r['formula'],seed=r['seed'],dimension=dimension,point=r[dimension],complete=complete,delta_bits=delta,
            delta_percentage_points=100*delta/r['total'] if complete else None,win_tie_loss=('win' if delta>0 else 'loss' if delta<0 else 'tie') if complete else 'incomplete',
            native_run=r['run'],plain_run=b['run'],arrival_delta_seconds=r['arrival_seconds']-b['arrival_seconds'] if complete and dimension=='prefix' else None))
    return result

def analyze(out):
    plan=json.loads((out/'plan.json').read_text());audit=json.loads((out/'audit_summary.json').read_text());require(audit['passed'],'independent audit required')
    runs=audit['runs'];expected={(i['name'],s,c) for i in plan['inputs'] for s in (0,1) for c in ('plain','native_random_one')}
    require(len(runs)==8 and {(r['formula'],r['seed'],r['configuration']) for r in runs}==expected,'incomplete prescribed run matrix')
    sessions=[];times=[];prefixes=[];costs=[];queries=[];resources=[]
    for r in runs:
        identity={k:r[k] for k in ('formula','seed','configuration','run')};raw=out/'runs'/r['run']/'raw';c=r['native_counters'];counts=r['query_counts'] or dict(sat=0,unknown=0,unsat=0)
        process=json.loads((raw.parent/'process_result.json').read_text());cpu={}
        resourcefile=raw.parent/'resource.txt'
        if resourcefile.exists():
            for line in resourcefile.read_text().splitlines():
                text=line.strip()
                for key,name in [('User time (seconds):','cpu_user_seconds'),('System time (seconds):','cpu_system_seconds'),('Maximum resident set size (kbytes):','max_rss_kib')]:
                    if text.startswith(key):cpu[name]=float(text[len(key):].strip())
        trace=read(out/'audit'/r['run']/'timeline.csv');observations=0;zero=longest=0
        for row in trace:
            current=int(row['observed']);zero=zero+1 if current==observations else 0;longest=max(longest,zero);observations=current
        sessions.append(dict(identity,total=r['total'],covered=r['covered'],coverage=r['coverage'],formal_models=r['distinct'],positive_models=r['positive'],
            process_wall_seconds=r['process_wall_seconds'],cleanup_seconds=r['cleanup_seconds'],returncode=r['returncode'],terminal=r['terminal'],resource_stop=process.get('resource_stop',False),
            first_model_seconds=r['first_model_seconds'],longest_no_growth_seconds=r['longest_no_growth_seconds'],longest_zero_new_observation_models=longest,
            first_model_observations=int(trace[0]['observed']) if trace else 0,post_first_observations=observations-int(trace[0]['observed']) if trace else 0,
            sat=counts['sat'],unknown=counts['unknown'],unsat=counts['unsat'],selected=c.get('selected',0),outside_catalog=c.get('outside_catalog',0),
            keep=c.get('KeepNative',0),flip=c.get('FlipOnce',0),realized=c.get('realized',0),unrealized=c.get('unrealized',0),no_model=c.get('no_model',0),
            mechanism_seconds=r['mechanism_seconds'],mechanism_stop=r['mechanism_stop'],full_cycle_seconds=r['cycle_cost_sum'],final_tail_seconds=r['tail_cost'],**cpu,
            **{'excluded_'+k:v for k,v in r['excluded'].items()}))
        for t in TIMES:
            available=[row for row in trace if float(row['completion'])<=t];n=len(available);covered=int(available[-1]['covered']) if n else 0
            times.append(dict(identity,time_seconds=t,complete=True,covered=covered,total=r['total'],coverage=covered/r['total'],models=n))
        for n in PREFIXES:
            row=trace[n-1] if len(trace)>=n else None
            prefixes.append(dict(identity,prefix=n,complete=row is not None,available=len(trace),covered=int(row['covered']) if row else None,total=r['total'],
                coverage=float(row['coverage']) if row else None,arrival_seconds=float(row['completion']) if row else None))
        if r['costs']:
            for key,v in r['costs']['seconds'].items():costs.append(dict(identity,stage=key,seconds=v,additive=False,note='stage categories overlap: hook within check; preparation contains subtasks'))
        costs.append(dict(identity,stage='complete_query_intervals',seconds=r['cycle_cost_sum'],additive=True,note='add only final tail for internal elapsed'))
        costs.append(dict(identity,stage='final_tail',seconds=r['tail_cost'],additive=True,note='last feedback maintenance'))
        fullcost={row['query']:row for row in map(json.loads,(raw/'costs.jsonl').read_text().splitlines())}
        for q in map(json.loads,(raw/'queries.jsonl').read_text().splitlines()):
            queries.append(dict(identity,query=q['query'],cycle_start=q['cycle_start'],start=q['start'],finished=q['finished'],full_cycle_seconds=fullcost[q['query']]['full_cycle_seconds'],
                gain=q['gain'],observations=q['observations'],emitted=q['emitted'],duplicate=q['duplicate'],**q['evidence'],
                **{k:v for k,v in q.items() if k.endswith('_seconds') or k=='hook_seconds_nested'}))
        samples=raw.parent/'resource_samples.jsonl'
        if samples.exists():
            rows=[json.loads(line) for line in samples.read_text().splitlines()]
            resources.append(dict(identity,sampled_peak_rss_kib=max((sum(int((p.get('VmRSS') or '0 kB').split()[0]) for p in row['processes']) for row in rows),default=0),
                peak_all_samplers_rss_kib=max((row['aggregate_rss_kib'] for row in rows),default=0),minimum_host_available_kib=min((row['host_available_kib'] for row in rows),default=None)))
    differences=paired(times,'time_seconds')+paired(prefixes,'prefix');aggregates=[]
    for key in sorted({(r['formula'],r['dimension'],r['point']) for r in differences}):
        rs=[r for r in differences if (r['formula'],r['dimension'],r['point'])==key];ds=[r['delta_bits'] for r in rs if r['complete']]
        aggregates.append(dict(zip(('formula','dimension','point'),key),pairs=len(ds),incomplete=len(rs)-len(ds),mean_delta_bits=mean(ds),median_delta_bits=median(ds),
            wins=sum(d>0 for d in ds),ties=sum(d==0 for d in ds),losses=sum(d<0 for d in ds)))
    historical=json.loads((out/'historical_highdiv.json').read_text());hrows=[];hprefix=[];htimes=[];hdiff=[]
    for h in historical['rows']:
        hrows.append({k:v for k,v in h.items() if k!='curve'})
        for t in TIMES:
            available=[r for r in h['curve'] if r['seconds']<=t];covered=available[-1]['covered'] if available else 0
            htimes.append(dict(formula=h['formula'],seed=0,configuration='HighDiv_historical',time_seconds=t,covered=covered,total=h['total'],coverage=covered/h['total'],models=len(available),same_condition_paired=False))
        for n in PREFIXES:
            row=next((r for r in h['curve'] if r['k']==n),None)
            hprefix.append(dict(formula=h['formula'],seed=0,prefix=n,complete=row is not None,covered=row['covered'] if row else None,total=h['total'],arrival_seconds=row['seconds'] if row else None,same_condition_paired=False))
        for r in sessions:
            if r['formula']==h['formula']:
                hdiff.append(dict(formula=r['formula'],new_seed=r['seed'],new_configuration=r['configuration'],new_covered=r['covered'],historical_seed=0,historical_covered=h['covered'],
                    difference_bits=r['covered']-h['covered'],difference_percentage_points=100*(r['coverage']-h['coverage']),same_condition_paired=False))
    dest=out/'analysis';dest.mkdir(exist_ok=True)
    tables=dict(sessions=sessions,time_coverage=times,sample_prefix=prefixes,paired_differences=differences,paired_statistics=aggregates,cost_components=costs,query_costs=queries,resources=resources,
        historical_highdiv_reference=hrows,historical_highdiv_time=htimes,historical_highdiv_prefix=hprefix,historical_highdiv_descriptive_differences=hdiff)
    for name,rows in tables.items():write_csv(dest/(name+'.csv'),rows)
    dump(dest/'summary.json',dict(passed=True,sessions=sessions,paired_statistics=aggregates,historical_highdiv=hrows,new_sampler_sessions=8,highdiv_new_runs=0,
        source_audit_sha256=file_hash(out/'audit_summary.json'),historical_sha256=file_hash(out/'historical_highdiv.json')))
    return sessions,times,prefixes,aggregates,hrows,hdiff

def report(out,data):
    sessions,times,prefixes,aggregates,hrows,hdiff=data
    lines=['# 原生随机900秒冻结验证报告','',
        '基于 `40874ea`，分支 `native-random-900s-frozen`。按用户最终指示只运行两个既定公式、seed 0/1、plain 与 native_random_one，共8个900秒会话。直接复用原冻结二进制和库，没有修改算法参数或重新构建。没有运行反馈学习、HighDiv或超过900秒的实验。','',
        '15个任务槽、共享CPU 0–14，实际8个单线程采样任务在同一个单调时钟起点同时释放；监督器使用CPU15。没有排队波次或为了填满15槽增加seed。每会话预算包含加载、首次求解、全部机制与失败、验证及输出成本。机器为8物理核/16逻辑核，不是15个独立物理核。',
        '', '全部新会话由冻结 ManualSatisfiesMetric 独立审计。完整原式合法性、seen0/seen1、短路访问、整数低64位、分母、去重、写出截止、边际累计、原生动作及完整成本区间均复核；审计未调用求解器搜索。',
        '', '## 900秒结果','',table(['公式','seed','配置','covered/分母','覆盖率','不同模型','实际进程秒','CPU用户/系统秒','首模型秒'],
            [[short(r['formula']),r['seed'],r['configuration'],f"{r['covered']}/{r['total']}",f"{100*r['coverage']:.4f}%",r['formal_models'],r['process_wall_seconds'],f"{fmt(r.get('cpu_user_seconds'))}/{fmt(r.get('cpu_system_seconds'))}",r['first_model_seconds']] for r in sessions]),
        '', '进程秒为监督器观察的退出墙钟，包含轮询/清理误差；正式覆盖由逐模型实际写完时间严格按900秒判定。query_costs.csv保留请求/实际授予时限、原始Unknown reason、check及完整查询成本；嵌套成本不能相加重复收费。',
        '', '## 同条件配对：native_random_one − plain','',table(['公式','时刻秒','配对','均值差bit','中位差bit','胜/平/负'],
            [[short(r['formula']),r['point'],r['pairs'],r['mean_delta_bits'],r['median_delta_bits'],f"{r['wins']}/{r['ties']}/{r['losses']}"] for r in aggregates if r['dimension']=='time_seconds']),
        '',table(['公式','前缀样本数','完整配对','未完成配对','均值差bit','中位差bit','胜/平/负'],
            [[short(r['formula']),r['point'],r['pairs'],r['incomplete'],r['mean_delta_bits'],r['median_delta_bits'],f"{r['wins']}/{r['ties']}/{r['losses']}"] for r in aggregates if r['dimension']=='prefix']),
        '', '未完成样本前缀保留NA，不补零，不删失败或只展示完成的seed。所有原始不同合法模型按生成顺序输出，没有筛选或候选重排。逐seed前缀和到达时间见sample_prefix.csv，逐seed配对见paired_differences.csv。',
        '', '## HighDiv历史参考（没有新运行）','',
        'HighDiv仅有历史seed 0、900秒；原外层编译-O0、12工作槽且实际9例并行。本次两新模式为冻结Release、实际8例共享15逻辑CPU。历史时间记录与内部成本没有补造，不能称为本次三算法同资源同完整成本的公平复跑，也不把同一个历史seed复制成两个独立重复。',
        '',table(['公式','历史seed','原始不同模型','covered/分母','历史覆盖率'],[[short(h['formula']),h['seed'],h['distinct'],f"{h['covered']}/{h['total']}",f"{100*h['coverage']:.4f}%"] for h in hrows]),
        '',table(['公式','新seed','新配置','相对历史差bit','差值百分点（非配对）'],[[short(r['formula']),r['new_seed'],r['new_configuration'],r['difference_bits'],r['difference_percentage_points']] for r in hdiff]),
        '', '原始历史manifest与两份审计的复制件及来源哈希在historical/。历史原始未过滤曲线用于historical_highdiv_time.csv和historical_highdiv_prefix.csv；缺失前缀仍为NA。',
        '', '## 机会、供给与高原','',table(['公式','seed','配置','SAT/Unknown','机会/目录外','Keep/Flip','实现/未实现/无模型','机制秒','停止原因','最长无增长秒','资源中止'],
            [[short(r['formula']),r['seed'],r['configuration'],f"{r['sat']}/{r['unknown']}",f"{r['selected']}/{r['outside_catalog']}",f"{r['keep']}/{r['flip']}",f"{r['realized']}/{r['unrealized']}/{r['no_model']}",r['mechanism_seconds'],r['mechanism_stop'] or '无',r['longest_no_growth_seconds'],r['resource_stop']] for r in sessions]),
        '', '原生随机的动作概率与5%机制比例、4096缓存等参数未变。保护触发后的普通探索和成本照常保留，不能声称回到从未干预的历史。查询结束模型未实现翻转方向，不改动实际动作标签。',
        '', '## 有限结论','']
    for formula in dict.fromkeys(r['formula'] for r in sessions):
        final=next(r for r in aggregates if r['formula']==formula and r['dimension']=='time_seconds' and r['point']==900)
        rs=[r for r in sessions if r['formula']==formula];plain=[r for r in rs if r['configuration']=='plain'];native=[r for r in rs if r['configuration']=='native_random_one']
        pmean=mean([r['covered'] for r in plain]);nmean=mean([r['covered'] for r in native]);pm=mean([r['formal_models'] for r in plain]);nm=mean([r['formal_models'] for r in native])
        conclusion='两个seed均有正面覆盖信号' if final['wins']==2 else '两个seed均退步' if final['losses']==2 else '跨seed未呈现一致正面优势'
        lines.append(f"- {short(formula)}：900秒{conclusion}；native−plain均值 {fmt(final['mean_delta_bits'])} bit，胜/平/负 {final['wins']}/{final['ties']}/{final['losses']}。平均覆盖bit {fmt(pmean)}→{fmt(nmean)}，平均模型数 {fmt(pm)}→{fmt(nm)}。同数量与早期风险按上表保留，不能只用最终产量解释质量。")
    lines+=['', '这只是两个已开发公式、每公式两个seed的冻结验证，不能推及整个bofill或LIA。与HighDiv的数值差异仅作历史参照，不作公平加速或统计显著性结论。本次仅到900秒，不能回答超过900秒是否仍有优势；不自动追加更长预算、参数扫描或新实验。',
        '', '## 复核','', '输入/源码/二进制/库/协议哈希见plan.json；逐模型审计见audit/；完整原始模型、查询与资源日志保存在逐运行无损归档，archive_manifest.json绑定归档分片及逐文件SHA-256。',
        '', '```bash','export HIGHDIV_ROOT=/tmp/lia-highdiv-frozen',
        '/tmp/lia-highdiv-frozen/venv/bin/python3 scripts/audit_native_feedback_results.py results/native_frozen_900s --jobs 15',
        '/tmp/lia-highdiv-frozen/venv/bin/python3 scripts/analyze_native_frozen_900s.py results/native_frozen_900s','```','']
    (out/'报告.md').write_text('\n'.join(lines))

def main():
    p=argparse.ArgumentParser();p.add_argument('out');a=p.parse_args();out=Path(a.out).resolve();report(out,analyze(out));print('Frozen 900s paired analysis and Chinese report complete; no search',flush=True)
if __name__=='__main__':main()
