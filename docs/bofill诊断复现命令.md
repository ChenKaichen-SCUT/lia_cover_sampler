# bofill 诊断与离线复核命令

本轮分支 `bofill-compiler-diagnosis`，首次SAT实验源提交 `a673c49`，基点 `a31e91e`。所有命令在仓库根目录运行。性能实验只运行已报告的40×30秒及2×120秒；以下性能重放命令仅供复现，不由交付脚本自动执行。

## 仅离线重放已有证据（不搜索）

```bash
export HIGHDIV_ROOT=/tmp/lia-highdiv-frozen
export PATH=/tmp/lia-highdiv-frozen/venv/bin:$PATH
python scripts/archive_bofill_results.py results/bofill_diagnosis/first_sat_30s --restore
python scripts/archive_bofill_results.py results/bofill_diagnosis/first_sat_120s --restore
python scripts/audit_bofill_diagnosis.py results/bofill_diagnosis/first_sat_30s
python scripts/audit_bofill_diagnosis.py results/bofill_diagnosis/first_sat_120s
python scripts/analyze_bofill_diagnosis.py results/bofill_diagnosis/first_sat_30s
python scripts/analyze_bofill_diagnosis.py results/bofill_diagnosis/first_sat_120s
python scripts/check_bofill_diagnosis_integrity.py results/bofill_diagnosis/first_sat_30s
python scripts/check_bofill_diagnosis_integrity.py results/bofill_diagnosis/first_sat_120s
```

这些审计禁用搜索，只解析、求值完整保存模型并检查收据/原始覆盖。源码散列、计划散列、原式/编译式和恢复、所有日志、模型、时间戳、查询统计在每会话无损 archives 中。旧900秒只读，不会执行其运行脚本。

## 首次SAT实验的独立复现（会调用求解器）

为严格保持当时版本，使用独立 worktree；不要用后续安全回退版本覆盖冻结实验二进制。实际原二进制与构建配置也压缩保存在 `results/bofill_diagnosis/frozen_build/`，其未压缩SHA对应计划。冻结Z3 4.14.2库必须符合计划中的SHA。

```bash
git worktree add /tmp/lia-bofill-replay a673c49
cd /tmp/lia-bofill-replay
cmake -S . -B build/bofill_diagnosis/release \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
  -DZ3_PREFIX=/tmp/lia-highdiv-frozen/venv
cmake --build build/bofill_diagnosis/release -j12
python3 scripts/run_bofill_diagnosis.py plan results/bofill_diagnosis/replay_30s
python3 -u scripts/run_bofill_diagnosis.py run results/bofill_diagnosis/replay_30s
```

该计划恰好40次、最多四路，完整命令逐条存于 plan.json。性能调度需在前台终端或可靠会话中运行；不要只把控制器留在会被回收的临时 shell 后台。记录的控制器中断恢复脚本仅用于已有完整探针收据的安全恢复，不重新运行已完成会话。

120秒延伸的计划命令为 `python3 scripts/run_bofill_diagnosis.py extension-plan NEW_OUT`，运行命令为同脚本 `run NEW_OUT`；只包含预选 random seed0 plain/full 两次 continuous。此延伸在本轮已完成，不继续延长。

## 当前安全保护与回归

当前分支重新构建 Release 后：

```bash
cmake -S . -B build/bofill_diagnosis/release \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
  -DZ3_PREFIX=/tmp/lia-highdiv-frozen/venv
cmake --build build/bofill_diagnosis/release -j12
bash scripts/test_bofill_diagnosis_build.sh release
cmake -S . -B build/bofill_diagnosis/sanitize \
  -DCMAKE_BUILD_TYPE=Debug -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
  -DZ3_PREFIX=/tmp/lia-highdiv-frozen/venv \
  '-DCMAKE_CXX_FLAGS_DEBUG=-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer -fno-pie' \
  '-DCMAKE_EXE_LINKER_FLAGS=-fsanitize=address,undefined -no-pie'
cmake --build build/bofill_diagnosis/sanitize -j8
bash scripts/test_bofill_diagnosis_build.sh sanitize
```

采样器接口为 `bofill_sampler F MODE SECONDS SEED NEW_OUT`。推荐 MODE=`plain`；实验性保护显式使用 `full_fallback` 等后缀。固定10秒阈值没有调参入口。这不是建议开启下一轮真实实例采样。

`verify_bofill_real_witnesses.py` 只读取冻结plain seed0前10个见证，调用探针的 verify 分支，执行提升/求值/恢复，不调用 check。性能探针拒绝携带保存见证参数。
