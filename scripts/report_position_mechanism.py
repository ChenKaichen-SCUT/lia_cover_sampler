#!/usr/bin/env python3
"""Session-level synthesis of the frozen stage-one diagnostic tables."""
import argparse,csv,json,statistics
from pathlib import Path
from collections import defaultdict,Counter
from diagnose_position_mechanism import OUT,SOURCE,ROOT,PHASES,records,csv_rows,check_source
from highdiv_gap_common import load_gz,quantile,dump_gz
from reevaluate_round6_saved_candidates import dump,write_csv,require,file_hash

def short(f):return 'random' if 'random' in f else 'real'
def num(x):
    if x is None or x=='':return 'NA'
    if isinstance(x,bool):return str(x)
    try:
        v=float(x);return str(int(v)) if v.is_integer() else f'{v:.5g}'
    except (ValueError,TypeError):return str(x)
def table(headers,rows):return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(num(v) for v in r)+' |' for r in rows])
def med(rs,key):
    vv=[float(r[key]) for r in rs if r[key] not in (None,'')];return statistics.median(vv) if vv else None
def mean(rs,key):
    vv=[float(r[key]) for r in rs if r[key] not in (None,'')];return statistics.mean(vv) if vv else None

def report(out):
    require(json.loads((out/'independent_audit.json').read_text())['passed'],'independent audit required')
    plan=json.loads((out/'plan.json').read_text());summary=json.loads((out/'summary.json').read_text());sessions=csv_rows(out/'sessions.csv');cells=csv_rows(out/'equal_seed_cells.csv');contrast=csv_rows(out/'seed_contrasts.csv');stages=csv_rows(out/'stages.csv');first=csv_rows(out/'first_model_pairs.csv');trans=csv_rows(out/'transition_classes.csv')
    structures=json.loads((out/'structure_summary.json').read_text());paired=[];firstids=[];gainbins=[];breaks=[];sat_retain=[];zero_witness=[];saturation=[];costs=[]
    for f in sorted({r['formula'] for r in sessions}):
        for seed in range(3,10):
            by={r['configuration']:r for r in sessions if r['formula']==f and int(r['seed'])==seed};a,b=by['first_immediate'],by['position_immediate']
            row=dict(formula=f,seed=seed)
            for k in ['unique_atoms','effective_atoms','unique_variables','unique_groups','atom_novel64_rate','variable_novel64_rate','group_novel64_rate','covered','models','breaks_30s','longest_no_growth_seconds']:
                row[k+'_first']=float(a[k]);row[k+'_position']=float(b[k]);row[k+'_difference']=float(b[k])-float(a[k])
            paired.append(row)
    for job in plan['jobs']:
        name=job['run'];dest=out/'runs'/name;rows=load_gz(dest/'queries.jsonl.gz');common={k:job[k] for k in ['run','formula','seed','configuration']}
        gainbins.extend(csv_rows(dest/'gain_bins.csv'));breaks.extend(csv_rows(dest/'positive_events.csv'))
        ds=load_gz(dest/'model_differences.jsonl.gz');zeros=[r for r in ds if r['gain']==0 and r['changed_variables'] is not None]
        if zeros:zero_witness.append(common|max(zeros,key=lambda r:len(r['changed_variables'])))
        selected=[r for r in rows if r['selected']];costs.append(common|dict(max_selection_conflicts=max((r['conflicts_at_selection'] for r in selected),default=None),
            nonzero_selection_conflicts=sum(r['conflicts_at_selection']>0 for r in selected),selected=len(selected),unknown_reasons=json.dumps(dict(Counter(r['raw_reason'] for r in rows if r['status']=='unknown')))))
        for action in ['KeepNative','FlipOnce']:
            rr=[r for r in selected if r['action']==action];sat=[r for r in rr if r['phase_retained'] is not None]
            sat_retain.append(common|dict(action=action,selected=len(rr),evaluable=len(sat),retained=sum(r['phase_retained'] for r in sat),retention_rate=mean(sat,'phase_retained')))
        # Features 11 and 12 are the frozen bounded related-node observation
        # fractions. Label their bounded scope; they are not a full group census.
        rawqs=list(records(Path(job['raw'])/'queries.jsonl'))
        for ph in PHASES:
            rr=[r for r in selected if r['phase']==ph];features=[rawqs[r['query']]['decision']['features'] for r in rr]
            saturation.append(common|dict(phase=ph,selected=len(rr),bounded_related_single_fraction=statistics.mean(x[11] for x in features) if features else None,
                bounded_related_unseen_fraction=statistics.mean(x[12] for x in features) if features else None,
                bounded_related_covered_fraction=statistics.mean(1-x[11]-x[12] for x in features) if features else None,
                new_groups=sum(r['new_groups'] for r in rr),positive_with_first_group=sum(r['gain']>0 and r['new_groups']>0 for r in rr),
                first_group_queries=sum(r['new_groups']>0 for r in rr),gain_with_first_group=sum(r['gain'] for r in rr if r['new_groups']>0)))
    contribution=[]
    for pair in paired:
        result={k:pair[k] for k in ['formula','seed']}
        for label in ['0','1-2','3-9','10-99','100+']:
            rr={r['configuration']:int(r['gain']) for r in gainbins if r['formula']==pair['formula'] and int(r['seed'])==pair['seed'] and r['scope']=='all' and r['bin']==label}
            result[label]=rr['position_immediate']-rr['first_immediate']
        result['total_difference']=sum(result[k] for k in ['0','1-2','3-9','10-99','100+'])
        require(result['total_difference']==pair['covered_difference'],'gain-bin difference does not telescope')
        contribution.append(result)
    write_csv(out/'gain_contribution_decomposition.csv',contribution)
    write_csv(out/'paired_diversity.csv',paired);write_csv(out/'phase_retention.csv',sat_retain);write_csv(out/'bounded_related_saturation.csv',saturation);write_csv(out/'conflict_evidence.csv',costs);write_csv(out/'gain_bins.csv',gainbins);dump_gz(out/'zero_gain_large_change_witnesses.jsonl.gz',zero_witness)
    write_csv(out/'breakthrough_events.csv',[r for r in breaks if r['break_30s']=='True'])
    text=['# 原生干预位置：已有28条轨迹的只读机制诊断','',
        f"基于`{plan['base_commit']}`保存结果，共{summary['queries']}次查询、{summary['models']}个及时正式模型，{summary['selected']}次实际选择。最多15个离线工作进程；没有新采样、没有搜索、没有HighDiv重跑、没有算法修改。原900秒结论保持不变。",'',
        '本报告只解释保存轨迹的关联，不把查询当成独立重复，不从即时相关推断整个900秒的因果收益。位置在查询前随机选择，Keep/Flip在到达目标后随机抽取；未到达位置不能当成随机Keep。H1/H2/H3分别讨论，阶段2–4未执行。','',
        '## 1. 首模型前差异必须先剥离','',
        f"14个公式/seed配对中，首模型完全相同的有{summary['first_model_same']}对；首模型前状态和授予预算序列完全相同的有{summary['first_sequence_same']}对。所有首模型前查询均未启用相位干预。下面的时间/Unknown差异不能归因于干预位置，可能涉及超时边界、运行竞争及增量求解历史，现有日志不能进一步拆成独立原因。",'',
        table(['公式','seed','相同首模型','first/position首模型秒','first/position此前Unknown','相同状态/预算序列'],[[short(r['formula']),r['seed'],r['same_first_model'],f"{num(r['first_seconds'])}/{num(r['position_seconds'])}",f"{r['first_unknowns']}/{r['position_unknowns']}",r['same_pre_first_status_budget_sequence']] for r in first]),'',
        '全部首模型前逐查询明细在runs/*/before_first.json。random原10秒差包含首模型尚未到达及仅有一个模型不能完成覆盖的因素，不能统称位置随机化的早期机制损失。前10样本质量仍须独立保留。','',
        '## 2. K×Keep/Flip：描述均值与负对照','',
        '下表限position_immediate，指标先在每会话内计算，再对7个seed等权平均。均值收益的分母包含Unknown、失败和零收益查询。耗时为含准备、求解、补全验证、输出和记录的保存完整周期；初始化及周期间未归属部分仍属于原900秒总成本，不能当作免费。', '',
        table(['公式','K','动作','查询数','bit/查询','正收益率','SAT率','Unknown率','完整周期均值(s)','周期p90的seed均值(s)'],[[short(r['formula']),r['K'],r['action'],r['queries'],r['mean_gain'],r['positive_rate'],r['sat_rate'],r['unknown_rate'],r['mean_seconds'],r['p90_seconds']] for r in cells if r['configuration']=='position_immediate']),'',
        '完整逐会话、逐阶段、逐查询前覆盖分箱4×2表在cells.csv。分配K但未选中的查询在assigned_K_itt.csv单列，不能只展示成功到达的事件。精确原子已选后的最终相位保持率在phase_retention.csv；软相位不是硬约束。','',
        '以下比较只使用两组共同的“阶段×查询前covered每250bit×实际授予timeout×先前零收益段”层；各层按两组较小样本数加权。K=2–4对K=1取三个位置差的等权平均。Flip-minus-Keep只是两种动作的描述性位置差之差，两动作共同层可能不同，不是随机试验的无偏交互效应。','',
        table(['公式','动作','指标','有效seed','中位差','正/零/负','最小共同权重'],[[short(r['formula']),r['action'],r['field'],r['seeds'],r['median'],f"{r['wins']}/{r['ties']}/{r['losses']}",r['minimum_common_weight']] for r in contrast if r['contrast']=='late_mean-K1' and r['field'] in ['gain','positive','full_cycle_seconds']]),'',
        'K2/K3/K4分别对K1及后续5查询对照完整保留在seed_contrasts.csv和within_session_contrasts.csv。KeepNative的位置差若与Flip同量级或跨seed不一致，就不能仅凭Flip组均值断言“较后位置更有价值”。状态分层不能恢复未记录的求解器内部状态，也不能识别持续随机混合相对固定后位置的效果。','',
        '## 3. H1：结构多样性与重复选择','',
        table(['公式','必要等式组','最大组变量数','必要整数等式','条件等式'],[[short(r['formula']),r['group_count'],r['largest_group'],r['necessary_equations'],r['conditional_equations']] for r in structures]),'',
        '这里只合并顶层合取下的必要整数等式。OR/ITE中的等式不升级为全局等式；这些组仍可能经其他关系耦合，不能称为已证明独立的可自由变化块。原子身份绑定公式哈希与完整表达式，直接变量依赖用精确线性系数消去后核对日志；不同cache_index不是新结构证据。','',
        table(['公式','seed','不同原子first/position','触及变量first/position','触及组first/position','64窗原子新颖率差','64窗组新颖率差'],[[short(r['formula']),r['seed'],f"{num(r['unique_atoms_first'])}/{num(r['unique_atoms_position'])}",f"{num(r['unique_variables_first'])}/{num(r['unique_variables_position'])}",f"{num(r['unique_groups_first'])}/{num(r['unique_groups_position'])}",r['atom_novel64_rate_difference'],r['group_novel64_rate_difference']] for r in paired]),'',
        '以下为同一动作下，新颖事件相对重复事件的收益差；除前述状态层外再匹配K。新颖性只查看此前64次实际选择，当前或未来模型不进入窗口。若新颖和重复事件缺少共同层则为NA，不能按0处理。','',
        table(['公式','新颖性定义','动作','有效seed','中位bit/查询差','正/零/负','最小共同权重'],[[short(r['formula']),r['contrast'],r['action'],r['seeds'],r['median'],f"{r['wins']}/{r['ties']}/{r['losses']}",r['minimum_common_weight']] for r in contrast if r['contrast'] in ['atom_novel64','variable_novel64','group_novel64'] and r['field']=='gain']),'',
        '首次触及组与窗口内重新触及分别保存；first-group的正收益计数、查询数和贡献在bounded_related_saturation.csv。观测关联不能直接证明按新颖性优先选择会得到相同收益。所选位置之后的候选原子没有完整记录，也不能离线假装已经执行了流式新颖性策略。','',
        '## 4. H2：收益集中度、跨组变化与高原','',
        table(['公式','配置','前10大收益占总覆盖(中位)','去首10模型后前10大收益占比(中位)','30秒高原突破数(中位)','最长无增长秒(中位)'],[[short(f),c,med([r for r in sessions if r['formula']==f and r['configuration']==c],'top10_share'),med([r for r in sessions if r['formula']==f and r['configuration']==c],'after10_top10_share'),med([r for r in sessions if r['formula']==f and r['configuration']==c],'breaks_30s'),med([r for r in sessions if r['formula']==f and r['configuration']==c],'longest_no_growth_seconds')] for f in sorted({r['formula'] for r in sessions}) for c in ['first_immediate','position_immediate']]),'',
        '前10大收益是集中度描述，不重排正式轨迹。正收益的1–2/3–9/10–99/100+分箱同时报告全程和去掉首10模型的情况；不能仅因最初两个模型补齐大量位就把整个长预算收益说成少数后期突破。','',
        table(['公式','配置','收益类','每会话平均模型数','平均改变变量(seed等权)','平均改变等式组(seed等权)'],[[short(f),c,g,mean([r for r in trans if r['formula']==f and r['configuration']==c and r['gain_class']==g],'queries'),mean([r for r in trans if r['formula']==f and r['configuration']==c and r['gain_class']==g],'mean_changed_variables'),mean([r for r in trans if r['formula']==f and r['configuration']==c and r['gain_class']==g],'mean_changed_groups')] for f in sorted({r['formula'] for r in sessions}) for c in ['first_immediate','position_immediate'] for g in ['zero','gain1-2','gain3-99','gain100+']]),'',
        '第一个模型没有前驱，改变数记NA。逐模型保留改变变量列表、等式组、重叠等式邻域以及真实新covered掩码；邻域重叠不算独立组。zero_gain_large_change_witnesses.jsonl.gz每会话保留变量改变最多但收益为0的反例，绑定原模型、前驱模型和参考覆盖状态。高收益与多组变化相关，并不推出多翻转一次会更好。','',
        '## 5. 分阶段：为什么终点优势逐渐缩小','',
        table(['公式','配置','查询起点阶段','mean新增bit','mean新触及组','64窗组新颖率均值','正收益率均值'],[[short(f),c,ph,mean([r for r in stages if r['formula']==f and r['configuration']==c and r['phase']==ph],'gain'),mean([r for r in stages if r['formula']==f and r['configuration']==c and r['phase']==ph],'new_groups'),mean([r for r in stages if r['formula']==f and r['configuration']==c and r['phase']==ph],'group_novel64_rate'),mean([r for r in stages if r['formula']==f and r['configuration']==c and r['phase']==ph],'positive_rate')] for f in sorted({r['formula'] for r in sessions}) for c in ['first_immediate','position_immediate'] for ph in PHASES]),'',
        '阶段按查询周期开始划分，跨边界查询的交付可能发生在下一阶段，故这张表不能冒充固定时间点差值。原900秒结果中优势幅度缩小是真实轨迹现象；同阶段结构重复和低收益的关联不能证明唯一原因。bounded_related_saturation.csv补充冻结日志中有界相关节点的单侧/未见/已覆盖比例，它不是完整等式组的覆盖率，相关节点采集有上限。','',
        '## 6. H3：已知与缺失的搜索轨迹证据','',
        f"{sum(int(r['nonzero_selection_conflicts']) for r in costs)}次已选事件记录了非零的选择时conflicts，最大值为{max((int(r['max_selection_conflicts']) for r in costs if r['max_selection_conflicts'] is not None),default=0)}。这只是选择发生前的累计计数，不能当作完整查询冲突数。每次查询总冲突、回溯、学习子句、决策序列后半段均没有足够记录，因此H3无法确认或排除。",'',
        '后续1/5查询的真实收益和成本都保留在逐查询表；窗口末尾不足为NA，窗口重叠且包含新的动作，不能累加成当次干预贡献，也不能把这些窗口当作独立重复。KeepNative同样执行位置查找、原子分类和日志记录，它是未改变所选相位的负对照，不是零成本或无测量干扰的对照。','',
        '## 7. 审计、限制与复现','',
        f"全体{summary['models']}个保存模型复用原冻结ManualSatisfiesMetric逐模型审计及哈希，重新检查全部查询前状态、逐模型边际和最终覆盖。新独立审计再按固定规则对每会话首模型、最大收益、最长正收益间隔的见证重新调用ManualSatisfiesMetric求值，搜索调用数为0。全部历史输入在分析前后重新核对哈希。",'',
        '首次分析因原子解析未显式使用评测器私有Z3上下文而崩溃；单工作进程复现为signal 11，修正解析上下文后补充回归。原错误日志、原冻结方案、修正哈希均保留。该实现错误未产生新模型，也未修改统计定义。原27项相关回归通过，修正后17项新诊断回归通过，其中16项与原27项重叠。','',
        '所有数据来自两个已有开发公式，不把7个seed当作7个新公式。不合并不同公式的bit数量。观察到的即时关联可能受求解历史、相位未保持、超时与状态分布影响；本轮未验证新在线策略，也不保证改变候选选择后搜索轨迹保持不变。','',
        '```bash',
        'HIGHDIV_ROOT=/tmp/lia-highdiv-frozen PYTHONPATH=scripts:python /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/diagnose_position_mechanism.py --out results/position_mechanism_replay --prepare',
        'HIGHDIV_ROOT=/tmp/lia-highdiv-frozen PYTHONPATH=scripts:python /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/diagnose_position_mechanism.py --out results/position_mechanism_replay --run --jobs 15',
        'HIGHDIV_ROOT=/tmp/lia-highdiv-frozen PYTHONPATH=scripts:python /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/audit_position_mechanism.py --out results/position_mechanism_replay --jobs 15',
        'HIGHDIV_ROOT=/tmp/lia-highdiv-frozen PYTHONPATH=scripts:python /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/report_position_mechanism.py --out results/position_mechanism_replay',
        '```','',
        '新目录重放不会覆盖历史。原日志缺失时自动从冻结归档恢复到新目录。结构数据和模型差分绑定公式、原AST节点、完整赋值身份及原输入manifest，具体模型无需重新求解。完整原始模型仍在冻结基点归档，本轮保存输入引用与哈希，不重复伪造模型。']
    (out/'报告.md').write_text('\n'.join(text)+'\n')
    # Interpretation is intentionally a separate, reviewable file created only
    # after inspecting complete tables, never used to select/filter trajectories.
    interpretation=out/'interpretation.md'
    if not interpretation.exists():interpretation=OUT/'interpretation.md'
    if interpretation.exists():(out/'报告.md').write_text((out/'报告.md').read_text()+'\n'+interpretation.read_text())
    (ROOT/'docs/原生干预位置机制诊断报告.md').write_text((out/'报告.md').read_text())
    check_source(out)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=OUT);a=p.parse_args();report(a.out.resolve())
