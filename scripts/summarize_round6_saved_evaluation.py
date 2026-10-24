#!/usr/bin/env python3
"""Summarize the offline saved-model audit; never run the original experiment."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import mean, median

from reevaluate_round6_saved_candidates import dump, file_hash, require, write_csv


ACTION_METRICS = ('g_m', 'g_x', 'candidate_minus_seed', 'h_x',
                  'seed_fallback_quality_upper_bound', 'first_observation_pairing',
                  'original_full_action_seconds')
SESSION_METRICS = ('U', 'U_prime', 'sum_g_x', 'sum_h_x', 'U_minus_sum_g_x',
                   'U_prime_minus_sum_h_x', 'original_initialization_seconds',
                   'original_session_seconds', 'original_process_wall_seconds')


def moments(rows, columns):
    result = {}
    for name in columns:
        values = [r[name] for r in rows if r.get(name) is not None]
        result[name+'_count'] = len(values)
        result[name+'_mean'] = mean(values) if values else None
        result[name+'_median'] = median(values) if values else None
    return result


def paired(rows, columns, action_level):
    fields = ['snapshot', 'solver_seed'] + (['action'] if action_level else [])
    grouped = defaultdict(dict)
    for row in rows:
        key = tuple(row[k] for k in fields) + (row['repeat'],)
        require(row['mode'] not in grouped[key], 'duplicate paired row')
        grouped[key][row['mode']] = row
    pairs = []
    for key, modes in sorted(grouped.items()):
        require(set(modes)=={'global','block'}, 'incomplete pair')
        a,b = modes['global'],modes['block']
        require(a['seed_id']==b['seed_id'] and a['snapshot_states_sha256']==b['snapshot_states_sha256'],
                'paired inputs differ')
        if action_level:
            require(a['primary_json']==b['primary_json'] and a['target_seed']==b['target_seed'],
                    'paired original primary differs')
        row = dict(zip(fields+['repeat'],key))
        row.update(global_session=a['session'],block_session=b['session'],
                   global_candidate_id=a.get('candidate_id'),block_candidate_id=b.get('candidate_id'))
        for c in columns:
            row.update({c+'_global':a[c],c+'_block':b[c],c+'_delta':b[c]-a[c]})
        pairs.append(row)
    repeated = defaultdict(list)
    for row in pairs:
        repeated[tuple(row[k] for k in fields)].append(row)
    units = []
    for key, group in sorted(repeated.items()):
        require(len(group)==2 and {r['repeat'] for r in group}=={0,1}, 'missing repeat')
        row = dict(zip(fields,key))
        row['repeats'] = 2
        for c in columns:
            for suffix in ('global','block','delta'):
                name=c+'_'+suffix
                row[name]=mean(r[name] for r in group)
        units.append(row)
    return pairs,units


def comparison(units, columns):
    result=[]
    for snapshot in ['ALL']+sorted({r['snapshot'] for r in units}):
        group=[r for r in units if snapshot=='ALL' or r['snapshot']==snapshot]
        for c in columns:
            delta=[r[c+'_delta'] for r in group]
            result.append(dict(snapshot=snapshot,metric=c,units=len(group),
                global_mean=mean(r[c+'_global'] for r in group),
                block_mean=mean(r[c+'_block'] for r in group),mean_delta=mean(delta),median_delta=median(delta),
                block_win=sum(v>0 for v in delta),tie=sum(v==0 for v in delta),block_loss=sum(v<0 for v in delta)))
    return result


def summarize(data):
    actions,sessions,union=data['actions'],data['sessions'],data['union_actions']
    require(all(r['evaluation_status']=='verified' for r in actions+sessions), 'unresolved records must not be silently dropped')
    action_summary,session_summary,contributions,source_summary=[],[],[],[]
    for snapshot in ['ALL']+sorted({r['snapshot'] for r in actions}):
        for mode in ('global','block'):
            a=[r for r in actions if r['mode']==mode and (snapshot=='ALL' or r['snapshot']==snapshot)]
            s=[r for r in sessions if r['mode']==mode and (snapshot=='ALL' or r['snapshot']==snapshot)]
            u=[r for r in union if r['mode']==mode and (snapshot=='ALL' or r['snapshot']==snapshot)]
            counts=Counter(r['candidate_vs_seed'] for r in a)
            action_summary.append(dict(snapshot=snapshot,mode=mode,actions=len(a),
                no_witness=counts['no_witness'],failure_rate=counts['no_witness']/len(a),
                candidate_better=counts['better'],candidate_equal=counts['equal'],candidate_worse=counts['worse'],
                seed_already_committed=sum(r['seed_already_in_S'] for r in a),
                primary_seed_observed=sum(r['primary_seed_observed'] is True for r in a),
                primary_seed_satisfied=sum(r['primary_seed_satisfied'] is True for r in a),
                primary_missing_after_seed=sum(r['primary_missing_after_seed'] is True for r in a),
                positive_pairing_actions=sum(r['first_observation_pairing']>0 for r in a),
                **moments(a,ACTION_METRICS)))
            session_summary.append(dict(snapshot=snapshot,mode=mode,sessions=len(s),
                no_witness_actions=sum(r['no_witness'] for r in s),
                all_failed_sessions=sum(r['timely_witnesses']==0 for r in s),
                distinct_candidates=sum(r['distinct_candidates'] for r in s),
                cross_action_duplicates=sum(r['cross_action_duplicates'] for r in s),
                U_less_equal_greater_than_sum=json.dumps([sum(r['U_minus_sum_g_x']<0 for r in s),
                    sum(r['U_minus_sum_g_x']==0 for r in s),sum(r['U_minus_sum_g_x']>0 for r in s)]),
                U_prime_less_equal_greater_than_sum=json.dumps([sum(r['U_prime_minus_sum_h_x']<0 for r in s),
                    sum(r['U_prime_minus_sum_h_x']==0 for r in s),sum(r['U_prime_minus_sum_h_x']>0 for r in s)]),
                **moments(s,SESSION_METRICS)))
            for action in (0,1,2):
                g=[r for r in u if r['action']==action]
                contributions.append(dict(snapshot=snapshot,mode=mode,action=action,records=len(g),
                    no_witness=sum(r['witness_status']=='no_witness' for r in g),
                    **moments(g,('g_x','h_x','union_marginal','residual_union_marginal','original_full_action_seconds'))))
            for source in sorted({r['candidate_source'] for r in u}):
                g=[r for r in u if r['candidate_source']==source]
                source_summary.append(dict(snapshot=snapshot,mode=mode,candidate_source=source,actions=len(g),
                    total_g_x=sum(r['g_x'] for r in g),total_h_x=sum(r['h_x'] for r in g),
                    ordered_U_contribution=sum(r['union_marginal'] for r in g),
                    ordered_U_prime_contribution=sum(r['residual_union_marginal'] for r in g),
                    full_action_seconds=sum(r['original_full_action_seconds'] for r in g),
                    causal_contribution='not identified; full action cost includes earlier failed/restricted queries',
                    direct_seed_full_cost_seconds=None,per_query_phase_costs='unknown'))
    action_columns=('g_x','h_x','seed_fallback_quality_upper_bound')
    session_columns=('U','U_prime')
    action_pairs,action_units=paired(actions,action_columns,True)
    session_pairs,session_units=paired(sessions,session_columns,False)
    return dict(action_summary=action_summary,session_summary=session_summary,ordered_contributions=contributions,
        source_summary=source_summary,action_pairs=action_pairs,action_units=action_units,
        session_pairs=session_pairs,session_units=session_units,
        action_comparison=comparison(action_units,action_columns),session_comparison=comparison(session_units,session_columns),
        original_decision='STOP; unchanged',definition='对已保存候选的离线重新评价',
        dependency_note='54 descriptive action units are correlated within sessions; session comparison uses 18 snapshot × solver-seed units after averaging two repeats',
        seed_fallback_note='saved-candidate single-model quality upper bound only; direct seed full cost is unknown; no speed or throughput claim',
        union_note='fixed saved-model offline set value, not a generator run against updated coverage')


def short(snapshot):
    if snapshot=='ALL':return '全部'
    name,prefix=snapshot.split('/')
    name='Bromberger' if 'Bromberger' in name else 'SMT_random' if 'SMT_random' in name else 'convert'
    return name+'/'+prefix


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    audit=args.audit.resolve();out=args.output.resolve()
    require(not out.is_relative_to(audit) and not audit.is_relative_to(out), 'summary must use an independent directory')
    out.mkdir(parents=True,exist_ok=False)
    provenance=json.loads((audit/'audit.json').read_text())
    require(provenance['all_records_verified'] and provenance['no_search_attempts'],'offline audit incomplete')
    for name,expected in provenance['output_hashes'].items():
        require(file_hash(audit/name)==expected,'offline audit output changed: '+name)
    summary=summarize(json.loads((audit/'details.json').read_text()))
    for name,value in summary.items():
        if isinstance(value,list):write_csv(out/(name+'.csv'),value)
    summary['audit_sha256']=file_hash(audit/'audit.json')
    summary['summary_script_sha256']=file_hash(__file__)
    dump(out/'summary.json',summary)
    lines=['# 对已保存候选的离线重新评价：数据汇总','',
        '所有计数以冻结原评估器的 covered bit 为单位，原分母保存在恢复状态中。无候选动作的 g_x、h_x 为 0，直接候选与种子差值为空。单模型回退是离线质量上界，联合收益是已保存模型集合的价值；均不是新增在线实验。','',
        '| 状态 | 模式 | 动作 | g_m 均值 | g_x 均值／中位数 | 候选优／平／劣种子 | 无候选 | h_x 均值／中位数 |',
        '|---|---|---:|---:|---:|---:|---:|---:|']
    for r in summary['action_summary']:
        lines.append(f'| {short(r["snapshot"])} | {r["mode"]} | {r["actions"]} | {r["g_m_mean"]:.2f} | {r["g_x_mean"]:.2f}／{r["g_x_median"]:.2f} | {r["candidate_better"]}／{r["candidate_equal"]}／{r["candidate_worse"]} | {r["no_witness"]} | {r["h_x_mean"]:.2f}／{r["h_x_median"]:.2f} |')
    lines+=['','| 状态 | 模式 | 会话 | Σg_x 均值 | U 均值／中位数 | Σh_x 均值 | U′ 均值／中位数 |','|---|---|---:|---:|---:|---:|---:|']
    for r in summary['session_summary']:
        lines.append(f'| {short(r["snapshot"])} | {r["mode"]} | {r["sessions"]} | {r["sum_g_x_mean"]:.2f} | {r["U_mean"]:.2f}／{r["U_median"]:.2f} | {r["sum_h_x_mean"]:.2f} | {r["U_prime_mean"]:.2f}／{r["U_prime_median"]:.2f} |')
    lines+=['','两次重复先平均。动作的 54 个描述性组合存在会话内相关性，不作为 54 个独立实验；联合收益采用 18 个快照×solver seed 会话组合。胜平负方向均为 block 相对 Global。','',
            '| 层次 | 状态 | 指标 | 组合数 | 差均值／中位数 | block 胜／平／负 |','|---|---|---|---:|---:|---:|']
    for level,name in [('动作','action_comparison'),('会话','session_comparison')]:
        for r in summary[name]:
            lines.append(f'| {level} | {short(r["snapshot"])} | {r["metric"]} | {r["units"]} | {r["mean_delta"]:.2f}／{r["median_delta"]:.2f} | {r["block_win"]}／{r["tie"]}／{r["block_loss"]} |')
    lines+=['','原始成本保存在三份主表、session_totals.csv 和 query_costs.csv；每个动作计入全部失败查询及目标、固定约束等阶段，会话初始化只计一次。逐查询阶段成本无法从原日志准确拆分，明确为 unknown。候选来源是时序关联，不能作为独立因果归因。','',
            '**原第六轮停止扩大实验的决定保持不变。**','']
    (out/'report.md').write_text('\n'.join(lines))
    dump(out/'COMPLETE.json',dict(offline_only=True,original_decision='STOP; unchanged',
        input_audit_sha256=file_hash(audit/'audit.json'),
        output_hashes={p.name:file_hash(p) for p in sorted(out.iterdir()) if p.is_file()}))
    print(json.dumps(dict(action_units=len(summary['action_units']),session_units=len(summary['session_units']),output=str(out))))


if __name__=='__main__':main()
