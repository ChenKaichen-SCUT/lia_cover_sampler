#!/usr/bin/env python3
"""Chinese report for the predeclared offline saved-candidate review."""
import argparse,csv,gzip,json,statistics
from pathlib import Path
from collections import defaultdict,Counter
from reevaluate_round6_saved_candidates import require,write_csv


def read(path):
    path=Path(path)
    if not path.exists():path=Path(str(path)+'.gz')
    with (gzip.open(path,'rt') if path.suffix=='.gz' else path.open()) as f:return list(csv.DictReader(f))

def case(name):
    return 'Bromberger' if 'Bromberger' in name else 'SMT_random' if 'SMT_random' in name else 'convert'

def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+
        ['| '+' | '.join(str(x) for x in row)+' |' for row in rows])+'\n'

def delta(s):
    return '未完成' if s in ('',None) else f'{int(s):+d}' if int(s) else '0'


def main():
    p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('--report',type=Path,required=True);a=p.parse_args();b=a.input
    done=json.loads((b/'COMPLETE.json').read_text());decision=json.loads((b/'decision.json').read_text())
    require(done['all_passed'] and json.loads((b/'ARTIFACT_AUDIT.json').read_text())['all_passed'],'two audits required')
    require(decision['decision']=='No-Go','rewrite conclusions if frozen input changes; never silently reuse prose')
    sessions=read(b/'sessions.csv');pairs=read(b/'pairs.csv');audits=read(b/'session_audit.csv')
    all_sources={rn:read(b/f'round{rn}/sources.csv') for rn in (10,11)}
    parts=['''# 第十二轮：已保存候选的离线复用分析报告

**结论：No-Go，投影模型生成机制正式退出下一轮性能研究主线。** 已完成实现和历史数据归档保留，不再加投影预算、扩变量集合、改投影约束或继续修补参数。本轮没有启动新的在线实验。

普通候选确实存在可恢复价值，但集中在 Bromberger 的 `plain_extra` 轨迹。第十一轮两个 seed 在相同正式输出数下分别多覆盖 **52、86 bit**。投影模型也不是完全没有互补性：已选投影模型在 Bromberger 中覆盖了一些所有已保存普通模型都没有覆盖的 bit；convert/seed 0 的落选投影模型经池复用可多覆盖 **714 bit**。这些事实不能替代跨公式、跨 seed、相同输出数的确认：保留更多投影候选在 Bromberger 最终不增益，SMT_random 没有投影模型，convert/seed 1 无第二个输出位置。因此未通过冻结的继续门槛。

本报告全部是**已保存候选的离线重放与集合价值分析**。历史查询流保持原样；改变正式输出后，真实在线阻塞集合、反馈和后续候选流可能改变。以下覆盖差不是已测得的在线墙钟性能改善。

## 1. 冻结、边界与证据

基点 `8937f66dc45502a3c04e8c77bcc5b02c73a1c82b`，分支 `round12-saved-candidate-review`。
协议在结果计算前提交于 `c087ffd`；离线执行代码提交于 `b0a3db7`。协议文件的 SHA-256 为 `a8c9b6d6d0f216b12ccde87d47b084d45708795a6113642abceb739cce637095`。容量仅 4/8，按首次及时到达 FIFO，不因结果修改规则、案例或门槛。

第十轮 27 个会话、第十一轮 18 个会话分开报告。无新采样，无求解搜索，无新增 SMT 约束，无 HighDiv 运行；只使用其冻结 ManualSatisfiesMetric 求值代码。85 份已跟踪历史文件逐一与基点 Git 对象一致，两个 raw.tar.gz 的全部 324/216 个成员与原始归档清单一致。134 条原代码/输入证据在各自历史代码提交上核对。复用原 audit_job 的只读接口，返回值与已保存审计逐项相同；没有调用会写原目录的旧 main()。

原公式、prefix 和 seed 有独立副本；候选完整赋值由原归档和新 model_catalog.jsonl.gz 双重绑定。完整赋值身份、query 行、cycle、version、checkpoint 写完时刻、原始行哈希及归档哈希均可追溯。缺失或歧义记录为 0；本批晚到候选为 0，晚到排除和后续超时保留及时见证仍有合成回归。
''']
    counts=[]
    for rn in (10,11):
        cc=read(b/f'round{rn}/candidates.csv');qq=read(b/f'round{rn}/queries.csv');aa=[r for r in audits if int(r['round'])==rn]
        counts.append([rn,len(aa),len(cc),sum(int(x['distinct_saved_models']) for x in aa),sum(int(x['original_outputs']) for x in aa),sum(x['outcome']=='query_no_model' for x in qq),sum(x['timely']=='False' for x in cc)])
    parts.append(table(['轮次','会话','候选出现次数','会话内不同模型数之和','及时正式输出','无模型查询','晚到候选'],counts))
    parts.append(f'''原正式轨迹共 {done['original_full_state_commits']:,} 次完整状态更新，模拟轨迹共 {done['simulated_full_state_commits']:,} 次更新，均逐节点核对 seen0/seen1、边际收益、最终覆盖和去重。首次运行耗时 {done['elapsed_seconds']:.2f} 秒，6 路离线并行；这不是采样运行时间。

又独立读取落盘 CSV 和完整模型目录，用列表实现的 FIFO 重放所有选择，逐位复核状态哈希、候选可用时间和输出数量。两次审计均禁止 solver/optimizer/fixedpoint 搜索，搜索调用为 0。最终代码再次重放全部45个会话，210,514行非计时结果及全部模型目录、前缀和判定完全一致；主表不替换为第二次更快的计时。后续修改仅增强历史绝对路径的迁移和CSV字段合并，不改变池规则。23 项字面量模型回归通过，覆盖短路访问、低64位、首次观察配对、独立状态、重复、FIFO、不刷新年龄、未来候选排除、来源可用时间和冻结判定规则。

原始分母和初始已覆盖数分别为：Bromberger 16,943 / 7,878；SMT_random 104,639 / 57；convert 179,302 / 72。下文数值都是新增 covered bit，不将跨公式的 bit 相加宣称总体提升。

## 2. 对照的准确含义

- `original`：原来及时写完的正式轨迹。
- `all_4/8`：所有及时历史候选可入池。
- `ordinary_retention_4/8`：只保留普通来源（plain、extra_plain）；仍可选当前原正式模型，即使它来自投影。这隔离的是**额外保留投影候选**的价值，不能称作完全无投影。
- `ordinary_strict_4/8`：连当前原正式投影模型也禁止选择，用于核查所有投影来源的附加集合价值。

每个真实正式输出位置最多选一个当时可用、从未输出过的模型；按当前状态新增 covered bit、再新增观察数排序，完全相同优先原正式模型。池超容量立即 FIFO 淘汰。所有候选先按到达顺序准入并接受容量检查；选择位置再独立提供原正式模型作为回退，若同身份已在池中则合并，不增加额外槽，也不取消先前FIFO淘汰。重复到达不刷新年龄，之后的普通来源不能追溯改变此前投影候选的来源。

所有模拟最终都保留了原输出数量，包括两个原本零输出的会话。零输出会话在最终统计中保留，但前1等前缀全部标为未完成。所有池的输出前缀、最终收益以及模型选择，容量4与8在本批完全相同；容量8多做评分、少一些淘汰，没有新增覆盖。

## 3. 被舍弃的普通候选是否浪费机会

**有，但必须区分哪种舍弃。** 第十一轮 Bromberger 的77次严格投影胜出，seed 0/1分别30/47次，其普通候选当时 covered 增益全部为0；投影提交后，它们的剩余 covered 增益仍全部为0。在后续真实正式覆盖状态和最终状态下也没有可恢复 covered bit。不能把这77次替换解释为丢掉了77个有价值的普通模型。

真正可恢复的普通模型来自 `plain_extra`：第一轮普通候选被额外普通候选替换。第十一轮每个seed各有一个这样的正剩余模型，候选ID均为 `0-0`；当时独立收益为207/231，提交胜者后还剩80/190，历史最终还剩52/86。池之后使用它时重新评分，最终同数量提升52/86，而不是把80/190加到后续每个状态。第十轮同类机会最终为53/138/58；仅作独立历史复核，不与第十一轮合成5个独立重复。
''')
    ordinary_rows=[]
    for rn in (10,11):
        for r in all_sources[rn]:
            if r['mode']=='plain_extra' and r['origin']=='ordinary_combined':
                ordinary_rows.append([rn,case(r['instance']),r['seed'],r['discarded_distinct'],r['positive_after_winner_distinct'],r['positive_at_end_distinct'],r['final_union_remaining_gain']])
    parts.append(table(['轮次','公式','seed','不同落选普通模型','提交胜者后正剩余个数','历史末尾正剩余个数','历史末尾联合剩余bit'],ordinary_rows))
    parts.append('''上表末列是**不同落选模型一起加入真实最终集合**的诊断值，输出数量不受限，不直接当作池表现。SMT_random 第十一轮seed 1有一个普通候选当时还有剩余，但后续正式模型消除了这个缺口，池也没有在配对位置改善累计覆盖。`plain_only`没有未用的及时不同候选，所有池与原轨迹一致。

## 4. 投影与普通候选的重叠及互补

第十一轮 convert 的两次事件为：
''')
    focus=read(b/'round11/focus_77_plus_2.csv')
    parts.append(table(['seed','普通独立增益','投影独立增益','新增covered交集','联合增益','投影在普通之后的剩余','首次观察配对额外增益'],
        [[r[k] for k in ('seed','plain_gain','extra_gain','covered_overlap','joint_gain','extra_after_plain','first_observation_pairing')] for r in focus if case(r['instance'])=='convert']))
    parts.append('''seed 0 的投影候选于 1.613944931 秒完成检查点；模拟到第4个原正式位置（历史时间7.857329548秒）才选用它，当时真实边际为821。后续选择随覆盖重算，最终8个样本新增42,762，原轨迹为42,048，净差**714**。该投影在原历史最终状态还剩772；772是追加第9个模型的集合价值，不能与同8个样本的714混淆。seed 1投影在原普通模型之后还剩1,953，但原会话只有1个正式位置；第一位置普通模型33,705明显优于投影6,092，因此不能凭空追加第二个位置，池最终差为0。

Bromberger 的落选投影模型，两个seed各只有1个在胜者提交后仍有正剩余；它们之后被真实正式模型完全覆盖。seed 1在前5/10/50/100个位置可以提前获得30/32/2/1 bit，但最终差回到0；seed 0只发生短暂的小规模重排，指定前缀及最终差为0。
''')
    proj_sources=[]
    for rn in (10,11):
        for r in all_sources[rn]:
            if r['mode'].startswith('projection') and r['origin'] in ('ordinary_combined','projection'):
                proj_sources.append([rn,case(r['instance']),r['seed'],r['origin'],r['discarded_distinct'],r['never_formally_output_distinct'],r['positive_after_winner_distinct'],r['positive_at_end_distinct'],r['final_union_remaining_gain']])
    parts.append(table(['轮次','公式','seed','来源','不同落选模型','从未正式输出','胜者之后正剩余','末尾正剩余','末尾联合剩余bit'],proj_sources))
    parts.append('''第十轮投影候选重复很多，候选“出现次数”不能当作不同模型数。`candidates.csv.gz`同时保留重复标记与未输出原因；例如680次投影落选并不代表680个不同候选。状态变化轨迹在 `residual_changes.csv.gz`，保存每个变化点及最终点；中间不变区间可精确恢复，不重复累加同一候选的收益，也不假设收益只能下降。

进一步把一个会话内**全部及时普通候选**先组成集合，再加入投影候选，可以直接检查互补bit是否真的不在现有普通候选集合里。这里不限制样本个数，因而不能据此宣称同数量性能：
''')
    unions=read(b/'source_union.csv')
    parts.append(table(['轮次','公式','seed','普通候选联合增益','全部候选联合增益','投影在全部普通之后的剩余'],
        [[r['round'],case(r['instance']),r['seed'],r['ordinary_unbounded_union_gain'],r['all_unbounded_union_gain'],r['projection_after_all_ordinary']] for r in unions if r['mode'].startswith('projection')]))
    parts.append('''因此第二个问题的答案是：**存在局部真实互补性**，不能说投影完全无价值。第十一轮 Bromberger 的46/280 bit来源于已有正式投影模型，普通保留池对照本来就允许这些原正式模型；它们不是“挽回落选投影”的新收益。convert有772/1,953 bit的无输出数上限集合潜力，落实到既有输出位置后只剩seed 0的714，seed 1为0。原历史普通候选还受当时投影介入后的增量求解历史影响，以上来源差不是投影模块的独立因果效应。

## 5. 相同正式输出数量的逐会话结果

表中原增益相对各自初始正式集合；ΔA为all池相对原轨迹，ΔR为ordinary_retention相对原轨迹。4与8的完整选择和收益一致，合并显示数值，不将两种容量当成两次重复。
''')
    for rn in (11,10):
        rows=[]
        for s in sessions:
            if int(s['round'])!=rn or s['variant']!='original':continue
            vs={r['variant']:r for r in sessions if r['round']==s['round'] and r['session']==s['session']}
            rows.append([case(s['instance']),s['seed'],s['mode'],s['outputs'],s['gain'],delta(vs['all_4']['gain_difference']),delta(vs['ordinary_retention_4']['gain_difference'])])
        parts.append(f'### 第{rn}轮（独立报告）\n\n'+table(['公式','seed','原模式','输出数','原增益','ΔA4=ΔA8','ΔR4=ΔR8'],rows))
    parts.append('''## 6. 前缀、胜平负与容量敏感性

以下列出有潜在复用价值的轨迹的全部指定前缀，不因某个前缀无提升而删除；其余会话所有已完成位置的差为0。NA表示该原会话未到达此输出数量，不是0。所有会话完整数据在 `prefixes.csv` 和 `pairs.csv`。
''')
    prefrows=[]
    for rn in (10,11):
        ids=sorted({r['session'] for r in pairs if int(r['round'])==rn and r['left']=='all_4' and r['right']=='original' and r['difference'] not in ('','0')})
        for sid in ids:
            rr=[r for r in pairs if int(r['round'])==rn and r['session']==sid and r['left']=='all_4' and r['right']=='original']
            by={r['prefix']:r for r in rr};s=rr[0]
            prefrows.append([rn,case(s['instance']),s['seed'],s['mode']]+[delta(by[str(n)]['difference']) if by[str(n)]['difference']!='' else 'NA' for n in (1,2,5,10,50,100,'final')])
    parts.append(table(['轮次','公式','seed','模式','前1','前2','前5','前10','前50','前100','最终'],prefrows))
    agg=read(b/'paired_summary.csv')
    aggregate_rows=[]
    for rn in (10,11):
        for r in agg:
            if int(r['round'])==rn and r['prefix']=='final' and r['left']=='all_4' and r['right']=='original':
                aggregate_rows.append([rn,case(r['instance']),r['mode'],f"{r['wins']}/{r['ties']}/{r['losses']}",f"{float(r['mean_difference']):.2f}",r['median_difference']])
    parts.append('\n最终按会话配对统计（4、8相同）：\n\n'+table(['轮次','公式','模式','胜/平/负','均值差bit','中位数差bit'],aggregate_rows))
    parts.append('''本批all相对original没有已完成指定前缀的退步，也没有缺失模拟输出，但这只是历史样本的离线选择结果。all相对ordinary_strict，第十一轮 Bromberger最终为+46/+280，SMT_random为0/0，convert为+714/0；第十轮分别+1/+56/+2、0/0/0、0/0/0。不得将容量、前缀或同会话候选当成独立重复。

容量8对所有已完成前缀和最终覆盖的差均为0。即使all池最终覆盖不增益，早期短暂收益仍保留报告；反过来也不能仅凭早期小幅正差判定继续。

## 7. 池利用率与成本

历史进程、所有普通/额外查询、失败查询和额外阶段消耗完整保留在 `sessions.csv`、`pool_sessions.csv`、`queries.csv.gz`；没有从原10秒预算中扣掉“没产出”的计算，也没有把池评分算成原来已经完成的工作。

`scoring_seconds`是重新评分实测，`maintenance_seconds`是入池、FIFO、集合去重和选择维护实测。另用冻结Manual为每个模型计算完整观察掩码，测得构造耗时，再按各池实际准入/回退需要分摊为`observation_build_charge_seconds`；这是基于实测的费用记账，不是声称又在线执行了一遍。`charged_pool_seconds`为上述三项之和。全局离线目录/审计的IO、解析、身份哈希及独立逐模型复核另属分析成本，不是在线速度数据。在线C++是否已有可复用观察、最终写出成本和新候选流的变化均未测，不能把这些Python测量直接换算为同墙钟净收益。

下表给出有代表性的all池实测（每行一个历史会话）。原成本列含完整历史进程；扫描/维护不包括独立完整状态审计，观察构造分摊单列。
''')
    costrows=[];cost_csv=[]
    for rn in (10,11):
        for r in read(b/f'round{rn}/pool_sessions.csv'):
            if not r['variant'].startswith('all_'):continue
            v=dict(r);v['score_maintenance_ms']=1000*(float(r['scoring_seconds'])+float(r['maintenance_seconds']))
            cost_csv.append(v)
            if (case(r['instance'])=='Bromberger' and r['mode']=='plain_extra') or (rn==11 and r['mode']=='projection_fixed'):
                costrows.append([rn,case(r['instance']),r['seed'],r['mode'],r['capacity'],
                    f"{float(r['historical_process_seconds']):.3f}",f"{v['score_maintenance_ms']:.3f}",
                    f"{float(r['observation_build_charge_seconds']):.3f}",
                    r['changed_choices'],f"{float(r['pool_utilization']):.1%}",r['fifo_evictions']])
    write_csv(b/'pool_costs.csv',cost_csv)
    parts.append(table(['轮次','公式','seed','模式','容量','原进程s','评分+维护ms','观察构造分摊s','改变选择次数','池取用/准入','FIFO淘汰'],costrows))
    parts.append('''“池取用/准入”包括当轮普通模型立即入池又被选择，因此plain_only可能达到100%却没有任何复用收益。应结合`changed_choices`及`pool_sources.csv`中的`rescued_discarded_outputs/gain`一起看。第十一轮Bromberger/plain_extra只各恢复1个原落选普通模型，选择时贡献80/190，最终净增益52/86；Bromberger/projection_fixed只各复用1个落选投影模型，选择时贡献2/35，最终净增益为0。convert/seed 0恢复1个投影模型，选择时821，最终净增益714。上述选择时贡献不能再与最终差重复相加。

容量8需要评更多模型且不增加覆盖。在本Python离线实现中，观察构造分摊通常远大于评分/维护本身；尚不能断言在线使用划算。通用普通候选复用可以独立保留为有限研究方向，理由是Bromberger中一致的同样本数潜力，而非已证实的成本优势。

## 8. 最终决策与边界

1. **普通模型是否浪费了实际可恢复机会？** 是，主要是Bromberger/plain_extra中被另一个普通候选替换的首个模型；第十一轮同数量恢复52/86 bit。77个被投影替换的普通模型则没有剩余covered价值。其他实例未形成同样信号。
2. **投影是否提供普通候选之外的互补价值？** 有局部集合互补：Bromberger已有正式投影的46/280 bit，以及convert落选投影的潜力。额外保留投影的最终同数量收益却仅convert/seed 0为714，seed 1为0；Bromberger只有暂时提前覆盖、最终为0，SMT_random没有投影模型。这不满足可重复的跨公式增益。
3. **继续还是退出？** 按分析前冻结规则，**No-Go，投影生成退出后续性能研究主线**。第十一轮没有一个公式在两个seed上同时获得“all优于ordinary_retention”的最终正差，更没有至少两个公式。第十轮同一对照最终也全部为0。即使放宽至完全排除投影的strict对照，只有Bromberger在两个seed上都受益，仍不足两个公式。

保留正确性实现和历史数据，不再增加投影预算、扩大变量集合或设计新投影约束；不启动任何下一轮投影实验。若以后独立研究普通候选复用，必须先设计同预算在线对照并计入真实池开销，不能把本次离线收益直接搬成在线结论。本轮不自动启动该方向。

## 9. 结果与复现

完整文件说明、命令和环境见 `results/round12_saved_candidate_review/README.md`。主要表：`sessions.csv`、`prefixes.csv`、`pairs.csv`、`paired_summary.csv`、`source_union.csv`、`pool_costs.csv`；逐模型证据按轮保存在candidates/selection_events/residual_changes/queries及两种trajectory表中，大表只做无损gzip压缩。模型目录、初始快照和原始归档哈希保证可回溯。

原第十、十一轮数据、报告、评分、算法源文件全部保持不变。没有重新挑选历史模型、伪造迟到候选、补造输出位置或把多个独立边际分数相加当联合覆盖。
''')
    a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text('\n'.join(parts))

if __name__=='__main__':main()
