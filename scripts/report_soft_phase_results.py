#!/usr/bin/env python3
"""Generate a Chinese evidence report; no extrapolation from filtered prefixes."""
import csv,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def read_csv(p):
    with p.open() as f:return list(csv.DictReader(f))
def main():
    exposure=ROOT/'results/soft_phase_exposure';screen=ROOT/'results/soft_phase_screen';validation=ROOT/'results/soft_phase_validation'
    native=json.loads((validation/'release/native_check.json').read_text());off=json.loads((validation/'hook_off/hook_off_check.json').read_text())
    formal=(screen/'decision.json').exists();out=screen if formal else exposure
    decision=json.loads((out/'decision.json').read_text());audit=json.loads((out/'audit_summary.json').read_text());rows=read_csv(out/'sessions.csv');pairs=read_csv(out/'paired_summary.csv');gate=json.loads((exposure/'gate.json').read_text())
    lines=['# 覆盖软偏好实现与实验报告','', '本轮基于 `07098bc`，独立分支 `coverage-soft-phase`。没有执行 HighDiv、投影、编译表示或额外受限查询；历史结果保持不变。所有性能配置使用同一补丁库和原公式，偏好只影响普通决策的相位。','']
    if formal:
        positive=decision['positive_formulas']
        verdict='存在跨两个开发公式的可重复正面信号；仅建议独立同预算验证，不自动扩大。' if positive==2 else '仅一个开发公式通过冻结门槛：有限正面信号，只建议独立实例验证。' if positive==1 else 'No-Go：未达到冻结性能门槛，停止扩大本轮方向；不增加干预强度、摘要规模或新调度。'
    else:verdict='当前未进入 60 秒性能筛选：功能曝光门槛没有全部满足。不能由空介入或未通过验证的会话推断覆盖反馈的性能；停止扩大并保留全部曝光数据。'
    lines+=['**判定：'+verdict+'**','', '以下结果严格区分实际改写、最终满足偏好和性能收益；新观察压缩仅为离线派生统计。关闭显式偏好不会撤销此前的学习及搜索历史。','', '## 正确性与生效','', f'原生回归中实际改写后被最终模型放弃的事件为 {native["actual_overrides_abandoned"]} 次。相同固定操作额度的小公式上，冻结库与关闭钩子的补丁库共 {off["decision_trace_lines"]} 条原生决策记录和完整模型轨迹相同。原生 RNG 调用位置与 guess/decide 静态一致；未声称测量不可见的 RNG 总调用计数。', '', 'Release、ASan/UBSan 的执行记录分别保存于 results/soft_phase_validation/release 和 sanitize。原生补丁单元直接链接 Z3，覆盖传播避让、特殊接口、替换清除、完整解集合、8 次和组内一次额度、回溯重启、冲突限额、Unknown 与外部取消。已有适用回归没有删除。','',f'曝光门槛记录：`{json.dumps(gate,ensure_ascii=False)}`。','']
    lines+=['## '+('60 秒正式开发筛选' if formal else '30 秒功能曝光（不作性能结论）'),'', '| 公式 | seed | 模式 | 合法模型 | covered bit | 新观察 | 实际改写 | 首模型秒 | 新增机制秒 |','|---|---:|---|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        short='random' if 'random_LIA' in r['formula'] else 'real'
        first=f"{float(r['first_model_seconds']):.3f}" if r['first_model_seconds'] else '未完成'
        lines.append(f"| {short} | {r['seed']} | {r['configuration']} | {r['models']} | {r['covered']} | {r['new_observations']} | {r['applied']} | {first} | {float(r['extra_seconds']):.4f} |")
    lines+=['','按完整会话配对，三个配置同步且轮换物理核；这些是已接触开发实例。查询成本、失败查询、初始化、准备、验证、写出与阻塞都计入会话。查表成本包含在 check 内，新增机制列用于说明开销构成，不能再加到总墙钟上。完整分阶段时间在各会话 summary.json。','', '| 公式 | 比较 | 胜/平/负 | covered 差中位数 | covered 差均值 |','|---|---|---|---:|---:|']
    for p in pairs:
        short='random' if 'random_LIA' in p['formula'] else 'real'
        lines.append(f"| {short} | {p['left']} − {p['right']} | {p['wins']}/{p['ties']}/{p['losses']} | {p['median_difference']} | {p['mean_difference']} |")
    lines+=['','逐 seed、前 2/5/10/50/100 原始样本与新观察派生序列分别见 prefixes.csv；双方均达到才产生 prefix_pairs.csv 差值，其余保留未完成。新观察派生序列到达时间来自原始写出记录，不能冒充筛选改变了生成器性能。time_coverage.csv 保存 10/30/60 秒覆盖；equal_coverage_time.csv 保存达到 plain 最终覆盖 25/50/75/100% 的时间，未达到留空。sessions.csv 保留零新观察比例、最长高原及截止尾段删失标记。','', '## 冻结门槛逐公式检查','']
    for d in decision['formulas']:
        lines.append('- '+('random' if 'random_LIA' in d['formula'] else 'real')+'：`'+json.dumps(d,ensure_ascii=False)+'`')
    lines+=['', '## 摘要及原始覆盖独立复核','',f'本阶段保存 {sum(r["distinct"] for r in audit["runs"])} 个及时不同合法正式模型；完成 {sum(r["subset_checks"] for r in audit["runs"])} 次条件摘要包含检查和 {sum(r["lower_bound_checks"] for r in audit["runs"])} 次单相位 covered 下界检查。冻结 ManualSatisfiesMetric 逐模型完整状态、边际、最终覆盖均核对。独立审计禁止调用求解器搜索。','', 'audit/*/phase_queries.csv 区分提出、映射、遇到、相同原生相位、实际覆盖及跳过原因；phase_realization.csv 分别记录建议是否最终实现。相位间摘要会重叠，同一模型的总收益不作逐原子相加归因。未映射不被擅自归因为已简化消去。摘要缺失、截断和关闭原因在 summaries.json。','', '## 核心问题','', '1. **是否改变了原生相位、是否可以放弃？** 原生单元确认实际覆盖与回溯放弃；真实公式的覆盖次数见会话表和曝光门槛，零介入会话不算已检验机制。','2. **是否比随机扰动更有用？** 只能由 coverage−random 的逐实例配对及冻结门槛判断，不能由覆盖模式自身成功次数推断。判定见报告首段及逐公式表。','3. **是否提高单位时间新观察和同数量样本覆盖？** sessions.csv 与原始序列 prefix_pairs.csv 分别回答；二者与吞吐保护、早期退步和成本一起验收，派生过滤序列不作独立生成优势证据。','', '## 重放与范围','', '```bash', 'python3 scripts/build_soft_phase_dependency.py --destination dependencies/z3-soft-phase-replay --jobs 12', '# 构建入口参见 dependencies/soft_phase_build.json（所有模式使用同一新库）', 'python3 scripts/run_soft_phase_pairs.py --stage exposure --out results/soft_phase_exposure_replay --postprocess', '# 只有正确性和实际介入门槛通过后，再运行：', 'python3 scripts/run_soft_phase_pairs.py --stage screen --out results/soft_phase_screen_replay --postprocess', '/tmp/lia-highdiv-frozen/venv/bin/python3 scripts/audit_soft_phase_results.py '+str(out.relative_to(ROOT))+' --jobs 3', '```', '', '原始模型、查询、偏好表、实际事件、收据及进程资源记录以无损 archives/ 保存，archive_manifest.json 给出每个文件哈希。plan.json 固定输入、代码、补丁库、二进制、物理核、同步时间和协议。归档可用 scripts/archive_bofill_results.py PATH --restore 解包。完整回归、构建命令和依赖哈希另存验证及 dependencies 目录。', '', '**本轮没有自动运行 900 秒实验，也不会因单个正例增加干预强度。**']
    (ROOT/'docs/覆盖软偏好实现与实验报告.md').write_text('\n'.join(lines)+'\n')
if __name__=='__main__':main()
