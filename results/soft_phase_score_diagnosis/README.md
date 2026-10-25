# 软相位评分只读诊断

基点 `56cc9ef`；协议冻结提交 `65607b9`。`screen` 为18个60秒正式会话，`exposure` 为6个30秒曝光会话，统计分开。所有模型均来自原始归档，本轮没有生成模型或启动任何新求解/采样实验。

中文结论见 [报告](../../docs/软相位评分有效性诊断报告.md)。协议见 [冻结协议](../../docs/软相位评分诊断协议.md)。原 No-Go 不变。

## 复现

使用原离线评测环境。`HIGHDIV_ROOT` 指向已经存在的冻结源码目录，**仅导入评测器**，不运行 HighDiv 算法。`calc_metric.py` 的哈希必须匹配 `dependencies/source_audit.json`；本次环境为 `/tmp/lia-highdiv-frozen/venv/bin/python3`，评测器位于 `/tmp/lia-highdiv-frozen/HighDiv/my_scripts/calc_metric.py`。原模型、摘要及日志位于基点已提交的 `results/soft_phase_{screen,exposure}/archives`，本轮来源哈希见 `source_manifest.json`。不需要重新运行任何历史实验。

```bash
cd /mnt/Space0/SMT_Sampling/lia_cover_sampler
export HIGHDIV_ROOT=/tmp/lia-highdiv-frozen

# 只运行本轮离线回归；不要运行旧测试全集。
/tmp/lia-highdiv-frozen/venv/bin/python3 -m unittest discover \
  -s tests -p test_soft_phase_score_diagnosis.py -v

# 新建独立重放目录；不得覆盖本轮或历史结果。
mkdir -p results/soft_phase_score_replay
cp results/soft_phase_score_diagnosis/protocol.json results/soft_phase_score_replay/protocol.json
/tmp/lia-highdiv-frozen/venv/bin/python3 scripts/diagnose_soft_phase_scores.py \
  --out results/soft_phase_score_replay --jobs 15
/tmp/lia-highdiv-frozen/venv/bin/python3 scripts/audit_soft_phase_score_diagnosis.py \
  --out results/soft_phase_score_replay --jobs 15

# 对已经审计的数据生成附加描述表及报告；不求值或搜索新模型。
# 该命令会更新本轮新增报告 docs/软相位评分有效性诊断报告.md，不写旧报告。
python3 scripts/report_soft_phase_score_diagnosis.py \
  --out results/soft_phase_score_replay
```

主脚本要求 `--out/inputs` 不存在，防止误覆盖已有分析。初次运行本轮使用相同命令，`--out` 为本目录。只需15个离线工作进程，不启动求解器线程。原始解包目录 `inputs/*/runs` 被仓库已有的 `runs/` 规则忽略；这些字节可由已提交原归档完整恢复。其余输入计划、独立审计副本和哈希保存在本目录。

## 表与语义

| 文件 | 内容 |
|---|---|
| `query_outcomes.csv` | 每次查询前覆盖版本、完整成本包络、check成本、授权毫秒、原始状态/原因、偏好各层聚合、及时交付收益；含全部失败 |
| `applied_phase_events.csv` | 仅原生 `applied=true` 事件，两侧 L/N、原生方向分差、最终实现/未实现/未知、关联查询成本 |
| `summary_explanation.csv` | D、实际介入 E、提出偏好 P、并集重叠、摘要外增益；零分母为 NA |
| `score_availability.csv` | 状态0至最终状态的完整目录评分，含零方向和过滤方向；plain 无目录为 NA |
| `repeated_direction_costs.csv` | 每个稳定原子方向的重复、覆盖版本、未知链、原始分数序列及查询ID |
| `session_summary.csv` | 单会话总量与首模型/后续观察分解；不合并会话内事件为独立重复 |
| `intervention_funnel.csv` | 安装/映射的对象数、确认遇到/未遇到/事件截断未知、完整事件聚合 |
| `opportunity_classes_complete.csv` | 四类机会，未发生的类明确为零；不足的非介入事件不分配给任何方向 |
| `stage_summary.csv` | 固定前缀位置与时间段，所有查询含失败；跨边界查询成本不裁剪 |
| `score_checkpoints.csv` | 固定正式版本1/10/50/100及最终、10/30/60秒时的完整目录评分 |
| `correlations.csv` | 单会话 max applied/proposed L 与完整周期/交付增益的描述相关；不足10点为 NA |
| `atom_catalog.csv`, `atom_identities.csv` | 稳定身份、原表达式、原进程编号和可解析变量/整数常量；不假装知道内部重写表达式 |
| `concurrency_intervals.csv`, `first_model_concurrency.csv` | 原实验实际重叠区间、CPU拓扑与首模型时SMT共享，不做时间校正 |
| `*/phase_opportunities.csv.gz` | 全部查询×目录原子×两方向，含未安装/被过滤方向、反方向静态分数及反事实未知列 |
| `*/bitmap_evidence.jsonl.gz` | 完整D/H/E/P位图及查询前状态指纹，供稀疏集合第二审计 |

公式、配置、seed、会话、原子键、查询/目录/来源哈希用于回溯；不适用的标识列为空。CSV 空值表示未知/不适用，不应填零。布尔字符串为 `True/False`。`query` 从0开始，正式 `coverage_version` 是查询开始前已经提交的模型数量。

`cycle_seconds` 为本条 finished−前条 finished（首条从0），覆盖前条日志加本条准备/求解/验证/写出/阻塞；不是精确的当前准备耗时或方向独占成本。周期加 `unallocated_tail_seconds` 等于原会话 elapsed，进程墙钟另列。同一查询可能关联多个方向或机会类，其关联成本不能跨方向/类相加。

`mapped_at_capture` 是查询后保存的映射状态；映射成功不能等同于原生决策实际遇到。前32个机会之后，原补丁仍保存所有介入事件，但可能截断非介入事件；缺失部分只保留聚合原因。`native_encountered` 为空时不能推断没有发生。

`catalog_outside_gain` 对 plain 的数值仅是空目录的算术差，plain 没有摘要目录，不能将其解释为摘要质量证据。报告的目录范围分析只使用软偏好会话。状态0的目录为事后恢复，不声称在线已存在；判断评分是否耗尽从首模型后的实际可用目录开始。

`opposite_summary_score` 可由已保存摘要算出，`opposite_generated_model_gain` 与 `opposite_query_cost` 均为空。没有构造“另一个方向本来会产生什么”的反事实。E/D 的名称是“已实现干预摘要的覆盖解释比例”，没有因果含义。

`analysis_summary.json` 与 `independent_audit.json` 是两次独立实现的重放结果；`offline_regressions.txt` 是仅本轮15项测试结果。`execution_plan.json` 绑定主计算脚本、协议和15并发设置；`report_provenance.json` 绑定报告、附加表与被审计输入。`delivery_manifest.json` 给出新增交付文件哈希；自身不自引用。
