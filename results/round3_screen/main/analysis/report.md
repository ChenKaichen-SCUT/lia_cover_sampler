# 第三轮阶段报告

阶段：screen；每次预算 120.0 秒；并发 12；只使用本阶段 manifest 中的案例与种子。

| 配置 | 运行数 | 终点平均覆盖 % | 样本数 | 达到 1000 |
|---|---:|---:|---:|---:|
| control_ab | 9 | 43.8832 | 6260 | 2 |
| candidate | 9 | 44.5769 | 9149 | 3 |

采样 CPU 2147.26 秒，采样墙钟 240.10 秒；独立审计 CPU 396.09 秒，审计墙钟 48.20 秒。

全部 15409 个新模型审计通过。逐案例、逐种子差值与未完成前缀见 paired_by_case_seed.csv；顶层互斥计时见 wall_breakdown.csv。

短预算和固定状态模块结果不称为 900 秒性能，也不当作完整求解器状态重放。

晋级：未通过，停止进入长预算实验。

- candidate_endpoint_mean_nonnegative: True
- candidate_positive_on_two_development_cases: False
- candidate_majority_seed_pairs_nonnegative: False
- candidate_prefix_10_nonnegative: True
- candidate_prefix_50_nonnegative: True
- micro_all_valid: True
- micro_positive_in_two_modules: True
- all_new_models_independently_valid: True
