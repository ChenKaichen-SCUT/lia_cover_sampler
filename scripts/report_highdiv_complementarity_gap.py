#!/usr/bin/env python3
"""Render the Chinese report from audited, saved-model tables."""
import argparse,csv,json,gzip
from pathlib import Path
from collections import Counter
from highdiv_gap_common import load_gz
from prepare_highdiv_complementarity_gap import OUT

def read(p):return list(csv.DictReader(p.open()))
def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(str(v) for v in row)+' |' for row in rows])+'\n'
def label(run):return 'H' if run.startswith('highdiv') else ('P' if run.endswith('plain') else 'N')+('0' if run[:2] in ['00','01'] else '1')
def report(out):
    audit=json.loads((out/'audit/summary.json').read_text());assert audit['passed']
    parts=['# HighDiv—原生随机互补性差距分析（仅保存模型）\n',
    '结论是 **A＋C，局部选集问题保留 D**：保存池里已有很强的互补模型和小集合，早期正式前缀没有把这些信息集中交付；同时，每条 native 的完整900秒池仍缺少 HighDiv 已给出合法见证的观察。**不能把早期大差距都归因于缺乏高信息量模型，也不能说重排就能补上900秒终点。** 本轮没有足够证据将 B（信息只能分散在远多于HighDiv的模型中）作为主因。\n',
    '基点 `ef3e9a7`，分支 `highdiv-complementarity-gap`。协议在分析前提交为 `3aae807`。未修改采样器、概率、历史数据或历史计分；没有新模型、原公式求解、HighDiv重跑或后续实验。下文 H 为该公式唯一历史HighDiv seed；N0/N1为native_random_one两个seed；P0/P1为plain。H在两次对照中被重复引用，**不是两个独立HighDiv重复**。历史HighDiv构建和并发资源与native不同，时间仅描述保存轨迹的信息到达，不能据此做同条件速度因果归因。\n',
    '## 数据与独立审计\n',
    '共10条轨迹、22,541个完整合法且及时交付的不同模型，未发现迟到、重复、缺失或损坏记录。公式SHA256、原分母、输出次序、写完时间和每个历史前缀covered均复现；native每个前缀完整seen0/seen1指纹及最终完整状态也与历史一致。HighDiv历史未保存逐前缀完整状态，本轮从原始赋值重建O(m)，不能声称与不存在的历史状态快照作过比较。独立审计再次从空状态逐模型求值，再独立累计求值，并用原评测器重算160个具体子集；172项原来源哈希保持不变。\n',
    '所有求值复用冻结ManualSatisfiesMetric，并禁用Z3搜索入口。AST先序身份绑定公式哈希；Bool宽1，Int低64位；未访问时seen0和seen1都为空。observations目录保留完整赋值、逐模型双侧观察掩码、身份、时间、位布局和所有前缀。原始输入复制到本轮独立归档，历史目录没有回写。\n']
    allruns=[]
    for p in sorted((out/'observations').iterdir()):
        s=json.loads((p/'summary.json').read_text());allruns.append(['random' if 'random' in s['formula'] else 'real',label(s['run']),s['models'],s['covered'],s['total']])
    parts.append(table(['公式','运行','及时不同模型','900秒covered','原分母'],allruns))
    for short,title in [('random','SMT_random_LIA_ex14600_2600_100.smt2'),('real','SMT_real_LIA_ex8500_2400_100.smt2')]:
        d=out/'analysis'/short;final=read(d/'final_sets.csv');arr=read(d/'arrival_summary.csv');sel=read(d/'selection_bounds.csv');scores=read(d/'common_state_summary.csv');jump=read(d/'highdiv_jump_summary.csv');cert=read(d/'selection_certificates.csv')
        parts.append('## '+title+'\n')
        parts.append('### 900秒：哪些信息仍没有出现\n')
        parts.append(table(['native seed','H covered','N covered','交集','H独有','N独有','联合covered','仅合并才配对'],[[r['seed'],r['highdiv_covered'],r['native_covered'],r['intersection'],r['highdiv_only'],r['native_only'],r['union_covered'],r['union_pairing']] for r in final]))
        gap=read(d/'gap_structure.csv');grows=[]
        for f in final:
            rr=[r for r in gap if r['native_run']==f['native_run'] and r['category']=='highdiv_only'];states=Counter();kinds=Counter();bands=Counter()
            for r in rr:states[r['native_state']]+=int(r['bits']);kinds[r['kind']]+=int(r['bits']);bands[r['band']]+=int(r['bits'])
            grows.append([f['seed'],states['unvisited'],states['only0'],states['only1'],kinds['Boolean'],kinds['integer_variable'],kinds['arithmetic_expression'],bands['0-7'],bands['8-31'],bands['32-63']])
        parts.append(table(['seed','完全未访问','只见0','只见1','Bool','原Int变量','算术表达式','Int位0–7','8–31','32–63'],grows))
        parts.append('类型列与位段列是两种分别互斥的分组，不能横向相加。反方向缺口和每个bit两侧的首次模型见证均在 [`bit_set_differences.csv.gz`](analysis/'+short+'/bit_set_differences.csv.gz)。本例合并后首次配对项确实为0，这是实际重算的结果，并非忽略配对效应。两条native合池只作为额外信息潜力：')
        pool=json.loads((d/'cross_seed_information_only.json').read_text());parts.append(f'联合covered={pool["covered"]}，仍缺H的{pool["highdiv_missing"]}个bit；它不是任何单次900秒成绩。\n')
        parts.append('### 早期前缀：后来补齐与始终缺失必须分开\n')
        parts.append('下表“早/晚”按相同样本数k分类；末两列另给在H第k个模型实际写完时，native已完成的H目标bit数。迟到信息没有移入早期成绩。\n')
        parts.append(table(['N','k','H covered','N实际前k covered','H目标：前k已有','后来补齐','900秒仍缺失','H时刻(s)','该时刻已有H目标'],[[label(r['native_run']),r['k'],r['highdiv_covered'],r['native_prefix_covered'],r['early_by_index'],r['later_by_index'],r['right_censored_900'],r['highdiv_seconds'],r['early_by_time']] for r in arr]))
        parts.append('每个bit的首次0/1索引、模型身份和时间都在 [`first_arrivals.csv.gz`](analysis/'+short+'/first_arrivals.csv.gz)；完成时间取两侧首次时刻较晚者。仍缺失仅表示本条900秒流右删失，不表示不可达。\n')
        parts.append('### 同一基底上的单模型互补能力\n')
        score_rows=[]
        for base,k in dict.fromkeys((r['base_run'],r['base_k']) for r in scores):
            values={label(r['run']):f'{float(r["median"]):g} / {r["max"]}' for r in scores if r['base_run']==base and r['base_k']==k}
            score_rows.append([label(base)+'前'+k,values['H'],values['N0'],values['N1'],values['P0'],values['P1']])
        parts.append('每格为全部保存模型的“收益中位数 / 最大值”，包含零分；每行共享完全相同的完整参考状态。此表是混合诊断，不是实测在线轨迹。\n')
        parts.append(table(['共同基底','H','N0','N1','P0','P1'],score_rows))
        parts.append('HighDiv前10个模型中全部9次正跳跃都被扫描。每次跳跃、每条native中都找到至少一个达到H该次covered增益数量的模型；因此“保存池完全没有这种数量级的单模型”不符合证据。但覆盖内容未必相同。最大的首次跳跃如下，其共同基底是H的首模型：\n')
        parts.append(table(['N','H增益','native中位数','native最大','达到H增益的模型数','首次索引 / 秒','最大候选与H增益交集'],[[label(r['native_run']),r['highdiv_gain'],r['median'],r['max'],r['matching_count'],r['first_matching_index']+' / '+f'{float(r["first_matching_seconds"]):.3f}',r['best_target_intersection']] for r in jump if r['event_index']=='2']))
        if short=='random':parts.append('例如N0在H首模型基底下，首次达到5192增益的是第42个模型（13.242秒）；最高5900的模型为第1272个（507.654秒），其中仅2904个bit与H此次新增集合相同，另有2996个不同bit。N1第1个模型在该H基底下已经合格，却不能由此声称它与N1自己的首模型互补：参考状态不同。\n')
        else:parts.append('两个seed甚至从第1个模型开始，在H首模型基底下就都达到2492的跳跃数量；它们真实前2个模型却只覆盖142/213个bit。问题包含“相邻输出组合相似”，不能把对另一方种子的高互补能力误当成对本方历史的高边际收益。\n')
        parts.append('完整p90/p95/p99、达到门槛的全部事件及首次出现、交集/差集/Jaccard在 [`highdiv_jump_summary.csv`](analysis/'+short+'/highdiv_jump_summary.csv) 和逐模型扫描表；频次只描述该保存流，不作IID概率估计。\n')
        parts.append('### 少量模型的可实现集合潜力\n')
        parts.append('每格为“固定实际首模型 / 允许重选首模型”两条冻结启发式路径的真实联合covered下界，k包含首模型。所有模型必须在池截止前已写完。并不是最优值；固定首模型的k=2因穷举第二个而有条件最优，达到整个池联合上界的条目也已闭合。\n')
        selection_rows=[]
        for name in sorted({r['native_run'] for r in sel}):
            for t in [30,120,300,900]:
                vals=[]
                for k in [2,5,10,50,100]:
                    pair=[next(r for r in sel if r['native_run']==name and int(r['cutoff'])==t and int(r['k'])==k and r['keep_first']==fixed)['lower_bound'] for fixed in ['True','False']]
                    vals.append(' / '.join(pair))
                selection_rows.append([label(name),t]+vals)
        parts.append(table(['N','池截止秒','k=2','k=5','k=10','k=50','k=100'],selection_rows))
        parts.append('对照H的相同k覆盖为 '+', '.join(f'{k}→'+next(r['highdiv_count'] for r in sel if int(r['k'])==k) for k in [2,5,10,50,100])+'。900秒池的结果使用了未来候选，只能称事后潜力，不能放入实时前k或10秒成绩。完整上下界、未闭合状态、所选身份和最晚候选时间见 [`selection_bounds.csv`](analysis/'+short+'/selection_bounds.csv) 及 `selected_subsets.jsonl.gz`。\n')
        if short=='random':
            parts.append('N0的30秒池：固定首模型选5个已达10101，超过H前5的9762；固定首模型选2个的严格上界5156却低于H的5192，自由首模型已经找到5299。这直接显示首模型条件会影响极小前缀，不能把“固定首模型2个不够”误说成整个池信息不够。自由启发式的5模型路径9683比固定路径差；固定路径同样是自由问题的合法见证，所以不能据此断言自由问题失败。`selection_certificates.csv`单列两条已找到路径可转用的证明，没有重跑或更改选集规则。\n')
            parts.append('N0在30秒的整个池只有11017 covered，达不到H前10的11445，单靠重排当时已有模型不够。N1的30秒池总量11519足够，但两条10模型路径仅11253/11268；上下界未闭合，属于D，不能直接判B。N1的300秒池选50模型也尚未闭合。900秒时N1全池13364仍低于H前100的13381，任何重排或子集都无法达到该数量。\n')
        else:
            parts.append('N0仅使用30秒池，保留真实首模型的10模型子集已达5983，超过H前10的5924；首2个可达到3486，具体模型是N0第1和第37个，后者2.866秒才出现。N1的30秒池联合6098超过5924，但10模型路径5783/5761尚未达到，属于D；到120秒池已给出6650/6634的10模型见证。900秒两seed均能用不超过100个保存模型复现各自全池覆盖，却仍低于H终点。\n')
        parts.append('这里的自由模式路径只是有限32锚点配对＋真实边际更新，不保证优于固定路径；二者互相可用的可行证据已另列。启发式没有使用未来收益来证明最优，也没有调用LIA或有限选集优化器。没有“超时即不可达”的推理。\n')
    parts.extend(['## 观察结构：大跳跃并非主要靠新的未访问区域\n',
    '两公式H第2个模型分别新增5192/2492个covered bit；同时首次访问257/115个Boolean节点。新增covered在random中分为原Int变量2025、算术表达式3018、Boolean149；real中分别为1012、1402、78。Int收益都在0–31位，32–63位为0。首次访问、首次单侧观察、补齐相反值分别记录，前两者没有被加成covered。\n',
    'random的H第2模型相对H第1模型改变368个变量，横跨10个无条件等式关联组、118个可识别OR常量模式发生变化；real对应183个变量、8组、65个模式。例如random的xH1从0变4795、xH10从2723变0；real的xH10从800变8300，xH103/xH104/xH105从2500变0。这里看到的是多个关联组的协同变化，而不是已证明由少量变量单独导致的大收益。完整前后赋值、组成员、模式、基底哈希和确切观察差在 `representative_witnesses.jsonl.gz`、`representative_structure.csv` 和 `structure.json`，HighDiv内部生成来源写作unknown。\n',
    'native并非完全没有大范围变化；在H共同基底下，代表性native候选能够改变更多变量并获得更高数量的covered。但实际连续轨迹大多是较小变化：random相邻模型改变变量数中位数为9/9，real为7/7，HighDiv为349/184。native零covered增益的相邻事件中仍分别存在最多374、360、306、289个变量变化的模型，所以“大距离一定带来新覆盖”也不成立。以下包含首模型之后的所有事件：\n'])
    ts=[]
    for short in ['random','real']:
        for r in read(out/'analysis'/short/'actual_transition_summary.csv'):
            ts.append([short,label(r['run']),r['zero_gain_after_first'],r['models'],r['median_changed_variables'],r['zero_gain_median_changed_variables'],r['zero_gain_max_changed_variables'],r['adjacent_same_observation']])
    parts.append(table(['公式','运行','首模型后零增益数','总模型数','相邻变量变化中位数','零增益时变化中位数','零增益时变化最大','相邻O完全相同'],ts))
    parts.extend(['相邻O完全相同为0，说明不能把这些零收益事件说成“完全相同的观察模型重复”；它们通常改变了取值组合，但没有为已经累计的S补上新covered。逐事件变量名、前一模型、真实基底、new observations和gain都已保存。结构统计是相关性描述，不是生成器内部动作或变量变化的因果证明。\n',
    '## 最终分类与下一阶段\n',
    table(['公式 / seed','早期前缀','900秒终点','仍未知的部分'],[
    ['random / 0','A：30秒池5模型、120秒池10模型已有强组合；极小前缀受首模型影响','C：缺H的163个bit，含16个H前10目标','30秒自由5模型路径虽较差，已有固定路径证据，不能误判D'],
    ['random / 1','A＋D：120秒已有强10模型集合；30秒10模型与300秒50模型界未闭合','C：缺194个bit；全池还达不到H前100总量','是否更少的30秒模型可达到H前10，未证明'],
    ['real / 0','A：30秒池10模型超过H前10；信息并非必须分散在数千模型中','C：缺134个bit，含24个H前10目标','未证明最少需要多少个模型'],
    ['real / 1','A＋D：120秒已有强10模型集合；30秒10模型未闭合','C：缺182个bit，含36个H前10目标','30秒池的精确10模型最优值未知']]),
    'A的“达到”主要指总covered数量；对H同一批bit的命中和缺失始终单列。没有一条native完整池包含H的全部早期目标；数目接近不等于同一批信息。所找到的900秒自由子集用91/97（random N0/N1）和70/66（real N0/N1）个模型分别复现整条native流的观察状态，这提供可实现压缩证据，**不是最小模型数证明**。\n',
    '因此下一阶段应分两条研究问题：A优先研究让互补组合更早到达，以及有等待/维护成本的有限选择；C优先沿已保存HighDiv见证定位native缺失的相反取值与进入模式。不能只增加候选池期待改善固定900秒终点。B只在有有效小集合上界或更充分集中性证据后再判断；D可先补充固定观察矩阵上的有限选集界，或补齐所需诊断证据。本轮没有自动实现任何策略，也没有启动下一轮采样。\n',
    '所有及时不同模型原本都已正式输出：对固定时刻已经得到的全部模型，重排不会改变联合covered。离线挑选改变后，真实在线阻塞和学习状态也可能变化，不能假设后续仍然生成原候选流。因此本报告既不宣称离线挑选是在线成绩，也不从两条seed推出算法的能力上限。\n',
    '## 成本、证据与复现\n'])
    costs=[]
    for short in ['random','real']:
        v=[float(r['seconds']) for r in read(out/'analysis'/short/'selection_cost.csv')];c=json.loads((out/'analysis'/short/'complete.json').read_text());costs.append([short,c['models'],c['selection_paths'],f'{sum(v):.3f}',f'{min(v):.3f}–{max(v):.3f}',f'{c["elapsed_seconds"]:.3f}'])
    parts.append(table(['公式','模型数','固定选集路径','选集耗时合计(s)','单路径范围(s)','全分析进程耗时(s)'],costs))
    parts.append(f'首次逐模型重放采用10个独立工作进程，单轨迹16.6–91.6秒；两公式分析并行。独立审计含原评测器复算、表一致性及来源哈希，墙钟{audit["seconds"]:.3f}秒。选集耗时包含固定有限配对和贪心维护，不含已有观察恢复、原评测器审计和归档；因此不是在线额外成本或吞吐率估计。原900秒生成成本完整保留在原始归档，没有把保存模型说成免费生成。\n')
    parts.append('9项回归覆盖首次观察配对、短路未访问、低64位、重复和未来候选、seed隔离、右删失、独立状态副本、启发式上下界以及条件配对上界不能冒充整个池上界。源数据全前缀及选集独立审计见 [`audit/summary.json`](audit/summary.json)，扩展结构表审计见 `audit/supplemental_tables.json`。\n')
    parts.append('证据入口：`plan.json`绑定运行/公式；`provenance.json`绑定原文件；`archive_manifest.json`绑定本轮原始数据归档；`artifact_sha256.json`绑定全部交付文件；`observations/<run>/models.jsonl.gz`保存完整赋值和O(m)；`analysis/{random,real}`保存全部逐bit、逐模型、选集和结构数据。模型索引为1开始，HighDiv原始0开始索引另外保留为source_index。\n')
    parts.append('复现只需Python和冻结评测器的Z3解析/求值接口，无需启动C++采样器。当前冻结环境为Python3.10.12、Z3 4.14.2；评测器源文件随本轮保存，SHA256仍由原 `dependencies/source_audit.json`检查。运行命令见 [`REPRODUCE.md`](REPRODUCE.md)。\n')
    (out/'REPORT.zh.md').write_text('\n'.join(parts))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=OUT);a=p.parse_args();report(a.out)
