# 离线复现

仅解析、求值已保存赋值与固定观察矩阵；不运行采样器、HighDiv或SMT搜索。输入基点为`ef3e9a7`，协议在`3aae807`提交。原公式、原始日志、模型、时间和独立审计的全部来源由`provenance.json`与`archive_manifest.json`绑定。

已验证环境：Python3.10.12，冻结Z3 4.14.2。当前机器的解释器在`/tmp/lia-highdiv-frozen/venv/bin/python3`。可以把下面的`PY`换为具备同一冻结依赖的解释器；这里不需要构建或运行任何C++二进制。

```bash
cd /mnt/Space0/SMT_Sampling/lia_cover_sampler
PY=/tmp/lia-highdiv-frozen/venv/bin/python3
export PYTHONPATH="$PWD/scripts:$PWD/python"
export HIGHDIV_ROOT="$PWD/results/highdiv_complementarity_gap/inputs/frozen_evaluator"
"$PY" scripts/test_highdiv_complementarity_gap.py
```

已有交付结果的只读检查（首次clone后先恢复**本轮新目录**的归档；不会向历史目录回写）：

```bash
python3 scripts/archive_bofill_results.py results/highdiv_complementarity_gap --restore
"$PY" scripts/audit_highdiv_complementarity_gap.py --workers 10
```

为保留交付结果不动，完整重放应输出到另一新目录：

```bash
REPLAY_OUT=results/highdiv_complementarity_gap_replay
"$PY" scripts/prepare_highdiv_complementarity_gap.py --out "$REPLAY_OUT" --workers 10
"$PY" scripts/analyze_highdiv_complementarity_gap.py --out "$REPLAY_OUT" --workers 2
"$PY" scripts/describe_highdiv_gap_structure.py --out "$REPLAY_OUT"
"$PY" scripts/audit_highdiv_complementarity_gap.py --out "$REPLAY_OUT" --workers 10
"$PY" scripts/report_highdiv_complementarity_gap.py --out "$REPLAY_OUT"
```

准备脚本校验冻结原归档和文件SHA256。若原始未压缩文件缺失，仅从已校验的原归档提取到`REPLAY_OUT`；缺失证据或哈希不匹配会失败，不猜测模型。共有10个独立轨迹重放任务，使用10个进程；分析按两公式独立执行，最多2个进程。所有脚本限制并发上限15。没有新求解或采样实验。

原评测器源文件随本轮保存在`inputs/frozen_evaluator/HighDiv/my_scripts/calc_metric.py`，未经修改；SHA256为`a51cdd069321cd32becf8eeb5e606cb71fbab16ae3e0231f68963eaadf493f04`，原来源见仓库`dependencies/lock.json`（HighDiv提交`f2c6e1756b13281d96cbf90a8bd940129e56cce4`）和`dependencies/source_audit.json`。离线解释器仍使用该源码；源文件哈希不一致会拒绝运行。

## 结果编码

- `observations/<run>/models.jsonl.gz`：完整原变量赋值、模型哈希、逐模型`zero`/`one`十六进制观察掩码、观察哈希、1开始索引、原索引、写完时间。`layout.json`按原AST节点给出位偏移和宽度；`O(m)`可无损还原为(node, bit, value)。
- `observations/<run>/timeline.csv`：每个历史前缀、真实增益和完整状态哈希；没有用独立收益相加替代联合覆盖。
- `analysis/*/bit_set_differences.csv.gz`：H独有/N独有/交集/仅合并配对；逐bit双方的首次0/1模型身份和时间。
- `first_arrivals.csv.gz`：H的固定前缀目标在每条native中的首次到达；缺失字段为空，并标记右删失。
- `reference_states.json`、`common_state_model_gains.csv.gz`：共同基底的完整状态和每个候选得分。
- `highdiv_jump_native_scan.csv.gz`：H前10所有正跳跃的逐native模型对照，包含真实增益集合交集、差集和Jaccard。
- `selected_subsets.jsonl.gz`：160个选集检查点的具体索引、身份、完整联合状态、上下界及时间；`selection_bounds.csv`为摘要。
- `selection_certificates.csv`：仅把已经找到的固定首模型见证合法用于自由首模型问题，不增加选集尝试，不更改任何路径。各路径的原下界保持单列。
- `structure.json`、`representative_witnesses.jsonl.gz`：变量依赖、全局/语法等式组、可识别离散模式、模型变化、确切观察差。内部HighDiv生成来源未知。
- `actual_transitions.csv.gz`：真实相邻模型变化和对当时累计状态的覆盖收益，包含零收益事件；`selected_pair_structures.jsonl.gz`为固定选集的代表性首对见证。
- `audit/summary.json`：原评测器独立重放及160个选集复算；`supplemental_tables.json`额外检查结构/证书表；`label_review.json`记录一次语义标签修正：条件k=2上界不能误称整个池上界，模型与数值未改变。
- `archive_manifest.json`：本轮独立原始输入归档，无损gzip；没有覆盖原第十/十一轮或900秒数据。`artifact_sha256.json`列出交付文件哈希（不包含自身和忽略的解压暂存目录）。

选集的`lower_bound`是具体可行子集，`upper_bound`只来自整个池的联合覆盖，或已穷举的模型对。`optimal_fixed_pool`仅在上下界闭合时出现，不表示用了最少模型。`proved_same_k_insufficient`必须同时看`keep_first`条件；`proved_pool_insufficient`才表示全部当前池也不足。`unknown`不得转述为不可达。总量达到H不等于覆盖同一批bit；目标命中/上界另列。

所有原生成成本仍在原始日志里。选集测时不含读取和原评测器准备；它不是在线维护总成本，也不能换算为在线吞吐。离线重放可能因机器负载产生不同耗时，但模型身份、覆盖、集合差、固定选集和证书应相同。
