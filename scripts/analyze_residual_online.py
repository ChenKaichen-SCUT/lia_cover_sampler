#!/usr/bin/env python3
"""Analyze audited continuous trajectories under the pre-frozen round-seven gate."""
import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import mean, median
from reevaluate_round6_saved_candidates import dump, write_csv, file_hash, require

MODES = ('global_pack', 'residual_cardinality')
PREFIXES = (1,2,5,10)


def stats(values):
    return dict(n=len(values), mean=mean(values) if values else None, median=median(values) if values else None)


def wtl(values):
    return dict(wins=sum(v>0 for v in values), ties=sum(v==0 for v in values), losses=sum(v<0 for v in values))


def fmt(value):
    return '未完成/无数据' if value is None else f'{value:.6f}'


def short(name):
    if 'Bromberger' in name: return 'Bromberger'
    if '14600' in name: return 'SMT_random_14600'
    return name.removesuffix('.smt2')


def analyze(input_dir, output):
    input_dir, output = Path(input_dir), Path(output); output.mkdir(parents=True,exist_ok=False)
    plan=json.loads((input_dir/'plan.json').read_text())
    complete=json.loads((input_dir/'COMPLETE.json').read_text())
    audit_complete=json.loads((input_dir/'audit/COMPLETE.json').read_text())
    audits=json.loads((input_dir/'audit/sessions.json').read_text())
    require(len(audits)==36,'analysis requires all 36 sessions, including failures')
    require(audit_complete['plan_sha256']==file_hash(input_dir/'plan.json'),'audit plan binding changed')
    require(audit_complete['runs_sha256']==file_hash(input_dir/'runs.jsonl'),'audit runs binding changed')
    for name, digest in audit_complete['files'].items():
        require(file_hash(input_dir/'audit'/name)==digest,'audited result hash changed')
    sessions=[]; trajectories=[]; rounds=[]; queries=[]
    for a in audits:
        identity={k:a[k] for k in ('session','snapshot','instance','prefix','seed','mode')}
        row=identity|dict(audit_passed=a['passed'],run_success=a.get('run_success',False),actual_gain=a['actual_gain'],gain_pp=a['gain_pp'],
            outputs=len(a['outputs']),failed=not a['passed'] or not a.get('run_success',False),zero_gain=a['actual_gain']==0,no_output=not a['outputs'],
            total=a.get('total'),s0_covered=a.get('s0_covered'),seed_gain_excluded=a.get('seed_gain'),
            full_session_seconds=a.get('full_session_seconds'),process_wall_seconds=a.get('process_wall_seconds'),cpu_seconds=a.get('cpu_seconds'),
            initialization_seconds=a.get('initialization_seconds'),terminal=a.get('terminal',a.get('error')),
            solver_calls=a.get('solver_calls'),target_attempts=a.get('target_attempts'),fallback_calls=a.get('fallback_calls'),
            late_formal_models=a.get('late_formal_models'),overrun_rounds=a.get('overrun_rounds'),session_overrun_seconds=a.get('session_overrun_seconds'),
            source_sha256=a.get('source_sha256',plan['source_sha256']))
        row.update({'cost_'+key:val for key,val in a.get('costs',{}).items()})
        for k in PREFIXES:
            reached=len(a['outputs'])>=k
            row[f'prefix_{k}_reached']=reached
            row[f'prefix_{k}_gain']=a['outputs'][k-1]['cumulative_gain'] if reached else None
            row[f'prefix_{k}_pp']=a['outputs'][k-1]['cumulative_gain_pp'] if reached else None
            row[f'prefix_{k}_seconds']=a['outputs'][k-1]['completion_timestamp'] if reached else None
        sessions.append(row)
        trajectories.extend(identity|o for o in a['outputs'])
        for r in a.get('rounds',[]):
            rounds.append(identity|{k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in r.items()})
        for q in a.get('queries',[]):
            queries.append(identity|{k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in q.items()})
    index={(r['snapshot'],r['seed'],r['mode']):r for r in sessions};pairs=[]
    for s in plan['snapshots']:
        for seed in plan['seeds']:
            a=index[(s['id'],seed,MODES[0])];b=index[(s['id'],seed,MODES[1])]
            pair=dict(snapshot=s['id'],instance=s['instance'],prefix=s['prefix'],seed=seed,
                global_gain=a['actual_gain'],residual_gain=b['actual_gain'],global_pp=a['gain_pp'],residual_pp=b['gain_pp'],difference_pp=b['gain_pp']-a['gain_pp'],
                both_valid=not a['failed'] and not b['failed'],global_outputs=a['outputs'],residual_outputs=b['outputs'])
            for k in PREFIXES:
                common=a[f'prefix_{k}_reached'] and b[f'prefix_{k}_reached']
                pair[f'prefix_{k}_common']=common
                pair[f'prefix_{k}_difference_pp']=b[f'prefix_{k}_pp']-a[f'prefix_{k}_pp'] if common else None
                pair[f'prefix_{k}_time_difference']=b[f'prefix_{k}_seconds']-a[f'prefix_{k}_seconds'] if common else None
            pairs.append(pair)
    cases=[]
    for instance in dict.fromkeys(s['instance'] for s in plan['snapshots']):
        subset=[p for p in pairs if p['instance']==instance]
        cases.append(dict(instance=instance,global_mean_pp=mean(p['global_pp'] for p in subset),residual_mean_pp=mean(p['residual_pp'] for p in subset),
            **stats([p['difference_pp'] for p in subset]),**wtl([p['difference_pp'] for p in subset])))
    states=[]
    for snapshot in plan['snapshots']:
        paired=[p for p in pairs if p['snapshot']==snapshot['id']]
        for mode in MODES:
            subset=[r for r in sessions if r['snapshot']==snapshot['id'] and r['mode']==mode]
            valid=[r for r in subset if r['full_session_seconds'] is not None]
            states.append(dict(snapshot=snapshot['id'],instance=snapshot['instance'],prefix=snapshot['prefix'],mode=mode,n=len(subset),
                mean_gain=mean(r['actual_gain'] for r in subset),median_gain=median(r['actual_gain'] for r in subset),mean_pp=mean(r['gain_pp'] for r in subset),median_pp=median(r['gain_pp'] for r in subset),
                mean_outputs=mean(r['outputs'] for r in subset),mean_full_seconds=mean(r['full_session_seconds'] for r in valid) if valid else None,
                mean_process_wall_seconds=mean(r['process_wall_seconds'] for r in valid) if valid else None,
                mean_init_seconds=mean(r['initialization_seconds'] for r in valid) if valid else None,
                mean_definition_seconds=mean(r.get('cost_registered_definition_seconds',0) for r in valid) if valid else None,
                mean_check_seconds=mean(r.get('cost_solver_check_seconds',0) for r in valid) if valid else None,
                mean_submission_seconds=mean(r.get('cost_final_submission_seconds',0) for r in valid) if valid else None,
                failed=sum(r['failed'] for r in subset),zero_gain=sum(r['zero_gain'] for r in subset),no_output=sum(r['no_output'] for r in subset),
                paired_difference_mean_pp=mean(p['difference_pp'] for p in paired),paired_difference_median_pp=median(p['difference_pp'] for p in paired),**wtl([p['difference_pp'] for p in paired])))
    early=[]
    for k in PREFIXES:
        subset=[p for p in pairs if p[f'prefix_{k}_common']]
        differences=[p[f'prefix_{k}_difference_pp'] for p in subset]
        case_diffs={c:mean(p[f'prefix_{k}_difference_pp'] for p in subset if p['instance']==c) for c in {p['instance'] for p in subset}}
        negative_cases=sum(v<0 for v in case_diffs.values())
        systematic=negative_cases>=2 or bool(differences and median(differences)<0 and sum(v<0 for v in differences)/len(differences)>=2/3)
        early.append(dict(k=k,**stats(differences),**wtl(differences),case_count=len(case_diffs),case_mean_differences=case_diffs,
            mean_time_difference=mean(p[f'prefix_{k}_time_difference'] for p in subset) if subset else None,
            systematic_regression=systematic,global_completed=sum(r[f'prefix_{k}_reached'] for r in sessions if r['mode']==MODES[0]),
            residual_completed=sum(r[f'prefix_{k}_reached'] for r in sessions if r['mode']==MODES[1])))
    diffs=[p['difference_pp'] for p in pairs]
    gates=dict(two_cases_improved=sum(c['mean']>0 for c in cases)>=2,paired_median_nondecreasing=median(diffs)>=0,
        no_systematic_early_regression=not any(p['systematic_regression'] for p in early),
        two_case_quality_evidence=all(e['case_count']>=2 for e in early if e['k'] in (1,2)),
        all_audited=complete['all_passed'] and audit_complete['all_passed'] and all(not r['failed'] for r in sessions))
    modes=[]
    for mode in MODES:
        subset=[s for s in sessions if s['mode']==mode]
        modes.append(dict(mode=mode,mean_pp=mean(r['gain_pp'] for r in subset),median_pp=median(r['gain_pp'] for r in subset),mean_outputs=mean(r['outputs'] for r in subset),
            failed=sum(r['failed'] for r in subset),no_output=sum(r['no_output'] for r in subset),zero_gain=sum(r['zero_gain'] for r in subset),
            terminal_counts=dict(Counter(r['terminal'] for r in subset))))
    summary=dict(label='第七轮真实在线继续采样；种子已提交',recommend_continue=all(gates.values()),gates=gates,
        endpoint_paired=stats(diffs)|wtl(diffs),cases=cases,states=states,early_prefixes=early,modes=modes,
        configuration=dict(session_seconds=10,round_seconds=1,window=8,target_calls=3,sessions=36,workers=plan['workers']),
        implementation_commit=plan['implementation_commit'],source_sha256=plan['source_sha256'],no_followup_started=True,
        inputs={n:file_hash(input_dir/n) for n in ('plan.json','runs.jsonl','COMPLETE.json','audit/COMPLETE.json','audit/sessions.json')})
    dump(output/'summary.json',summary)
    for name,rows in (('sessions',sessions),('paired',pairs),('states',states),('cases',cases),('early_prefixes',early),('trajectory',trajectories),('rounds',rounds),('queries',queries)):
        write_csv(output/(name+'.csv'),rows)
    lines=['# 第七轮实验报告：实时剩余覆盖与可选择目标组合','',
        '本轮完成了真实在线覆盖更新和独立阈值查询的实现。所有指标从已提交种子的 S0 开始，未把种子收益或内部候选计入新增覆盖。', '',
        ('冻结晋级条件全部通过；只建议进一步验证，本轮没有自动启动后续实验。' if summary['recommend_continue'] else '冻结晋级条件未全部通过，停止扩大实验。没有调整窗口、调用次数或预算来补救结果。'),'',
        f'基于 `{plan["frozen_base"][:7]}`；实现提交 `{plan["implementation_commit"]}`。6 份已有快照 × 3 个 seed × 2 个配置，共 36 个会话，每会话 10 秒，最多 {plan["workers"]} 路并行。所有采样结束后才进行冻结原评测器的独立审计。', '',
        '## 功能与边界','',
        'Global 对照直接使用原有 repair（3 次目标查询、最多 2 次扩充），保留冲突修复与包大小适应。新模式用至多 8 个剩余 bit 组成窗口，查询至少命中 k 个目标；SAT 后按实际命中 c 跳到 c+1。实际最优候选按全部 AST 的边际覆盖保留，选择变量真值个数仅作诊断。两侧共用目标规则、验证、去重、预算和一次探索回退。', '',
        '合成回归核对兼容组合、有限枚举的阈值结论、单向激活、Reach、首次观察、在线覆盖与版本、作用域、负大整数、重复及截止行为。阈值 UNSAT 单独记录，不进入原合取冲突缓存。没有改原固定三动作入口、HighDiv、方向生成、polish 或 block。', '',
        f'独立审计通过：{audit_complete["all_passed"]}；36 会话和来源完整：{gates["all_audited"]}。每个正式模型都按保存顺序重算完整 seen0/seen1，并验证边际之和等于最终覆盖减 S0。首次窗口和起点逐配对核对。零收益、无输出、超时和编码超期均保留。', '',
        '检查点是内部已验证候选，正式模型行实际 flush 完成才是输出时间。后续失败保留此前最好候选；不可中断操作造成迟到正式输出时不计分，不能回填早先检查点时间。回执、状态更新和日志仍计入会话成本。外部进程时间额外包含摘要写入和退出。','',
        '## 终点新增覆盖','',
        '以下覆盖率增量单位均为原始分母上的百分点。每状态每侧 3 个会话，平均值不去掉零收益。','',
        '| 案例 / 历史前缀 | Global 新增 bit 均值 | 新模式新增 bit 均值 | Global 增量 pp | 新模式增量 pp | 配对差均值 pp | 新模式胜/平/负 |','|---|---:|---:|---:|---:|---:|---|']
    for s in plan['snapshots']:
        a,b=[next(x for x in states if x['snapshot']==s['id'] and x['mode']==m) for m in MODES]
        lines.append(f'| {short(s["instance"])} / {s["prefix"]} | {a["mean_gain"]:.3f} | {b["mean_gain"]:.3f} | {a["mean_pp"]:.6f} | {b["mean_pp"]:.6f} | {b["paired_difference_mean_pp"]:+.6f} | {b["wins"]}/{b["ties"]}/{b["losses"]} |')
    e=summary['endpoint_paired']
    lines += ['',f'18 对会话终点差：均值 {e["mean"]:+.6f} pp，中位数 {e["median"]:+.6f} pp，胜/平/负 {e["wins"]}/{e["ties"]}/{e["losses"]}。每案例的两个前缀共同构成 6 对，不视为两个独立 benchmark 家族。','',
        '| 案例 | Global 均值 pp | 新模式均值 pp | 配对差 pp |','|---|---:|---:|---:|']
    for c in cases: lines.append(f'| {short(c["instance"])} | {c["global_mean_pp"]:.6f} | {c["residual_mean_pp"]:.6f} | {c["mean"]:+.6f} |')
    lines += ['', '## 相同新增样本数的质量', '', '只比较双方共同达到的前 k 个正式新输出；不包含历史前缀和种子。未达到的会话在 sessions.csv 中明确未完成，以下同时列出完成数量，避免只展示共同完成者。时间差为新模式减 Global，包含初始化。', '',
        '| k | Global / 新模式完成会话数 | 共同配对 / 案例数 | 质量差均值 pp | 中位数 pp | 胜/平/负 | 达到时间差均值 s | 系统性退步 |',
        '|---|---|---|---:|---:|---|---:|---|']
    for p in early: lines.append(f'| {p["k"]} | {p["global_completed"]}/{p["residual_completed"]} | {p["n"]}/{p["case_count"]} | {fmt(p["mean"])} | {fmt(p["median"])} | {p["wins"]}/{p["ties"]}/{p["losses"]} | {fmt(p["mean_time_difference"])} | {p["systematic_regression"]} |')
    lines += ['', '## 完整成本与失败', '', '下表为每状态每侧会话均值。定义构建包含原 repair 扩充时新建的目标和 Reach；check 为目标 solver.check，探索耗时另在逐会话成本列中保留。阶段存在嵌套，不将它们相加冒充总时间。', '',
        '| 案例 / 前缀 | 模式 | 新输出数 | 内部总耗时 s | 外部进程 s | 初始化 s | 新定义 s | 目标 check s | 最终提交 s | 失败 / 无输出 / 零收益 |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---|']
    for s in states: lines.append(f'| {short(s["instance"])} / {s["prefix"]} | {s["mode"]} | {s["mean_outputs"]:.2f} | {fmt(s["mean_full_seconds"])} | {fmt(s["mean_process_wall_seconds"])} | {fmt(s["mean_init_seconds"])} | {fmt(s["mean_definition_seconds"])} | {fmt(s["mean_check_seconds"])} | {fmt(s["mean_submission_seconds"])} | {s["failed"]}/{s["no_output"]}/{s["zero_gain"]} |')
    lines += ['', '## 冻结判断与解释', '']
    labels={'two_cases_improved':'至少两个案例终点改善','paired_median_nondecreasing':'总体配对中位数不下降','no_systematic_early_regression':'共同早期前缀无系统性退步','two_case_quality_evidence':'前 1、2 个共同输出覆盖至少两个案例','all_audited':'来源、36 会话与输出审计通过'}
    lines += [f'- {labels[k]}：{"通过" if v else "未通过"}。' for k,v in gates.items()]
    lines += ['', '功能正确性与性能收益分开判断：合成阈值测试能证明约束语义，却不能证明新机制更快。在线终点体现预算内累计覆盖；共同前缀反映单模型质量；新增定义、查询和提交成本均保留。窗口命中增加本身不等于全 AST 收益更大。若主要改善局限于一个案例或共同前缀证据不足，不能宣称同时改善时间和样本质量。', '',
        '本轮输出之间相关，3 个 solver seed 只提供小规模开发证据，没有置信区间或显著性宣称。没有重跑 HighDiv，也没有把带历史状态的 10 秒结果写成从零开始的 900 秒表现。原第六轮结果及停止决定保持原样。', '',
        '## 数据和重放', '',
        '`sessions.csv` 为 36 个完整会话；`paired.csv` 为 18 对；`states.csv`、`cases.csv` 和 `early_prefixes.csv` 给出分层汇总；`trajectory.csv` 记录每条已审计正式输出；`rounds.csv` 与 `queries.csv` 包含窗口、调用状态和完整成本。原始模型/检查点/回执/日志位于 raw（并归档为 raw.tar.gz），逐文件哈希见 archive.json。审计结果在 audit。', '',
        '可重放命令见 [冻结实验协议](../../../../docs/第七轮实验协议.md)；`plan.json` 和 `runs.jsonl` 保存确切二进制、源文件哈希、逐会话命令和运行环境。离线重放不会生成新模型。', '',
        '下一阶段仍须区分：未提交种子是已有合法候选或回退；种子已提交时必须针对更新后的覆盖生成。本轮只测后一场景。未通过冻结条件就停在这里；即使通过，也只规划后续验证，不自动扩大实验。','']
    (output/'报告.md').write_text('\n'.join(lines))
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();summary=analyze(a.input,a.output);print(json.dumps({'recommend_continue':summary['recommend_continue']}))

if __name__=='__main__': main()
