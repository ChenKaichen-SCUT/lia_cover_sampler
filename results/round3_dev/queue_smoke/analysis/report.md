# 第三轮阶段报告

阶段：screen；每次预算 2.0 秒；并发 3；只使用本阶段 manifest 中的案例与种子。

| 配置 | 运行数 | 终点平均覆盖 % | 样本数 | 达到 1000 |
|---|---:|---:|---:|---:|
| control_ab | 3 | 24.7807 | 92 | 0 |
| candidate | 3 | 28.7445 | 50 | 0 |

采样 CPU 11.89 秒，采样墙钟 4.07 秒；独立审计 CPU 8.25 秒，审计墙钟 3.80 秒。

全部 142 个新模型审计通过。逐案例、逐种子差值与未完成前缀见 paired_by_case_seed.csv；顶层互斥计时见 wall_breakdown.csv。

短预算和固定状态模块结果不称为 900 秒性能，也不当作完整求解器状态重放。

晋级：未通过，停止进入长预算实验。

- candidate_endpoint_mean_nonnegative: True
- candidate_positive_on_two_development_cases: False
- candidate_majority_seed_pairs_nonnegative: True
- candidate_prefix_10_nonnegative: True
- candidate_prefix_50_nonnegative: False
- micro_all_valid: True
- micro_positive_in_two_modules: True
- all_new_models_independently_valid: True
