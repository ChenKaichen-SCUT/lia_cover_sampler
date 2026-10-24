#!/usr/bin/env python3
"""Report round-six actions and initialized sessions; never start experiments."""
import argparse,csv,hashlib,json
from collections import defaultdict
from pathlib import Path
from statistics import mean,median

PREP=('target_selection_seconds','dependency_seconds','pack_selection_seconds','conflict_expansion_seconds','target_encoding_seconds','fixed_constraint_seconds','seed_evaluation_seconds')
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,d):Path(p).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def csvfile(p,rows):
    with Path(p).open('w',newline='') as f:
        w=csv.DictWriter(f,list(dict.fromkeys(k for r in rows for k in r)));w.writeheader();w.writerows(rows)
def alias(s):return 'Bromberger' if 'Bromberger' in s else 'SMT_random' if 'SMT_random' in s else 'convert'

def tables(rows,audits):
    calls=[];sessions=[]
    for r in rows:
        ok=r['success'] and audits.get(r['output'],{}).get('passed',False)
        s={k:r.get(k) for k in ('snapshot','instance','prefix','seed','repeat','mode','order','process_wall_seconds','cpu_seconds','output')}
        s.update(r.get('summary',{}));s['audited']=ok;s['score_sum']=0 if ok else None;s['no_candidate']=0 if ok else None
        for index in range(3):
            item=next((a for a in r.get('actions',[]) if a['action']==index),None)
            c={k:s[k] for k in ('snapshot','instance','prefix','seed','repeat','mode','order','output')};c.update(action=index,audited=bool(ok and item))
            if item:
                result=item['result'];counts=item['statistics'].get('counts',{});times=item['statistics'].get('seconds',{})
                c.update({k:v for k,v in result.items() if k not in ('queries','releases','primary')})
                c.update(primary=json.dumps(result['primary']),target_seed=item['target_seed'],budget_seconds=item['budget_seconds'],
                    delivered=result['checkpoint_seconds'] is not None,positive=result['gain']>0,
                    action_efficiency=result['gain']/max(1e-9,result['full_seconds']),
                    initialized_action_seconds=result['full_seconds']+s['initialization_seconds']/3,
                    initialized_action_efficiency=result['gain']/max(1e-9,result['full_seconds']+s['initialization_seconds']/3),
                    preparation_seconds=sum(times.get(k,0) for k in PREP),query_attempts=len(result['queries']),
                    solver_checks=counts.get('global_calls',0),fixed_definitions=counts.get('fixed_definitions',0),
                    restricted_queries=sum(q['restricted'] for q in result['queries']),
                    local_fixed_conflicts=sum(bool(q['fixed_core']) for q in result['queries']),
                    exclusion_conflicts=sum(q['exclusion_core'] for q in result['queries']),
                    global_target_proofs=sum(q['global_proof'] for q in result['queries']),
                    nonproof_queries=sum(q['status'] in ('timeout','unknown','budget_exhausted','resource_exhausted','cancelled') for q in result['queries']),
                    returned_after_deadline=result['full_seconds']>item['budget_seconds'],
                    checkpoint_before_later_failure=result['checkpoint_seconds'] is not None and result['status']!='sat',
                    timeout_queries=sum(q['status']=='timeout' for q in result['queries']))
                for name,value in times.items():c['seconds_'+name]=value
                for name,value in counts.items():c['count_'+name]=value
                if ok:s['score_sum']+=result['gain'];s['no_candidate']+=not c['delivered']
            else:c.update(gain=None,full_seconds=None,delivered=False,positive=False,primary=None)
            calls.append(c)
        if ok:
            s['score_per_session_second']=s['score_sum']/max(1e-9,s['session_seconds'])
            s['score_per_process_second']=s['score_sum']/max(1e-9,s['process_wall_seconds'])
        sessions.append(s)
    return calls,sessions

def analyze(calls,sessions):
    groups=defaultdict(dict)
    for c in calls:
        k=(c['snapshot'],c['seed'],c['repeat'],c['action']);assert c['mode'] not in groups[k];groups[k][c['mode']]=c
    pairs=[]
    for k,d in sorted(groups.items()):
        a,b=d.get('global'),d.get('block');v=a or b;ok=bool(a and b and a['audited'] and b['audited'])
        p=dict(snapshot=k[0],instance=v['instance'],seed=k[1],repeat=k[2],action=k[3],audited=ok,
            same_primary=bool(a and b and a['primary']==b['primary']))
        for m,row in (('global',a),('block',b)):
            for f in ('gain','full_seconds','initialized_action_seconds','positive','delivered','action_efficiency','initialized_action_efficiency'):
                p[m+'_'+f]=row.get(f) if row else None
        p['gain_delta']=b['gain']-a['gain'] if ok else None
        p['cost_delta']=b['full_seconds']-a['full_seconds'] if ok else None
        pairs.append(p)
    grouped=defaultdict(list)
    for p in pairs:grouped[p['snapshot'],p['seed'],p['action']].append(p)
    units=[]
    for k,rows in sorted(grouped.items()):
        ok=len(rows)==2 and {r['repeat'] for r in rows}=={0,1} and all(r['audited'] and r['same_primary'] for r in rows)
        u=dict(snapshot=k[0],instance=rows[0]['instance'],seed=k[1],action=k[2],complete=ok)
        for f in ('gain_delta','cost_delta','global_gain','block_gain','global_full_seconds','block_full_seconds'):
            u['mean_'+f]=mean(r[f] for r in rows) if ok else None
        units.append(u)
    valid=[u for u in units if u['complete']];delta=[u['mean_gain_delta'] for u in valid]
    cases=[];states=[]
    for field,out in (('instance',cases),('snapshot',states)):
        for name in sorted({u[field] for u in units}):
            g=[u for u in valid if u[field]==name]
            out.append({field:name,'units':len(g),'global_mean_gain':mean(u['mean_global_gain'] for u in g) if g else None,
                'block_mean_gain':mean(u['mean_block_gain'] for u in g) if g else None,'mean_delta':mean(u['mean_gain_delta'] for u in g) if g else None})
    modes=[]
    for mode in ('global','block'):
        g=[c for c in calls if c['mode']==mode];s=[s for s in sessions if s['mode']==mode]
        ok=all(r['audited'] for r in g+s);row=dict(mode=mode,actions=len(g),sessions=len(s),all_audited=ok,
            no_candidate=sum(not c['delivered'] for c in g),no_positive=sum(not c['positive'] for c in g))
        fields=('gain','full_seconds','initialized_action_seconds','action_efficiency','initialized_action_efficiency',
                'initial_block','final_block','variables','expansions','validated_candidates','duplicates','late_candidates','branch_changes','preparation_seconds',
                'solver_checks','query_attempts','fixed_definitions','restricted_queries','local_fixed_conflicts','exclusion_conflicts','global_target_proofs','nonproof_queries',
                'returned_after_deadline','checkpoint_before_later_failure','timeout_queries')
        for f in fields:row['mean_'+f]=mean(c.get(f,0) for c in g) if ok else None;row['sum_'+f]=sum(c.get(f,0) for c in g) if ok else None
        for f in ('formula_initialization_seconds','snapshot_restore_seconds','engine_initialization_seconds','initialization_seconds','session_seconds','process_wall_seconds','cpu_seconds','score_per_session_second','score_per_process_second'):
            row['mean_'+f]=mean(x[f] for x in s) if ok else None
        first=[c['first_positive_seconds'] for c in g if c.get('first_positive_seconds') is not None]
        row['positive_latency_count']=len(first);row['conditional_mean_first_positive']=mean(first) if first else None
        row['fallback_actions']=sum(bool(c.get('fallback_reason')) for c in g)
        modes.append(row)
    a,b=modes
    complete=len(calls)==216 and len(sessions)==72 and len(valid)==len(units)==54 and all(x['audited'] for x in calls)
    common=dict(all_data_complete=complete,same_primary_all_pairs=all(p['same_primary'] for p in pairs),no_candidate_rate_not_worse=b['no_candidate']<=a['no_candidate'])
    gain=dict(mean_delta_positive=bool(delta) and mean(delta)>0,median_delta_nonnegative=bool(delta) and median(delta)>=0,
        two_thirds_nonnegative=bool(delta) and sum(d>=0 for d in delta)>=2*len(delta)/3,
        two_positive_cases=sum((r['mean_delta'] or 0)>0 for r in cases)>=2,two_positive_snapshots=sum((r['mean_delta'] or 0)>0 for r in states)>=2,
        full_session_efficiency_better=complete and b['mean_score_per_process_second']>a['mean_score_per_process_second'])
    equal=[u for u in valid if u['mean_global_gain']==u['mean_block_gain'] and u['mean_global_gain']>0]
    cost_ratio=mean(u['mean_block_full_seconds'] for u in equal)/max(1e-9,mean(u['mean_global_full_seconds'] for u in equal)) if equal else None
    faster_cases=set();faster_states=set()
    for field,dest in (('instance',faster_cases),('snapshot',faster_states)):
        for name in {u[field] for u in equal}:
            g=[u for u in equal if u[field]==name]
            if mean(u['mean_block_full_seconds'] for u in g)<=.85*mean(u['mean_global_full_seconds'] for u in g):dest.add(name)
    cost=dict(all_unit_coverage_noninferior=bool(delta) and all(d>=0 for d in delta),same_positive_coverage_cost_lower_15pct=cost_ratio is not None and cost_ratio<=.85,
        initialized_full_session_cost_lower_15pct=complete and b['mean_process_wall_seconds']<=.85*a['mean_process_wall_seconds'],
        two_faster_cases=len(faster_cases)>=2,two_faster_snapshots=len(faster_states)>=2)
    return dict(units=len(units),valid_units=len(valid),mean_gain_delta=mean(delta) if delta else None,median_gain_delta=median(delta) if delta else None,
        positive_units=sum(d>0 for d in delta),zero_units=sum(d==0 for d in delta),negative_units=sum(d<0 for d in delta),
        common_checks=common,gain_checks=gain,cost_checks=cost,same_positive_coverage_units=len(equal),same_positive_cost_ratio=cost_ratio,
        recommend_continue=all(common.values()) and (all(gain.values()) or all(cost.values())),
        modes=modes,cases=cases,states=states,pairs=pairs,unit_details=units,
        interpretation='Scores are repeated fixed-S action evaluations, not cumulative coverage of an online sample stream; session actions are correlated',no_automatic_followup=True)

def report(out):
    out=Path(out);done=json.loads((out/'COMPLETE.json').read_text());assert all(digest(out/p)==h for p,h in done['bindings'].items())
    plan=json.loads((out/'plan.json').read_text());audit=json.loads((out/'AUDIT_COMPLETE.json').read_text());timed=json.loads((out/'TIMED_COMPLETE.json').read_text())
    assert audit['started_utc']>=timed['finished_utc']
    assert all(digest(out/p)==h for p,h in audit['audit_files_sha256'].items())
    audits={r['output']:r for p in audit['audit_files_sha256'] for r in json.loads((out/p).read_text())['sessions']}
    rows=[json.loads(l) for l in (out/'runs.jsonl').read_text().splitlines()]
    calls,sessions=tables(rows,audits);a=analyze(calls,sessions)
    a['all_evidence_passed']=done['all_passed'];a['recommend_continue']&=done['all_passed']
    for name,table in [('calls',calls),('sessions',sessions),('pairs',a['pairs']),('units',a['unit_details']),('cases',a['cases']),('snapshots',a['states'])]:csvfile(out/(name+'.csv'),table)
    write(out/'analysis.json',a)
    def number(x,scale=1):return '缺失／审计失败' if x is None else f'{x*scale:.6f}'
    lines=['# 第六轮固定快照机制结果','',
        '比较独立 coverage_block_generator 与现有 Global::query() 的直接全局模式；共用初始目标、Reach／整数 bit 编码、目标包、验证和检查点规则。变量块模式额外施加固定 assumptions，并依据 core 释放变量。','',
        f'6 份已有开发快照，每份 3 个 solver seed、2 次配对重复；每会话常驻同一公式、覆盖和 Global 求解器执行 3 个动作。共 {len(sessions)} 会话、{len(calls)} 动作，每动作双方同为 60 ms，最多 6 次查询、目标包最多 3 个。固定 {plan["workers"]} 并发槽，先后顺序交错，全部计时结束后才独立审计。','',
        '**计分：** 输出作为新增样本，gain 是对冻结正式 S 的真实边际覆盖，不减种子 gain，也不要求保留种子观察。内部动作不修改 S，返回模型必须不同于种子且不在正式前缀中。这里的动作得分相加只是固定状态回放分数，不是在线累计覆盖。','',
        '**完整成本：** 初始化和历史前缀恢复每会话只收一次；动作包括目标、依赖、固定约束、编码、求解、补全、验证、评分及检查点落盘。动作预留 20%（至少 2 ms）作交付；只接受截止前完成写出的检查点。后续超时仍保留此前及时见证，同时计入所有已消耗成本；无见证调用保留为 0。进程墙钟还包含最终证据序列化与退出。','',
        '| 模式 | 动作 | 无候选／无正收益 | 平均新增 bit | 平均动作 ms | 平均新增 bit／动作秒 | 平均会话分数／进程秒 |','|---|---:|---:|---:|---:|---:|---:|']
    for m in a['modes']:lines.append(f'| {m["mode"]} | {m["actions"]} | {m["no_candidate"]}/{m["no_positive"]} | {number(m["mean_gain"])} | {number(m["mean_full_seconds"],1000)} | {number(m["mean_action_efficiency"])} | {number(m["mean_score_per_process_second"])} |')
    lines+=['',f'两次重复先平均，形成 {a["units"]} 个快照×solver seed×动作组合；同会话动作相关，不视为独立统计样本。组合平均差为 **{number(a["mean_gain_delta"])} bit**，中位数 {number(a["median_gain_delta"])}；正／零／负为 **{a["positive_units"]}/{a["zero_units"]}/{a["negative_units"]}**。','',
        '| 案例 | 组合 | 全局平均 bit | 块模式平均 bit | 差值 |','|---|---:|---:|---:|---:|']
    for r in a['cases']:lines.append(f'| {alias(r["instance"])} | {r["units"]} | {number(r["global_mean_gain"])} | {number(r["block_mean_gain"])} | {number(r["mean_delta"])} |')
    lines+=['','| 状态 | 全局平均 bit | 块模式平均 bit | 差值 |','|---|---:|---:|---:|']
    for r in a['states']:lines.append(f'| {alias(r["snapshot"])}/{r["snapshot"].split("/")[-1]} | {number(r["global_mean_gain"])} | {number(r["block_mean_gain"])} | {number(r["mean_delta"])} |')
    lines+=['','## 初始化、准备和失败','',
        '| 模式 | 一次初始化 ms／会话 | 完整进程 ms／会话 | 准备 ms／动作 | 平均初始／最终块 | 全局回退动作 | 固定 core 次数 | 迟到候选数 |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for m in a['modes']:lines.append(f'| {m["mode"]} | {number(m["mean_initialization_seconds"],1000)} | {number(m["mean_process_wall_seconds"],1000)} | {number(m["mean_preparation_seconds"],1000)} | {number(m["mean_initial_block"])}/{number(m["mean_final_block"])} | {m["fallback_actions"]} | {number(m["sum_local_fixed_conflicts"])} | {number(m["sum_late_candidates"])} |')
    lines+=['','首次正收益时间只对有及时正见证的调用取均值，同时保留完整分母：']
    for m in a['modes']:lines.append(f'- {m["mode"]}：{m["positive_latency_count"]}/{m["actions"]} 次，条件均值 {number(m["conditional_mean_first_positive"],1000)} ms；有候选后后续失败仍保留检查点 {number(m["sum_checkpoint_before_later_failure"])} 次，动作返回超预算 {number(m["sum_returned_after_deadline"])} 次。')
    lines+=['','## 事前继续／停止判定','', '| 条件组 | 条件 | 结果 |','|---|---|---|']
    for group in ('common_checks','gain_checks','cost_checks'):
        for name,v in a[group].items():lines.append(f'| {group} | {name} | {"通过" if v else "未通过"} |')
    lines+=['', '**满足小规模继续研究门槛；仍不自动启动集成或筛选。**' if a['recommend_continue'] else '**没有满足继续条件，到此停止，不增加目标包、查询次数或预算。**',
        '',f'全部证据独立审计通过：{done["all_passed"]}。计时阶段墙钟 {timed["wall_seconds"]:.3f} 秒、子进程 CPU {timed["cpu_seconds"]:.3f} 秒；离线审计墙钟 {audit["wall_seconds"]:.3f} 秒、CPU {audit["cpu_seconds"]:.3f} 秒。',
        '', '## 数据与可重放命令','',
        '`calls.csv` 保留所有动作、初末块、完整成本、分支变化、失败和检查点；`sessions.csv` 记录一次初始化及完整会话；`pairs.csv` 为逐重复配对；`units.csv` 为重复平均。原始 `raw/` 保存完整模型、每次查询、释放变量／原因、检查点及截止时间；`audit/` 保存独立核验。','',
        '```bash','HIGHDIV_ROOT=/tmp/lia-highdiv-frozen /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/run_coverage_block_pairs.py \\',
        f'  --binary {plan["binary"]} \\', '  --output /tmp/round6-block-replay --jobs 15','```','',
        '输出目录必须不存在。重放只执行这里的固定机制协议，不自动接入 A/B，不调用 HighDiv 采样，不启动端到端长实验。','']
    (out/'report.md').write_text('\n'.join(lines));return a

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('output',type=Path);args=p.parse_args();print(json.dumps({'recommend_continue':report(args.output)['recommend_continue']}))
