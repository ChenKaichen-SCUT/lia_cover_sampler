# 第三轮实验报告

状态：framework_smoke。本轮基于 21befad，在独立分支 round3-execution-efficiency 修改；原 HighDiv、第一轮 LIA 与第二轮结果均未重跑或改写。

## 实现与正确性

已实现软/硬不可用区分、全部软冷却时的受限工作保持调度、顶层互斥计时、按方向流式产出、方向关联目标池、逐步 polish 预算、轮转扫描修复和空目标包明确状态。保留旧行为开关用于同状态对照。

C++ 回归和 Python 数学/覆盖差分回归通过。ASan/UBSan 回归通过；LeakSanitizer 对冻结 Z3 的 lp::dioph_eq::imp 大整数分配存在已独立复现的例外，仅窄范围排除该栈，其他泄漏检测保留。原失败、最小 Z3 独立复现和排除规则见 results/round3_dev/sanitize_diagnostic。

正式覆盖只提交最终完整模型，内部候选不累积成虚假的单模型增益；LineDomain 数学语义与原覆盖分母不变。

## 先行调度对照：30 秒、三个开发例、seed 0

| 配置 | 案例 | 样本 | 覆盖 % | 休眠占比 |
|---|---|---:|---:|---:|
| control_ab | 20180326-Bromberger_v45_problem__003.smt2.slack.smt2 | 681 | 96.376 | 旧二进制无此计时 |
| control_ab | SMT_random_LIA_ex14600_2600_100.smt2 | 12 | 8.462 | 旧二进制无此计时 |
| control_ab | convert_convert-jpg2gif-query-1459.smt2 | 11 | 25.281 | 旧二进制无此计时 |
| scheduler_fixed | 20180326-Bromberger_v45_problem__003.smt2.slack.smt2 | 1420 | 96.370 | 0.00% |
| scheduler_fixed | SMT_random_LIA_ex14600_2600_100.smt2 | 139 | 7.533 | 0.00% |
| scheduler_fixed | convert_convert-jpg2gif-query-1459.smt2 | 9 | 23.932 | 0.00% |
| scheduler_old | 20180326-Bromberger_v45_problem__003.smt2.slack.smt2 | 400 | 96.110 | 21.59% |
| scheduler_old | SMT_random_LIA_ex14600_2600_100.smt2 | 62 | 8.007 | 15.02% |
| scheduler_old | convert_convert-jpg2gif-query-1459.smt2 | 10 | 23.593 | 0.59% |

该对照在修改局部候选生成前执行。休眠下降是执行问题的修复，不能单独证明覆盖提高。第二轮约 4384 秒未分项时间不能被事后全部归为休眠；新版本单独核算初始化、动作、调度、休眠、日志和其他时间。

## 固定快照模块对照

快照只来自三个开发案例的已保存前缀；包含公式、当前候选、精确 AST 覆盖、种子和哈希。未恢复求解器学习状态，不把独立模块增益相加当作采样轨迹收益。首次候选耗时仅对成功返回的调用取均值，同时报告成功数，避免掩盖失败。

| 模块 | 版本 | 有合法候选/运行 | 首合法均值 ms | 首正增益均值 ms | 最好增益合计 | 模块秒 | 接受移动 |
|---|---|---:|---:|---:|---:|---:|---:|
| safe | eager | 1/1 | 4.476 | 4.476 | 7879 | 0.009 | 0 |
| safe | new | 1/1 | 4.575 | 4.575 | 7879 | 0.009 | 0 |
| line | eager | 1/1 | 6.324 | 6.324 | 7879 | 0.014 | 0 |
| line | new | 1/1 | 6.201 | 6.201 | 7879 | 0.013 | 0 |
| polish | eager | 1/1 | 5.646 | 5.646 | 7881 | 0.017 | 2 |
| polish | new | 1/1 | 4.662 | 4.662 | 7878 | 0.007 | 0 |

微基准进程 CPU 0.15 秒，整体墙钟（含交错审计）0.24 秒，审计 worker 秒 0.38，审计 CPU 未单独计量。逐快照和种子效率差保存在 micro/analysis.json；单模型改进次数不是性能成功判据。

## screen：2.0 秒统一在线轨迹

| 配置 | 运行数 | 终点覆盖 % | 样本数 | 满 1000 |
|---|---:|---:|---:|---:|
| control_ab | 3 | 24.7807 | 92 | 0 |
| candidate | 3 | 28.7445 | 50 | 0 |

| 候选相对 A/B | 指标 | 预算 | 配对数 | 平均差 pp | 胜/平/负 |
|---|---|---:|---:|---:|---|
| candidate | prefix | 5 | 1 | +7.9384 | 1/0/0 |
| candidate | prefix | 10 | 1 | +8.1450 | 1/0/0 |
| candidate | prefix | 50 | 0 | 未完成 | 0/0/0 |
| candidate | prefix | 1000 | 0 | 未完成 | 0/0/0 |
| candidate | time | 2.0 | 3 | +3.9638 | 1/1/1 |

候选顶层墙钟分解（统计检查点可能轻微截尾；各项互斥）：

- candidate：parsing_and_initialization=0.316s；action_wall_seconds=1.805s；scheduler_decision_seconds=0.000s；idle_sleep_seconds=0.000s；checkpoint_and_logging_seconds=0.008s；other_wall_seconds=0.000s

polish 尝试步骤 112，接受步骤 86，改善模型 17，改进前/后单模型增益累计 9824/12743，耗时 0.807s。这些是同一冻结快照下的改进诊断，不能重复加入正式覆盖。

| 父动作 | 秒 | 正式新增 bit | bit/秒 | 正式输出 |
|---|---:|---:|---:|---:|
| explore | 0.574 | 8556 | 14899.304 | 11 |
| local_line | 0.253 | 450 | 1780.708 | 2 |
| local_safe | 0.592 | 1747 | 2950.769 | 7 |
| target | 0.386 | 1990 | 5153.694 | 4 |

晋级：未通过；不执行下一阶段长预算。
- 未满足：candidate_positive_on_two_development_cases
- 未满足：candidate_prefix_50_nonnegative

逐实例、逐种子配对差和缺失前缀保存在 results/round3_dev/queue_smoke/analysis/paired_by_case_seed.csv，不能只挑最好种子。

## 成本与结论边界

| 阶段 | 采样 CPU 秒 | 采样墙钟秒 | 审计 CPU 秒 | 审计墙钟秒 |
|---|---:|---:|---:|---:|
| screen | 11.89 | 4.07 | 8.25 | 3.80 |

最终正确性回归共用 CPU 21.35s、墙钟 23.43s。早期增量编译、诊断和试运行未完整计量 CPU，不把以上成本声称为整轮开发总成本。

本轮尚无新的 900 秒结论。只有通过固定快照、120 秒开发筛选和 300 秒确认的候选才进入正式长跑；若晋级失败，保留全部数据并报告执行效率修复与覆盖性能之间的差距，不继续叠加未经证实的启发式。

`-n` 代码路径与受控前缀审计见 results/round3_dev/prefix_audit_final/audit.json。统一轨迹仅按真实顺序取前 k 个，不重排、不事后挑选，未达到 1000 标记未完成。审计缓存绑定公式、模型、时间戳、评测器、验证代码和报告哈希；旧基线直接复用其已有证据。
