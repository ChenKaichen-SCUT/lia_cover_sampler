#!/usr/bin/env python3
"""Report audited paired mechanisms; repetitions remain clustered by snapshot/seed."""
import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
from statistics import mean,median

PREPARATION_TIMERS=('preparation_base_evaluation_seconds','preparation_region_seconds',
    'preparation_equality_seconds','preparation_direction_seconds',
    'preparation_target_pool_seconds','preparation_feasibility_seconds')


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,data):Path(path).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def csvfile(path,rows):
    if not rows:Path(path).write_text('');return
    fields=list(dict.fromkeys(k for r in rows for k in r))
    with Path(path).open('w',newline='') as f:
        writer=csv.DictWriter(f,fields);writer.writeheader();writer.writerows(rows)

def alias(instance):return 'Bromberger' if 'Bromberger' in instance else 'SMT_random' if 'SMT_random' in instance else 'convert'


def flatten(row,audit):
    data=row.get('probe',{});counts=data.get('statistics',{}).get('counts',{});times=data.get('statistics',{}).get('seconds',{})
    out={k:row.get(k) for k in ('snapshot','instance','prefix','seed','repeat','mode','pair_index','order_position',
        'budget_seconds','process_wall_seconds','child_cpu_seconds','exitcode','watchdog_timeout','success','error','output','full_probe_sha256')}
    out.update({k:v for k,v in data.items() if k!='statistics'})
    out.update(audit_passed=audit.get('passed',False),audit_error=audit.get('error'),
        actual_candidate_gain=audit.get('actual_candidate_gain'),
        preparation_seconds=sum(times.get(k,0) for k in PREPARATION_TIMERS),
        preparation_region_builds=counts.get('preparation_region_builds',0),
        preparation_equality_builds=counts.get('preparation_equality_builds',0),
        preparation_direction_builds=counts.get('preparation_direction_builds',0),
        preparation_feasibility_builds=counts.get('preparation_feasibility_builds',0),
        repeated_preparation_builds=counts.get('preparation_repeated_region_builds',0),
        preparation_reuse_hits=counts.get('preparation_reuse_hits',0),
        preparation_move_invalidations=counts.get('preparation_move_invalidations',0),
        candidate_attempts=counts.get('candidate_attempts',0),
        total_attempt_limit=counts.get('polish_total_attempt_limit',1536),
        attempt_limited_batches=counts.get('polish_attempt_limited_batches',0),
        completed_flat_batches=counts.get('polish_completed_flat_batches',0),
        budget_exits=counts.get('polish_budget_exits',0),
        old_budget_exit_as_flat=counts.get('polish_budget_exit_as_flat_control',0),
        proven_local_locked=counts.get('direction_nullspace_zero',0)>0,
        had_valid_candidate=bool(data) and data.get('valid_candidates',0)>0,
        no_valid_candidate=bool(data) and data.get('valid_candidates',0)==0,
        extra_per_process_wall_second=(data.get('accepted_extra_coverage',0)/max(1e-9,row.get('process_wall_seconds',0))))
    out.update({'count_'+k:v for k,v in sorted(counts.items())})
    out.update({'seconds_'+k:v for k,v in sorted(times.items())})
    return out


def pair_and_cluster(calls,repeats):
    paired=defaultdict(dict)
    for row in calls:
        key=(row['snapshot'],row['seed'],row['repeat'])
        if row['mode'] in paired[key]:raise ValueError('duplicate mode in a paired call')
        paired[key][row['mode']]=row
    pairs=[]
    for key,group in sorted(paired.items()):
        a,b=group.get('old'),group.get('reuse');sample=a or b
        valid=bool(a and b and a['audit_passed'] and b['audit_passed'])
        row=dict(snapshot=key[0],instance=sample['instance'],prefix=sample['prefix'],seed=key[1],repeat=key[2],
                 paired_and_audited=valid,order='old,reuse' if a and a['order_position']==0 else 'reuse,old')
        for mode,item in (('old',a),('reuse',b)):
            for field in ('accepted_extra_coverage','full_seconds','process_wall_seconds','extra_per_full_second',
                          'extra_per_process_wall_second','valid_candidates','first_valid_seconds','late_extra_coverage',
                          'setup_exhausted','preparation_seconds','repeated_preparation_builds','preparation_region_builds',
                          'preparation_reuse_hits','candidate_attempts','total_attempt_limit','proven_local_locked'):
                row[mode+'_'+field]=item.get(field) if item else None
        for metric in ('accepted_extra_coverage','extra_per_full_second','extra_per_process_wall_second','valid_candidates',
                       'full_seconds','process_wall_seconds','preparation_seconds','repeated_preparation_builds'):
            row['delta_'+metric]=(b[metric]-a[metric]) if valid else None
        row['delta_extra_pp']=100*row['delta_accepted_extra_coverage']/sample['total'] if valid else None
        pairs.append(row)
    grouped=defaultdict(list)
    for row in pairs:grouped[row['snapshot'],row['seed']].append(row)
    clusters=[]
    for key,group in sorted(grouped.items()):
        valid=len(group)==repeats and {r['repeat'] for r in group}==set(range(repeats)) and all(r['paired_and_audited'] for r in group)
        row=dict(snapshot=key[0],instance=group[0]['instance'],prefix=group[0]['prefix'],seed=key[1],
                 repeats=len(group),complete_and_audited=valid)
        metrics=['delta_accepted_extra_coverage','delta_extra_per_full_second','delta_extra_per_process_wall_second',
                 'delta_valid_candidates','delta_full_seconds','delta_process_wall_seconds','delta_preparation_seconds',
                 'delta_repeated_preparation_builds','delta_extra_pp']
        for name in metrics:row['mean_'+name]=mean(r[name] for r in group) if valid else None
        for mode in ('old','reuse'):
            for metric in ('accepted_extra_coverage','extra_per_full_second','extra_per_process_wall_second','full_seconds',
                           'process_wall_seconds','valid_candidates','preparation_seconds','repeated_preparation_builds'):
                row['mean_'+mode+'_'+metric]=mean(r[mode+'_'+metric] for r in group) if valid else None
        clusters.append(row)
    return pairs,clusters


def aggregate(calls,clusters,plan):
    valid=[r for r in clusters if r['complete_and_audited']]
    deltas=[r['mean_delta_accepted_extra_coverage'] for r in valid]
    by_case=defaultdict(list);by_snapshot=defaultdict(list)
    for r in valid:by_case[r['instance']].append(r);by_snapshot[r['snapshot']].append(r)
    cases=[]
    for instance,rows in sorted(by_case.items()):
        cases.append(dict(instance=instance,clusters=len(rows),mean_delta_bits=mean(r['mean_delta_accepted_extra_coverage'] for r in rows),
            median_delta_bits=median(r['mean_delta_accepted_extra_coverage'] for r in rows),
            mean_delta_pp=mean(r['mean_delta_extra_pp'] for r in rows),
            mean_old_extra_bits=mean(r['mean_old_accepted_extra_coverage'] for r in rows),
            mean_reuse_extra_bits=mean(r['mean_reuse_accepted_extra_coverage'] for r in rows)))
    positive_snapshots=[name for name,rows in by_snapshot.items() if mean(r['mean_delta_accepted_extra_coverage'] for r in rows)>0]
    gates=dict(all_clusters_complete=len(valid)==len(clusters) and len(clusters)==len(plan['snapshots'])*len(plan['seeds']),
        mean_cluster_delta_positive=bool(deltas) and mean(deltas)>0,
        median_cluster_delta_nonnegative=bool(deltas) and median(deltas)>=0,
        two_thirds_clusters_nonnegative=bool(deltas) and sum(x>=0 for x in deltas)>=len(deltas)*2/3,
        positive_on_two_development_cases=sum(r['mean_delta_bits']>0 for r in cases)>=2,
        positive_on_multiple_snapshots=len(positive_snapshots)>=2,
        primary_60ms_protocol=plan['budget_seconds']==.06 and len(plan['snapshots'])==12 and plan['repeats']==3 and plan['seeds']==[0,1,2])
    modes=[]
    for mode in ('old','reuse'):
        group=[r for r in calls if r['mode']==mode];ok=[r for r in group if r['audit_passed']]
        # No-candidate and cold-setup-exhausted calls remain in every denominator.
        row=dict(mode=mode,calls=len(group),audit_failures=len(group)-len(ok),
            no_candidate_calls=sum(r['no_valid_candidate'] for r in group),
            valid_candidate_calls=sum(r['had_valid_candidate'] for r in group),
            setup_exhausted_calls=sum(r.get('setup_exhausted',False) for r in group),
            overdue_calls=sum(not r.get('completed_in_budget',False) for r in group),
            local_lock_proof_calls=sum(r['proven_local_locked'] for r in group))
        numeric=('accepted_extra_coverage','late_extra_coverage','full_seconds','process_wall_seconds','child_cpu_seconds',
                 'extra_per_full_second','extra_per_process_wall_second','valid_candidates','timely_valid_candidates',
                 'preparation_seconds','preparation_region_builds','preparation_equality_builds',
                 'preparation_direction_builds','preparation_feasibility_builds','repeated_preparation_builds',
                 'preparation_reuse_hits','candidate_attempts','setup_seconds','formula_initialization_seconds',
                 'snapshot_restore_seconds','final_validation_seconds','preservation_seconds')
        for field in numeric:
            values=[r.get(field,0) for r in ok]
            row['mean_'+field]=mean(values) if len(ok)==len(group) and values else None
            row['sum_'+field]=sum(values) if len(ok)==len(group) and values else None
        first=[r['first_valid_seconds'] for r in ok if r.get('first_valid_seconds') is not None]
        row['first_valid_conditional_mean_seconds']=mean(first) if first else None
        row['first_valid_observed_calls']=len(first)
        modes.append(row)
    return dict(clusters=len(clusters),valid_clusters=len(valid),
        mean_cluster_delta_bits=mean(deltas) if deltas else None,
        median_cluster_delta_bits=median(deltas) if deltas else None,
        nonnegative_clusters=sum(x>=0 for x in deltas),positive_clusters=sum(x>0 for x in deltas),
        negative_clusters=sum(x<0 for x in deltas),positive_snapshots=positive_snapshots,
        checks=gates,stable_primary_gain=all(gates.values()),cases=cases,modes=modes,
        always_stop_before_end_to_end=True)


def report(out):
    out=Path(out).resolve();plan=json.loads((out/'plan.json').read_text());done=json.loads((out/'COMPLETE.json').read_text())
    assert digest(out/'plan.json')==done['plan_sha256'] and digest(out/'runs.jsonl')==done['runs_sha256']
    assert digest(out/'TIMED_COMPLETE.json')==done['timed_complete_sha256']
    assert digest(out/'AUDIT_COMPLETE.json')==done['audit_complete_sha256']
    timed=json.loads((out/'TIMED_COMPLETE.json').read_text());audit_summary=json.loads((out/'AUDIT_COMPLETE.json').read_text())
    assert audit_summary['started_utc']>=timed['finished_utc'], 'audit overlapped timing'
    assert all(digest(out/path)==sha for path,sha in audit_summary['audit_files_sha256'].items())
    audits={}
    for file in sorted((out/'audit').glob('**/audit.json')):
        for r in json.loads(file.read_text())['calls']:audits[r['output']]=r
    rows=[json.loads(x) for x in (out/'runs.jsonl').read_text().splitlines()]
    calls=[flatten(r,audits.get(r['output'],{})) for r in rows]
    calls.sort(key=lambda r:(r['snapshot'],r['seed'],r['repeat'],r['mode']))
    pairs,clusters=pair_and_cluster(calls,plan['repeats']);summary=aggregate(calls,clusters,plan)
    summary['all_evidence_passed']=done['all_passed']
    if not done['all_passed']:summary['stable_primary_gain']=False
    csvfile(out/'calls.csv',calls);csvfile(out/'pairs.csv',pairs);csvfile(out/'clusters.csv',clusters);csvfile(out/'cases.csv',summary['cases'])
    write(out/'analysis.json',summary)
    budget=plan['budget_seconds']*1000
    lines=[f'# 准备复用机制验证：双方同为 {budget:g} ms','',
        f'固定已有 {len(plan["snapshots"])} 个开发快照，种子 0/1/2，每个种子重复 {plan["repeats"]} 次，共 {len(calls)} 次调用、{len(pairs)} 个顺序配对。每对 old/reuse 串行；按快照序号＋种子＋重复序号的奇偶交错先后顺序。最多 {plan["jobs"]} 对并行，内部 OMP／BLAS 线程固定为 1。没有重新校准并发。','',
        '**完整成本口径：** 单次预算从 Formula 冷初始化之前开始，包含公式构建、原始快照恢复及验证、Search 初始化、准备、目标尝试、候选评分、观察保留检查及最后原公式验证。`full_seconds` 为完整算法耗时，证据序列化不在其中；另报告含启动、序列化和退出的 `process_wall_seconds`。两种版本使用同一预算，不把新版较长预算与旧版较短预算比较。','',
        '超出总预算的调用，即使内部已经找到更好模型，主收益也计 0，并交付输入原模型；潜在改善独立列在 `late_extra_coverage`。输入模型原有覆盖只作基线，绝不计入额外收益。无候选、准备耗尽预算、局部锁死和超时调用全部保留。候选首次出现时间对无候选调用为缺失，并同时报告缺失数量，不将缺失调用删除后宣称总体性能。','',
        f'全部计时进程结束于 `{timed["finished_utc"]}`，独立原覆盖评测审计开始于 `{audit_summary["started_utc"]}`，二者不重叠。审计校验固定输入／覆盖、原公式合法性、真实候选增益、原始新观察保留、实际输出去重与超期原模型回退。','',
        '## 主结果与稳定性','',
        f'统计单元为 **{summary["clusters"]} 个快照 × 种子组合**，每个组合先对 3 次重复取均值；重复次数不作为新增独立样本。所有后续均值和中位数均明确区分这个组合层级与调用层级。','',
        '| 版本 | 调用数 | 无合法候选 | 冷准备耗尽预算 | 总耗时超期 | 平均实际额外 bit | 平均完整耗时（ms） | 平均额外 bit／完整秒 |','|---|---:|---:|---:|---:|---:|---:|---:|']
    def num(value,scale=1):return '缺失／审计失败' if value is None else f'{value*scale:.6f}'
    for r in summary['modes']:
        lines.append(f'| {r["mode"]} | {r["calls"]} | {r["no_candidate_calls"]} | {r["setup_exhausted_calls"]} | {r["overdue_calls"]} | {num(r["mean_accepted_extra_coverage"])} | {num(r["mean_full_seconds"],1000)} | {num(r["mean_extra_per_full_second"])} |')
    lines += ['',f'组合均值的平均收益差（reuse − old）为 **{num(summary["mean_cluster_delta_bits"])} bit**，中位数为 **{num(summary["median_cluster_delta_bits"])} bit**；正／零／负组合为 {summary["positive_clusters"]}／{summary["nonnegative_clusters"]-summary["positive_clusters"]}／{summary["negative_clusters"]}。','',
        '| 案例 | 组合数 | old 平均额外 bit | reuse 平均额外 bit | 平均差 bit | 平均差百分点 |','|---|---:|---:|---:|---:|---:|']
    for r in summary['cases']:
        lines.append(f'| {alias(r["instance"])} | {r["clusters"]} | {r["mean_old_extra_bits"]:.6f} | {r["mean_reuse_extra_bits"]:.6f} | {r["mean_delta_bits"]:+.6f} | {r["mean_delta_pp"]:+.6f} |')
    lines += ['', '预声明稳健规则：组合均值差大于 0、组合差中位数不小于 0、至少 2/3 组合非负、至少两个开发案例均值为正、改善不限于单一快照，并且数据完整且独立审计通过。这些只是本次少量机制实验的稳定性门槛，不是统计显著性的证明。','',
        '| 检查 | 结果 |','|---|---|']
    for k,v in summary['checks'].items():lines.append(f'| {k} | {"通过" if v else "未通过"} |')
    if plan['budget_seconds']!=.06:
        lines += ['', '**本表为单独的对称较长预算诊断，不替代 60 ms 主比较，也不触发任何后续实验。**']
    if summary['stable_primary_gain']:
        lines += ['', '同为 60 ms 的完整成本口径下，准备复用通过预声明的机制稳定性门槛；改善存在于上表的多个案例和快照。这个结论仍限于现有开发状态，不能外推到端到端效果。本轮到此停止，不自动启动完整筛选。']
    else:
        lines += ['', '**没有获得符合预声明规则的稳定主收益，停止扩大实验。** 局部区域已经锁死时，复用只能减少重复工作，不能产生该区域中不存在的自由方向；“局部区域锁死、需要跨分支生成”保留为独立研究问题，不增加 safe 调用或继续微调时间片来掩盖。']
    lines += ['', '## 每个快照的状态范围','', '| 案例／原始前缀 | old 平均额外 bit | reuse 平均额外 bit | 差值 | old／reuse 重复准备次数 | old／reuse 合法候选调用 | old／reuse 局部锁死证明调用 |','|---|---:|---:|---:|---:|---:|---:|']
    for s in plan['snapshots']:
        group={m:[r for r in calls if r['snapshot']==s['id'] and r['mode']==m] for m in ('old','reuse')}
        if not all(r['audit_passed'] for rows in group.values() for r in rows):continue
        a,b=group['old'],group['reuse'];av=mean(r['accepted_extra_coverage'] for r in a);bv=mean(r['accepted_extra_coverage'] for r in b)
        lines.append(f'| {alias(s["instance"])}/{s["prefix"]} | {av:.6f} | {bv:.6f} | {bv-av:+.6f} | {sum(r["repeated_preparation_builds"] for r in a)}/{sum(r["repeated_preparation_builds"] for r in b)} | {sum(r["had_valid_candidate"] for r in a)}/{sum(r["had_valid_candidate"] for r in b)} | {sum(r["proven_local_locked"] for r in a)}/{sum(r["proven_local_locked"] for r in b)} |')
    lines += ['', '## 准备成本与候选出现','', '| 版本 | 平均准备 ms | 平均区域／消元／方向／可行性构建数 | 重复区域准备总数 | 首次合法候选均值 ms（仅出现者） | 有首次候选时间／全部调用 | 平均额外 bit／进程墙钟秒 |','|---|---:|---|---:|---:|---:|---:|']
    for r in summary['modes']:
        builds='/'.join(num(r['mean_'+f]) for f in ('preparation_region_builds','preparation_equality_builds','preparation_direction_builds','preparation_feasibility_builds'))
        lines.append(f'| {r["mode"]} | {num(r["mean_preparation_seconds"],1000)} | {builds} | {num(r["sum_repeated_preparation_builds"])} | {num(r["first_valid_conditional_mean_seconds"],1000)} | {r["first_valid_observed_calls"]}/{r["calls"]} | {num(r["mean_extra_per_process_wall_second"])} |')
    lines += ['', '准备耗时为基点求值、区域、等式消元、方向、目标池、可行性六项互斥计时之和；完整耗时仍是所有收益率分母，绝不只用准备时间计算“加速”。`calls.csv` 保存每项成本及构建数、首次候选时间、有效候选数、迟到收益、重复准备、移动失效、预算退出、无改善完成批次和全部原始统计。合法候选数来自逐个验证后触发的内部观察器；最终 proposed／实际交付候选另受独立原公式与覆盖评测审计。','',
        f'双方最多 {plan["attempt_caps"]["batches"]} 批、{plan["attempt_caps"]["candidates"]} 个评分候选，每批最多 {plan["attempt_caps"]["attempts_per_batch"]} 次尝试，总上限 {plan["attempt_caps"]["total_attempts"]}。新版 BudgetExceeded 单列为预算退出，不能算一次已完成的无改善搜索；仍受总时间、总尝试数和批次数约束。','',
        f'计时阶段：墙钟 {timed["wall_seconds"]:.3f} 秒、子进程 CPU {timed["child_cpu_seconds"]:.3f} 秒。独立审计：墙钟 {audit_summary["wall_seconds"]:.3f} 秒、CPU {audit_summary["cpu_seconds"]:.3f} 秒。审计通过：{audit_summary["all_passed"]}；输入哈希保持不变：{done["unchanged_sources"]}。','',
        '## 可重放命令与文件','', '```bash',
        'HIGHDIV_ROOT=/tmp/lia-highdiv-frozen /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/run_preparation_pairs.py \\',
        f'  --binary {plan["binary"]} \\',
        f'  --output /tmp/preparation-pairs-replay-{int(budget)}ms --budget {plan["budget_seconds"]} --jobs {plan["jobs"]} --repeats 3'+(' --prefix 1' if plan['budget_seconds']==.3 else ''),
        '```','',
        '`plan.json` 在计时前冻结快照、输入、二进制、源代码、脚本、种子、配对顺序与稳健规则；`raw/` 保存每次完整探针输出和失败日志；`runs.jsonl` 记录命令、退出码、逐子进程 CPU 和墙钟。`calls.csv` 保留所有调用，`pairs.csv` 为每次重复的配对，`clusters.csv` 为快照 × 种子的三次均值，`cases.csv` 为案例汇总。输出目录必须不存在；无候选调用不会被删除。','']
    (out/'report.md').write_text('\n'.join(lines))
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('output',type=Path)
    args=parser.parse_args();summary=report(args.output)
    print(json.dumps(dict(stable_primary_gain=summary['stable_primary_gain'],clusters=summary['clusters'])))

if __name__=='__main__':main()
