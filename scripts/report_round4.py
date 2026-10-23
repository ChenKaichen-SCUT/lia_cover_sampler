#!/usr/bin/env python3
"""Report only the completed round-four mechanisms and 120-second screen."""
import argparse,json,csv
from pathlib import Path
from collections import Counter,defaultdict
from statistics import mean
from run_iteration import ROOT
def main():
    p=argparse.ArgumentParser();p.add_argument('--state',type=Path,default=ROOT/'results/round4_dev/workflow.json')
    p.add_argument('--output',type=Path,default=ROOT/'docs/第四轮实验报告.md');a=p.parse_args();state=json.loads(a.state.read_text())
    screen=Path(state['screen']);data=json.loads((screen/'analysis/analysis.json').read_text());mechanisms=json.loads((screen/'analysis/mechanisms.json').read_text())
    if data['budget_seconds']!=120:raise ValueError('This report is only for the formal 120s screen; refusing to label smoke data as performance')
    diag=Path(state['diagnosis']);replay=Path(state['replay']);fixed=json.loads((replay/'analysis.json').read_text())
    out=['# 第四轮实验报告','',f'状态：{state["status"]}。冻结起点 31cc227；A/B 生成源码固定为 269de45，既有二进制 SHA-256 a8d36788502fe3feb685f5acf8ffba555ef8d11c01b1f4dd9f04ea41421629de。仅比较 ab_reference 与 ab_polish；第三轮完整版只作原因诊断。','',
         '## 实现与隔离证据','',
         '完整冻结 A/B 源码以独立命名空间编译；原局部方向、目标求解、探索和控制逻辑保留。输出适配补丁可逐行检查 vendor/frozen_ab/output_adapter.patch。精修不反馈 current、seed_bank、内部覆盖、冲突缓存、探索阻塞或 RNG；调试模式逐调用比较这些状态及求解器表达式。关闭精修的受控操作前缀与冻结 A/B 的模型、类别和决策计数一致。',
         '', '精修只使用流式 safe，多步总预算 6 步/24 个候选/60ms，每步 4 个；没有 LineDomain 回退或新增调度器。独立随机种子只由运行种子、原始模型序号和固定标签派生。正式输出保留原模型所有尚未见过的 0/1 观察状态，缺少合格候选时回退；内部原始模型不计入正式样本数。',
         '', '额外成本包含冷初始化、门控、结构分析、候选评分及替换检查，额度 B0=0.06s、eta=0.05；不足即跳过，不等待。首模型直接输出；记录不可中断超额并在后续扣除。额度不保证墙钟性能最多下降 5%。gate 只使用当前分支观察、覆盖、最近收益和已知区域自由度，不使用文件名或实例身份。',
         '', '最终 Release 与 ASan/UBSan 回归记录见 results/round4_dev/builds。LSan 沿用已独立复现的冻结 Z3 分配栈窄范围例外。文件改名的纯门控输入测试通过；实际限时调用可能因运行成本差异选择不同候选，不能要求两次真实时间轨迹完全相同。',
         '', '## A：固定快照分层诊断','',
         '复用第三轮十二个开发快照，按模块/实例/种子和方向来源保存原因次数、耗时。普通运行只用轻量计数；完整 trace 仅作重放诊断。以下方向、目标和批次的单位不同，不把嵌套耗时相加；第三轮历史逐调用原因未保存，不能事后恢复。']
    light=list(csv.DictReader((diag/'lightweight_reasons.csv').open()));groups=defaultdict(lambda:[0,0.])
    for r in light:
        if r['code'].startswith('diag_source_'):continue
        k=(r['module'],r['code']);groups[k][0]+=int(r['count']);groups[k][1]+=float(r['seconds'])
    out+=['','| 模块 | 分层原因码 | 次数 | 累计秒 |','|---|---|---:|---:|']
    for (module,code),(count,seconds) in sorted(groups.items()):
        if any(s in code for s in ('_target_','_phase_','_batch_','_stage_NUMERIC_HIT_NOT_OBSERVED','_stage_TARGET_POOL_EMPTY','_stage_BUDGET_EXIT','_stage_BUDGET_OBSERVED')):out.append(f'| {module} | {code} | {count} | {seconds:.6f} |')
    layers=defaultdict(lambda:[0,0.]);failure_codes={'NEW_OBSERVATION_ONLY','ZERO_INCREMENT'}
    for r in csv.DictReader((diag/'trace_reasons.csv').open()):
        unit=r['unit'];code=r['code'];stage=r['stage']
        # Parent direction/terminal-batch costs overlap their children. Do not sum them.
        if unit=='batch' and stage in ('region','direction_generation'):layer='方向构造/区域准备'
        elif unit not in ('phase','target'):continue
        elif 'BUDGET' in code or (code=='FEASIBILITY_NOT_CHECKED' and 'budget' in stage):layer='执行预算'
        elif unit=='phase':layer='可行性/目标准备'
        elif code.startswith('DUPLICATE_') or code=='SAME_AS_BASE':layer='见证新颖性'
        elif code in failure_codes:layer='候选质量不足'
        elif code=='NEW_COVERAGE':layer='新覆盖成功'
        else:layer='观察/目标取值'
        k=(r['instance'],layer,unit,code);layers[k][0]+=int(r['count']);layers[k][1]+=float(r['seconds'])
    out+=['','按实例拆分的阶段终止结果如下。次数的单位分别为批次、方向准备或目标尝试；不能把它们除以同一个分母。成本为走到该终止状态的实测工作，含 trace 开销；不叠加父方向和终止批次。无方向证明的成本含此前区域准备；polish 平坦步骤是父步骤成本，单独保留在上表。',
        '', '| 案例 | 层次 | 单位 | 原因 | 次数 | 累计秒 |','|---|---|---|---|---:|---:|']
    for (case,layer,unit,code),(count,seconds) in sorted(layers.items(),key=lambda x:(x[0][0],x[0][1],-x[1][1])):
        out.append(f'| {case} | {layer} | {unit} | {code} | {count} | {seconds:.6f} |')
    independent=json.loads((diag/'independent_checks.json').read_text());checks=independent['checks']
    out+=['',f'独立作用域复核 {len(checks)} 项，其中未知 {independent["unknown"]} 项；unknown 不产生不可达证明。重复查询只检查是否有其他见证，不把一个重复见证视为整条方向耗尽。逐次记录、方向终止混合原因及分组详见 {diag.relative_to(ROOT)}/trace_reasons.csv。',
         '', '所有方向结论保留 safe_region 或 structural_line 作用域。safe 的数值命中后未观察与精确 Reach 空集分开；首次观察不会计为新增覆盖。较大预算诊断保存为 budget_followup 文件，原 60ms 失败记录保留。']
    micro=json.loads((diag/'replay/analysis.json').read_text())
    out+=['','| 模块 | 诊断模式 | 模块累计秒 | 新候选调用/总调用 |','|---|---|---:|---:|']
    for r in micro['summary']:out.append(f'| {r["module"]} | {r["configuration"]} | {r["module_seconds"]:.4f} | {r["valid_candidate_runs"]}/{r["runs"]} |')
    out+=['','trace 序列包含完整现场记录的开销，其预算下原因比例不等于历史普通运行的比例。轻量计数与关闭计数均使用同一仪表化代码骨架，不能把两者差值当作相对于第三轮原二进制的完整开销测量。',
         '', '## B：固定 A/B 原始前缀的输出变换','',
         '| 案例 | 种子 | 原始模型 | 实际输出 | 原始覆盖 % | 变换后覆盖 % | 额外秒 |','|---|---:|---:|---:|---:|---:|---:|']
    for r in fixed['rows']:out.append(f'| {r["instance"]} | {r["seed"]} | {r["raw_prefix"]} | {r["emitted"]} | {r["raw_percent"]:.4f} | {r["emitted_percent"]:.4f} | {r["summary"]["extra_seconds"]:.4f} |')
    accepted=sum(r['summary']['output_statistics']['counts'].get('replacement_accepted',0) for r in fixed['rows'])
    attempted=sum(r['summary']['output_statistics']['counts'].get('polish_attempted_raw_models',0) for r in fixed['rows'])
    out+=['',f'固定前缀诊断实际尝试精修 {attempted} 个原始模型，接受替换 {accepted} 个；以尝试模型为分母的接受率为 {100*accepted/attempted if attempted else 0:.2f}%。各步观察保留率为 100%，这是必须成立的正确性条件。']
    out+=['','每个已经处理的原始前缀均独立验证观察集合包含关系及覆盖不降。这是固定原始序列的输出变换，不是实时性能：历史时间只用于分配额外预算额度，输出时间戳重新记录实测变换时间，不能把精修当作免费工作。',
         '', '## C：120 秒、三开发例 × 三种子、两配置','',
         '| 配置 | 次数 | 平均覆盖 % | 正式样本数 |','|---|---:|---:|---:|']
    for r in data['configurations']:out.append(f'| {r["configuration"]} | {r["runs"]} | {r["mean_percent"]:.4f} | {r["samples"]} |')
    out+=['','| 案例 | 120s 候选相对 A/B 平均差 pp |','|---|---:|']
    for c,v in data['per_case_mean_delta'].items():out.append(f'| {c} | {v:+.4f} |')
    out+=['','| 指标 | 预算 | 完成配对 | 平均差 pp | 中位数差 pp | 胜/平/负 |','|---|---:|---:|---:|---:|---|']
    for r in data['paired']:
        avg='未完成' if r['mean_delta_pp'] is None else f'{r["mean_delta_pp"]:+.4f}';med='未完成' if r['median_delta_pp'] is None else f'{r["median_delta_pp"]:+.4f}'
        out.append(f'| {r["metric"]} | {r["budget"]} | {r["paired"]} | {avg} | {med} | {r["wins"]}/{r["ties"]}/{r["losses"]} |')
    counts=Counter();refinement=Counter();refine_seconds=Counter()
    for r in mechanisms:
        if r['configuration']=='ab_polish':
            m=r['metadata'];counts.update(m.get('output_statistics',{}).get('counts',{}))
            refinement.update(m.get('refinement_statistics',{}).get('counts',{}));refine_seconds.update(m.get('refinement_statistics',{}).get('seconds',{}))
    out+=['','## 替换、成本和结构适用性','',
        f'记录的候选检查 {refinement["replacement_candidates_checked"]} 次，观察保留通过 {refinement["replacement_preserves_observations"]} 次、因保留条件拒绝 {refinement["replacement_rejected_by_preservation"]} 次；实际接受替换 {counts["replacement_accepted"]} 次，回退原始模型 {counts["fallback_to_raw"]} 次。候选级与模型级次数不混用。保留检查耗时 {refine_seconds["replacement_preservation_checks"]:.6f}s。',
        '', '门控与替换计数（检查点可能截尾，完整事务成本另列）：','']
    out += ['- '+k+': '+str(v) for k,v in sorted(counts.items())]
    raw=json.loads((screen/'STATE_AUDIT_COMPLETE.json').read_text())
    out+=['','| 配置 | 案例/种子 | 内部原始模型 | 正式输出 | 待完成事务 | 额外秒 | 已记录 A/B 主流程秒 |','|---|---|---:|---:|---:|---:|---:|']
    for r in sorted(raw['rows'],key=lambda x:(x['configuration'],x['instance'],x['seed'])):
        c=r['cost'];out.append(f'| {r["configuration"]} | {r["instance"]}/{r["seed"]} | {r["raw_models"]} | {r["emitted_models"]} | {r["pending_raw_models"]} | {c.get("extra_seconds",0):.4f} | {c.get("ab_main_seconds",0):.4f} |')
    out+=['','所有已完成输出事务均核验原始/正式观察包含关系；被截止打断的未完成事务单列，不能把其未记录部分当作零成本。门控中的 structure_unknown 表示尚未精确计算结构自由度，不能当作跨分支可行证明。细分无候选原因应按 A 阶段实例/来源/预算查看，不使用案例身份解释差异。',
        '', '输出变换的收益由 B 阶段隔离确认；C 阶段仍可能因精修占用墙钟、独立求解超时或运行调度改变原始模型序列。没有逻辑反馈不等于真实时间轨迹逐模型相同。',
        '', '## 预设筛选与结论边界','', '筛选条件全部满足。' if data['promising'] else '未通过全部预设条件，停止本轮后续长预算。']
    out += [f'- {k}: {v}' for k,v in data['checks'].items()]
    out+=['','本轮只完成指定的 120 秒筛选，不自动运行 300/900 秒，不加入第三个配置。前 k 指标仅取真实正式输出前缀；不足 k 保留未完成，不能把少数完成案例外推到全部案例。HighDiv 和前两轮、第三轮历史结果没有重新生成。',
        '', '## 实测成本','',
        f'A 阶段进程 CPU {micro["cpu_seconds"]:.2f}s，含交错审计墙钟 {micro["wall_seconds"]:.2f}s；模型审计 CPU {micro["audit_cpu_seconds"]:.2f}s，独立作用域检查墙钟 {independent["wall_seconds"]:.2f}s；预算跟进另见 budget_followups.json。',
        f'B 阶段进程 CPU {fixed["cpu_seconds"]:.2f}s，含交错审计墙钟 {fixed["wall_seconds"]:.2f}s；审计 CPU {fixed["audit_cpu_seconds"]:.2f}s。',
        f'C 阶段采样 CPU {data["sampling_cpu_seconds"]:.2f}s，墙钟 {data["sampling_wall_seconds"]:.2f}s；正式输出审计 CPU/墙钟 {data["formal_audit_cpu_seconds"]:.2f}/{data["formal_audit_wall_seconds"]:.2f}s，原始/正式状态复核 CPU/墙钟 {data["state_audit_cpu_seconds"]:.2f}/{data["state_audit_wall_seconds"]:.2f}s。',
        '', '编译、早期试运行和失败诊断并未全部计量，不能把这些数字当作整个开发过程的完整成本。逐案例、逐种子原始数字及完整命令保存在本轮 results 目录。']
    a.output.write_text('\n'.join(out)+'\n');print(a.output)
if __name__=='__main__':main()
