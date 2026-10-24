# 对已保存候选的离线重新评价：数据汇总

所有计数以冻结原评估器的 covered bit 为单位，原分母保存在恢复状态中。无候选动作的 g_x、h_x 为 0，直接候选与种子差值为空。单模型回退是离线质量上界，联合收益是已保存模型集合的价值；均不是新增在线实验。

| 状态 | 模式 | 动作 | g_m 均值 | g_x 均值／中位数 | 候选优／平／劣种子 | 无候选 | h_x 均值／中位数 |
|---|---|---:|---:|---:|---:|---:|---:|
| 全部 | global | 108 | 1541.83 | 321.24／0.00 | 18／0／18 | 72 | 116.46／0.00 |
| 全部 | block | 108 | 1541.83 | 885.00／0.00 | 18／0／18 | 72 | 88.35／0.00 |
| Bromberger/prefix-1 | global | 18 | 7878.00 | 1681.78／1704.00 | 0／0／18 | 0 | 453.11／433.00 |
| Bromberger/prefix-1 | block | 18 | 7878.00 | 5083.00／4559.00 | 0／0／18 | 0 | 303.11／306.00 |
| Bromberger/prefix-10 | global | 18 | 0.00 | 245.67／158.00 | 18／0／0 | 0 | 245.67／158.00 |
| Bromberger/prefix-10 | block | 18 | 0.00 | 227.00／87.00 | 18／0／0 | 0 | 227.00／87.00 |
| SMT_random/prefix-1 | global | 18 | 57.00 | 0.00／0.00 | 0／0／0 | 18 | 0.00／0.00 |
| SMT_random/prefix-1 | block | 18 | 57.00 | 0.00／0.00 | 0／0／0 | 18 | 0.00／0.00 |
| SMT_random/prefix-10 | global | 18 | 1.00 | 0.00／0.00 | 0／0／0 | 18 | 0.00／0.00 |
| SMT_random/prefix-10 | block | 18 | 1.00 | 0.00／0.00 | 0／0／0 | 18 | 0.00／0.00 |
| convert/prefix-1 | global | 18 | 72.00 | 0.00／0.00 | 0／0／0 | 18 | 0.00／0.00 |
| convert/prefix-1 | block | 18 | 72.00 | 0.00／0.00 | 0／0／0 | 18 | 0.00／0.00 |
| convert/prefix-10 | global | 18 | 1243.00 | 0.00／0.00 | 0／0／0 | 18 | 0.00／0.00 |
| convert/prefix-10 | block | 18 | 1243.00 | 0.00／0.00 | 0／0／0 | 18 | 0.00／0.00 |

| 状态 | 模式 | 会话 | Σg_x 均值 | U 均值／中位数 | Σh_x 均值 | U′ 均值／中位数 |
|---|---|---:|---:|---:|---:|---:|
| 全部 | global | 36 | 963.72 | 518.67／0.00 | 349.39 | 223.94／0.00 |
| 全部 | block | 36 | 2655.00 | 1368.78／0.00 | 265.06 | 217.22／0.00 |
| Bromberger/prefix-1 | global | 6 | 5045.33 | 2534.67／2425.00 | 1359.33 | 766.33／642.00 |
| Bromberger/prefix-1 | block | 6 | 15249.00 | 7581.67／8384.00 | 909.33 | 672.33／629.00 |
| Bromberger/prefix-10 | global | 6 | 737.00 | 577.33／589.00 | 737.00 | 577.33／589.00 |
| Bromberger/prefix-10 | block | 6 | 681.00 | 631.00／791.00 | 681.00 | 631.00／791.00 |
| SMT_random/prefix-1 | global | 6 | 0.00 | 0.00／0.00 | 0.00 | 0.00／0.00 |
| SMT_random/prefix-1 | block | 6 | 0.00 | 0.00／0.00 | 0.00 | 0.00／0.00 |
| SMT_random/prefix-10 | global | 6 | 0.00 | 0.00／0.00 | 0.00 | 0.00／0.00 |
| SMT_random/prefix-10 | block | 6 | 0.00 | 0.00／0.00 | 0.00 | 0.00／0.00 |
| convert/prefix-1 | global | 6 | 0.00 | 0.00／0.00 | 0.00 | 0.00／0.00 |
| convert/prefix-1 | block | 6 | 0.00 | 0.00／0.00 | 0.00 | 0.00／0.00 |
| convert/prefix-10 | global | 6 | 0.00 | 0.00／0.00 | 0.00 | 0.00／0.00 |
| convert/prefix-10 | block | 6 | 0.00 | 0.00／0.00 | 0.00 | 0.00／0.00 |

两次重复先平均。动作的 54 个描述性组合存在会话内相关性，不作为 54 个独立实验；联合收益采用 18 个快照×solver seed 会话组合。胜平负方向均为 block 相对 Global。

| 层次 | 状态 | 指标 | 组合数 | 差均值／中位数 | block 胜／平／负 |
|---|---|---|---:|---:|---:|
| 动作 | 全部 | g_x | 54 | 563.76／0.00 | 12／36／6 |
| 动作 | 全部 | h_x | 54 | -28.11／0.00 | 7／36／11 |
| 动作 | 全部 | seed_fallback_quality_upper_bound | 54 | -3.11／0.00 | 3／45／6 |
| 动作 | Bromberger/prefix-1 | g_x | 9 | 3401.22／2855.00 | 9／0／0 |
| 动作 | Bromberger/prefix-1 | h_x | 9 | -150.00／-2.00 | 4／0／5 |
| 动作 | Bromberger/prefix-1 | seed_fallback_quality_upper_bound | 9 | 0.00／0.00 | 0／9／0 |
| 动作 | Bromberger/prefix-10 | g_x | 9 | -18.67／-70.00 | 3／0／6 |
| 动作 | Bromberger/prefix-10 | h_x | 9 | -18.67／-70.00 | 3／0／6 |
| 动作 | Bromberger/prefix-10 | seed_fallback_quality_upper_bound | 9 | -18.67／-70.00 | 3／0／6 |
| 动作 | SMT_random/prefix-1 | g_x | 9 | 0.00／0.00 | 0／9／0 |
| 动作 | SMT_random/prefix-1 | h_x | 9 | 0.00／0.00 | 0／9／0 |
| 动作 | SMT_random/prefix-1 | seed_fallback_quality_upper_bound | 9 | 0.00／0.00 | 0／9／0 |
| 动作 | SMT_random/prefix-10 | g_x | 9 | 0.00／0.00 | 0／9／0 |
| 动作 | SMT_random/prefix-10 | h_x | 9 | 0.00／0.00 | 0／9／0 |
| 动作 | SMT_random/prefix-10 | seed_fallback_quality_upper_bound | 9 | 0.00／0.00 | 0／9／0 |
| 动作 | convert/prefix-1 | g_x | 9 | 0.00／0.00 | 0／9／0 |
| 动作 | convert/prefix-1 | h_x | 9 | 0.00／0.00 | 0／9／0 |
| 动作 | convert/prefix-1 | seed_fallback_quality_upper_bound | 9 | 0.00／0.00 | 0／9／0 |
| 动作 | convert/prefix-10 | g_x | 9 | 0.00／0.00 | 0／9／0 |
| 动作 | convert/prefix-10 | h_x | 9 | 0.00／0.00 | 0／9／0 |
| 动作 | convert/prefix-10 | seed_fallback_quality_upper_bound | 9 | 0.00／0.00 | 0／9／0 |
| 会话 | 全部 | U | 18 | 850.11／0.00 | 4／12／2 |
| 会话 | 全部 | U_prime | 18 | -6.72／0.00 | 2／12／4 |
| 会话 | Bromberger/prefix-1 | U | 3 | 5047.00／5959.00 | 3／0／0 |
| 会话 | Bromberger/prefix-1 | U_prime | 3 | -94.00／-134.00 | 1／0／2 |
| 会话 | Bromberger/prefix-10 | U | 3 | 53.67／-88.00 | 1／0／2 |
| 会话 | Bromberger/prefix-10 | U_prime | 3 | 53.67／-88.00 | 1／0／2 |
| 会话 | SMT_random/prefix-1 | U | 3 | 0.00／0.00 | 0／3／0 |
| 会话 | SMT_random/prefix-1 | U_prime | 3 | 0.00／0.00 | 0／3／0 |
| 会话 | SMT_random/prefix-10 | U | 3 | 0.00／0.00 | 0／3／0 |
| 会话 | SMT_random/prefix-10 | U_prime | 3 | 0.00／0.00 | 0／3／0 |
| 会话 | convert/prefix-1 | U | 3 | 0.00／0.00 | 0／3／0 |
| 会话 | convert/prefix-1 | U_prime | 3 | 0.00／0.00 | 0／3／0 |
| 会话 | convert/prefix-10 | U | 3 | 0.00／0.00 | 0／3／0 |
| 会话 | convert/prefix-10 | U_prime | 3 | 0.00／0.00 | 0／3／0 |

原始成本保存在三份主表、session_totals.csv 和 query_costs.csv；每个动作计入全部失败查询及目标、固定约束等阶段，会话初始化只计一次。逐查询阶段成本无法从原日志准确拆分，明确为 unknown。候选来源是时序关联，不能作为独立因果归因。

**原第六轮停止扩大实验的决定保持不变。**
