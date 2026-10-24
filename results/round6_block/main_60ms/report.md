# 第六轮固定快照机制结果

比较独立 coverage_block_generator 与现有 Global::query() 的直接全局模式；共用初始目标、Reach／整数 bit 编码、目标包、验证和检查点规则。变量块模式额外施加固定 assumptions，并依据 core 释放变量。

6 份已有开发快照，每份 3 个 solver seed、2 次配对重复；每会话常驻同一公式、覆盖和 Global 求解器执行 3 个动作。共 72 会话、216 动作，每动作双方同为 60 ms，最多 6 次查询、目标包最多 3 个。固定 15 并发槽，先后顺序交错，全部计时结束后才独立审计。

**计分：** 输出作为新增样本，gain 是对冻结正式 S 的真实边际覆盖，不减种子 gain，也不要求保留种子观察。内部动作不修改 S，返回模型必须不同于种子且不在正式前缀中。这里的动作得分相加只是固定状态回放分数，不是在线累计覆盖。

**完整成本：** 初始化和历史前缀恢复每会话只收一次；动作包括目标、依赖、固定约束、编码、求解、补全、验证、评分及检查点落盘。动作预留 20%（至少 2 ms）作交付；只接受截止前完成写出的检查点。后续超时仍保留此前及时见证，同时计入所有已消耗成本；无见证调用保留为 0。进程墙钟还包含最终证据序列化与退出。

| 模式 | 动作 | 无候选／无正收益 | 平均新增 bit | 平均动作 ms | 平均新增 bit／动作秒 | 平均会话分数／进程秒 |
|---|---:|---:|---:|---:|---:|---:|
| global | 108 | 72/72 | 321.240741 | 43.713214 | 29377.333009 | 12591.249770 |
| block | 108 | 72/72 | 885.000000 | 44.891796 | 90521.213926 | 31927.214939 |

两次重复先平均，形成 54 个快照×solver seed×动作组合；同会话动作相关，不视为独立统计样本。组合平均差为 **563.759259 bit**，中位数 0.000000；正／零／负为 **12/36/6**。

| 案例 | 组合 | 全局平均 bit | 块模式平均 bit | 差值 |
|---|---:|---:|---:|---:|
| Bromberger | 18 | 963.722222 | 2655.000000 | 1691.277778 |
| SMT_random | 18 | 0.000000 | 0.000000 | 0.000000 |
| convert | 18 | 0.000000 | 0.000000 | 0.000000 |

| 状态 | 全局平均 bit | 块模式平均 bit | 差值 |
|---|---:|---:|---:|
| Bromberger/prefix-1 | 1681.777778 | 5083.000000 | 3401.222222 |
| Bromberger/prefix-10 | 245.666667 | 227.000000 | -18.666667 |
| SMT_random/prefix-1 | 0.000000 | 0.000000 | 0.000000 |
| SMT_random/prefix-10 | 0.000000 | 0.000000 | 0.000000 |
| convert/prefix-1 | 0.000000 | 0.000000 | 0.000000 |
| convert/prefix-10 | 0.000000 | 0.000000 | 0.000000 |

## 初始化、准备和失败

| 模式 | 一次初始化 ms／会话 | 完整进程 ms／会话 | 准备 ms／动作 | 平均初始／最终块 | 全局回退动作 | 固定 core 次数 | 迟到候选数 |
|---|---:|---:|---:|---:|---:|---:|---:|
| global | 79.611090 | 236.173553 | 26.752245 | 623.333333/623.333333 | 0 | 0.000000 | 0.000000 |
| block | 80.759785 | 245.475067 | 27.587626 | 3.629630/413.037037 | 76 | 175.000000 | 0.000000 |

首次正收益时间只对有及时正见证的调用取均值，同时保留完整分母：
- global：36/108 次，条件均值 7.700589 ms；有候选后后续失败仍保留检查点 2.000000 次，动作返回超预算 17.000000 次。
- block：36/108 次，条件均值 13.490253 ms；有候选后后续失败仍保留检查点 14.000000 次，动作返回超预算 17.000000 次。

## 事前继续／停止判定

| 条件组 | 条件 | 结果 |
|---|---|---|
| common_checks | all_data_complete | 通过 |
| common_checks | same_primary_all_pairs | 通过 |
| common_checks | no_candidate_rate_not_worse | 通过 |
| gain_checks | mean_delta_positive | 通过 |
| gain_checks | median_delta_nonnegative | 通过 |
| gain_checks | two_thirds_nonnegative | 通过 |
| gain_checks | two_positive_cases | 未通过 |
| gain_checks | two_positive_snapshots | 未通过 |
| gain_checks | full_session_efficiency_better | 通过 |
| cost_checks | all_unit_coverage_noninferior | 未通过 |
| cost_checks | same_positive_coverage_cost_lower_15pct | 未通过 |
| cost_checks | initialized_full_session_cost_lower_15pct | 未通过 |
| cost_checks | two_faster_cases | 未通过 |
| cost_checks | two_faster_snapshots | 未通过 |

**没有满足继续条件，到此停止，不增加目标包、查询次数或预算。**

全部证据独立审计通过：True。计时阶段墙钟 1.764 秒、子进程 CPU 15.555 秒；离线审计墙钟 2.759 秒、CPU 9.005 秒。

## 数据与可重放命令

`calls.csv` 保留所有动作、初末块、完整成本、分支变化、失败和检查点；`sessions.csv` 记录一次初始化及完整会话；`pairs.csv` 为逐重复配对；`units.csv` 为重复平均。原始 `raw/` 保存完整模型、每次查询、释放变量／原因、检查点及截止时间；`audit/` 保存独立核验。

```bash
HIGHDIV_ROOT=/tmp/lia-highdiv-frozen /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/run_coverage_block_pairs.py \
  --binary /mnt/Space0/SMT_Sampling/lia_cover_sampler/build/round6/final/coverage_block_probe \
  --output /tmp/round6-block-replay --jobs 15
```

输出目录必须不存在。重放只执行这里的固定机制协议，不自动接入 A/B，不调用 HighDiv 采样，不启动端到端长实验。
