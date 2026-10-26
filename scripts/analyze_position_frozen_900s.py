#!/usr/bin/env python3
"""Read-only exact step-curve, paired-prefix and observation-set analysis."""
import argparse,bisect,csv,gzip,json,math,statistics,hashlib,subprocess
from collections import Counter
from pathlib import Path
from reevaluate_round6_saved_candidates import dump,write_csv,require,file_hash
from highdiv_gap_common import add,covered,observed,obs_id,categories,dump_gz
from analyze_native_delivery_results import pack_export
ROOT=Path(__file__).resolve().parents[1]
TIMES=[1,5,10,30,60,120,180,300,450,600,750,900]
PREFIXES=[2,5,10,20,50,100,200,500,1000]
MODES=['first_immediate','position_immediate']

def csv_rows(path):
    with path.open() as f:return list(csv.DictReader(f))
def timeline(path):
    return [{k:(float(v) if k in ['completion','ready','wait'] else int(v) if k in ['index','generation_index','gain','covered','new_observations','observations','query'] else v) for k,v in r.items()} for r in csv_rows(path)]
def row_at(rows,t):
    i=bisect.bisect_right([r['completion'] for r in rows],t);return rows[i-1] if i else None
def prefix_at(rows,k):return rows[k-1] if len(rows)>=k and k>0 else None
def count_at(rows,t):
    r=row_at(rows,t);return (r['covered'],r['index']) if r else (0,0)
def exact_auc(rows,end=900):
    require(all(0<=r['completion']<=end for r in rows),'late/negative timestamp in AUC');require(all(a['completion']<=b['completion'] for a,b in zip(rows,rows[1:])),'AUC order')
    previous_t=previous_c=0;parts=[]
    for r in rows:
        require(r['covered']>=previous_c,'coverage decreased');parts.append(previous_c*(r['completion']-previous_t));previous_t=r['completion'];previous_c=r['covered']
    parts.append(previous_c*(end-previous_t));area=math.fsum(parts);alternative=math.fsum(r['gain']*(end-r['completion']) for r in rows)
    require(math.isclose(area,alternative,rel_tol=1e-12,abs_tol=1e-6),'AUC marginal identity');return area
def longest_plateau(rows,end=900):
    ts=[0]+[r['completion'] for r in rows if r['gain']>0]+[end];return max((b-a for a,b in zip(ts,ts[1:])),default=end)
def zero_segment(rows,field):
    best=[];current=[]
    for r in rows:
        if r[field]==0:
            current.append(r)
            if len(current)>len(best):best=current.copy()
        else:current=[]
    return dict(length=len(best),first_index=best[0]['index'] if best else None,last_index=best[-1]['index'] if best else None,start=best[0]['completion'] if best else None,end=best[-1]['completion'] if best else None)
def early_decomposition(first,position,t):
    cf,nf=count_at(first,t);cp,np=count_at(position,t);n=min(nf,np)
    f=first[n-1]['covered'] if n else 0;p=position[n-1]['covered'] if n else 0
    quality=p-f;supply=(cp-p)-(cf-f);require(quality+supply==cp-cf,'early decomposition')
    return dict(time=t,first_count=nf,position_count=np,common_prefix=n,delta=cp-cf,common_prefix_quality=quality,arrival_count_component=supply,interpretation='exact observed decomposition, not causal attribution')
def paired_stats(values):
    xs=[x for x in values if x is not None]
    return dict(pairs=len(xs),missing=len(values)-len(xs),wins=sum(x>0 for x in xs),ties=sum(x==0 for x in xs),losses=sum(x<0 for x in xs),mean=statistics.mean(xs) if xs else None,median=statistics.median(xs) if xs else None,seed_differences=values)
def decision_for(aggregates,formulas,resources):
    outcomes=[];risks=[]
    for formula in formulas:
        def find(metric,point):return next(r for r in aggregates if r['formula']==formula and r['metric']==metric and r['checkpoint']==point)
        end=find('time',900);good=end['pairs']==7 and end['wins']>=5 and end['median']>0;outcomes.append(dict(formula=formula,long_budget_signal=good,endpoint=end))
        for metric,k in [('time',10),('time',30),('time',60),('prefix',10)]:
            r=find(metric,k)
            if r['losses']>=4:risks.append(dict(formula=formula,metric=metric,checkpoint=k,losses=r['losses'],kind='repeated_early_regression'))
            if r['pairs']<7:risks.append(dict(formula=formula,metric=metric,checkpoint=k,kind='incomplete_evidence',missing=r['missing']))
    signal=all(x['long_budget_signal'] for x in outcomes)
    return dict(long_budget_signal_both_formulas=signal,classification='long_budget_gain_with_early_risk' if signal and risks else 'preliminary_long_budget_signal' if signal else 'unstable_or_formula_specific_no_promotion',formula_results=outcomes,early_risks=risks,resource_quality=resources,automatic_next_experiment=False)

def load_history(out,formula,layout,total):
    short='random' if 'random' in formula else 'real';base=ROOT/'results/highdiv_complementarity_gap/observations';names=['highdiv_'+short]+(['00_native_random_one','02_native_random_one'] if short=='random' else ['01_native_random_one','03_native_random_one'])
    expected=file_hash(out/'inputs'/formula);states=[];sources=[]
    for name in names:
        d=base/name;s=json.loads((d/'summary.json').read_text());require(s['formula_sha256']==expected and s['total']==total and json.loads((d/'layout.json').read_text())==layout,'historical formula/AST identity')
        states.append((int(s['zero'],16),int(s['one'],16)))
        for p in [d/'summary.json',d/'layout.json',d/'models.jsonl.gz']:
            relative=str(p.relative_to(ROOT));actual=file_hash(p);frozen=hashlib.sha256(subprocess.check_output(['git','show','091b6a7:'+relative],cwd=ROOT)).hexdigest()
            require(actual==frozen,'historical content changed after frozen report');sources.append(dict(path=relative,sha256=actual,frozen_commit='091b6a7'))
    hc=covered(states[0]);return hc,hc&~covered(states[1])&~covered(states[2]),sources

def analyze(out):
    plan=json.loads((out/'plan.json').read_text());audit=json.loads((out/'audit_summary.json').read_text());require(audit['passed'] and len(audit['runs'])==28,'complete audited matrix required');dest=out/'analysis';dest.mkdir(exist_ok=True)
    sessions=[];time_rows=[];prefix_rows=[];curves=[];cost_rows=[];query_rows=[];position_rows=[];runs={};checkpoints=[];history={};history_sources=[]
    resources={r['run']:r for r in csv_rows(out/'resource_audit.csv')}
    for audited in audit['runs']:
        name=audited['run'];d=out/'audit'/name;raw=out/'runs'/name/'raw';rows=timeline(d/'delivery.csv');generated=timeline(d/'generation.csv');s=json.loads((raw/'summary.json').read_text());layout=json.loads((d/'layout.json').read_text());resource=resources[name]
        common={k:audited[k] for k in ['run','group','formula','seed','configuration','total']};times={t:row_at(rows,t) for t in TIMES};needed={r['index'] for r in times.values() if r}|{k for k in PREFIXES if k<=len(rows)}|({len(rows)} if rows else set());snapshots={0:(0,0)};state=(0,0)
        formal_sources={r['generation_index']:r for r in rows}
        with gzip.open(d/'models.jsonl.gz','rt') as f:
            for line in f:
                model=json.loads(line)
                if model['index'] not in formal_sources:continue
                r=formal_sources[model['index']];state=add(state,(int(model['zero'],16),int(model['one'],16)))
                require(covered(state).bit_count()==r['covered'] and observed(state)==r['observations'],'formal observation replay mismatch')
                if r['index'] in needed:snapshots[r['index']]=state
        require(state==pack_export(json.loads((d/'coverage_final.json').read_text()),layout),'final state export')
        for index,st in snapshots.items():checkpoints.append(common|dict(index=index,zero=hex(st[0]),one=hex(st[1]),state_id=obs_id(st)))
        auc=exact_auc(rows);intervals=[b['completion']-a['completion'] for a,b in zip(rows,rows[1:])];zero=zero_segment(rows,'gain');zero_obs=zero_segment(rows,'new_observations');c600,n600=count_at(rows,600);counts=audited['counts']
        session=common|dict(formal_count=len(rows),generated_count=len(generated),covered=covered(state).bit_count(),observations=observed(state),coverage_fraction=covered(state).bit_count()/audited['total'],auc=auc,mean_coverage_over_time=auc/900,normalized_auc=auc/(900*audited['total']),first_model_time=rows[0]['completion'] if rows else None,first_positive_time=next((r['completion'] for r in rows if r['gain']>0),None),generation_first_positive_time=next((r['completion'] for r in generated if r['gain']>0),None),interval_median=statistics.median(intervals) if intervals else None,interval_max=max(intervals,default=None),longest_covered_plateau=longest_plateau(rows),longest_generation_plateau=longest_plateau(generated),zero_gain_length=zero['length'],zero_gain_first=zero['first_index'],zero_gain_last=zero['last_index'],zero_observation_length=zero_obs['length'],zero_observation_first=zero_obs['first_index'],zero_observation_last=zero_obs['last_index'],last300_models=len(rows)-n600,last300_covered=covered(state).bit_count()-c600,tail=audited['tail'],tail_covered_gap=audited['tail_covered_gap'],process_wall_seconds=float(resource['process_wall_seconds']),user_seconds=float(resource['user_seconds']),system_seconds=float(resource['system_seconds']),max_rss_kib=int(resource['max_rss_kib']),check_queries=s['queries'],sat=counts.get('sat',0),unknown=counts.get('unknown',0),unsat=counts.get('unsat',0),keep=counts.get('KeepNative',0),flip=counts.get('FlipOnce',0),position_eligible=counts.get('position_eligible',0),position_unselected=counts.get('target_not_reached',0),mechanism_seconds=s['mechanism_seconds'],delivery_management_seconds=s['delivery_management_seconds'],terminal=s['terminal'])
        sessions.append(session)
        for t,r in times.items():time_rows.append(common|dict(time=t,covered=r['covered'] if r else 0,count=r['index'] if r else 0,observations=r['observations'] if r else 0,last_complete=r['completion'] if r else None))
        for k in PREFIXES:
            r=prefix_at(rows,k);prefix_rows.append(common|dict(k=k,completed=r is not None,covered=r['covered'] if r else None,completion=r['completion'] if r else None,observations=r['observations'] if r else None))
        curves.extend(common|r for r in rows)
        for key,value in s['stats']['seconds'].items():cost_rows.append(common|dict(component=key,seconds=value,accounting='nested, not mutually exclusive; full wall time reported separately'))
        for key,value in counts.items():
            if key.startswith(('target_','reached_','not_selected_reason_')):position_rows.append(common|dict(event=key,count=value))
        for line in (raw/'queries.jsonl').read_text().splitlines():
            q=json.loads(line);e=q['evidence'];query_rows.append(common|dict(query=q['query'],start=q['start'],finish=q['finished'],raw_status=e['raw_status'],raw_unknown_reason=e['raw_reason'],classification=e['control_classification'],requested_ms=e['requested_timeout_ms'],granted_ms=e['granted_timeout_ms'],check_seconds=e['actual_check_seconds'],completion_seconds=e['completion_seconds'],validation_seconds=e['validation_seconds'],generation_scoring_seconds=q['generation_scoring_seconds'],preparation_seconds=q['preparation_seconds'],blocking_seconds=q['blocking_seconds'],budget_clip_reason=e['budget_clip_reason'],external_interrupt=e['external_interrupt'],session_deadline=e['session_deadline_reached'],selected=q['decision']['selected'],action=q['decision'].get('action'),generation_gain=q['generation_gain']))
        if audited['formula'] not in history:
            hc,oldgap,sources=load_history(out,audited['formula'],layout,audited['total']);history[audited['formula']]=(hc,oldgap);history_sources+=sources
        runs[audited['formula'],audited['seed'],audited['configuration']]=dict(common=common,rows=rows,session=session,snapshots=snapshots,state=state,layout=layout)
    pairs=[];early=[];sets=[];set_masks=[];category_rows=[];prefix_masks=[];gaps=[]
    for formula in [i['name'] for i in plan['inputs']]:
        hc,oldgap=history[formula]
        for seed in plan['seeds']:
            f,p=[runs[formula,seed,c] for c in MODES];common=dict(formula=formula,seed=seed,total=f['common']['total'],first_run=f['common']['run'],position_run=p['common']['run'])
            def pair(metric,k,fv,pv,**extra):
                complete=fv is not None and pv is not None;pairs.append(common|dict(metric=metric,checkpoint=k,completed=complete,first=fv,position=pv,delta=pv-fv if complete else None)|extra)
            for t in TIMES:
                cf,nf=count_at(f['rows'],t);cp,np=count_at(p['rows'],t);pair('time',t,cf,cp,first_count=nf,position_count=np)
                for mode,run,n in [(MODES[0],f,nf),(MODES[1],p,np)]:
                    mask=covered(run['snapshots'][n]);gaps.append(common|dict(configuration=mode,time=t,old_common_gap_size=oldgap.bit_count(),old_gap_filled=(mask&oldgap).bit_count(),highdiv_covered_seen=(mask&hc).bit_count(),highdiv_missing=(hc&~mask).bit_count()))
                if t in [10,30,60]:
                    e=early_decomposition(f['rows'],p['rows'],t)
                    for label,run in [('first',f),('position',p)]:
                        available=[r for r in run['rows'] if r['completion']<=t];ints=[b['completion']-a['completion'] for a,b in zip(available,available[1:])];e[label+'_first_model_time']=run['session']['first_model_time'];e[label+'_interval_median']=statistics.median(ints) if ints else None
                    early.append(common|e)
            for k in PREFIXES:
                fr=prefix_at(f['rows'],k);pr=prefix_at(p['rows'],k);pair('prefix',k,fr['covered'] if fr else None,pr['covered'] if pr else None,first_time=fr['completion'] if fr else None,position_time=pr['completion'] if pr else None,time_delta=pr['completion']-fr['completion'] if fr and pr else None)
                if fr and pr:
                    cf=covered(f['snapshots'][k]);cp=covered(p['snapshots'][k]);plus=cp&~cf;minus=cf&~cp
                    fc=categories(f['layout'],cf);pc=categories(f['layout'],cp);gc=categories(f['layout'],plus);lc=categories(f['layout'],minus)
                    all_categories={(n['kind'],band) for n in f['layout'] for band in (['Boolean'] if n['width']==1 else ['0-7','8-31','32-63'])}
                    for kind,band in sorted(all_categories):
                        key=(kind,band);category_rows.append(common|dict(k=k,node_kind=kind,bit_band=band,first=fc.get(key,0),position=pc.get(key,0),gained=gc.get(key,0),lost=lc.get(key,0),net=pc.get(key,0)-fc.get(key,0)))
                    require(sum(pc.values())-sum(fc.values())==pr['covered']-fr['covered'],'category partition')
                    prefix_masks.append(common|dict(k=k,position_only=hex(plus),first_only=hex(minus),layout_path='audit/'+f['common']['run']+'/layout.json'))
            for metric in ['auc','first_positive_time','longest_covered_plateau','formal_count','last300_models','last300_covered']:
                pair(metric,900,f['session'][metric],p['session'][metric])
            cf=covered(f['state']);cp=covered(p['state']);cu=covered(add(f['state'],p['state']));masks=dict(position_only=cp&~cf,first_only=cf&~cp,common=cf&cp,union_only_pairing=cu&~(cf|cp),highdiv_neither_individually=hc&~(cf|cp),highdiv_missing_after_union=hc&~cu,highdiv_first_missing=hc&~cf,highdiv_position_missing=hc&~cp)
            sets.append(common|{k:v.bit_count() for k,v in masks.items()}|dict(first_covered=cf.bit_count(),position_covered=cp.bit_count(),joint_covered=cu.bit_count(),position_new_zero_observations=(p['state'][0]&~f['state'][0]).bit_count(),position_new_one_observations=(p['state'][1]&~f['state'][1]).bit_count(),first_new_zero_observations=(f['state'][0]&~p['state'][0]).bit_count(),first_new_one_observations=(f['state'][1]&~p['state'][1]).bit_count(),historical_highdiv_covered=hc.bit_count(),historical_highdiv_seed=0))
            set_masks.append(common|{k:hex(v) for k,v in masks.items()}|dict(layout_path='audit/'+f['common']['run']+'/layout.json'))
    aggregates=[]
    for formula in [i['name'] for i in plan['inputs']]:
        keys=sorted({(r['metric'],r['checkpoint']) for r in pairs if r['formula']==formula})
        for metric,k in keys:
            rs=sorted([r for r in pairs if r['formula']==formula and r['metric']==metric and r['checkpoint']==k],key=lambda r:r['seed']);require([r['seed'] for r in rs]==plan['seeds'],'pair seed mix')
            aggregates.append(dict(formula=formula,metric=metric,checkpoint=k,**paired_stats([r['delta'] for r in rs]),seed_order=plan['seeds'],positive_direction='lower is better' if metric in ['first_positive_time','longest_covered_plateau'] else 'higher is better'))
    for name,rows in [('sessions',sessions),('time_points',time_rows),('prefixes',prefix_rows),('paired',pairs),('paired_summary',aggregates),('early_decomposition',early),('coverage_sets',sets),('prefix_categories',category_rows),('historical_gaps',gaps),('positions',position_rows),('costs',cost_rows),('queries',query_rows)]:write_csv(dest/(name+'.csv'),rows)
    dump_gz(dest/'full_curves.jsonl.gz',curves);dump_gz(dest/'state_checkpoints.jsonl.gz',checkpoints);dump_gz(dest/'coverage_set_masks.jsonl.gz',set_masks);dump_gz(dest/'prefix_difference_masks.jsonl.gz',prefix_masks);dump(dest/'historical_source_hashes.json',history_sources)
    decision=decision_for(aggregates,[i['name'] for i in plan['inputs']],audit['resources']);dump(dest/'decision.json',decision);dump(dest/'summary.json',dict(decision=decision,aggregates=aggregates,sessions=sessions,statistical_unit='formula/seed paired session; no candidate/query pseudo-replication',historical_highdiv='one prior seed per formula; descriptive only'))
    report(out,plan,decision,sessions,aggregates,pairs,early,sets)
    print(json.dumps(decision,ensure_ascii=False))

def table(headers,rows):return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(str(x) if x is not None else 'NA' for x in r)+' |' for r in rows])
def short(formula):return 'random' if 'random' in formula else 'real'
def conclusions(plan,decision,aggregates,sets):
    signal=decision['long_budget_signal_both_formulas'];risk=bool(decision['early_risks'])
    answer='达到两个公式各至少5/7胜出且中位差为正的初步长期改善标准' if signal else '未达到跨两个公式的初步长期改善标准，不晋级'
    lines=['## 本轮五项回答','',f'**900秒：{answer}。**'+(' 存在早期风险，结论限定为“长预算收益伴随早期风险”。' if signal and risk else ' 不能据此声称所有预算和前缀均有优势。'),'']
    for formula in [i['name'] for i in plan['inputs']]:
        rows=[r for r in aggregates if r['formula']==formula];medians=[next(r['median'] for r in rows if r['metric']=='time' and r['checkpoint']==t) for t in [120,300,600,900]]
        lines.append(f"**{short(formula)}的延续性：** 120/300/600/900秒中位差依次为{medians}；"+('这四个检查点均为正。' if all(m>0 for m in medians) else '其中存在非正中位差，不能称优势在这些检查点连续保持。'))
        prs=[r for r in rows if r['metric']=='prefix' and r['checkpoint'] in [10,100,1000]]
        lines.append('**同数量互补性：** '+'；'.join(f"前{r['checkpoint']}个：中位差{r['median'] if r['median'] is not None else 'NA'}，胜/平/负{r['wins']}/{r['ties']}/{r['losses']}，缺失{r['missing']}" for r in prs)+'。实际达到时间另表披露。')
        ss=[r for r in sets if r['formula']==formula];lines.append(f"**新观察内容：** {sum(r['position_only']>0 for r in ss)}/7条position轨迹取得first最终没有的covered位；{sum(r['first_only']>0 for r in ss)}/7条first轨迹也保有独有位。相较first，position的HighDiv已见证缺失集合在{sum(r['highdiv_position_missing']<r['highdiv_first_missing'] for r in ss)}/7配对中更小；这些是集合内容诊断，不是对HighDiv的同条件速度证明。")
    lines+=['','**早期权衡：** '+('按事先冻结规则检出了早期重复负差或未完成前缀，详见逐seed10/30/60秒和前10样本表。' if risk else '未触发预设的4/7早期重复退步标记；仍保留全部个别负差，不把未触发当成无风险证明。'),'','早期表使用首模型时间、实际模型间隔，以及覆盖差=共同数量质量项+到达数量项的恒等式帮助定位；不将同预算竞争中的相关变化说成独立因果效应。','']
    return lines
def report(out,plan,decision,sessions,aggregates,pairs,early,sets):
    def rows_for(metric,points):return [r for r in aggregates if r['metric']==metric and r['checkpoint'] in points]
    def summary_table(rows):return table(['公式','指标','胜/平/负','未完成','均值差','中位差','seed3–9差值'],[[short(r['formula']),str(r['checkpoint']),f"{r['wins']}/{r['ties']}/{r['losses']}",r['missing'],round(r['mean'],6) if r['mean'] is not None else None,round(r['median'],6) if r['median'] is not None else None,r['seed_differences']] for r in rows])
    text=['# 原生干预位置900秒新seed验证报告','',f"算法固定`75cd6b6`，当前冻结计划见`results/position_frozen_900s/plan.json`，28次900秒、每公式7个新seed。两种即时交付配置唯一主要区别为干预位置；没有批次排序、学习、HighDiv重跑或运行中调参。本轮由新的明确指引授权执行，未使用旧自动晋级门槛阻止。",'',f"本次{plan['max_concurrent_sessions']}路单线程采样，使用{len(plan['worker_physical_cores'])}个工作物理核心的SMT逻辑线程，监督器CPU {plan['supervisor_cpu']}、预留CPU {plan['reserved_cpu']}。完整组同步、批间屏障、第二批核心/配置轮换。不是14个独立物理核心实验；详细内存预算、先前RSS、拓扑和实际亲和性均归档。审计只在采样后进行，全部模型以冻结ManualSatisfiesMetric逐个验证，原AST、短路、低64位和去重口径未变。",'', '两个公式均为既有开发实例，新seed不构成新公式测试集。每个公式/seed配对是统计单位；共享机器和SMT竞争也限制了外推，不能把查询/候选数当作独立重复。','', '## 1. 新seed下900秒是否提高','',summary_table(rows_for('time',[900])),'', '冻结结论：'+decision['classification']+'。两个公式各至少5/7严格胜出且中位差为正才满足初步长期改善标准；即使满足，也不会自动开展下一轮实验。所有差值为position − first，单位covered bit。','', '## 2. 120秒优势是否延续','',summary_table(rows_for('time',[120,300,600,900])),'', '精确0–900秒AUC（bit·秒，使用全部正式提交事件的阶梯积分，与Σ增益×剩余时间交叉核对）：','',summary_table(rows_for('auc',[900])),'', '端点与AUC可能结论不同；以上逐seed序列用于检验中后期优势是否持续，不能仅凭900秒一列宣称全程更好。','', '## 3. 早期风险与到达/质量分解','',summary_table(rows_for('time',[10,30,60])),'','早期风险记录：`'+json.dumps(decision['early_risks'],ensure_ascii=False)+'`。重复退步阈值事先冻结为任一公式至少4/7负差；未完成前缀另标证据不足。','',table(['公式','seed','秒','覆盖差','共同前缀质量项','到达数量项','first/position模型数','first/position首模型时间'],[[short(r['formula']),r['seed'],r['time'],r['delta'],r['common_prefix_quality'],r['arrival_count_component'],f"{r['first_count']}/{r['position_count']}",f"{r['first_first_model_time']}/{r['position_first_model_time']}"] for r in early]),'', '两项逐条加总为时间覆盖差。共同前缀质量项比较相同数量已有模型，其余项来自该时刻不同输出数量；首模型延迟和模型间隔同时列出。这是观察到的精确分解，并不证明可独立改变某项而保持另一项不变。','', '## 4. 同样本数互补性及取样时间','',summary_table(rows_for('prefix',[10,100,1000])),'',table(['公式','seed','k','覆盖差','first达到时间','position达到时间','时间差'],[[short(r['formula']),r['seed'],r['checkpoint'],r['delta'],r.get('first_time'),r.get('position_time'),r.get('time_delta')] for r in pairs if r['metric']=='prefix' and r['checkpoint'] in [10,100,1000]]),'', '全部指定前缀2/5/10/20/50/100/200/500/1000保留在prefixes和paired表，未完成为NA，零收益样本未删除。Boolean、整数变量、算术表达式、整数常数以及低/中/高整数位交叉拆分见prefix_categories.csv；具体差异bit掩码绑定layout和源模型，未使用模型距离替代覆盖质量。','', '## 5. 新观察、互补集合与HighDiv历史缺口','',table(['公式','seed','position独有','first独有','共同','合并才覆盖','HighDiv双方各自未得','合并后仍缺'],[[short(r['formula']),r['seed'],r['position_only'],r['first_only'],r['common'],r['union_only_pairing'],r['highdiv_neither_individually'],r['highdiv_missing_after_union']] for r in sets]),'', '集合差由完整seen0/seen1重算；合并配对不等于两边covered集合的普通并集。position独有位说明这条保存轨迹取得了first没有获得的覆盖，但必须同时披露丢失位，不能将观察差异全部解释为净提升。historical_gaps.csv使用固定旧native两seed共同缺口，只作辅助诊断；历史HighDiv每公式仅一个seed，编译和并发条件不同，不是本轮严格配对对照。HighDiv内容仅在全部采样后读取。','', '## 高原、完整资源与成本','',table(['公式','seed','配置','模型数','最长无covered增长(s)','最长零增益/零新观察段','最后300s模型/covered','CPU用户/系统(s)','峰值RSS(KiB)','未交付'],[[short(r['formula']),r['seed'],r['configuration'],r['formal_count'],round(r['longest_covered_plateau'],3),f"{r['zero_gain_length']}/{r['zero_observation_length']}",f"{r['last300_models']}/{r['last300_covered']}",f"{r['user_seconds']}/{r['system_seconds']}",r['max_rss_kib'],r['tail']] for r in sessions]),'', 'sessions.csv还列首次正增益、生成侧高原、实际进程耗时与相位到达率；positions.csv列未选中真实原因，queries.csv保留逐查询授权、check、Unknown原始原因、补全验证与其他成本。costs.csv为嵌套成本项，不应直接相加；所有成本均占用同一个900秒墙钟预算。后期新增少、最长高原长时不能称已解决高原。','', '## 证据、曲线与重放','', '完整逐模型阶梯曲线：analysis/full_curves.jsonl.gz；时间点、前缀、集合差、来源哈希、原始模型归档及其SHA256均在results/position_frozen_900s。figures目录提供每seed曲线、精确差值阶梯和均值曲线的PNG/PDF；均值不代替逐seed证据。','', '```bash', '/tmp/lia-highdiv-frozen/venv/bin/python3 scripts/archive_bofill_results.py results/position_frozen_900s --restore', 'HIGHDIV_ROOT=/tmp/lia-highdiv-frozen /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/audit_position_frozen_900s.py results/position_frozen_900s --jobs 15', 'HIGHDIV_ROOT=/tmp/lia-highdiv-frozen /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/analyze_position_frozen_900s.py results/position_frozen_900s', 'python3 scripts/plot_position_frozen_900s.py results/position_frozen_900s', '```','', '重新采样必须使用新的输出目录，先--prepare冻结计划并提交，再--run；不得覆盖本轮目录。完整协议、哈希检查、测试及命令见docs/原生干预位置900秒新seed冻结协议.md。本轮至此结束；只有依据完整证据另行提出后续计划，不追加seed、不改参数、不自动运行HighDiv。']
    text[4:4]=conclusions(plan,decision,aggregates,sets)
    text+=['','## 覆盖曲线图','']
    for name in ['random_coverage','random_difference','real_coverage','real_difference']:
        text.append(f'![{name}](https://github.com/ChenKaichen-SCUT/lia_cover_sampler/raw/position-frozen-900s/results/position_frozen_900s/figures/{name}.png)')
    content='\n'.join(text)+'\n';(out/'报告.md').write_text(content);(ROOT/'docs/原生干预位置900秒新seed验证报告.md').write_text(content)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('out',type=Path);a=p.parse_args();analyze(a.out.resolve())
