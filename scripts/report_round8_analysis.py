#!/usr/bin/env python3
import csv,json,subprocess,gzip,statistics,math
from collections import defaultdict
from pathlib import Path
from analyze_early_coverage import ROOT,BATCHES
from reevaluate_round6_saved_candidates import write_csv,dump,file_hash
NAMES=['Bromberger','40-9','CAV-019','SMT_random','SMT_real','convert','dillig','pb2010','prime_cone']
def read(p):return list(csv.DictReader(p.open()))
def corr(a,b):
    if len(a)<2:return None
    x=statistics.mean(a);y=statistics.mean(b);den=(sum((v-x)**2 for v in a)*sum((v-y)**2 for v in b))**.5
    return sum((v-x)*(w-y) for v,w in zip(a,b))/den if den else None

def main():
    out=ROOT/'results/round8_analysis';cases=json.loads((ROOT/'benchmarks/manifest.json').read_text());short={c['path']:n for c,n in zip(cases,NAMES)}
    prefixes=read(out/'prefixes.csv');early=read(out/'early_samples.csv');pidx={(r['instance'],r['mode'],int(r['prefix'])):r for r in prefixes}
    text=['# 第八轮早期覆盖差距分析','', '这是对四份冻结 900 秒运行的离线重放，不是新采样实验。HighDiv、第一轮原 LIA、第二轮 A/B 修复版、第二轮 full 分开标记；每案例每算法只有 seed 0，不能作多重复显著性推断。原始分母、短路访问与整数低 64 位均由冻结 ManualSatisfiesMetric 原实现计算。全历史逐模型合法性、完整赋值、去重、时戳及累计覆盖与原审计曲线逐条一致；离线入口封禁求解器搜索。','', '不同批次的并发历史不同（原批次 12；第二轮后来扩到 15），墙钟不是新的严格同条件比较。下表仅比较各自完整有效前缀质量。整数常量独列为第四类以保证互斥完备，贡献恒为 0；前三类收益之和等于总收益。','', '|实例|方法|前2 %|前10 %|前50 %|','|---|---|---:|---:|---:|']
    for c in cases:
        for m in BATCHES:text.append('|'+short[c['path']]+'|'+m+'|'+'|'.join(f"{float(pidx[c['path'],m,k]['percent']):.4f}" for k in (2,10,50))+'|')
    text+=['','## 第十个样本之前的差距来源','', '下表为 HighDiv 减原 LIA 的前 10 累计新增 bit 分解。分类互斥；位段只分整数节点，Boolean 另列。正差不能直接解释成算法内部某项机制的因果作用。','', '|实例|总差 bit|Boolean|整数变量|算术式|低位|中位|高位|第二模型 HD/LIA bit|','|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    correlations=[];details=[]
    for c in cases:
        path=c['path'];rs={m:[r for r in early if r['instance']==path and r['mode']==m and int(r['index'])<=10] for m in BATCHES};delta=[]
        for f in ('gain','boolean','integer_variable','arithmetic_expression','low','mid','high'):delta.append(sum(int(r[f]) for r in rs['highdiv'])-sum(int(r[f]) for r in rs['lia_original']))
        text.append('|'+short[path]+'|'+'|'.join(map(str,delta))+f"|{rs['highdiv'][1]['gain']}/{rs['lia_original'][1]['gain']}|")
        for mode,rows in rs.items():
            for field in ('mode_changed','sign_changes','variable_changes','observed_ast_changes','visited_symmetric_difference','max_variable_delta'):
                a=[float(r[field]=='True') if field=='mode_changed' else math.log2(1+int(r[field])) if field=='max_variable_delta' else float(r[field]) for r in rows[1:]];b=[int(r['gain']) for r in rows[1:]]
                correlations.append(dict(instance=path,mode=mode,feature=field,n=len(a),pearson=corr(a,b),scope='within first 10, sample 1 excluded; descriptive only'))
        hd=rs['highdiv'];li=rs['lia_original'];catalog=json.loads((out/'cases'/path/'catalog.json').read_text())
        details+=['',f"**{short[path]}**：前 10 总差 {delta[0]:+d} bit；其中算术式 {delta[3]:+d}、高位 {delta[6]:+d}。前 2 的互补收益分别 {hd[1]['gain']} 和 {li[1]['gain']}，直接由两模型共同观察计算，而非距离估计。可靠选择组 {len(catalog['groups'])} 个；前 10 实际模式切换 {sum(r['mode_changed']=='True' for r in hd)} / {sum(r['mode_changed']=='True' for r in li)} 次，变量符号变化合计 {sum(int(r['sign_changes']) for r in hd)} / {sum(int(r['sign_changes']) for r in li)} 次。该关联尚不能识别求解器内部的原因。"]
    text+=details
    text+=['','## 可复核逐模型证据与限制','', '`early_samples.csv` 保存所有前 100 逐模型边际、观察、符号与访问差异；`early_first10.csv` 为要求的前 10 明细；`early_nodes.csv` 给出原节点身份、位掩码和首次观察；`early_modes.csv` 是可靠组的实际备选集合。原始表达式在每案例 `nodes.json.gz`，节点未语义合并。`correlations.csv` 是每案例每方法前 10 内描述相关系数，零方差留空，未作因果归因。','', '“首次观察”只增加 seen0/seen1，不立即算覆盖；`opposite_completions` 才是新增 covered bit。没有可靠选择组并不说明公式没有分支；不支持或条件生效的 OR 不作强制选择统计。无法由旧输出判断一次具体高位变化为何被求解器选中。局部离散锁死是假设和已有快照证据，不从这些相关性直接推导。','', '六个 evaluation 案例只用于这次事后诊断；新参数统一预先固定，不据此选择案例规则。HighDiv prime_cone 最后 1 行没有时戳，列在 anomalies.csv，并未补入任何有效前缀或参考池。']
    (ROOT/'docs/第八轮早期覆盖差距分析.md').write_text('\n'.join(text)+'\n');write_csv(out/'early_first10.csv',[r for r in early if int(r['index'])<=10]);write_csv(out/'correlations.csv',correlations)
    rows=read(out/'plateau_summary.csv');text=['# 第八轮覆盖高原期分析','', '逐条重放原始模型，使用同一 C++ PlateauTracker 离线汇总。正增益到下一次正增益之间是完成的无增长段；最后一段为右删失，900 秒只是观察截止。初始阶段尚未有正增益也保留。零增益正式输出必须不同且合法；内部候选、重复、失败与求解细分耗时未在这些历史输出中完整保存，均为 unknown，不能把它们算成零收益输出。','', '|实例|方法|模型数|零收益数|最长零段|最长无增长秒|末段秒（删失）|已知可达缺口时间 %|最终覆盖 %|','|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:text.append(f"|{short[r['instance']]}|{r['mode']}|{r['models']}|{r['zero_outputs']}|{r['longest_zero_run']}|{float(r['longest_no_growth_seconds']):.3f}|{float(r['tail_seconds']):.3f}|{100*float(r['known_reachable_gap_time_fraction']):.2f}|{float(r['percent']):.4f}|")
    text+=['','## 可达缺口证据','', '参考池仅包含四份完整公式验证通过、时戳有效的历史正式模型。对于高原起点已观察一侧的 bit，只要池中一个实际模型提供相反侧，就标为 PLATEAU_WITH_KNOWN_REACHABLE_GAP；逐段 CSV 包含见证来源、行号、模型哈希、原节点、bit 和取值。完全未观察的 bit 不用两个未来模型的合并伪装成单模型正收益。否则仅为 REACHABILITY_UNKNOWN，绝非证明饱和。未来池只用于事后说明，线上代码不会读取该池。','', '`plateau_segments.csv` 同时记录零收益期间模式改变和恢复时模式变化。模式不变可能停滞，模式改变也可能零收益，二者都不能单独证明效率。每个正收益间隔都保留；需要研究长高原时可按 duration 或 zero_outputs 筛选原表，报告未事后改变定义。低速正增长仍在 positive_outputs 和完整 covered/time 曲线体现，不混成完全零增长。','', '历史终止为预算截止或外部 timeout_partial；旧运行内部 Unknown 与每段失败次数无法从正式曲线恢复。模型数差异影响每样本高原长度，所以同时报告墙钟和最终覆盖。跨批次时间仅描述原记录，不是严格重跑的速度对照。']
    for c in cases:
        rr=[r for r in rows if r['instance']==c['path']];text+=['',f"**{short[c['path']]}**："+'；'.join(f"{r['mode']} 末段 {float(r['tail_seconds']):.1f}s / {r['tail_zero_outputs']} 个不同零收益模型，覆盖 {float(r['percent']):.3f}%" for r in rr)+'。']
    (ROOT/'docs/第八轮覆盖高原期分析.md').write_text('\n'.join(text)+'\n')
    structures=[]
    extra=ROOT.parent/'HighDiv-master/HighDiv/LIA_bench/LIA_bofill_real_ex5680_2400_100.smt2'
    catalogs=[(c['path'],c['split'],json.loads((out/'cases'/c['path']/'catalog.json').read_text())) for c in cases]
    if extra.exists():
        catalog=json.loads(subprocess.check_output([str(ROOT/'build/round8/release/choice_catalog_probe'),str(extra)],text=True));dump(out/'bofill_catalog.json',dict(formula=str(extra),sha256=file_hash(extra),catalog=catalog));catalogs.append((extra.name,'additional_static_diagnostic',catalog))
    for name,split,cat in catalogs:
        groups=cat['groups'];vs=set(v for g in groups for v in g['variables']);structures.append(dict(instance=name,split=split,groups=len(groups),alternatives=sum(len(g['alternatives']) for g in groups),mean_alternatives=statistics.mean(len(g['alternatives']) for g in groups) if groups else 0,mean_assignment_size=statistics.mean(len(a['assignments']) for g in groups for a in g['alternatives']) if groups else 0,choice_variables=len(vs),multivariable_ratio=sum(len(g['variables'])>1 for g in groups)/len(groups) if groups else 0,max_equality_linked_variables=max((len(g['equality_variables']) for g in groups),default=0),complete=cat['complete'],operations=cat['operations'],seconds=cat['seconds'],local_zero_dof='fixed prefix-1 proved in rounds 4/5' if name.startswith('SMT_random') else 'unknown'))
    write_csv(out/'structures.csv',structures)
    text=['# 第八轮离散选择结构分析','','ChoiceCatalog 只遍历根的 AND 必要路径并提取常量等式及其 AND。一般 OR 不作 XOR；仅当每对备选存在冲突赋值才标 exclusive。实际模式为所有满足备选的集合。必要等式仅作为静态变量关联范围，不把选中分支当全局约束。算术范围是语法祖先范围，不是已证明会改变的节点集合。','', '统一上限：50,000 个必要路径节点、200,000 次遍历操作、512 组、16 个备选、每备选 64 个赋值，静态工作预算 100 ms。超限保留可靠已识别组并标 incomplete，不枚举组合。参数在新模型实验前确定；这些轻量特征不宣称能预测性能。','', '|公式|组|备选|选择变量|多变量组比例|必要等式关联最大变量数|完整|','|---|---:|---:|---:|---:|---:|---|']
    for r in structures:text.append(f"|{short.get(r['instance'],r['instance'])}|{r['groups']}|{r['alternatives']}|{r['choice_variables']}|{r['multivariable_ratio']:.3f}|{r['max_equality_linked_variables']}|{r['complete']}|")
    text+=['','每个备选要求变化的坐标数应相对具体模型计算，不能把组大小一律写成必须移动数；机制日志会报告 forced_changes。完整公式决定必要等式如何协同，语法关联数并非方程秩或自由度。历史正式模式来自 early_modes / 全流模式哈希，可与目录编号关联。','', 'SMT_random/prefix-1 在第五轮相同快照较长预算对照中双方各九次确认当前 safe 区域零维（见 docs/第五轮实验报告.md），不是全局无解。LineDomain 和方向移动受该区域分支等式约束，无法凭缓存或更长方向步长离开零维区域；原 Global bit 查询保留完整 F，理论上可以跨越，但精确模编码、Reach 与算术耦合可能提高求解成本。本轮只把离散选择查询作为待检验替代，不预先解释 bit 查询超时的因果。','', '真实 bofill 公式来自本地 HighDiv 原始 LIA_bench，路径和 SHA 保存在 bofill_catalog.json。它只作额外静态结构诊断，不加入九案例性能表或三开发实验，更不以合成数据冒充真实 benchmark。对其他没有可靠组或静态分析不完整的结构，通用探索保留。','', '重放：`HIGHDIV_ROOT=/tmp/lia-highdiv-frozen /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/analyze_early_coverage.py --output results/round8_analysis_replay --jobs 9`，随后 `scripts/analyze_plateaus.py --input results/round8_analysis_replay`。输出必须新目录；所有历史原始文件只读。']
    (ROOT/'docs/第八轮离散选择结构分析.md').write_text('\n'.join(text)+'\n')
if __name__=='__main__':main()
