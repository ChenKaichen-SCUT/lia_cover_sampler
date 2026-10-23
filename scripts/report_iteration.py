#!/usr/bin/env python3
"""Render completed round-three evidence without rerunning generation or frozen audits."""
import argparse,csv,json
from collections import Counter,defaultdict
from pathlib import Path
from run_iteration import ROOT,write

def main():
    p=argparse.ArgumentParser();p.add_argument('--state',type=Path,default=ROOT/'results/round3_dev/workflow.json')
    p.add_argument('--output',type=Path,default=ROOT/'docs/第三轮实验报告.md');a=p.parse_args();state=json.loads(a.state.read_text())
    out=['# 第三轮实验报告','',f'状态：{state["status"]}。本轮基于 21befad，在独立分支 round3-execution-efficiency 修改；原 HighDiv、第一轮 LIA 与第二轮结果均未重跑或改写。','',
         '## 实现与正确性','',
         '已实现软/硬不可用区分、全部软冷却时的受限工作保持调度、顶层互斥计时、按方向流式产出、方向关联目标池、逐步 polish 预算、轮转扫描修复和空目标包明确状态。保留旧行为开关用于同状态对照。',
         '', 'C++ 回归和 Python 数学/覆盖差分回归通过。ASan/UBSan 回归通过；LeakSanitizer 对冻结 Z3 的 lp::dioph_eq::imp 大整数分配存在已独立复现的例外，仅窄范围排除该栈，其他泄漏检测保留。原失败、最小 Z3 独立复现和排除规则见 results/round3_dev/sanitize_diagnostic。',
         '', '正式覆盖只提交最终完整模型，内部候选不累积成虚假的单模型增益；LineDomain 数学语义与原覆盖分母不变。']
    source=ROOT/'results/round3_dev/scheduler_check'
    if source.exists():
        out+=['','## 先行调度对照：30 秒、三个开发例、seed 0','','| 配置 | 案例 | 样本 | 覆盖 % | 休眠占比 |','|---|---|---:|---:|---:|']
        for file in sorted(source.glob('*/audit_summary.json')):
            for r in json.loads(file.read_text()):
                w=r.get('sampler',{}).get('wall_timing',{});idle=f'{100*w["idle_fraction"]:.2f}%' if w else '旧二进制无此计时'
                out.append(f'| {file.parent.name} | {r["instance"]} | {r["unique_valid_in_budget"]} | {r["percent"]:.3f} | {idle} |')
        out+=['','该对照在修改局部候选生成前执行。休眠下降是执行问题的修复，不能单独证明覆盖提高。第二轮约 4384 秒未分项时间不能被事后全部归为休眠；新版本单独核算初始化、动作、调度、休眠、日志和其他时间。']
    if state.get('concurrency'):
        data=json.loads(Path(state['concurrency']).read_text());out+=['','## 并发吞吐校准','',f'本轮后续采用 {data["selected_jobs"]} 路。每档执行相同 15 个开发任务、15 秒上限，全部独立审计。','',
            '| 并发 | 采样墙钟秒 | CPU 秒 | CPU 利用率 | 峰值 RSS MiB | 归一化有效工作/秒 |','|---:|---:|---:|---:|---:|---:|']
        for r in data['rows']:out.append(f'| {r["jobs"]} | {r["wall_seconds"]:.2f} | {r["cpu_seconds"]:.2f} | {100*r["cpu_utilization_fraction"]:.1f}% | {r["peak_rss_kib"]/1024:.1f} | {r["normalized_work_per_second"]:.5f} |')
        out+=['','不同并发下的覆盖不用于算法胜负比较；最终候选与 A/B 使用同一并发队列。峰值内存是 100ms 采样值，可能漏过更短峰值；上下文切换和 cgroup throttling 见 selection.json。',
            f'并发校准共用采样 CPU {sum(r["cpu_seconds"] for r in data["rows"]):.2f}s、采样墙钟 {sum(r["wall_seconds"] for r in data["rows"]):.2f}s；审计 CPU {sum(r.get("audit_cpu_seconds",0) for r in data["rows"]):.2f}s、审计墙钟 {sum(r.get("audit_wall_seconds",0) for r in data["rows"]):.2f}s。']
    if state.get('micro'):
        data=json.loads((Path(state['micro'])/'analysis.json').read_text());out+=['','## 固定快照模块对照','',
            '快照只来自三个开发案例的已保存前缀；包含公式、当前候选、精确 AST 覆盖、种子和哈希。未恢复求解器学习状态，不把独立模块增益相加当作采样轨迹收益。首次候选耗时仅对成功返回的调用取均值，同时报告成功数，避免掩盖失败。','',
            '| 模块 | 版本 | 产生新合法候选/运行 | 首合法均值 ms | 首正增益均值 ms | 最好增益合计 | 模块秒 | 接受移动 |','|---|---|---:|---:|---:|---:|---:|---:|']
        for r in data['summary']:
            first='—' if r['mean_first_valid'] is None else f'{1000*r["mean_first_valid"]:.3f}'
            positive='—' if r['mean_first_positive'] is None else f'{1000*r["mean_first_positive"]:.3f}'
            out.append(f'| {r["module"]} | {r["configuration"]} | {r["valid_candidate_runs"]}/{r["runs"]} | {first} | {positive} | {r["gain"]} | {r["module_seconds"]:.3f} | {r["accepted_moves"]} |')
        out+=['','polish 的候选计数指内部搜索产生新候选的调用数；全部调用均保留合法输入回退。最好增益包含输入模型相对 S 的已有增益，不能全部归因于 polish。']
        audit_cpu=data.get('audit_cpu_seconds')
        out += ['',f'微基准进程 CPU {data["cpu_seconds"]:.2f} 秒，整体墙钟（含交错审计）{data["wall_seconds"]:.2f} 秒，审计 worker 秒 {data["audit_worker_seconds"]:.2f}，审计 CPU '+(f'{audit_cpu:.2f} 秒。' if audit_cpu is not None else '未单独计量。')+'逐快照和种子效率差保存在 micro/analysis.json；单模型改进次数不是性能成功判据。']
    costs=[]
    for stage in ('screen','confirm','final'):
        if not state.get(stage):continue
        directory=Path(state[stage]);path=directory/'analysis/analysis.json'
        if not path.exists():continue
        d=json.loads(path.read_text());costs.append((stage,d))
        out+=['',f'## {stage}：{d["budget_seconds"]} 秒统一在线轨迹','',
              '| 配置 | 运行数 | 终点覆盖 % | 样本数 | 满 1000 |','|---|---:|---:|---:|---:|']
        for r in d['summary']:out.append(f'| {r["configuration"]} | {r["cases"]} | {r["mean_percent"]:.4f} | {r["samples"]} | {r["reached_1000"]} |')
        endpoint=list(csv.DictReader((directory/'analysis/endpoints.csv').open()))
        out+=['','| 案例（本阶段种子均值） | A/B 覆盖 % | 候选覆盖 % | 差 pp |','|---|---:|---:|---:|']
        for case in sorted({r['instance'] for r in endpoint}):
            values={name:[float(r['percent']) for r in endpoint if r['instance']==case and r['configuration']==name] for name in ('control_ab','candidate')}
            if not all(values.values()):continue
            old=sum(values['control_ab'])/len(values['control_ab']);new=sum(values['candidate'])/len(values['candidate'])
            out.append(f'| {case} | {old:.4f} | {new:.4f} | {new-old:+.4f} |')
        out+=['','| 候选相对 A/B | 指标 | 预算 | 配对数 | 平均差 pp | 胜/平/负 |','|---|---|---:|---:|---:|---|']
        for r in d['paired']:
            if r['metric']=='prefix' and r['budget'] not in (5,10,50,1000):continue
            delta='未完成' if r['mean_delta_pp'] is None else f'{r["mean_delta_pp"]:+.4f}'
            out.append(f'| {r["configuration"]} | {r["metric"]} | {r["budget"]} | {r["paired"]} | {delta} | {r["wins"]}/{r["ties"]}/{r["losses"]} |')
        wall=list(csv.DictReader((directory/'analysis/wall_breakdown.csv').open()))
        buckets=('parsing_and_initialization','action_wall_seconds','scheduler_decision_seconds','idle_sleep_seconds','checkpoint_and_logging_seconds','other_wall_seconds')
        out+=['','候选顶层墙钟分解（统计检查点可能轻微截尾；各项互斥）：','']
        for name in sorted({r['configuration'] for r in wall}):
            sums={k:sum(float(r[k]) for r in wall if r['configuration']==name) for k in buckets}
            out.append('- '+name+'：'+ '；'.join(f'{k}={v:.3f}s' for k,v in sums.items()))
            partial=sum(r['partial']=='True' for r in wall if r['configuration']==name)
            out.append(f'  检查点截尾 {partial} 次；记录墙钟合计 {sum(sums.values()):.3f}s。覆盖终点仍由全部完整样本和时间戳独立计算，不截断到检查点。')
        stats=json.loads((directory/'analysis/mechanisms.json').read_text());counts=Counter();times=Counter();actions=Counter()
        for r in stats:
            if r['configuration']=='candidate':
                counts.update(r['statistics'].get('counts',{}));times.update(r['statistics'].get('seconds',{}));actions.update(r['statistics'].get('action_seconds',{}))
        out+=['',f'polish 尝试步骤 {counts["polish_attempted_steps"]}，接受步骤 {counts["polish_accepted_steps"]}，改善模型 {counts["polish_improved_models"]}，改进前/后单模型增益累计 {counts["polish_gain_before"]}/{counts["polish_gain_after"]}，耗时 {times["polish_elapsed_seconds"]:.3f}s。这些是同一冻结快照下的改进诊断，不能重复加入正式覆盖。','',
              '| 父动作 | 秒 | 正式新增 bit | bit/秒 | 正式输出 |','|---|---:|---:|---:|---:|']
        for name,seconds in actions.items():
            gain=counts['action_'+name+'_gain'];out.append(f'| {name} | {seconds:.3f} | {gain} | {gain/seconds if seconds else 0:.3f} | {counts["action_"+name+"_samples"]} |')
        if (directory/'promotion.json').exists():
            decision=json.loads((directory/'promotion.json').read_text());out+=['','晋级：'+('通过' if decision['approved'] else '未通过；不执行下一阶段长预算。')]
            out += ['- 未满足：'+k for k,v in decision['checks'].items() if not v]
        out+=['',f'逐实例、逐种子配对差和缺失前缀保存在 {directory.relative_to(ROOT)}/analysis/paired_by_case_seed.csv，不能只挑最好种子。']
    if state.get('final') and (Path(state['final'])/'analysis/endpoints.csv').exists():
        final_root=Path(state['final']);entries=list(csv.DictReader((final_root/'analysis/endpoints.csv').open()))
        candidate={r['instance']:r for r in entries if r['configuration']=='candidate' and r['seed']=='0'}
        out+=['','## 与冻结对照的最终 900 秒比较','',
            '| 对照 | 对照时间覆盖 % | 候选时间覆盖 % | 时间差 pp | 共同满 1000 案例 | 对照前 1000 % | 候选前 1000 % |','|---|---:|---:|---:|---:|---:|---:|']
        for label,folder in [('HighDiv','highdiv'),('第一轮 LIA','lia')]:
            timed={r['instance']:r for r in json.loads((ROOT/'results'/(folder+'_time900')/'audit_summary.json').read_text())}
            counted={r['instance']:r for r in json.loads((ROOT/'results'/(folder+'_count1000_t900')/'audit_summary.json').read_text())}
            common=[i for i in candidate if candidate[i]['reached_1000']=='True' and counted[i]['unique_valid_in_budget']>=1000]
            old=sum(timed[i]['percent'] for i in candidate)/len(candidate)
            new=sum(float(r['percent']) for r in candidate.values())/len(candidate)
            old_k=sum(counted[i]['percent'] for i in common)/len(common) if common else 0
            new_k=sum(float(candidate[i]['prefix1000_percent']) for i in common)/len(common) if common else 0
            out.append(f'| {label} | {old:.4f} | {new:.4f} | {new-old:+.4f} | {len(common)} | {old_k:.4f} | {new_k:.4f} |')
        out+=['','新方法固定样本数来自同一次时间轨迹的真实前缀；冻结对照沿用其原独立数量组。只比较共同达到的 k，不将不同完成子集混为同一均值。旧基线的编译优化与并发负载不同，时间比较存在限制；本轮 A/B 与候选在统一队列中配对。']
    out+=['','## 成本与结论边界' ,'','| 阶段 | 采样 CPU 秒 | 采样墙钟秒 | 审计 CPU 秒 | 审计墙钟秒 |','|---|---:|---:|---:|---:|']
    for stage,d in costs:out.append(f'| {stage} | {d["sampling_cpu_seconds"]:.2f} | {d["sampling_wall_seconds"]:.2f} | {d["audit_cpu_seconds"]:.2f} | {d["audit_wall_seconds"]:.2f} |')
    phase0=ROOT/'results/round3_dev/builds/phase0_resources.json'
    if phase0.exists():
        checks=json.loads(phase0.read_text())['runs']
        out+=['',f'最终正确性回归共用 CPU {sum(r["cpu_seconds"] for r in checks):.2f}s、墙钟 {sum(r["wall_seconds"] for r in checks):.2f}s。早期增量编译、诊断和试运行未完整计量 CPU，不把以上成本声称为整轮开发总成本。']
    if not state.get('final') or not (Path(state['final'])/'analysis/analysis.json').exists():
        out+=['','本轮尚无新的 900 秒结论。只有通过固定快照、120 秒开发筛选和 300 秒确认的候选才进入正式长跑；若晋级失败，保留全部数据并报告执行效率修复与覆盖性能之间的差距，不继续叠加未经证实的启发式。']
    if state.get('screen'):
        review=Path(state['screen'])/'analysis/review.md'
        if review.exists():out+=['',review.read_text().strip()]
    out+=['',f'`-n` 代码路径与受控前缀审计见 {state.get("prefix_audit","results/round3_dev/prefix_audit_final/audit.json")}。统一轨迹仅按真实顺序取前 k 个，不重排、不事后挑选，未达到 1000 标记未完成。审计缓存绑定公式、模型、时间戳、评测器、验证代码和报告哈希；旧基线直接复用其已有证据。']
    a.output.write_text('\n'.join(out)+'\n');print(a.output)
if __name__=='__main__':main()
