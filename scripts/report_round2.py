#!/usr/bin/env python3
"""Render the completed, independently audited round-two comparison in Chinese."""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
NAMES=dict(highdiv="HighDiv 冻结",old_lia="原 LIA 冻结",control_ab="A/B 控制修复",
    schedule="＋动作调度",directions="＋方向与目标",polish="＋单模型改进",
    line="＋LineDomain／关闭 repair",full="完整版本",no_line="完整版本关闭 line",
    no_polish="完整版本关闭 polish")
ORDER=list(NAMES)
SHORT=["Bromberger","40-9","CAV-019","SMT_random","SMT_real","convert","dillig","pb2010","prime_cone"]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--analysis",type=Path,default=ROOT/"results/round2_analysis")
    p.add_argument("--runs",type=Path,default=ROOT/"results/round2_formal_v2")
    p.add_argument("--output",type=Path,default=ROOT/"docs/第二轮实验报告.md")
    a=p.parse_args()
    info=json.loads((a.analysis/"analysis.json").read_text())
    def read(name):
        with (a.analysis/name).open() as f:return list(csv.DictReader(f))
    endpoints=read("endpoints.csv");origins=read("provenance.csv");modules=read("modules.csv")
    mechanisms=json.loads((a.analysis/"mechanisms.json").read_text())
    manifest=json.loads((ROOT/"benchmarks/manifest.json").read_text())
    short={r["path"]:name for r,name in zip(manifest,SHORT)}
    summary={(r["algorithm"],r["protocol"],r["split"]):r for r in info["summary"]}
    paired=info["paired"]
    endpoint={(r["algorithm"],r["protocol"],r["instance"]):r for r in endpoints}
    completion=json.loads((a.runs/"COMPLETE.json").read_text())
    assert completion["runs"]==144 and not completion["cancelled"] and completion["errors"]==0
    plan=json.loads((a.runs/"plan.json").read_text())
    changes_path=a.runs/"concurrency_changes.jsonl"
    changes=[json.loads(s) for s in changes_path.read_text().splitlines()] if changes_path.exists() else []
    workers=changes[-1]["new_workers"] if changes else plan["workers"]
    scheduling=f'初始 {plan["workers"]} 槽，按用户要求扩为 {workers} 槽，保留在跑采样进程' if changes else f'{workers} 槽'
    queue=[json.loads(x) for x in (a.runs/"queue.jsonl").read_text().splitlines()]
    events=[]
    for r in queue:
        events.extend([(datetime.fromisoformat(r["started_utc"]),1),(datetime.fromisoformat(r["finished_utc"]),-1)])
    events.sort(key=lambda x:(x[0],x[1]));active=peak=0
    for _,delta in events:active+=delta;peak=max(peak,active)
    hours=(events[-1][0]-events[0][0]).total_seconds()/3600
    def pair(ref,protocol,metric="endpoint",budget=None,split="all"):
        return next(r for r in paired if r["algorithm"]=="full" and r["reference"]==ref and
                    r["protocol"]==protocol and r["metric"]==metric and r["budget"]==budget and r["split"]==split)
    def verdict(r):return f'{r["delta_pp"]:+.3f} 个百分点，{r["wins"]} 胜/{r["ties"]} 平/{r["losses"]} 负'
    out=["# 第二轮实验报告","",
         "全部 144 个正式任务和独立审计已完成。HighDiv 与原 LIA 直接复用保存的 seed=0 结果，没有重新运行。",""]
    for ref in ("old_lia","highdiv"):
        r=pair(ref,"time")
        out.append(f'完整版本相对 {NAMES[ref]}，900 秒九案例平均覆盖差为 **{verdict(r)}**。')
    out += ["", "完整版没有同时改善时间与严格等样本量两项目标。相对原 LIA，前 5/10/50 个输出覆盖明显提高，但前 1000 个的配对覆盖略低；相对 HighDiv，前 5/10/50 个和严格前 1000 个均落后。",
            f'仅修复 A/B 的版本在本轮九例终点均值最高：900 秒 {summary["control_ab","time","all"]["mean_percent"]:.3f}%，数量组 {summary["control_ab","count","all"]["mean_percent"]:.3f}%。完整版与其差值分别为 {pair("control_ab","time")["delta_pp"]:+.3f} 和 {pair("control_ab","count")["delta_pp"]:+.3f} pp。该排名只描述本轮单种子、既定运行负载下的结果。']
    out+=["","这里的胜负指逐案例覆盖 bit 数，平均覆盖率是各案例百分比的算术平均。只有一个配对种子，不作显著性或普遍优越性声明。","",
          "## 实验范围、冻结和正确性","",
          f"- benchmarks 中 9 个案例：3 个开发、6 个评测；8 个新配置，每个案例分别执行纯时间 900 秒和 1000 个/最多 900 秒两组。",
          f"- 所有预算组、配置共享队列（{scheduling}），调度上限 {workers}，接管后进程快照验证 15 个采样进程；采样队列墙钟约 {hours:.2f} 小时。数量任务结束后立即补位，时间组不在 1000 个样本处停止。",
          f"- 接管任务的外层开始时刻由内核启动 tick 或日志创建时间估计，原生采样计时未重置。估计时刻在换批边界产生短暂重叠，直接扫描这些时间戳会得到 {peak}，不能用它认定真实并发峰值；原始记录保留，未为消除该现象修改时间戳。",
          "- 开发阶段完成 48×30 秒、42×30 秒、12×60 秒短实验并审计，然后冻结策略参数。",
          "- 首次正式队列发现探索求解器返回不合法模型，已中止并保存；统一改用显式增量 SMT 探索。12 次压力重放的 2,562 个模型全部独立验证通过，再重新运行全部正式任务。",
          "- 此正确性修复涉及一个评测案例，故不能声称冻结后从未查看评测数据；没有根据该案例调方向、目标、调度或改进参数。详情见 [正确性事件](第二轮正确性事件.md)。",
          "- 最终二进制和源码提交见 results/round2_builds/final_fixed.json；纯 A/B 见 control_ab_fixed.json。所有新配置使用同一冻结依赖，除独立 A/B 外使用同一最终二进制。",
          f"- 原 LIA 与新方法均为 Release/O3，冻结 HighDiv 为 O0；旧基线每批至多 9 个并发案例，新队列至多 {workers} 个。时间结论受到编译级别和并发负载差异限制。","",
          "## 两组终点与逐阶段比较","",
          "数量组终点表示“达到 1000 个或用完 900 秒”。未达到数量目标的案例不能作为严格的等样本量结论。","",
          "| 配置 | 时间组平均覆盖 % | 时间组样本数 | 数量组终点覆盖 % | 达到 1000 个 |",
          "|---|---:|---:|---:|---:|"]
    for algorithm in ORDER:
        t=summary[algorithm,"time","all"];c=summary[algorithm,"count","all"]
        out.append(f'| {NAMES[algorithm]} | {t["mean_percent"]:.3f} | {t["samples"]:,} | {c["mean_percent"]:.3f} | {c["reached_1000"]}/9 |')
    out+=["","六个评测案例单独统计：","",
          "| 配置 | 时间组覆盖 % | 数量组终点覆盖 % | 数量完成 |","|---|---:|---:|---:|"]
    for algorithm in ORDER:
        t=summary[algorithm,"time","evaluation"];c=summary[algorithm,"count","evaluation"]
        out.append(f'| {NAMES[algorithm]} | {t["mean_percent"]:.3f} | {c["mean_percent"]:.3f} | {c["reached_1000"]}/6 |')
    out += ["", f'六例评测集上，完整版时间均值相对原 LIA 为 {pair("old_lia","time",split="evaluation")["delta_pp"]:+.3f} pp，相对 HighDiv 为 {pair("highdiv","time",split="evaluation")["delta_pp"]:+.3f} pp；九例总体提升主要来自开发例 convert，不应表述为评测集整体提升。']
    out+=["","## 前缀质量和早期时间","",
          "下表只使用数量组中双方均已达到相应不同合法模型数的案例。缺失案例不填补，不把各算法不同的完成子集直接平均比较。","",
          "| 对照 | 前缀 k | 配对案例 | 完整版本覆盖 % | 对照覆盖 % | 差值 pp | 胜/平/负 |",
          "|---|---:|---:|---:|---:|---:|---|"]
    for ref in ("old_lia","highdiv"):
        for k in (2,5,10,50,100,1000):
            r=pair(ref,"count","prefix",k)
            out.append(f'| {NAMES[ref]} | {k} | {r["paired_cases"]}/9 | {r["algorithm_mean"]:.3f} | {r["reference_mean"]:.3f} | {r["delta_pp"]:+.3f} | {r["wins"]}/{r["ties"]}/{r["losses"]} |')
    prefix_rows=read("prefixes.csv")
    common1000={alg:{r["instance"]:float(r["percent"]) for r in prefix_rows
                    if r["algorithm"]==alg and r["protocol"]=="count" and r["k"]=="1000" and r["complete"]=="True"}
                for alg in ("full","old_lia","highdiv")}
    common=set.intersection(*(set(v) for v in common1000.values()))
    out += ["", f'三者共同完成 1000 个的相同 {len(common)} 个案例：HighDiv {sum(common1000["highdiv"][i] for i in common)/len(common):.3f}%，原 LIA {sum(common1000["old_lia"][i] for i in common)/len(common):.3f}%，完整版 {sum(common1000["full"][i] for i in common)/len(common):.3f}%。配对表中不同对照的可用案例数不同，因此另列这个共同子集，避免直接比较不同案例集合的均值。']
    out+=["","时间组的早期覆盖使用全部九例，尚未输出模型时覆盖为 0：","",
          "| 秒数 | 完整版本 % | 原 LIA % | 相对原 LIA pp | HighDiv % | 相对 HighDiv pp |",
          "|---:|---:|---:|---:|---:|---:|"]
    for t in (1,5,10,30,60,120,300,900):
        old=pair("old_lia","time","time",t);hd=pair("highdiv","time","time",t)
        out.append(f'| {t} | {old["algorithm_mean"]:.3f} | {old["reference_mean"]:.3f} | {old["delta_pp"]:+.3f} | {hd["reference_mean"]:.3f} | {hd["delta_pp"]:+.3f} |')
    out+=["","## 逐案例结果与退化","",
          "| 案例 | 划分 | 时间：HighDiv % | 原 LIA % | 完整版 % | 完整版−原 LIA pp | 数量组：完整版实际个数 |",
          "|---|---|---:|---:|---:|---:|---:|"]
    regressions=[]
    for row in manifest:
        i=row["path"];old=endpoint["old_lia","time",i];hd=endpoint["highdiv","time",i]
        full=endpoint["full","time",i];count=endpoint["full","count",i]
        delta=float(full["percent"])-float(old["percent"])
        if delta < -1e-10:regressions.append(f'{short[i]}（{delta:+.3f} pp）')
        out.append(f'| {short[i]} | {"开发" if row["split"]=="development" else "评测"} | {float(hd["percent"]):.3f} | {float(old["percent"]):.3f} | {float(full["percent"]):.3f} | {delta:+.3f} | {int(count["samples"]):,} |')
    out+=["","相对原 LIA 的时间终点退化案例："+("、".join(regressions) if regressions else "无")+"。前缀及到达时间的逐案例数据保存在 prefixes.csv 和 early_times.csv；终点未退化也不意味着前 5/10/50 个样本都占优。","",
          "## 机制消融、真实触发与成本","",
          "正差表示完整版覆盖更高。line 配置与完整版关闭 repair 相同，其余功能和预算相同。消融同时影响后续种子和搜索轨迹，不能用来源计数代替。","",
          "| 关闭项 | 时间终点差 pp | 数量终点差 pp | 数量前 10 个差 pp（配对数） |",
          "|---|---:|---:|---:|"]
    for ref,label in (("no_line","LineDomain"),("no_polish","单模型改进"),("line","目标包修复")):
        t=pair(ref,"time");c=pair(ref,"count");k=pair(ref,"count","prefix",10)
        out.append(f'| {label} | {t["delta_pp"]:+.3f} | {c["delta_pp"]:+.3f} | {k["delta_pp"]:+.3f}（{k["paired_cases"]}） |')
    selected=[r for r in mechanisms if r["algorithm"]=="full" and r["protocol"]=="time"]
    counts=Counter()
    for r in selected:counts.update(r["counts"])
    out+=["","完整版时间组的实际机制计数：","",
          "| 指标 | 次数/数量 |","|---|---:|"]
    for key,label in (("line_domain_exact","精确 LineDomain 分析"),("line_domain_unsupported","LineDomain 不支持"),
        ("line_domain_budget_exceeded","LineDomain 分析超预算"),("line_cross_branch_moves","跨安全分支的候选/内部移动"),
        ("line_constant_reach_opportunities","常数值但 Reach 有机会改变"),
        ("direction_equation_kernel_selected","等式关联方向入选"),("direction_multi_free_selected","多自由变量方向入选"),
        ("polish_calls","单模型改进调用"),("polish_moves","接受的内部改进移动"),
        ("repair_core_removals","按 core 删除目标后重试"),("repair_expansions","目标包扩充"),
        ("repair_preserved_earlier_witness","后续查询非 SAT 时保留此前见证"),
        ("target_singleton_proofs","单目标不可达证明"),("target_multi_conflicts","多目标组合冲突"),
        ("exploration_timeout_growth","探索请求超时增长"),("exploration_block_reused","重复阻塞避免"),
        ("exploration_rebuilds","探索重建")):
        out.append(f'| {label} | {counts[key]:,} |')
    totals=defaultdict(Counter)
    for r in modules:
        if r["algorithm"]=="full" and r["protocol"]=="time":
            for key in ("seconds","calls","samples","covered","unique_valid_candidates"):
                totals[r["module"]][key]+=float(r[key])
    out+=["","父动作成本包含其准备、求解/生成、验证、必要改进和输出，且失败动作也计入：","",
          "| 动作 | 总秒数 | 调用 | 不同合法候选 | 正式输出 | 正式新增 bit |","|---|---:|---:|---:|---:|---:|"]
    for module,c in totals.items():
        out.append(f'| {module} | {c["seconds"]:.2f} | {int(c["calls"]):,} | {int(c["unique_valid_candidates"]):,} | {int(c["samples"]):,} | {int(c["covered"]):,} |')
    stage=Counter()
    for r in selected:stage.update(r["stage_seconds"])
    refinement=sum(r["refinement_seconds"] for r in selected)
    action_total=sum(c["seconds"] for c in totals.values())
    process_total=sum(float(r["process_seconds"]) for r in endpoints if r["algorithm"]=="full" and r["protocol"]=="time")
    out+=["",f'嵌套细分统计：方向构造 {stage["directions"]:.2f} 秒，LineDomain 路径 {stage["line_analysis"]:.2f} 秒，目标缓存检查 {stage["conflict_cache_check"]:.2f} 秒，单模型改进 {refinement:.2f} 秒。这些计时存在父子包含关系，不能与父动作或彼此重复相加。',
          f'父动作合计 {action_total:.2f} 秒，九例进程墙钟合计 {process_total:.2f} 秒，差额 {process_total-action_total:.2f} 秒没有完整分项计时。代码在全部动作冷却时会短暂休眠，此外差额也包含初始化、动作外调度及截止截尾；不能将差额全算作求解或 LineDomain 分析成本。',
          "硬截止运行的机制统计取最后一次 progress 检查点，存在轻微截尾；精确样本数与覆盖以完整输出和独立审计为准。","",
          "## 覆盖收益来源","",
          "以下按原种子来源汇总完整版时间组。正式收益只计一次：最终收益＝种子保留＋改进新增；种子原有收益＝种子保留＋改进丢失。初始模型提供观察基准，通常没有已覆盖 bit 的增量。","",
          "| 种子来源 | 输出数 | 最终新增 bit | 原种子保留 | 改进新增 | 改进丢失 |","|---|---:|---:|---:|---:|---:|"]
    provenance=defaultdict(Counter)
    for r in origins:
        if r["algorithm"]=="full" and r["protocol"]=="time":
            for key in ("samples","final_gain","seed_gain_retained","refinement_added","seed_gain_lost"):
                provenance[r["origin"]][key]+=int(r[key])
    for origin,c in provenance.items():
        out.append(f'| {origin} | {c["samples"]:,} | {c["final_gain"]:,} | {c["seed_gain_retained"]:,} | {c["refinement_added"]:,} | {c["seed_gain_lost"]:,} |')
    convert={r["origin"]:r for r in origins if r["algorithm"]=="full" and r["protocol"]=="time" and r["instance"].startswith("convert")}
    out += ["", f'convert 单例的最终新增 bit 按原种子来源为：探索 {int(convert["exploration"]["final_gain"]):,}、全局目标 {int(convert["target"]["final_gain"]):,}、安全局部 {int(convert["local_safe"]["final_gain"]):,}、LineDomain {int(convert["local_line"]["final_gain"]):,}。探索种子的改进新增只有 {int(convert["exploration"]["refinement_added"])} bit、丢失 {int(convert["exploration"]["seed_gain_lost"])} bit，因此不能把该例收益归为局部模命中或跨分支优势。该例 A/B 控制修复版本时间覆盖为 {float(endpoint["control_ab","time",next(r["path"] for r in manifest if r["path"].startswith("convert"))]["percent"]):.3f}%，完整版为 {float(endpoint["full","time",next(r["path"] for r in manifest if r["path"].startswith("convert"))]["percent"]):.3f}%。']
    total_gain=sum(c["final_gain"] for c in provenance.values())
    out += ["", f'探索种子贡献最终新增 bit 的 {100*provenance["exploration"]["final_gain"]/total_gain:.2f}%，local_line 种子贡献 {100*provenance["local_line"]["final_gain"]/total_gain:.2f}%。实际跨安全分支移动只有 {counts["line_cross_branch_moves"]} 次，全部发生于 SMT_random（6 次）与 SMT_real（2 次）；两例时间终点均退化，因此跨分支能力已触发，但本次分析成本没有转化为整体优势。',
            f'全局目标动作另有 {counts["action_target_status_"]:,} 次记录为空状态名，来自修复目标包在没有实际查询时返回默认结果的路径；不能将其归为 SAT、UNSAT 或超时。这是本轮诊断记录仍需补全的缺口，原统计未重标记。']
    out+=["","## 已确认修复与仍存限制","",
          "- 回归确认：独立探索预算增长、重复阻塞去重、探索正式进展反馈、动作完整成本、冷却后重试、等式作用域、{0,10} 跨分支、负系数/严格比较/无界集合、方向配额、公平候选、单模型协同、区域重建、常数值 Reach、三目标冲突不误伤兼容对、超时非证明、归因不重复计数。",
          "- 实际触发与减少的浪费以以上计数为证：避免重复阻塞，过滤单目标不可达，修复当前冲突包，保留已获得见证；它们不自动等价于最终覆盖提高，必须结合消融和逐案例退化。",
          "- 保守分析仍可能超预算或不支持，算术 ite 没有实现分段仿射跨分支；精确有理核方向不等于完整整数核枚举。",
          "- 早期低增益局部输出、目标注册增长、模型验证与分析成本仍可能限制样本效率。按本次结果判断收益，不能因机制正确就宣称两个预算组全面领先。","",
          "## 审计与复现文件",""]
    counts=info["validation"]
    out+=[f'分析包含 {info["runs"]} 个正式/冻结对照运行。完整合法输出行合计 {counts["valid"]:,}，非法 {counts["invalid"]}，未知 {counts["unknown"]}，格式错误 {counts["malformed"]}，覆盖差分不一致 {counts["coverage_mismatch"]}。冻结 HighDiv 中缺少末尾时间戳的行仍排除在预算内比较之外。',
          "",
          "- results/round2_formal_v2：144 个正式运行的计划、命令、时间戳、审计、原始压缩包及成员 SHA-256。",
          "- results/round2_analysis：summary/endpoints/prefixes/early_times/paired/modules/provenance CSV，mechanisms.json 和输入证据哈希。",
          "- results/round2_dev01、dev02、dev03：完整开发过程；results/round2_formal：中止的第一次正式尝试；results/round2_diagnostics：错误现场与修复验证。",
          "- [实现与复现命令](第二轮实现与复现.md)、[正确性事件](第二轮正确性事件.md)、[更新后的指引](指引第二轮.md)。",
          "",
          "运行 scripts/analyze_round2.py 可从保存的审计重建所有表格；scripts/report_round2.py 重建本报告。二者不运行采样器。",""]
    a.output.write_text("\n".join(out))
    print(a.output)

if __name__=="__main__":main()
