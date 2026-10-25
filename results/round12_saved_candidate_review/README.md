# 第十二轮离线结果与复现

结论为 **No-Go：投影生成退出后续性能研究主线**。完整中文解释见 [报告](../../docs/第十二轮已保存候选离线复用报告.md)，分析前冻结规则见 [协议](../../docs/第十二轮离线实验协议.md)。所有数据是历史已保存模型的离线价值，不能解释为在线墙钟性能。

## 输入与证据

基点 `8937f66dc45502a3c04e8c77bcc5b02c73a1c82b`，协议提交 `c087ffd`，初次执行代码 `b0a3db7`。本目录不覆盖历史数据。

- `input_manifest.json`：历史文件、原代码提交、原归档每个成员、输入副本的 SHA-256；原绝对路径由脚本转为仓库相对路径，支持不同检出位置。
- `inputs/benchmarks/` 与 `inputs/results/round3_snapshots/`：本次实际读取的三个公式、prefix 和 seed 副本。
- `inputs/frozenHighDiv/HighDiv/my_scripts/calc_metric.py`：冻结 ManualSatisfiesMetric 源码副本，来自 HighDiv `f2c6e1756b13281d96cbf90a8bd940129e56cce4`，原 SHA-256 `a51cdd069321cd32becf8eeb5e606cb71fbab16ae3e0231f68963eaadf493f04`。
- 完整历史原始归档继续使用本仓库原有 `results/round10_projection/main_10s/raw.tar.gz` 和 `results/round11_projection/main_10s/raw.tar.gz`。两者已由基点提交跟踪，不重复复制、不重新生成。归档包含查询、完整赋值、初始状态、收据和原始时间戳。
- `round10/`、`round11/` 各会话 `sessions/<session>/model_catalog.jsonl.gz`：另存候选完整赋值及规范化模型身份。所有候选行通过 model_hash 精确绑定该目录和原归档。
- `COMPLETE.json`、`session_audit.csv`：原审计一致性、全部正式及模拟提交的完整 seen0/seen1 差分验证。
- `ARTIFACT_AUDIT.json`：重新读取落盘结果，用独立列表 FIFO 重放选择并复核覆盖、来源、截止和去重；包含无上限来源联合覆盖诊断。
- `REPLAY_AUDIT.json`：全45会话再次只读重放，验证非计时结果可复现。计时值随机器负载变化，不挑选更快的一次；主表始终使用第一次实测成本。
- `SHA256SUMS.json`：本目录交付文件哈希；`PACKAGING.json`记录大CSV无损压缩前后的身份。

## 表与语义

| 文件 | 内容 |
| --- | --- |
| `sessions.csv` | 原轨迹和所有池的逐会话结果；包含零输出会话、历史进程及查询成本 |
| `prefixes.csv` | 每个会话/模式/池前1、2、5、10、50、100及最终覆盖；未达到数量留空 |
| `pairs.csv` | 同会话、同原正式位置的配对差值，标记 incomplete |
| `paired_summary.csv` | 分轮、公式、模式的会话级胜平负、均值、中位数；不混合两轮 |
| `pool_costs.csv` | all池4/8的评分、维护、观察构造分摊、淘汰、利用率、原历史成本 |
| `source_union.csv` | 全部普通候选与投影候选的真实联合增益；**不限制模型数，不能当作同样本数性能** |
| `round*/candidates.csv.gz` | 每次原候选出现的身份、query/checkpoint哈希、完成时间、覆盖版本、边际、是否输出及原因 |
| `round*/queries.csv.gz` | 所有原查询，包括无模型、Unknown和失败的完整原成本 |
| `round*/selection_events.csv` | 同一冻结状态的普通/额外候选覆盖交集、首次观察、双向残余和联合收益 |
| `round*/residual_changes.csv.gz` | 落选候选沿后续真实正式状态的剩余收益变化点及末尾点；不是累加收益 |
| `round*/sources.csv` | 按来源的出现次数、不同落选模型数、正剩余数量、最终联合残余 |
| `round*/original_trajectory.csv.gz` | 重放原正式轨迹的逐位置边际、完整状态哈希 |
| `round*/simulated_trajectory.csv.gz` | 每个池每个原正式位置的实际选择、边际、状态哈希与身份来源 |
| `round*/pool_lifecycle.csv.gz` | 准入和FIFO淘汰事件，重复到达不更新年龄 |
| `round*/pool_sessions.csv` | 各池完整费用、利用率和同数量结果，包含严格普通来源对照 |
| `round*/pool_sources.csv` | 模拟真实选择顺序下的来源贡献、被挽回落选模型边际；不是因果归因 |
| `round11/focus_77_plus_2.csv` | 用户指定的77次Bromberger严格投影胜出、2次convert投影落选 |
| `decision.json` | 分析前冻结门槛的机器判定，不启动下一轮实验 |

模型的 `origin` 是可唯一关联的原查询来源，普通包括 `plain` 和 `extra_plain`。每个候选出现独立记录，不把重复模型的多次出现视作不同模型。`formal_duplicate`、`internal_duplicate`和未输出原因是不同维度，不互相抹掉。

`ordinary_retention`只禁止投影**进入保留池**，当前原正式投影仍可用；`ordinary_strict`连当前原正式投影也不能选。两者不能混称。所有候选先按到达顺序准入并做容量检查；到正式选择位置，再独立提供原正式模型回退，同身份合并且不撤销已经发生的FIFO淘汰。只有及时 checkpoint 写完的模型才能入池；模型的 validated 时间不能替代写完时刻。`historical_seconds`只是离线可用性边界，不是新算法实测交付时间。

评分以完整观察状态 OR 后的 covered 增量为首关键字、观察增量为次关键字。`observations`是新0/1观察数，`first_observed_bits`是以前完全没观察过的bit数；同 covered 增益下两者作为次关键字等价。`first_observation_pairing`记录单独两个候选均不能补齐、组合才补齐的覆盖。

构造观察掩码的费用使用共享离线测量按每个有界池的实际准入/外部回退需要分摊，单列为 `observation_build_charge_seconds`；它不是新的在线计时。独立审核、身份构建、文件IO等属于完整离线分析耗时，并未冒充池在线吞吐率。池取用率包括立即选择原本就要输出的模型，务必同时查看 changed_choices 和 rescued_discarded_*。

## 复现命令（只重放历史赋值）

从仓库根目录执行。当前环境是 Python 3.10.12，HighDiv附带的 Z3 4.14.2，库SHA为 `c66acd3c4743444ce90b255820a88a65cbeaa4bd13808ed59ac7be91721967ca`。不要运行全量测试发现、C++探针或 `scripts/build.sh`，这些旧入口包含求解/采样测试。本轮只运行指定的新离线测试。

```bash
export HIGHDIV_ROOT="$PWD/results/round12_saved_candidate_review/inputs/frozenHighDiv"
ROUND12_PY=/tmp/lia-highdiv-frozen/venv/bin/python3

"$ROUND12_PY" -m unittest discover -s tests -p test_saved_candidate_review.py -v

# 输出路径必须尚不存在；不会写原第十、十一轮目录。
"$ROUND12_PY" scripts/review_saved_candidates.py \
  --output /tmp/round12-fresh-review --jobs 6
"$ROUND12_PY" scripts/summarize_saved_candidate_review.py /tmp/round12-fresh-review
"$ROUND12_PY" scripts/audit_saved_candidate_results.py /tmp/round12-fresh-review --jobs 6
"$ROUND12_PY" scripts/report_saved_candidate_review.py /tmp/round12-fresh-review \
  --report /tmp/round12-fresh-report.md
"$ROUND12_PY" scripts/package_saved_candidate_review.py /tmp/round12-fresh-review
```

只想复核本次已交付结果，不需要重新模拟：

```bash
"$ROUND12_PY" scripts/audit_saved_candidate_results.py \
  results/round12_saved_candidate_review --jobs 6
```

上条会在**第十二轮目录**更新独立审计耗时和source_union表；若需要保持交付哈希完全不变，应先复制整个第十二轮目录到新的临时目录，再对副本执行。不得对第十、十一轮运行旧审计 main()，因为旧入口会覆盖旧审计输出。

若缺少原冻结依赖，可单独编译固定提交的Z3解析/AST库，不启动HighDiv、采样器或测试：

```bash
git clone https://github.com/laigroup/HighDiv.git /tmp/round12-highdiv-parser
git -C /tmp/round12-highdiv-parser checkout --detach f2c6e1756b13281d96cbf90a8bd940129e56cce4
python3 -m venv /tmp/round12-highdiv-parser/venv
(cd /tmp/round12-highdiv-parser/z3 && ../venv/bin/python3 scripts/mk_make.py \
  --python --prefix=/tmp/round12-highdiv-parser/venv)
make -C /tmp/round12-highdiv-parser/z3/build -j6
make -C /tmp/round12-highdiv-parser/z3/build install
ROUND12_PY=/tmp/round12-highdiv-parser/venv/bin/python3
```

换环境后的实测成本可能不同，不能择优替换主结果。新机器不要求二进制重编译哈希相同，但必须保留原库哈希证据、冻结Manual源码哈希、全状态差分和搜索入口防护。
