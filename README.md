# lia_cover_sampler

独立的 C++20 SMT(LIA) 覆盖采样器。局部模块通过安全区域、整数核方向与模区间命中生成候选；全局模块使用增量目标打包和带上下文的 UNSAT core 缓存。使用 GMP 任意精度整数和冻结 Z3 的标准 API，不修改求解器内核。

要求见 [指引](docs/指引.md)，结果见 [中文分析报告](docs/实验报告.md)，细节见 [实现说明](docs/实现说明.md)。正式实验只使用 benchmarks 中用户抽取的 **9 个实例**，不运行 HighDiv 全量数据集。

第一轮冻结结果见 [900 秒两组独立实验报告](docs/900秒实验报告.md)：固定时间下 HighDiv / LIA 平均覆盖率为 55.733% / 56.106%，样本总数为 206,438 / 33,254；双方均完成 1000 个样本的六个案例中，平均覆盖率为 76.651% / 76.592%。四批原始结果、逐案例审计、完整压缩包和可复现分析脚本均已保存；包含超时的九案例终点与严格等样本量比较分别报告。

第二轮按更新后的指引增加独立控制预算、动作调度、方向配额、单模型改进、精确跨分支区间集合和目标包修复。配置、15 槽混合队列与复现命令见 [第二轮实现与复现](docs/第二轮实现与复现.md)。

第二轮 **144 个实验与全部独立审计已完成**，见 [第二轮实验报告](docs/第二轮实验报告.md)。HighDiv / 原 LIA / 完整版的 900 秒九例平均覆盖率分别为 55.733% / 56.106% / 56.151%；三者共同完成 1000 个的六例覆盖率为 76.651% / 76.592% / 76.557%。完整版改善了相对原 LIA 的前 5/10/50 个样本覆盖，但没有同时改善时间与严格等样本量指标。仅修复 A/B 的对照版本终点均值最高，评测集退化与机制成本均在报告中保留。结果位于 results/round2_formal_v2 和 results/round2_analysis；未重跑冻结基线。

## 构建与测试

要求 Linux、C++20、CMake、GNU Make、Python 3.10+（含 venv）和 GMP：

    sudo apt-get install build-essential cmake git python3 python3-venv libgmp-dev
    bash scripts/build.sh

脚本检出 HighDiv 固定提交 f2c6e1756b13281d96cbf90a8bd940129e56cce4，构建其附带的 Z3 4.14.2，再以 -O3 构建本程序。最多 12 个编译任务。构建脚本**不会运行 HighDiv 采样实验**。include、library 和 RPATH 显式指定，不使用系统默认 Z3。

复用冻结依赖：

    HIGHDIV_ROOT=/path/to/HighDiv Z3_PREFIX=/path/to/HighDiv/venv bash scripts/build.sh

本次实际库路径、库哈希、编译器、GMP 版本及 Z3 编译配置在 dependencies/lock.json。它记录本次测量环境；其他机器重建的二进制哈希可能不同。dependencies/source_audit.json 核对本地 HighDiv 与冻结提交的关键文件，每个实验 manifest 另记录实际可执行文件 SHA-256。

单独运行测试：

    ctest --test-dir build --output-on-failure
    HIGHDIV_ROOT=dependencies/HighDiv dependencies/HighDiv/venv/bin/python3 \
      -m unittest discover -s tests -v

Python 数学参考实现只作独立 Oracle，不参与性能对照。测试包含 8,002 个模命中用例、2,424 个有符号除法用例、区域蕴含、完整等式核、覆盖逐节点差分、共享节点 Reach、三元冲突、资源耗尽、完整类型验证、特殊名字与中断。

## 采样

    ./build/lia_cover_sampler \
      -i benchmarks/40-9.slack.smt2 -o runs/example -n 1000 -t 30 -s 0

| 输出 | 含义 |
|---|---|
| 输入文件名.samples | 兼容 HighDiv 的完整整数赋值 |
| models.jsonl | 所有原始自由 Int/Bool 变量；Int 以十进制字符串保存 |
| emission.csv | 样本写出、刷新后的时间戳 |
| format.json | 原格式和原评测器的兼容性及原因 |
| summary.json | 实际数量、终止原因、覆盖整数计数和阶段统计 |
| progress.json | 定期原子保存的统计，供硬超时后分析 |
| trace.jsonl | debug/trace 模式中的区域、方向、命中和冲突日志 |
| replay/ | 检测到错误时保存的原公式、模型、配置及查询 |

输出目录不能已有 models.jsonl 或 summary.json。-n 是不同合法完整赋值的数量上限；在线追加，不撤回、不重排、不用重复模型补足数量。去重比较完整赋值字符串，每次写出前用原公式具体求值验证。

--mode 可取 benchmark（默认）、debug（增加区域蕴含与缓存检查）、trace（逐候选日志）。--variant：

| 版本 | 行为 |
|---|---|
| full | 定向模命中 + 全局目标 + 冲突缓存 |
| random | 保留合法方向与区间，替换为随机合法参数 |
| no-cache | 禁用跨轮冲突复用 |
| global-only | 关闭局部生成，仅目标求解与独立阻塞探索 |

## 覆盖契约与边界

冻结评测器为 HighDiv 的 HighDiv/my_scripts/calc_metric.py。复现顶层 And(assertions)、常量分母、共享 AST 去重、and/or 短路和整数 ite 分支访问。Int 的低 64 位按数学整数模处理，原赋值和线性计算不截断。未访问与已访问零分别记录，候选评分只读正式状态，接受输出才更新。

原样本解析器不能读取 true/false，也不能可靠表示含冒号、分号、换行的名字、同名异类型变量和空赋值。此时不生成有误导性的 .samples；完整赋值写入 JSONL，并记录原因。原评测器不支持 Boolean ite、distinct、蕴含、xor、div/mod 等操作；标记指标不兼容，保留原公式，不能把扩展求值指标当作正式覆盖。局部算术不支持时回退到原公式上的全局探索，不报告成 UNSAT。

第一版只支持无量词 Int/Bool 输入，不支持数组、实数、量词、非零元未解释函数。无界方向仅在有限完整周期内处理单目标，不声称无限域均匀采样。清分母的有理核向量是合法方向来源，不声称生成全部整数核。

## 独立复核与重放

使用与构建相同的 Python/Z3 环境：

    dependencies/HighDiv/venv/bin/python3 python/validation.py input.smt2 runs/example/models.jsonl
    HIGHDIV_ROOT=dependencies/HighDiv dependencies/HighDiv/venv/bin/python3 \
      python/coverage_differential_test.py input.smt2 runs/example/models.jsonl
    python3 scripts/replay.py runs/example/replay --output runs/replayed

差分测试比较每个前缀的访问事件顺序、访问集合、seen0/seen1 掩码以及覆盖/总 bit 数，不只比较百分比。真实实验全部模型独立验证，逐样本比较覆盖整数计数，每次运行前五个前缀还比较完整状态和事件。

## 后续实验

第三轮按新指引改用分级实验：固定状态微基准 → 120 秒筛选 → 晋级后的 300 秒确认 → 晋级后的 900 秒正式实验，并实测选择并发。数量上限审计通过后从一次在线轨迹提取时间与前 k 个样本两类指标；下文独立两组协议是已完成的第一、二轮协议。实现、命令、晋级条件和限制见 [第三轮流程](docs/第三轮实现与实验流程.md)。

第三轮已在 120 秒开发筛选后结束：候选相对 A/B 平均 +0.6936 个百分点，但 9 对仅 2 胜、2 平、5 负，未满足稳定性晋级条件；没有运行新的 300/900 秒实验。完整结果见 [第三轮实验报告](docs/第三轮实验报告.md)，原始开发与筛选证据见 [证据包](results/round3_evidence/README.md)。

results/baseline_once 是不可覆盖的一次基线批次：9 实例 × 种子 0/1/2，30 秒严格墙钟，最多 1000 样本，12 路并发。执行的是仅增加写出时间戳与 flush 的独立 -O0 副本，补丁见 dependencies/highdiv_timing.patch；原始源码和原始构建保留。**默认复用冻结基线，不再运行 HighDiv，也不另跑 O3 基线。** 用户另行明确要求新预算实验时，必须使用 --new-highdiv-batch 和新的输出目录；原基线及冻结记录均保留。新方法 -O3 与 HighDiv -O0 的差异是性能结论的限制。

新算法迭代须使用新目录：

    python3 scripts/run_experiments.py --binary build/lia_cover_sampler --variant full \
      --benchmarks benchmarks --output results/iteration_02 \
      --jobs 15 --seconds 30 --samples 1000 --seeds 0,1,2
    HIGHDIV_ROOT=dependencies/HighDiv dependencies/HighDiv/venv/bin/python3 \
      scripts/audit_experiments.py results/iteration_02 --jobs 15

runner 默认用 15 个控制线程管理最多 15 个独立采样进程，各求解器单线程；Python/Z3 复核用独立进程，避免共享 context。计时从创建进程前开始，严格截止时终止进程组；timeout_partial 与正常完成分开，只统计完整行且时间戳未超预算的有效不同样本。

### 两组独立实验：相同时间与相同样本数

每个实例、每个算法分别执行两组独立实验，共四个批次，不能用一次“900 秒或 1000 个，先到为止”的实验代替两组。

| 实验组 | 样本目标 | 时间预算 | 停止规则 |
|---|---|---|---|
| 相同时间 | 不以 1000 个为限制 | 900 秒 | 到时间停止，保留已生成样本 |
| 相同个数 | 1000 个 | 最多 900 秒 | 满 1000 个或到时间停止，超时保留实际生成数量 |

相同时间组将 --samples 设为双方支持的最大值 4294967295，使数量实际上不构成限制；若解空间耗尽或算法自身结束，仍可能提前停止，需按实际状态报告。相同个数组若超时未满 1000 个，必须报告实际数量，不能标记为已经完成等样本量比较。

第一轮四个批次已经完成，保存于 results/highdiv_time900、results/lia_time900、results/highdiv_count1000_t900、results/lia_count1000_t900。第二轮直接读取这些审计与样本，不再启动两个冻结版本。新算法的两种预算共用一个 15 槽队列，见 [第二轮命令](docs/第二轮实现与复现.md)。

HighDiv + filter 重放冻结候选序列，不再生成候选。样本数覆盖可精确复算；其时间包含实测验证/评分成本，但属于串行重建，不能当作重新运行过的在线包装实验。

完整模型、日志和时间戳以每批 raw.tar.gz 保存。解压即可复核，不需重新采样：

    tar -xzf results/baseline_once/raw.tar.gz -C results/baseline_once

开发/评测划分在 benchmarks/manifest.json。只在三个开发实例上调试，最终保留全部九实例及六个评测实例的结果，不按文件名定制策略。
