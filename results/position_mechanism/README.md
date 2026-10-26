# 已有位置随机化轨迹的只读机制诊断

输入冻结于 `7d3d2e7`，算法仍为 `75cd6b6`；本轮没有生成新模型、修改算法或重跑HighDiv。

- `报告.md`：完整中文报告；`interpretation.md`：基于全量表的机制判断和下一阶段建议。
- `plan.json`、`input_manifest.json`：分析前冻结的定义及原始日志、公式和旧ManualSatisfiesMetric审计的来源哈希。
- `implementation_correction.json`：记录显式Z3解析上下文修正；原始失败日志和回归记录全部保留。没有改变协议、样本或统计定义。
- `sessions.csv`、`first_model_pairs.csv`：28个会话和14个首模型配对。
- `cells.csv`、`equal_seed_cells.csv`：逐会话/阶段/覆盖状态的K×动作统计及seed等权汇总。`assigned_K_itt.csv`包含未到达分配位置的事件。
- `within_session_contrasts.csv`、`seed_contrasts.csv`：共同状态分层对照及逐seed差；缺失层保留NA。
- `paired_diversity.csv`、`bounded_related_saturation.csv`：原子、直接变量、必要等式组的多样性和有界相关节点观察状态。
- `transition_classes.csv`、`gain_bins.csv`、`gain_contribution_decomposition.csv`：模型变化、真实边际收益及逐seed顺序贡献分解。
- `breakthrough_events.csv`、`zero_gain_large_change_witnesses.jsonl.gz`：终止30秒高原的模型，以及大范围变化仍无收益的反例。
- `phase_retention.csv`、`conflict_evidence.csv`：最终模型的软相位保持率和实际已记录的冲突证据；未记录完整查询冲突/回溯/学习状态。
- `structure/`：完整原变量、必要等式组、原AST布局和依赖掩码。组间仍可能被不等式/分支耦合，不声称独立。
- `independent_audit.json`：58,086个模型的全部前缀重放及84个固定见证的ManualSatisfiesMetric重新求值；搜索调用数为0。
- `archives/`、`archive_manifest.json`、`archive_audit.json`：无损保存28个运行的逐查询CSV/JSONL、原子身份、逐模型差分和正收益事件，全部成员经SHA256核对。原始完整模型由`input_manifest.json`引用旧冻结归档，无需重复生成。

`runs/`受仓库已有gitignore规则影响，因此通过归档交付。查看本轮逐查询文件可先执行：

```bash
python3 scripts/archive_bofill_results.py results/position_mechanism --restore
```

全量重算请使用报告中的新目录重放命令，保持原目录不变。分析使用15个工作进程；本轮结果不能当作新在线算法或新性能实验。
