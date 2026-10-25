# bofill 变换消融与首次求解报告

结论：整数选择算术化已足以造成此次主要首次求解退化；延长单次查询没有救回 SMT_random 完整编译式。未找到值得进入60秒覆盖验证的稳定候选，停止扩大。

## 实验范围与完整成本

冻结源提交 `a673c49`（基点 `a31e91e`），两公式、seeds 0/1、五种表示、两种控制，共40次30秒总预算首次SAT探针。使用同一 Release 二进制与 Z3 4.14.2 simple solver，四个单核进程为同步配对批次。每个预算包含进程启动、公式载入、编译、结构导出、约束加入、check、完整恢复、原式验证、写出与统计。历史见证仅用于独立语义核验。25ms为验证和交付保留，不是额外预算。

32次有及时合法见证，8次 NO_SAT_WITHIN_BUDGET，全部失败来自 SMT_random choice_only/full；没有把 Unknown 记成 UNSAT。独立冻结 ManualSatisfiesMetric 审计通过；所有实际求解式逐字匹配已通过真实见证往返的相应表示。源码、输入、实际二进制、构建配置、Z3库哈希、完整命令与计时均在 plan.json 和原始归档。

首批控制器在已启动12个探针后提前退出；前8个保留完整控制器记录，后4个 eq_only 已正常完成并由 GNU time 记录进程墙钟（0.01秒精度）。独立恢复队列从第13个继续，无重跑、无遗漏、无混用新模型。该偏差详见 controller_recovery.json，模型完成时刻仍取探针的原始单调时钟收据，不从文件修改时间推测。其余28次及120秒延伸正常完成。

## 同预算结果

下表为两个seed首次**完整原模型验证成功**时刻的均值/中位数（n=2时相等，单位秒）。两次失败保留为失败，未以30秒冒充真实首次SAT时间；所有失败成本仍保留在逐会话表。

|实例|模式|重复分级查询|一次连续查询|
|---|---|---:|---:|
|SMT_random|plain|6.073 (2/2 SAT)|7.374 (2/2 SAT)|
|SMT_random|simplify_only|10.763 (2/2 SAT)|6.169 (2/2 SAT)|
|SMT_random|eq_only|5.606 (2/2 SAT)|6.364 (2/2 SAT)|
|SMT_random|choice_only|未检出 (0/2 SAT)|未检出 (0/2 SAT)|
|SMT_random|full|未检出 (0/2 SAT)|未检出 (0/2 SAT)|
|SMT_real|plain|0.891 (2/2 SAT)|0.716 (2/2 SAT)|
|SMT_real|simplify_only|0.948 (2/2 SAT)|0.673 (2/2 SAT)|
|SMT_real|eq_only|1.660 (2/2 SAT)|1.585 (2/2 SAT)|
|SMT_real|choice_only|5.429 (2/2 SAT)|5.743 (2/2 SAT)|
|SMT_real|full|7.850 (2/2 SAT)|6.595 (2/2 SAT)|

逐seed原始数值（秒；失败写缺失）：

|实例|模式|seed|重复|连续|
|---|---|---:|---:|---:|
|random|plain|0|4.405|9.130|
|random|plain|1|7.741|5.619|
|random|simplify_only|0|11.793|5.971|
|random|simplify_only|1|9.733|6.368|
|random|eq_only|0|6.787|4.328|
|random|eq_only|1|4.425|8.401|
|random|choice_only|0|未检出|未检出|
|random|choice_only|1|未检出|未检出|
|random|full|0|未检出|未检出|
|random|full|1|未检出|未检出|
|real|plain|0|0.851|0.760|
|real|plain|1|0.930|0.672|
|real|simplify_only|0|0.679|0.546|
|real|simplify_only|1|1.216|0.800|
|real|eq_only|0|1.237|1.646|
|real|eq_only|1|2.083|1.524|
|real|choice_only|0|4.698|5.058|
|real|choice_only|1|6.160|6.428|
|real|full|0|7.641|7.532|
|real|full|1|8.059|5.657|

## 唯一120秒延伸

预先指定 SMT_random、seed0，plain/full 各一次 continuous。原式9.030秒得到完整合法模型（纯check 8.984秒）；full 的一次check实际获得约118.57秒并全部用完，仍为 Unknown，完整会话119.979秒，零及时见证。没有再增加预算。该结果不能证明编译式永远不可求，但表明原900秒零输出不只是每次2秒取消造成的现象。

## 求解器公开计数

逐查询精确保存所有实际返回键，末次快照见 final_solver_statistics.csv；不同check的累计计数不相加，缺项不伪造为0。以 SMT_real seed0 continuous 为例：plain decisions=3349、arith-max-rows=377；choice_only decisions=20525、arith-max-rows=385、arith-branch=282、arith-gomory-cuts=248；full decisions=54670、arith-max-rows=386、arith-branch=461、arith-gomory-cuts=435。这些说明选择算术化后的整数算术活动与搜索路径发生变化，变量少并不等价于推理更容易。不能仅凭计数把某个内部步骤宣布为唯一因果原因。

SMT_random seed0 中，原式重复查询末次冲突1775、决策21365，choice_only 则冲突711、决策30722且仍失败：性能差并非简单的“冲突总数更多”。选择版公开出现额外的整数分支、gcd/HNF与割平面计数；这些是支持性描述，受不同持续时间、取消历史影响。

## A–F结论

**A.** 主要嫌疑已隔离到整数0/1选择编码：choice_only 本身在 random 两seed、两控制下失败，而 eq_only 与 simplify_only 均能解。full 连续120秒仍失败。无法进一步唯一归因于某个 Z3 内部算术子过程，也不能把有限时间失败解释为不可满足或模型丢失。

**B.** real 的 choice_only 已从原式约0.7–0.9秒退化到约4.7–6.4秒；full 又达到约5.7–8.1秒。eq_only 约1.2–2.1秒，预处理本身也有成本。算术选择保留了语义，却改变了逻辑/算术推理表示和约束联系，变量减少不是求解效率保证。

**C.** random 的选择阶段先用了188.8万 tick，real用了103.3万；2/33次之后均在后续事务的联合DAG检查触顶，仍有95/86个合法主元位置。扫描占主要tick额度，native simplify却是更大的单项墙钟。eq_only 没有跨实例稳定首次求解收益，依据指引情况A第4项停止进一步消元优化，不实现增量队列、不加大操作上限。

**D.** 连续查询并不普遍更好：random plain seed0变慢、seed1变快；eq_only同样反向变化。real plain连续略快，但选择式依然显著落后。没有证据支持统一修改首次查询控制作为新性能策略；同预算120秒也未救回full。

**E.** 没有得到跨两个实例且已验证真实覆盖收益的修复版本。10秒回退只提供容错，不是编译算法获胜。

**F.** **No-Go：停止当前编译表示优化的性能扩展，plain 保持默认研究主线。** 不执行60秒筛选，不执行900秒，不重跑HighDiv。正确性实现、负结果和可选安全保护保留。

## 主要数据

- `results/bofill_diagnosis/first_sat_30s/analysis/sessions.csv`：每会话进程/CPU时间、首次合法模型、查询数、阶段成本及失败。
- `queries.csv`、`solver_statistics.csv`、`final_solver_statistics.csv`：实际请求/授予timeout、纯check、原始Unknown理由、分类及原生统计。
- `paired_to_plain.csv`、`grouped.csv`：逐seed配对、含失败的汇总。
- `first_sat_120s/`：仅两个预选延伸；不并入40次的均值。
- `archives/`、`archive_manifest.json`、`representation_hash_audit.json`、`integrity.json`：完整模型、收据、恢复、求解式与哈希证据。
