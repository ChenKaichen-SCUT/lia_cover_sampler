# 完整依赖插桩后的旧语义测试时限

初次完整 ASan/UBSan 构建成功，19 项 C++ 测试中 18 项通过。失败为 residual_cardinality 的“固定 conjunction 必须 UNSAT”断言；没有 sanitizer 内存/未定义行为诊断。初次记录保存在 ../sanitize_initial_timeout/。

conjunction_probe.cpp 使用同一合成公式和 Global 入口记录原始状态。慢栈回溯、50 ms 时纯求解 50.93 ms，返回 timeout/canceled；2000 ms 上限时纯求解 50.00 ms，返回 UNSAT。完整逐次数据见 conjunction_comparison.json。诊断时限用于区分超时与错误答案，不是性能实验。

修复仅在旧语义回归里将期待 SAT/UNSAT 的 target 查询上限设为 2000 ms，总测试预算仍为原值。资源上限和截止语义的专门回归未移除，Global 和生产采样参数未修改。失败断言现在包含实际状态和原始 reason。

本轮项目及 Z3 都使用 -fno-omit-frame-pointer，因此 sanitizer 驱动使用标准 fast_unwind_on_malloc=1；地址、未定义行为和泄漏检测仍启用，原有仅针对 lp::dioph_eq::imp::imp 的泄漏豁免不扩大。四种诊断组合均有原始记录。正式实验仍为 Release，预算与评分规则不受此测试修正影响。
