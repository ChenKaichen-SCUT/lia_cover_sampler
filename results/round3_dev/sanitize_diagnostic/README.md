# Sanitizer 诊断

ASan/UBSan 单元回归通过。首次带 LeakSanitizer 的 Python 集成回归在特殊变量名与大整数常量案例报告退出时泄漏。

full_stack.log 将分配定位到冻结 libz3.so 的 lp::dioph_eq::imp 构造回调；z3_only.cpp 不链接任何采样器源码，仅创建一个 simple solver 并断言大整数等式，就独立复现该泄漏。调用官方 Z3_finalize_memory 后仍复现，因此未保留无效的退出清理改动，也未更改冻结求解器。

最终 ASan/UBSan/LSan 回归保留 detect_leaks=1；仅使用 lsan_external_z3.supp 对已独立复现的 lp::dioph_eq::imp 栈作窄范围排除，未关闭其他泄漏检测。完整失败日志保留；不能将该运行表述成“无例外的 LSan 通过”。

诊断还暴露出软冷却旁路在有限解空间中快速重复重建同一耗尽探索上下文。当前将该已 UNSAT 的上下文标记为不可执行，直到其他动作产生新正式输出后才重建；这不是原公式 UNSAT 或覆盖最优的声明。历史收益已衰减且全部软冷却时按近期未尝试动作分配机会，避免旧的微小正收益垄断重试。
