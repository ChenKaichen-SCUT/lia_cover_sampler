#!/usr/bin/env python3
"""Full-strategy paired statistics: no conditioning on reach or prefix completion."""
import argparse,csv,json,statistics
from pathlib import Path
from collections import Counter
from reevaluate_round6_saved_candidates import dump,write_csv,require
from analyze_position_frozen_900s import timeline,count_at,prefix_at,exact_auc,longest_plateau
from run_position_fixed4 import CONFIGS,ROOT,SEEDS
TIMES=[1,5,10,30,60,120]
PREFIXES=[1,2,5,10,50,100,1000]
PAIRS=[('fixed4_immediate','first_immediate'),('fixed4_immediate','position_immediate'),('position_immediate','first_immediate')]
LABELS=dict(zip(CONFIGS,['固定1','随机1–4','固定4']))
def csvrows(path):
    with path.open() as f:return list(csv.DictReader(f))
def num(x):return None if x in ['',None] else float(x)
def gain_bin(x):return '0' if x==0 else '1–2' if x<=2 else '3–9' if x<=9 else '10–99' if x<=99 else '100+'
def paired_summary(rows):
    xs=[r['difference'] for r in rows if r['difference'] is not None]
    return dict(total_pairs=len(rows),complete_pairs=len(xs),incomplete_pairs=len(rows)-len(xs),mean=statistics.mean(xs) if xs else None,median=statistics.median(xs) if xs else None,wins=sum(x>0 for x in xs),ties=sum(x==0 for x in xs),losses=sum(x<0 for x in xs),worst_difference=min(xs) if xs else None,maximum_regression=max(0,-min(xs)) if xs else None)
def fmt(x,precision=2):return '未达到/未知' if x is None else f'{x:.{precision}f}' if isinstance(x,float) else str(x)
def table(rows,columns):
    return '\n'.join(['| '+' | '.join(label for key,label in columns)+' |','| '+' | '.join('---' for _ in columns)+' |']+['| '+' | '.join(fmt(r.get(key)) for key,label in columns)+' |' for r in rows])
def analyze(out):
    audit=json.loads((out/'audit_summary.json').read_text());require(audit['passed'] and len(audit['runs'])==42,'incomplete audit');plan=json.loads((out/'plan.json').read_text())
    resources={r['run']:r for r in csvrows(out/'resource_audit.csv')};costs={r['run']:r for r in csvrows(out/'intervention_costs.csv')};sessions=[];metrics=[];bins=[];events=[];data={}
    for run in audit['runs']:
        name=run['run'];ds=timeline(out/'audit'/name/'delivery.csv');data[run['formula'],run['seed'],run['configuration']]=ds
        common={k:run[k] for k in ['formula','seed','configuration','group','run']};initial=json.loads((out/'runs'/name/'raw/initial.json').read_text());raw=json.loads((out/'runs'/name/'raw/summary.json').read_text());cost=costs[name];res=resources[name]
        sess=common|dict(total_bits=run['total'],covered=run['covered'],coverage=run['covered']/run['total'],outputs=len(ds),queries=sum(run['counts'].get(k,0) for k in ['sat','unknown','unsat']),sat=run['counts'].get('sat',0),unknown=run['counts'].get('unknown',0),auc_bit_seconds=exact_auc(ds,120),longest_plateau_seconds=longest_plateau(ds,120),process_wall_seconds=float(res['process_wall_seconds']),cpu_seconds=float(res['user_seconds'])+float(res['system_seconds']),max_rss_kib=int(res['max_rss_kib']),mechanism_seconds=float(cost['mechanism_seconds']),mechanism_stop=cost['mechanism_stop'],stop_at_seconds=num(cost['stop_at_seconds']),stop_query=num(cost['stop_query']),eligible=int(cost['eligible']),enabled_checks=int(cost['enabled_checks']),target_arrived=int(cost['target_arrived']),selected=int(cost['selected']),flip=int(cost['flip']),not_reached=int(cost['not_reached']),disabled=int(cost['disabled']),scan_attempts=int(cost['scan_attempts']),feature_seconds=float(cost['feature_seconds']),statistics_capture_seconds=float(cost['statistics_capture_seconds']),diagnostic_journal_seconds=float(cost['diagnostic_journal_seconds']),arrival_rate_all=num(cost['arrival_rate_all']),selection_rate_all=num(cost['selection_rate_all']),flip_rate_all=num(cost['flip_rate_all']),arrival_rate_eligible=num(cost['arrival_rate_eligible']))
        sessions.append(sess)
        for t in TIMES:
            c,n=count_at(ds,t);metrics.append(common|dict(kind='time',point=t,attained=True,covered=c,outputs=n,completion=None))
        for k in PREFIXES:
            r=prefix_at(ds,k);metrics.append(common|dict(kind='prefix',point=k,attained=r is not None,covered=r['covered'] if r else None,outputs=len(ds),completion=r['completion'] if r else None))
        metrics.append(common|dict(kind='auc',point=120,attained=True,covered=sess['auc_bit_seconds'],outputs=len(ds),completion=None))
        for label in ['0','1–2','3–9','10–99','100+']:
            selected=[r for r in ds if gain_bin(r['gain'])==label];bins.append(common|dict(bin=label,count=len(selected),covered_contribution=sum(r['gain'] for r in selected),first_time=selected[0]['completion'] if selected else None))
        qs=[json.loads(l) for l in (out/'runs'/name/'raw/positions.jsonl').read_text().splitlines()]
        for r in ds:
            p=qs[r['query']];events.append(common|r|dict(bin=gain_bin(r['gain']),target=p['position']['target'],eligible=p['position']['eligible'],target_arrived=p['position'].get('target_arrived',False),selected=p['decision']['selected'],action=p['decision'].get('action')))
    pairs=[];summaries=[];lookup={(r['formula'],r['seed'],r['configuration'],r['kind'],r['point']):r for r in metrics}
    formulas=[i['name'] for i in plan['inputs']]
    for formula in formulas:
        for a,b in PAIRS:
            for kind,points in [('time',TIMES),('prefix',PREFIXES),('auc',[120])]:
                for point in points:
                    rs=[]
                    for seed in SEEDS:
                        x,y=(lookup[formula,seed,c,kind,point] for c in [a,b]);diff=x['covered']-y['covered'] if x['attained'] and y['attained'] else None
                        row=dict(formula=formula,seed=seed,a=a,b=b,kind=kind,point=point,a_attained=x['attained'],b_attained=y['attained'],a_covered=x['covered'],b_covered=y['covered'],a_time=x['completion'],b_time=y['completion'],difference=diff);rs.append(row);pairs.append(row)
                    summaries.append(dict(formula=formula,a=a,b=b,kind=kind,point=point,**paired_summary(rs)))
    completion=[]
    for formula in formulas:
        for mode in CONFIGS:
            for k in PREFIXES:
                rs=[r for r in metrics if r['formula']==formula and r['configuration']==mode and r['kind']=='prefix' and r['point']==k];times=[r['completion'] for r in rs if r['attained']]
                completion.append(dict(formula=formula,configuration=mode,k=k,total_sessions=len(rs),attained=len(times),unattained=len(rs)-len(times),time_mean=statistics.mean(times) if times else None,time_median=statistics.median(times) if times else None))
    for path,rows in [('sessions.csv',sessions),('metrics.csv',metrics),('paired_differences.csv',pairs),('paired_summary.csv',summaries),('prefix_completion.csv',completion),('gain_bins.csv',bins),('model_events.csv',events)]:write_csv(out/path,rows)
    # Initialization mismatch is a measured nuisance, never erased by excluding pairs.
    initial=csvrows(out/'initialization_pairs.csv');costsummary=[];binsummary=[]
    for formula in formulas:
        for mode in CONFIGS:
            rs=[r for r in sessions if r['formula']==formula and r['configuration']==mode]
            q=sum(r['queries'] for r in rs);eligible=sum(r['eligible'] for r in rs)
            costsummary.append(dict(formula=formula,configuration=mode,queries=q,eligible=eligible,enabled_checks=sum(r['enabled_checks'] for r in rs),arrived=sum(r['target_arrived'] for r in rs),selected=sum(r['selected'] for r in rs),selection_rate_all=sum(r['selected'] for r in rs)/q if q else None,flip_rate_all=sum(r['flip'] for r in rs)/q if q else None,flips=sum(r['flip'] for r in rs),not_reached=sum(r['not_reached'] for r in rs),arrival_rate_all=sum(r['target_arrived'] for r in rs)/q if q else None,arrival_rate_eligible=sum(r['target_arrived'] for r in rs)/eligible if eligible else None,mechanism_seconds_mean=statistics.mean(r['mechanism_seconds'] for r in rs),feature_seconds_mean=statistics.mean(r['feature_seconds'] for r in rs),scan_attempts_mean=statistics.mean(r['scan_attempts'] for r in rs),quota_stops=sum(r['mechanism_stop']=='mechanism_cost_quota' for r in rs),other_stops=json.dumps(Counter(r['mechanism_stop'] for r in rs if r['mechanism_stop'] and r['mechanism_stop']!='mechanism_cost_quota')),statistics_cost_mean=statistics.mean(r['statistics_capture_seconds']+r['diagnostic_journal_seconds'] for r in rs)))
            for label in ['0','1–2','3–9','10–99','100+']:
                bs=[r for r in bins if r['formula']==formula and r['configuration']==mode and r['bin']==label];binsummary.append(dict(formula=formula,configuration=mode,bin=label,count_mean=statistics.mean(r['count'] for r in bs),covered_mean=statistics.mean(r['covered_contribution'] for r in bs)))
    write_csv(out/'intervention_summary.csv',costsummary);write_csv(out/'gain_bin_summary.csv',binsummary)
    lines=['# 固定第四位置120秒最小消融报告','', '本轮完成两个原开发公式 × seed 10–16 × 三配置，共42次120秒会话。所有查询及零收益、未到达目标、Unknown均保留。HighDiv没有重跑；没有启动900秒或下一阶段。', '', '三配置唯一策略差别是固定1、每查询均匀随机1–4、固定4。固定4确实通过不同支持AST原子序号计数；重复原子不推进，回溯/重启不重置，窗口仍为前32次原生决策。Keep/Flip在实际到达且通过相关检查后抽取；同查询序号不强制相同动作。完整公式、增量求解、完整赋值阻塞及立即交付保持原规则。', '', '新增原始Z3统计抓取和诊断记录的成本计入120秒会话，机制启用后的抓取/诊断成本也计入既有5%额度。计数器只提供原始快照及相邻不下降差值，不能当作完整冲突/学习/回溯轨迹或机制因果证据。', '', '执行15、15、12路三个同步批次，每个三配置组同一时刻释放且分配到不同物理核。机器为8物理核/16逻辑CPU，前两批15个采样进程使用15个逻辑CPU，监督器占剩余逻辑CPU并与一个采样进程共享物理核；该单例配置轮换。它们并非15个独立物理核。Z3超时辅助线程与主线程绑定同一CPU。独立覆盖审计在所有采样结束后以15个进程执行。', '', f'独立审计通过42/42会话，逐模型重放完整seen0/seen1、短路访问、整数低64位、合法性、去重、截止和边际累计；离线搜索调用为0。三方初始化参数一致；首模型一致组数 {sum(r["first_models_equal"]=="True" for r in initial)}/14，首模型前状态/授时序列一致组数 {sum(r["prefirst_status_grants_equal"]=="True" for r in initial)}/14。未干预首模型之前的实际结果差异保留，不能把它当作位置效应。', '']
    for formula in formulas:
        short='SMT_random' if 'random' in formula else 'SMT_real';lines+=['## '+short,'', '逐seed的120秒终点；差值始终为左配置减右配置。','']
        rows=[]
        for seed in SEEDS:
            ss={r['configuration']:r for r in sessions if r['formula']==formula and r['seed']==seed};rows.append(dict(seed=seed,first=ss[CONFIGS[0]]['covered'],random=ss[CONFIGS[1]]['covered'],fixed4=ss[CONFIGS[2]]['covered'],d1=ss[CONFIGS[2]]['covered']-ss[CONFIGS[0]]['covered'],dr=ss[CONFIGS[2]]['covered']-ss[CONFIGS[1]]['covered'],n1=ss[CONFIGS[0]]['outputs'],nr=ss[CONFIGS[1]]['outputs'],n4=ss[CONFIGS[2]]['outputs']))
        lines+=[table(rows,[('seed','seed'),('first','固定1 bit'),('random','随机1–4 bit'),('fixed4','固定4 bit'),('d1','固定4−固定1'),('dr','固定4−随机'),('n1','固定1模型'),('nr','随机模型'),('n4','固定4模型')]),'']
        for kind,point,title in [('time',120,'120秒累计覆盖'),('time',10,'10秒早期覆盖'),('prefix',10,'严格前10模型'),('prefix',100,'严格前100模型'),('prefix',1000,'严格前1000模型'),('auc',120,'完整120秒AUC（bit·秒）')]:
            rs=[r|dict(comparison=LABELS[r['a']]+'−'+LABELS[r['b']],wtl=f"{r['wins']}/{r['ties']}/{r['losses']}") for r in summaries if r['formula']==formula and r['kind']==kind and r['point']==point]
            lines+=[title+'：','',table(rs,[('comparison','比较'),('complete_pairs','完整配对/7'),('incomplete_pairs','未达标配对'),('mean','均值'),('median','中位数'),('wtl','胜/平/负'),('maximum_regression','最大退步')]),'']
        cs=[r|dict(label=LABELS[r['configuration']]) for r in costsummary if r['formula']==formula]
        lines+=['实际干预与机制成本（计数为7次会话描述性合计，不作为独立重复）：','',table(cs,[('label','配置'),('queries','全部查询'),('eligible','预备启用'),('enabled_checks','实际挂钩查询'),('arrived','目标到达'),('selected','实际选择'),('flips','Flip'),('not_reached','启用但未到达'),('arrival_rate_all','到达/全部'),('selection_rate_all','选择/全部'),('flip_rate_all','Flip/全部'),('arrival_rate_eligible','到达/启用'),('quota_stops','额度停用会话'),('mechanism_seconds_mean','机制秒均值'),('feature_seconds_mean','扫描/特征秒均值'),('scan_attempts_mean','尝试原子数均值'),('statistics_cost_mean','新增诊断秒均值')]),'']
        ps=[r|dict(label=LABELS[r['configuration']]) for r in completion if r['formula']==formula and r['k']==1000];lines+=['前1000达标情况（时间仅描述已达标会话，所有未达标保留为NA）：','',table(ps,[('label','配置'),('attained','达标/7'),('unattained','未达到'),('time_mean','达标时间均值'),('time_median','达标时间中位数')]),'']
        bs=[]
        for label in ['1–2','3–9','10–99','100+']:
            rr={r['configuration']:r for r in binsummary if r['formula']==formula and r['bin']==label};bs.append(dict(bin=label,first=rr[CONFIGS[0]]['covered_mean'],random=rr[CONFIGS[1]]['covered_mean'],fixed4=rr[CONFIGS[2]]['covered_mean'],delta_random=rr[CONFIGS[2]]['covered_mean']-rr[CONFIGS[1]]['covered_mean']))
        lines+=['逐模型真实边际收益分箱：每会话贡献bit均值，不能把同一状态下的独立评分相加。','',table(bs,[('bin','收益箱'),('first','固定1'),('random','随机1–4'),('fixed4','固定4'),('delta_random','固定4−随机')]),'']
        bmap={(r['configuration'],r['bin']):r['covered_mean'] for r in binsummary if r['formula']==formula}
        small=sum(bmap[CONFIGS[2],b]-bmap[CONFIGS[1],b] for b in ['1–2','3–9','10–99']);large=bmap[CONFIGS[2],'100+']-bmap[CONFIGS[1],'100+']
        c4=next(r for r in cs if r['configuration']==CONFIGS[2]);cr=next(r for r in cs if r['configuration']==CONFIGS[1])
        lines += [f"固定4相对随机1–4，中小收益（1–99 bit）每会话贡献差 {fmt(small)} bit，100+事件贡献差 {fmt(large)} bit。这是实际顺序下的收益分解，不能用单一大事件替代完整累计评价。固定4有 {c4['not_reached']}/{c4['eligible']} 次启用查询未到达目标，随机策略为 {cr['not_reached']}/{cr['eligible']}；机制总耗时均值分别 {fmt(c4['mechanism_seconds_mean'])}/{fmt(cr['mechanism_seconds_mean'])} 秒，额度停用分别 {c4['quota_stops']}/{cr['quota_stops']} 个会话。",'']
        stopped=[r for r in sessions if r['formula']==formula and r['mechanism_stop']]
        if stopped:
            lines+=['机制停用逐例（首次记录，后续普通查询继续保留）：','',table([r|dict(label=LABELS[r['configuration']]) for r in stopped],[('seed','seed'),('label','配置'),('mechanism_stop','原因'),('stop_at_seconds','时刻/秒'),('stop_query','查询')]),'']
        else:lines+=['本公式21次会话均未因机制额度或其他机制条件永久停用；成本差异仍计入同一120秒预算。','']
        for a,b in PAIRS[:2]:
            r=next(r for r in summaries if r['formula']==formula and r['a']==a and r['b']==b and r['kind']=='time' and r['point']==120)
            lines+=[f"固定4对{LABELS[b]}：7seed终点差值中位数 {fmt(r['median'])} bit，胜/平/负 {r['wins']}/{r['ties']}/{r['losses']}，最大退步 {fmt(r['maximum_regression'])} bit。" ,'']
    lines+=['## 解释边界与下一步','', '本轮是完整策略对比。较后的位置、到达困难与扫描成本同时可能影响结果；到达后子集只供诊断，不替代完整策略统计。两个公式分别报告，7seed是同一公式的重复，不是7个新公式。前1000不足时，120秒数据无法回答严格前1000的优劣。', '', '收益应结合1–2、3–9、10–99和100+分箱判断，不能仅凭更大变量变化或孤立大跳跃解释。初始化首模型、授时轨迹、实际成本和额度停用在独立表中保留；即便总体差值为正，也不能仅归因于某一次软相位。H3目前仍只有低开销统计，缺少完整因果轨迹。', '', '固定4没有获得自动晋级。是否值得更长预算验证，需同时审阅逐公式seed配对、早期退步、未到达率和成本；本轮不自动改变参数或启动长实验。', '', '## 复现与证据','', '冻结协议：docs/固定第四位置120秒消融冻结协议.md。plan.json绑定输入、代码、Release/ASan二进制与依赖哈希；archive_manifest.json及archives保存完整原始模型、日志和逐线程资源证据。audit/*保存独立覆盖重放及每个模型身份。initialization*.csv、query_diagnostics.csv、intervention_costs.csv、gain_bins.csv、metrics.csv、paired_differences.csv、paired_summary.csv及prefix_completion.csv包含所有会话。', '', '```bash', '# 恢复本轮原始日志并离线重算；不生成模型', '/tmp/lia-highdiv-frozen/venv/bin/python3 scripts/archive_bofill_results.py results/position_fixed4_120s --restore', 'HIGHDIV_ROOT=/tmp/lia-highdiv-frozen PYTHONPATH=scripts:python /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/audit_position_fixed4.py results/position_fixed4_120s --jobs 15', 'HIGHDIV_ROOT=/tmp/lia-highdiv-frozen PYTHONPATH=scripts:python /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/analyze_position_fixed4.py results/position_fixed4_120s', '```','']
    report='\n'.join(lines);(out/'报告.md').write_text(report);(ROOT/'docs/固定第四位置120秒消融报告.md').write_text(report)
    dump(out/'analysis_summary.json',dict(sessions=42,formula_count=2,seeds=SEEDS,all_queries_retained=True,endpoint=[r for r in summaries if r['kind']=='time' and r['point']==120],first_1000= [r for r in completion if r['k']==1000],additional_experiments=False));return sessions

def main():
    p=argparse.ArgumentParser();p.add_argument('out',type=Path);a=p.parse_args();analyze(a.out.resolve())
if __name__=='__main__':main()
