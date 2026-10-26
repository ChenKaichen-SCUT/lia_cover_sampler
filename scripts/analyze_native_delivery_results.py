#!/usr/bin/env python3
"""Pre-frozen four-configuration comparisons of generated and delivered states."""
import argparse,bisect,csv,json,statistics
from pathlib import Path
from collections import Counter
from reevaluate_round6_saved_candidates import dump,write_csv,require
from highdiv_gap_common import load_gz,add,covered,observed,quantile
ROOT=Path(__file__).resolve().parents[1]
KS=[2,5,10,50,100,1000]
COMPARISONS=[('first_batch','first_immediate'),('position_immediate','first_immediate'),('position_batch','position_immediate'),('position_batch','first_immediate')]
def read(p):
    with p.open() as f:return list(csv.DictReader(f))
def plateau(rows,end,field):
    points=[0]+[float(r['completion']) for r in rows if int(r[field])>0]+[end]
    return max((b-a for a,b in zip(points,points[1:])),default=end)
def pack_export(export,layout):
    z=o=0
    for (zero,one),n in zip(export['states'],layout):z|=int(zero)<<n['offset'];o|=int(one)<<n['offset']
    return z,o
def gate_900(aggregates,configs,formulas):
    assessments=[]
    for mode in configs:
        if mode=='first_immediate':continue
        formula_scores=[];reasons=[]
        for formula in formulas:
            def find(track,dimension,k):return next((r for r in aggregates if r['formula']==formula and r['track']==track and r['dimension']==dimension and r['checkpoint']==k and r['left']==mode and r['right']=='first_immediate'),None)
            end=find('delivery','time',120)
            if not end or end['pairs']!=3 or end['wins']<2 or end['median_delta']<=0:reasons.append(formula+': 120s lacks repeated positive formal gain')
            else:formula_scores.append(end['median_delta']/end['total'])
            for t in [30,60]:
                row=find('delivery','time',t)
                if not row or row['pairs']!=3 or row['losses']>=2:reasons.append(formula+f': formal time {t}s regression or incomplete')
            for track in ['generation','delivery']:
                for k in [10,100]:
                    row=find(track,'prefix',k)
                    if not row or row['pairs']!=3 or row['losses']>=2:reasons.append(formula+f': {track} k={k} systematic regression or incomplete')
        assessments.append(dict(configuration=mode,qualified=not reasons,reasons=reasons,minimum_formula_median_relative_gain=min(formula_scores) if len(formula_scores)==2 else None))
    priority={'position_immediate':0,'position_batch':1,'first_batch':2};passed=[a for a in assessments if a['qualified']]
    passed.sort(key=lambda r:(-r['minimum_formula_median_relative_gain'],priority[r['configuration']]))
    return dict(enter_900=bool(passed),selected=passed[0]['configuration'] if passed else None,assessments=assessments,rule='frozen protocol, no parameter tuning or per-formula configuration selection')

def interpretation(plan,aggregates,paired,sessions,setdiff):
    lines=['','## 逐公式解释','']
    for formula in [i['name'] for i in plan['inputs']]:
        label='random' if 'random' in formula else 'real'
        for left,right in (COMPARISONS if plan['kind']!='long' else [(plan['configurations'][1],'first_immediate')]):
            rs=[r for r in aggregates if r['formula']==formula and r['left']==left and r['right']==right]
            def row(track,dimension,k):return next(r for r in rs if r['track']==track and r['dimension']==dimension and r['checkpoint']==k)
            end=row('delivery','time',plan['seconds']);g10=row('generation','prefix',10);d10=row('delivery','prefix',10);d100=row('delivery','prefix',100)
            def value(r):return 'NA（配对未完整）' if not r['pairs'] else f"中位差{r['median_delta']:+g}，胜/平/负{r['wins']}/{r['ties']}/{r['losses']}，未完成{r['incomplete_pairs']}"
            lines.append(f"**{label}，{left} − {right}：** {plan['seconds']}秒正式覆盖{value(end)}；原始候选前10为{value(g10)}；正式前10为{value(d10)}，前100为{value(d100)}。")
            pr=[r for r in paired if r['formula']==formula and r['left']==left and r['right']==right and r['track']=='delivery' and r['dimension']=='prefix' and r['checkpoint']==10 and r['completed']]
            if pr:lines.append('取得第10份正式样本的时间差（seed顺序，秒）：'+', '.join(f"{r['seed']}:{r['time_delta']:+.6f}" for r in pr)+'。')
            diff=[r for r in setdiff if r['formula']==formula and r['left']==left and r['right']==right and r['track']=='delivery']
            lines.append('终点新增/丢失 covered bit（seed顺序）：'+', '.join(f"{r['seed']}:{r['left_only']}/{r['right_only']}" for r in diff)+'；不能用补齐旧缺口代替本次净差。')
            prefix_positive=any(r['pairs'] and r['median_delta']>0 for r in [d10,d100])
            if left=='first_batch' or right=='position_immediate':
                if prefix_positive and end['median_delta']<=0:lines.append('判断：有同数量前缀改善信号，但没有同时得到正的终点中位净收益；只能保留为交付顺序的权衡，不能称生成器变强。')
                elif prefix_positive:lines.append('判断：观察到正式前缀与终点的正中位差；是否可重复仍以各seed胜负及冻结跨公式门槛为准。真实限时下候选流可能不同，不能把终点差单独归因于排序。')
                else:lines.append('判断：本次未在正式前10/100的中位数上证明这项排序实现有收益；这不推翻历史保存池的离线组合潜力。')
            elif left=='position_immediate':
                if plan['kind']=='screen' and end['wins']>=2 and end['median_delta']>0:lines.append('判断：该公式达到2/3 seed的有限正面时间覆盖信号；同时检查原始前缀退步。位置变化也改变了实际干预频率，本轮不能拆成后续原子价值的因果结论。')
                else:lines.append('判断：依据本阶段数据逐seed报告位置策略效果，不将一次正差当作跨公式稳定改善。')
            elif left=='position_batch':lines.append('判断：组合需同时通过终点、早期时间与原始/正式前缀约束；不因为两个功能均已实现就默认保留组合。')
            lines.append('')
    if plan['kind']=='exposure':lines.append('本节曝光差值仅为记录，不用于调参或取消完整120秒筛选。')
    return lines

def analyze(out):
    plan=json.loads((out/'plan.json').read_text());audit=json.loads((out/'audit_summary.json').read_text());require(audit['passed'],'audit required');dest=out/'analysis';dest.mkdir(exist_ok=True)
    sessions=[];prefixes=[];times=[];stall_rows=[];coverage_states={};historical=[];position_rows=[];cost_rows=[];reason_rows=[]
    timeset=[t for t in [1,5,10,30,60,120,300,900] if t<=plan['seconds']]
    for run in audit['runs']:
        name=run['run'];d=out/'audit'/name;gen=read(d/'generation.csv');delivery=read(d/'delivery.csv');batches=read(d/'batches.csv');pos=read(d/'positions.csv');raw=out/'runs'/name/'raw';s=json.loads((raw/'summary.json').read_text());process=json.loads((out/'runs'/name/'process_result.json').read_text())
        common={k:run[k] for k in ['formula','seed','configuration','seconds','group','run','total']}
        waits=[float(r['wait']) for r in delivery];finish=run['seconds'];bygen={int(r['index']):r for r in gen}
        for track,rows in [('generation',gen),('delivery',delivery)]:
            for k in KS:
                r=rows[k-1] if len(rows)>=k else None
                prefixes.append(common|dict(track=track,k=k,completed=r is not None,covered=int(r['covered']) if r else None,observations=int(r['observations']) if r else None,completion=float(r['completion']) if r else None,generated_when_delivered=int(r['generated_when_delivered']) if r and track=='delivery' else k if r else None))
            for t in timeset:
                available=[r for r in rows if float(r['completion'])<=t];last=available[-1] if available else None
                times.append(common|dict(track=track,time=t,covered=int(last['covered']) if last else 0,observations=int(last['observations']) if last else 0,count=len(available)))
        layout=json.loads((d/'layout.json').read_text());gstate=pack_export(json.loads((d/'generation_final.json').read_text()),layout);dstate=pack_export(json.loads((d/'coverage_final.json').read_text()),layout);coverage_states[name]=dict(generation=gstate,delivery=dstate)
        models=load_gz(d/'models.jsonl.gz');ob={r['index']:(int(r['zero'],16),int(r['one'],16)) for r in models};events=[]
        events.extend((float(r['completion']),0,int(r['index'])) for r in gen);events.extend((float(r['completion']),1,int(r['generation_index'])) for r in delivery);events.sort()
        gp=dp=(0,0);last=0;lag_covered=lag_obs=0;longest_lag=0;lag_start=None
        for t,track,index in events+[(finish,2,0)]:
            gap=covered(gp)&~covered(dp);ogap=observed(gp)-observed(dp)
            if gap:lag_covered+=t-last
            if ogap:lag_obs+=t-last
            if gap and lag_start is None:lag_start=last
            if not gap and lag_start is not None:longest_lag=max(longest_lag,last-lag_start);lag_start=None
            if t>last:stall_rows.append(common|dict(start=last,end=t,duration=t-last,generated_covered=covered(gp).bit_count(),delivered_covered=covered(dp).bit_count(),pending_covered=gap.bit_count(),pending_observations=ogap,classification='pending_delivery_information' if ogap else 'no_pending_information'))
            if track==0:gp=add(gp,ob[index])
            elif track==1:dp=add(dp,ob[index])
            last=t
        if lag_start is not None:longest_lag=max(longest_lag,finish-lag_start)
        both_positive=sorted({0,finish,*[float(r['completion']) for r in gen+delivery if int(r['gain'])>0]});both_stall=max((b-a for a,b in zip(both_positive,both_positive[1:])),default=finish)
        counts=run['counts'];eligible=counts.get('position_eligible',0)
        for component,value in s['stats']['seconds'].items():cost_rows.append(common|dict(component=component,seconds=value,accounting='nested components; do not sum as disjoint wall time'))
        for key,value in counts.items():
            if key.startswith('not_selected_reason_'):reason_rows.append(common|dict(reason=key.removeprefix('not_selected_reason_'),queries=value))
        sessions.append(common|dict(generated_count=run['generated'],formal_count=run['delivered'],generation_covered=run['generated_covered'],covered=run['covered'],process_wall_seconds=process['process_wall_seconds'],sampler_elapsed=s['elapsed'],sat=counts.get('sat',0),unknown=counts.get('unknown',0),unsat=counts.get('unsat',0),keep=counts.get('KeepNative',0),flip=counts.get('FlipOnce',0),actual_phase_changes=counts.get('FlipOnce',0),target_not_reached=counts.get('target_not_reached',0),position_eligible=eligible,not_reached_fraction=counts.get('target_not_reached',0)/eligible if eligible else None,wait_median=quantile(waits,.5),wait_p95=quantile(waits,.95),wait_max=max(waits,default=None),wait_above_trigger=sum(x>.5 for x in waits),management_seconds=s['delivery_management_seconds'],management_fraction=s['delivery_management_seconds']/finish,common_delivery_seconds=s['stats']['seconds']['delivery_common_processing'],write_seconds=s['stats']['seconds']['model_and_receipt_write'],delivery_all_seconds=s['stats']['seconds']['delivery_all'],mechanism_seconds=s['mechanism_seconds'],fifo_batches=s['fifo_batches'],quota_fallbacks=s['quota_fallbacks'],undelivered=run['tail'],tail_covered_gap=run['tail_covered_gap'],tail_observation_gap=run['tail_observation_gap'],generation_no_covered_longest=plateau(gen,finish,'gain'),delivery_no_covered_longest=plateau(delivery,finish,'gain'),generation_no_observation_longest=plateau(gen,finish,'new_observations'),delivery_no_observation_longest=plateau(delivery,finish,'new_observations'),both_no_covered_longest=both_stall,pending_coverage_seconds=lag_covered,pending_observation_seconds=lag_obs,longest_pending_coverage_interval=longest_lag,threshold_delayed_by_check_batches=sum(r['delay_explanation']=='active_query' and float(r['trigger_delay'])>0 for r in batches),timely_model_records_exact=True))
        for k in range(1,5):position_rows.append(common|dict(target=k,queries=counts.get('target_'+str(k),0),reached=counts.get('reached_'+str(k),0)))
        # Historical information is opened ONLY by this post-sampling analyzer.
        old=ROOT/'results/highdiv_complementarity_gap';short='random' if 'random' in run['formula'] else 'real';H='highdiv_'+short;h=json.loads((old/'observations'/H/'summary.json').read_text());hc=int(h['zero'],16)&int(h['one'],16)
        names=['00_native_random_one','02_native_random_one'] if short=='random' else ['01_native_random_one','03_native_random_one'];old_states=[json.loads((old/'observations'/n/'summary.json').read_text()) for n in names];gaps=[hc&~(int(x['zero'],16)&int(x['one'],16)) for x in old_states]
        expected_hash=next(i['sha256'] for i in plan['inputs'] if i['name']==run['formula'])
        for old_name,old_state in [(H,h),*zip(names,old_states)]:
            require(old_state['formula_sha256']==expected_hash and old_state['total']==run['total'],'historical formula/denominator mismatch')
            require(json.loads((old/'observations'/old_name/'layout.json').read_text())==layout,'historical original AST layout mismatch')
        target=gaps[run['seed']] if run['seed']<2 else gaps[0]&gaps[1]
        for track,state in [('generation',gstate),('delivery',dstate)]:historical.append(common|dict(track=track,reference='same_seed_old_native_gap' if run['seed']<2 else 'intersection_of_old_seed0_and_seed1_gaps',target_bits=target.bit_count(),covered_target=(covered(state)&target).bit_count(),missing_target=(target&~covered(state)).bit_count(),highdiv_historical_seed=0,highdiv_current_run=False))
    comparisons=COMPARISONS if plan['kind']!='long' else [(plan['configurations'][1],'first_immediate')]
    # All candidate-vs-baseline pairs are retained for the frozen promotion gate.
    gate_pairs=[(c,'first_immediate') for c in plan['configurations'] if c!='first_immediate'];pairs=list(dict.fromkeys(comparisons+gate_pairs));paired=[];setdiff=[]
    for collection,dimension,field in [(prefixes,'prefix','k'),(times,'time','time')]:
        groups={}
        for r in collection:groups.setdefault((r['formula'],r['seed'],r['track'],r[field]),{})[r['configuration']]=r
        for (formula,seed,track,k),rs in groups.items():
            for left,right in pairs:
                x,y=rs[left],rs[right];ok=dimension=='time' or x['completed'] and y['completed']
                paired.append(dict(formula=formula,seed=seed,track=track,dimension=dimension,checkpoint=k,left=left,right=right,completed=ok,left_value=x['covered'] if ok else None,right_value=y['covered'] if ok else None,delta=x['covered']-y['covered'] if ok else None,time_delta=x['completion']-y['completion'] if ok and dimension=='prefix' else None,left_run=x['run'],right_run=y['run'],total=x['total']))
    aggregates=[]
    keys=['formula','track','dimension','checkpoint','left','right'];groups={}
    for r in paired:groups.setdefault(tuple(r[k] for k in keys),[]).append(r)
    for key,rs in groups.items():
        ds=[r['delta'] for r in rs if r['completed']];aggregates.append(dict(zip(keys,key))|dict(pairs=len(ds),incomplete_pairs=len(rs)-len(ds),wins=sum(x>0 for x in ds),ties=sum(x==0 for x in ds),losses=sum(x<0 for x in ds),mean_delta=statistics.mean(ds) if ds else None,median_delta=statistics.median(ds) if ds else None,total=rs[0]['total']))
    for formula in dict.fromkeys(r['formula'] for r in sessions):
        for seed in plan['seeds']:
            rs={r['configuration']:r for r in sessions if r['formula']==formula and r['seed']==seed}
            for left,right in comparisons:
                x,y=rs[left],rs[right]
                for track in ['generation','delivery']:
                    a,b=coverage_states[x['run']][track],coverage_states[y['run']][track];ca,cb=covered(a),covered(b);uc=covered(add(a,b));setdiff.append(dict(formula=formula,seed=seed,left=left,right=right,track=track,left_only=(ca&~cb).bit_count(),right_only=(cb&~ca).bit_count(),intersection=(ca&cb).bit_count(),union_only_pairing=(uc&~(ca|cb)).bit_count(),left_observations_only=(a[0]&~b[0]).bit_count()+(a[1]&~b[1]).bit_count(),right_observations_only=(b[0]&~a[0]).bit_count()+(b[1]&~a[1]).bit_count()))
    for name,rows in [('sessions',sessions),('prefixes',prefixes),('time_points',times),('paired',paired),('paired_summary',aggregates),('final_set_differences',setdiff),('positions',position_rows),('position_unselected_reasons',reason_rows),('costs',cost_rows),('stall_intervals',stall_rows),('historical_gaps',historical)]:write_csv(dest/(name+'.csv'),rows)
    decision=gate_900(aggregates,plan['configurations'],[i['name'] for i in plan['inputs']]) if plan['kind']=='screen' else dict(enter_900=False,selected=None,reason='exposure does not select parameters or cancel screen' if plan['kind']=='exposure' else 'long verification complete; no further experiments')
    decision.update(binary_sha256=plan['files']['build/native_delivery/release/native_delivery_sampler'],stage=plan['kind'],audit_passed=True,frozen_files=plan['files']);dump(dest/'decision.json',decision)
    dump(dest/'summary.json',dict(stage=plan['kind'],sessions=sessions,paired_summary=aggregates,decision=decision,unit_of_replication='one formula/seed session, not candidates or batches'))
    def table(headers,rows):return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(str(v) for v in row)+' |' for row in rows])+'\n'
    text=['# 有界交付与干预位置实验报告：'+plan['kind'],'',f'基点ba9cca4；算法冻结提交 `{plan["git_commit"]}`。两个开发公式，{len(sessions)}个会话，预算{plan["seconds"]}秒。四物理核同步配对，组间屏障；HighDiv未运行。所有正式统计只使用及时写完模型及提交收据，生成状态与交付状态分别通过原评测器审计。', '',table(['公式','seed','配置','生成数/覆盖','正式数/覆盖','等待p95(s)','管理耗时(s)','未交付'],[[('random' if 'random' in r['formula'] else 'real'),r['seed'],r['configuration'],f'{r["generated_count"]}/{r["generation_covered"]}',f'{r["formal_count"]}/{r["covered"]}',f'{r["wait_p95"]:.6f}' if r['wait_p95'] is not None else 'NA',f'{r["management_seconds"]:.6f}',r['undelivered']] for r in sessions]),'','## 四个独立比较（正式覆盖）','']
    rows=[r for r in aggregates if r['track']=='delivery' and (r['left'],r['right']) in comparisons and (r['dimension']=='time' and r['checkpoint']==plan['seconds'] or r['dimension']=='prefix' and r['checkpoint'] in [10,100])]
    text.append(table(['公式','比较','指标','胜/平/负','均值差','中位差','未完成配对'],[[('random' if 'random' in r['formula'] else 'real'),r['left']+' − '+r['right'],r['dimension']+' '+str(r['checkpoint']),f'{r["wins"]}/{r["ties"]}/{r["losses"]}',r['mean_delta'] if r['mean_delta'] is not None else 'NA',r['median_delta'] if r['median_delta'] is not None else 'NA',r['incomplete_pairs']] for r in rows]))
    text+=interpretation(plan,aggregates,paired,sessions,setdiff)
    text+=['','## 前10份交付的实际工作量','',table(['公式','seed','配置','取得时间(s)','届时已生成候选'],[[('random' if 'random' in r['formula'] else 'real'),r['seed'],r['configuration'],r['completion'] if r['completed'] else 'NA',r['generated_when_delivered'] if r['completed'] else 'NA'] for r in prefixes if r['track']=='delivery' and r['k']==10]),'', '## 实际干预与等待','',table(['公式','seed','配置','Keep/Flip','有资格/未选中','等待最大(s)','跨阈值check批次','20ms或5%回退批次'],[[('random' if 'random' in r['formula'] else 'real'),r['seed'],r['configuration'],f'{r["keep"]}/{r["flip"]}',f'{r["position_eligible"]}/{r["target_not_reached"]}',r['wait_max'],r['threshold_delayed_by_check_batches'],r['fifo_batches']] for r in sessions]),'未选中的详细原因见`analysis/position_unselected_reasons.csv`，包括目标未到达与机制预算停止，不能把所有未选中都解释为没有后续原子。初始化、评分、阻塞、求解与记录成本见`analysis/costs.csv`和原始逐查询日志；嵌套成本不直接相加，完整进程时间另列。']
    report_decision={k:v for k,v in decision.items() if k!='frozen_files'}
    text+=['','## A：交付排序与代价','', '批次没有丢弃零收益候选，逐次交付后按完整状态重新评分。完整排空的每批和会话均核对seen0/seen1，而非只对covered数。`prefixes.csv`逐条列出前k正式输出实际时间，以及当时已经生成多少候选；这些候选的全部加载、查询、准备、验证、记录和管理成本都在会话墙钟里。若仅正式前缀改善而原始前缀未改善，只称交付排序收益。', '0.5秒是查询边界处触发阈值，不能抢占正在进行的check。逐批`audit/*/batches.csv`列真实延迟与跨阈值查询，`sessions.csv`给等待中位/p95/最大及超阈值数量。管理成本与模型/收据写出、通用交付处理分列；5%保护是管理成本上限触发，不承诺不会发生一次操作的越界。', '', '## C：干预位置与新增信息','', '位置流和Keep/Flip流独立审计；每次check最多一次机会，Keep也消耗机会，32决策窗口不扩大。`positions.csv`按目标1/2/3/4列次数和到达；`sessions.csv`列未到达、Keep、Flip和实际相位改变数。位置策略可能减少干预频率，因此正差也不能单独因果归于后续原子更有价值。', '`final_set_differences.csv`给出新配置相对本次基线独有/丢失/共同covered及仅合并配对。`historical_gaps.csv`只在采样后引用旧HighDiv合法见证；seed0/1用各自旧缺口，新seed使用旧两seed缺口交集。补齐旧缺口不抵消总体覆盖的退步，历史HighDiv也不是多个独立重复。', '', '## 两侧高原与组合','', '`stall_intervals.csv`保存真实时间段及生成/交付完整状态差：内部已有而尚未交付的信息单列。分别统计生成侧与交付侧无covered/无观察增长的最长时间、两侧同时无covered增长最长时间及待交付信息延迟；批次跳跃不解释为某次求解突然生成大收益模型。真实限时下管理成本会影响剩余求解时间，未强求候选流一致；只有固定操作额度回归证明切换交付策略不主动改变生成输入与阻塞顺序。','', '## 冻结决定','', '```json',json.dumps(report_decision,ensure_ascii=False,indent=2),'```','', '曝光只验证功能，不按覆盖输赢调参或取消筛选。正式筛选只按预冻结规则决定是否运行一次seed3/4的900秒验证；不满足即停止，不扩大B、W或K寻找正例。组合版不是默认最终版本，四组效应分别保留。','', '完整命令、构建与冻结条件见仓库 `docs/有界交付与干预位置实验协议.md`；本目录plan绑定源文件、二进制、库及输入哈希，raw在独立归档中。未完成前缀为NA，未删除失败/零收益会话。']
    (out/'报告.md').write_text('\n'.join(text)+'\n');print(json.dumps(decision),flush=True)
    return decision
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('out',type=Path);a=p.parse_args();analyze(a.out.resolve())
