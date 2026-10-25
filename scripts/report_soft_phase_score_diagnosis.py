#!/usr/bin/env python3
"""Render the frozen offline diagnosis. Standard library only; no SMT imports.

Counts are descriptions of each original session, never additional independent
replicates. Zero opportunity classes are explicitly retained in the report grid.
"""
import argparse
import csv
import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLASSES = ('covered_difference', 'observation_only_difference',
           'full_tie_positive_observation', 'no_new_observation')


def read(path):
    with path.open() as f:
        return list(csv.DictReader(f))


def write(path, rows):
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w', newline='') as f:
        w = csv.DictWriter(f, fields, lineterminator='\n')
        w.writeheader()
        w.writerows(rows)


def table(header, rows):
    def cell(x):
        if x is None or x == '':
            return 'NA'
        return str(x).replace('|', '\\|').replace('\n', ' ')
    return '\n'.join(['| ' + ' | '.join(map(cell, header)) + ' |',
                      '| ' + ' | '.join('---' for _ in header) + ' |'] +
                     ['| ' + ' | '.join(map(cell, row)) + ' |' for row in rows])


def pct(x, y):
    return f'{100*x/y:.2f}%' if y else 'NA'


def identity(r):
    return {k: r[k] for k in ('stage', 'formula', 'formula_sha256', 'configuration',
            'seed', 'session', 'source_bundle_sha256', 'catalog_sha256')}


def build(root):
    analysis = json.loads((root/'analysis_summary.json').read_text())
    audit = json.loads((root/'independent_audit.json').read_text())
    assert analysis['passed'] and audit['passed'] and audit['history_unchanged']
    sessions = analysis['sessions']
    data = {}
    for stage in ('screen', 'exposure'):
        directory = root/stage
        data[stage] = {name: read(directory/(name+'.csv')) for name in (
            'query_outcomes', 'score_availability', 'opportunity_classes',
            'applied_phase_events', 'repeated_direction_costs', 'correlations',
            'summary_explanation', 'first_model_concurrency', 'stage_summary', 'atom_catalog')}
        classes = []
        funnels = []
        checkpoints = []
        normalized = []
        for session in [r for r in sessions if r['stage'] == stage]:
            name = session['session']
            common = identity(session)
            qs = [r for r in data[stage]['query_outcomes'] if r['session'] == name]
            av = [r for r in data[stage]['score_availability'] if r['session'] == name]
            present = session['catalog_present']
            count = Counter()
            if present:
                with gzip.open(directory/name/'phase_opportunities.csv.gz', 'rt') as f:
                    for row in csv.DictReader(f):
                        if row['proposed'] != 'True':
                            continue
                        count['installed_direction_query_pairs'] += 1
                        if row['mapped_at_capture'] == 'True':
                            count['mapped_direction_query_pairs'] += 1
                            count[{'True': 'mapped_encounter_confirmed',
                                   'False': 'mapped_not_encountered_confirmed',
                                   '': 'mapped_encounter_unknown_truncated'}[row['native_encountered']]] += 1
                found = {r['opportunity_class']: r for r in data[stage]['opportunity_classes'] if r['session'] == name}
                keys = ('atom_query_opportunities', 'proposed', 'mapped_at_capture',
                        'saved_native_opportunities', 'applied', 'applied_candidate_known',
                        'applied_final_realized', 'applied_final_unrealized', 'unique_applied_queries')
                for kind in CLASSES:
                    old = found.get(kind, {})
                    classes.append(dict(common, opportunity_class=kind,
                                        **{k: int(old.get(k, 0) or 0) for k in keys},
                                        missing_native_events_not_attributed=session['unattributable_nonapplied_events'],
                                        note='Session counts; unlogged non-adoptions have no class attribution.'))
                for n in dict.fromkeys([1, 10, 50, 100, session['formal_models']]):
                    row = next((r for r in av if int(r['coverage_version']) == n), None)
                    if row:
                        checkpoints.append(dict(common, checkpoint_kind='formal_version', checkpoint=n,
                            **{k: row[k] for k in ('coverage_version', 'state_start_seconds', 'state_fnv1a64',
                            'positive_L_directions', 'only_N_directions', 'tied_atoms', 'zero_N_atoms',
                            'maximum_L', 'maximum_N', 'describable_missing_bits', 'L_distribution', 'N_distribution')}))
                for t in [x for x in (10, 30, 60) if x <= session['seconds']]:
                    row = max((r for r in av if float(r['state_start_seconds']) <= t), key=lambda r: int(r['coverage_version']))
                    checkpoints.append(dict(common, checkpoint_kind='wall_seconds', checkpoint=t,
                        **{k: row[k] for k in ('coverage_version', 'state_start_seconds', 'state_fnv1a64',
                        'positive_L_directions', 'only_N_directions', 'tied_atoms', 'zero_N_atoms',
                        'maximum_L', 'maximum_N', 'describable_missing_bits', 'L_distribution', 'N_distribution')}))
            funnels.append(dict(common, catalog_present=present,
                **{k: count[k] if present else None for k in ('installed_direction_query_pairs',
                    'mapped_direction_query_pairs', 'mapped_encounter_confirmed',
                    'mapped_not_encountered_confirmed', 'mapped_encounter_unknown_truncated')},
                **{k: sum(int(r[k]) for r in qs) for k in ('opportunities', 'same_native', 'applied',
                    'already_used', 'override_cap', 'conflict_cap', 'unattributable_nonapplied_events')},
                queries_with_adoption=sum(int(r['applied']) > 0 for r in qs),
                high_cost_without_adoption_seconds=sum(float(r['cycle_seconds']) for r in qs
                    if r['high_cost_cycle'] == 'True' and r['applied'] == '0')))
        for r in data[stage]['atom_catalog']:
            # This is a readable original equality, not an internal Z3 rewrite.
            m = re.fullmatch(r'\(=\s+([^\s()]+)\s+(-?\d+|\(-\s+\d+\))\)', r['original_expression'])
            constant = None
            if m:
                constant = str(-int(re.search(r'\d+', m[2])[0])) if m[2].startswith('(') else str(int(m[2]))
            normalized.append(dict(r, original_variable=m[1] if m else None,
                                   original_integer_constant=constant,
                                   normalization_available=bool(m)))
        write(directory/'opportunity_classes_complete.csv', classes)
        write(directory/'intervention_funnel.csv', funnels)
        write(directory/'score_checkpoints.csv', checkpoints)
        write(directory/'atom_identities.csv', normalized)
        data[stage].update(classes=classes, funnels=funnels, checkpoints=checkpoints)

    main = [r for r in sessions if r['stage'] == 'screen']
    soft = [r for r in main if r['catalog_present']]
    exp = [r for r in sessions if r['stage'] == 'exposure']
    # All displayed table cells below are generated from the audited session data.
    paired = []
    for formula in dict.fromkeys(r['formula'] for r in main):
        for seed in (0, 1, 2):
            rs = {r['configuration']: r for r in main if r['formula'] == formula and r['seed'] == seed}
            p, r, c = (rs[k] for k in ('plain', 'random_soft', 'coverage_soft'))
            paired.append([formula.split('_')[1], seed, p['covered'], r['covered'], c['covered'],
                           c['covered']-r['covered'], c['covered']-p['covered']])
    explain_rows = [[r['session'], r['covered'], r['explained_applied'],
                     pct(r['explained_applied'], r['covered']), r['explained_proposed'],
                     pct(r['catalog_outside_gain'], r['covered'])] for r in soft]
    final_scores = []
    for r in soft:
        av = [x for x in data['screen']['score_availability'] if x['session'] == r['session']]
        versions = {int(x['coverage_version']): x for x in av}
        final_scores.append([r['session'], '/'.join(versions[v]['positive_L_directions']
                             for v in (1, 10, 50, 100)), av[-1]['positive_L_directions'],
                             av[-1]['zero_N_atoms'], av[-1]['maximum_L'], av[-1]['describable_missing_bits']])
    funnel_rows = [[r['session'], r['installed_direction_query_pairs'], r['mapped_encounter_confirmed'],
                   r['mapped_not_encountered_confirmed'], r['mapped_encounter_unknown_truncated'],
                   r['opportunities'], r['applied'], r['queries_with_adoption'],
                   f"{r['override_cap']}/{r['conflict_cap']}"] for r in data['screen']['funnels'] if r['catalog_present']]
    outcome_rows = [[r['session'], r['all_applied'], r['applied_realized'], r['applied_unrealized'],
                    r['applied_without_candidate'], pct(r['applied_realized'], r['applied_known_final'])] for r in soft]
    cost_rows = [[r['session'], f"{r['process_wall_seconds']:.3f}", r['high_cost_queries'],
                 r['high_cost_queries_without_adoption'], f"{r['high_cost_cycle_seconds']:.3f}",
                 f"{r['applied_query_cycle_seconds']:.3f}", f"{r['no_applied_query_cycle_seconds']:.3f}",
                 r['status_counts'].get('unknown', 0)] for r in main]
    first_rows = [[r['session'], f"{r['first_model_seconds']:.3f}", r['first_model_observations'],
                  r['post_first_observations'], r['post_first_only_first_side'], r['post_first_covered'],
                  f"{r['all_observations_per_full_budget']:.2f}/{r['post_first_observations_per_full_budget']:.2f}"] for r in main]
    class_rows = [[r['session'], r['opportunity_class'], r['atom_query_opportunities'], r['proposed'],
                   r['mapped_at_capture'], r['applied'], r['applied_final_realized']] for r in data['screen']['classes']]
    stage_rows = [[r['session'], r['dimension'], r['stage_label'], r['queries'], r['applied'],
                   r['delivered_gain'], r['explained_applied'], f"{float(r['cycle_seconds']):.3f}"]
                  for r in data['screen']['stage_summary'] if r['configuration'] == 'coverage_soft']
    corr_rows = [[r['session'], r['x'], r['y'], r['n'],
                 f"{float(r['pearson']):.3f}" if r['pearson'] else None,
                 f"{float(r['spearman']):.3f}" if r['spearman'] else None]
                for r in data['screen']['correlations'] if r['configuration'] != 'plain' and r['x'] == 'max_applied_L']
    exposure_rows = [[r['session'], r['formal_models'], r['covered'], r['explained_applied'],
                     pct(r['explained_applied'], r['covered']) if r['catalog_present'] else None,
                     f"{r['applied_realized']}/{r['applied_unrealized']}/{r['applied_without_candidate']}",
                     r['unattributable_nonapplied_events']] for r in exp]
    parts = [
        '# 条件观察评分有效性诊断报告：只读历史日志分析\n',
        '基点 `56cc9ef`，分支 `soft-phase-score-diagnosis`。指标与分组先在提交 `65607b9` 冻结，再计算结果。'
        '本轮只重放已保存赋值；没有调用求解器搜索、采样器或优化器，没有修改历史源码、库补丁、协议或归档。**原 No-Go 保持不变。**\n',
        '## 结论\n',
        '最明确的证据是**摘要描述范围不足，以及有分数的偏好很少真正进入原生相位选择**。'
        f"正式软偏好会话中，{min(r['catalog_outside_gain']/r['covered'] for r in soft):.2%}–"
        f"{max(r['catalog_outside_gain']/r['covered'] for r in soft):.2%} 的真实新增 covered bit 不在当时完整摘要目录可描述的缺口集合中；"
        '实际采用且最终实现的摘要只能解释 0.48%–5.70% 的覆盖增益。这是集合包含关系，不是因果贡献。\n',
        '没有观察到首模型之后“全目录 covered 下界归零”；也没有观察到在线的“L 相同而 N 不同”或“L/N 完全平分但 N>0”机会。'
        '因此，“评分彻底耗尽”和“coverage 平分弃权、random 继续介入”都不是这批轨迹中得到支持的解释。'
        '部分实际介入没有在 SAT 模型中实现，另有 Unknown 无模型，但实现概率低并非统一现象。'
        '高成本主要出现在本次没有实际介入的查询；仅一个方向有同覆盖版本、同分数的连续 Unknown 重复。'
        '不能据此证明高分方向普遍错误，或者相位偏好与成本无关。\n',
        '## 数据与复核\n',
        f"18 个 60 秒正式会话：{sum(r['queries'] for r in main)} 条查询、{sum(r['formal_models'] for r in main)} 个及时模型；"
        f"6 个 30 秒曝光会话：{sum(r['queries'] for r in exp)} 条查询、{sum(r['formal_models'] for r in exp)} 个及时模型。"
        '两组独立报告，不合并为重复。24 条正式轨迹逐模型恢复 seen0/seen1、coverage_version、模型身份、提交时间、FNV 状态哈希、边际和最终覆盖，全部一致。'
        '实际数据没有缺失、损坏、迟到或未关联候选；Unknown 保留为无模型，交付收益零、最终真假和潜在收益 NA。'
        '独立审计使用累计状态和稀疏 bit 集合再次求值，与主脚本的单模型位图合并实现交叉验证。\n',
        '冻结 ManualSatisfiesMetric 保留原 AST、短路访问、整数低64位和分母：random 104639，real 66061。'
        '正式组独立复核 599438 个方向状态评分、314 个实际介入 H；曝光组为 179982 和 99。'
        '原子关联使用公式 SHA256、原 AST 前序位置、原表达式结构，不跨进程比较数字 AST ID。'
        '原表达式规范形式与可解析变量/整数常量另存；内部 rewriter 后的表达式没有保存，明确未知。\n',
        '历史最终覆盖复现如下；这是原结果，不是本轮新实验。\n',
        '`random` 指 `SMT_random_LIA_ex14600_2600_100.smt2`，`real` 指 `SMT_real_LIA_ex8500_2400_100.smt2`。'
        '会话前缀00/02/04分别是 random 的 seed0/1/2，01/03/05分别是 real 的 seed0/1/2；曝光组只含00/01、seed0。\n',
        table(['公式','seed','plain','random_soft','coverage_soft','coverage−random','coverage−plain'], paired),
        '\n## 摘要究竟解释多少真实覆盖\n',
        '对每个及时模型重算 D；每个实际采用且最终实现的方向检查 |H|=L 和 H⊆D，再取 H 的并集 E。'
        '另算全部提出且最终实现的偏好并集 P。所有查询验证 E⊆P⊆D。比例分母为零时为 NA。'
        '完整目录范围包含两侧全部摘要，不限于已安装对象；它描述不到的缺口不等于不可达。\n',
        table(['会话','真实增益 D 合计','实际介入解释 E','E/D','提出偏好解释 P','完整目录外增益比例'], explain_rows),
        '\n本批实际介入摘要之间未出现重复覆盖的 bit，但实现仍严格取并集，并通过重叠合成回归。'
        '例如 real/seed1 的 coverage_soft 比 random_soft 多解释 239 bit（328 对89），最终覆盖却少174 bit（5750 对5924）；'
        '说明更高的局部摘要解释量并不自动转化为全局优势。反方向 L/N 可计算，反方向会生成什么模型、需要多少成本一律未知。\n',
        '## 目录信息与评分阶段\n',
        'random 目录只有44个原子、88个方向；real 为59个原子、118个方向。所有目录 complete=false，'
        '各有一次 observation_cap；另分别记录250/184个 unsupported_mandatory_or 跳过。'
        '首模型后可描述缺失 bit 并集分别仅482和429。此后目录仍有正分，但范围持续有限。\n',
        table(['会话','正 L 方向：版本1/10/50/100','最终正 L 方向','最终两侧 N=0 原子','最终最大 L','最终可描述缺口'], final_scores),
        '\n全部软偏好会话首模型后都没有全 L=0 区间，故首次归零时间 NA、归零后的恢复列表为空。'
        '版本0没有任何相反观察，L=0、N>0；版本1出现正 L，证明不能假定评分单调下降。版本0目录只是事后评分，在线尚未构造。'
        '目录内正分减少与模型继续带来目录外覆盖可以同时发生。plain 无目录，相关指标不适用。'
        '完整每版本 L/N 分布在 score_availability.csv，固定输出版本及10/30/60秒截点在 score_checkpoints.csv。\n',
        '## 从提出偏好到实际改写相位\n',
        '每行的提出数是原子方向×查询对数；原生机会数是事件数，一个对象可被遇到多次，不能相互当作同一分母。'
        '所有已安装偏好在查询后捕获的映射都成功，故没有映射失败的证据；该快照不提供首次映射的精确时刻。'
        '“确未遇到”只用于事件无截断的查询；截断时保留未知。\n',
        table(['会话','提出=映射','确认遇到对象','确认未遇到对象','遇到情况未知','原生机会','实际介入','有介入查询','次数/冲突上限跳过'], funnel_rows),
        '\n完整聚合计数保留 same_native、already_used、8次额度与64冲突门槛。正式组共160个非介入事件没有逐方向记录，'
        '曝光组45个；它们的聚合原因可恢复，但不能归到具体方向。全部实际介入事件均保存且与 hook.applied 完全相等。'
        '方向排序、原生相位是否已经相同、是否遇到、额度与学习历史都会影响实际介入数量。\n',
        '四类机会逐会话如下，零类显式列出。所有在线非零机会均属于 L 不同；'
        'N 仅负责打破 covered 平分的机制和“正观察平分弃权”没有在这些数据里生效。'
        '因此不能把两模式差距归因于这项源码差异；也不能把它们拥有不同历史的查询当作严格配对反事实。\n',
        table(['会话','机会类','原子×查询','提出','映射','实际介入','最终实现'], class_rows),
        '\n## 实现概率与昂贵查询\n',
        '“未实现”仅指有保存 SAT 候选但最终真假不同，不重构未记录的回溯过程。Unknown 单列，不进入 SAT 实现率分母。\n',
        table(['会话','实际介入事件','SAT 后实现','SAT 后未实现','Unknown 无模型','SAT 实现率'], outcome_rows),
        '\ncoverage_soft 的 SAT 实现率随会话从54.17%到100%，random_soft 从37.50%到91.18%。'
        'real/seed0 的 coverage 35次全部实现，仍输给 random，因此低实现率不足以解释全部退步。\n',
        '高成本阈值在计算前固定为完整记录周期≥200ms。q.start 在评分/安装之后，不能只用 finished−start 冒充完整成本。'
        '本轮用上条 q.finished 到本条 q.finished 作为成本记账周期（首条从会话0开始），包括前条日志和本条准备、求解、验证、输出及阻塞。'
        '这是成本包络，不是当前查询准备时间的精确分解。末尾单列，周期之和加尾部严格等于保存的会话 elapsed；进程墙钟另列。\n',
        table(['会话','进程秒','高成本查询','其中无本次介入','高成本合计秒','有介入查询秒','无介入查询秒','Unknown'], cost_rows),
        '\n所有正式 coverage 会话中，高成本查询的86.27%–100%没有本次实际介入。此前偏好可能改变学习和搜索历史，所以这不能证明软偏好对成本无影响。'
        '单独限定首模型之后，六个 coverage 会话的“高成本且无本次介入/全部高成本”依次为33/36、25/25、33/39、48/49、38/45、5/5；'
        '该现象也并非仅由首模型前的初始化与 Unknown 构成，但不能反向归因于某个相位。'
        'actual_check_seconds、授权时限、原始 Unknown reason、局部超时分类和父预算截短保留在逐查询表；'
        '事件只记录当时冲突数，没有查询总冲突或精确相位时间戳，不能估计某个相位耗费了多少毫秒。\n',
        '正式组154条 Unknown、曝光组34条 Unknown，保存的原始 reason 均为 `canceled`，控制层均标记 `suspected_local_timeout`。'
        '保留这一区别，不把原始 canceled 单独当作已经证实的外部取消或局部超时。\n',
        '唯一同覆盖版本重复采用的方向出现在 random/seed1 的 coverage_soft：原子键 `fa0aa06d…`，'
        '查询12/13均为版本5，L=N=7，均 Unknown，周期分别约2.002/2.003秒；查询18在版本9又采用，SAT 后实现，模型交付增益1938 bit。'
        '三次关联查询共约5.644秒；方向本身保证的7 bit 不等于造成1938 bit的收益。前两次支持一个局部反复无交付的例子，'
        '后一次成功也必须保留，不能推广成该方向不可取或所有高分方向的普遍原因。'
        '同查询的多个方向共享成本；方向表成本不得跨方向相加。\n',
        '以下相关性只按单会话、唯一查询计算；少于10个点直接 NA。多数会话实际介入查询太少，'
        '不能以几百个相位事件伪装独立重复。提出分数相关性另在 correlations.csv，符号随会话改变，不支持统一成本关系。\n',
        table(['会话','分数','结果','有效查询数','Pearson','Spearman'], corr_rows),
        '\n## 首模型与真实并发条件\n',
        '首模型发生在偏好介入前，全部新观察/秒大量来自初次看到的单侧值。下表的观察速率分母始终为原60秒预算，'
        '未扣除启动时间；实际完整成本另见上表。后续新增观察=后续首次单侧观察+真正 covered 增益。\n',
        table(['会话','首模型秒','首模型观察','之后新增观察','之后首次单侧','之后 covered','全部/后续观察每60秒均值'], first_rows),
        '\n正式运行先有5个三配置组同时执行，峰值15进程；最后 real/seed2 三配置组首模型时只有3进程。'
        '机器8物理核/16逻辑CPU；前五组除 cpu14 外均有同物理核的另一个实验，末组三个独占物理核。'
        'random 首模型7.47–13.88秒，real 0.36–1.69秒，且首模型前不可能由偏好评分造成差别。'
        '这些是解释限制，不做事后“公平时间”校正。具体波次、CPU、重叠区间见 concurrency_intervals.csv 和 first_model_concurrency.csv。\n',
        '## 固定分段结果\n',
        '下面完整列出 coverage_soft 的固定前缀和时间分段；其他两模式在 stage_summary.csv。'
        '前缀按查询开始时 coverage_version+1 分组，Unknown 归当时待交付位置，不是虚构输出。'
        '时间分段按 query.start 分组，跨边界查询的完整成本保留在所属组；并非裁剪成严格窗口内 CPU 时间。\n',
        table(['会话','分组维度','固定阶段','查询','实际介入','真实增益','E','周期秒'], stage_rows),
        '\n## 曝光会话：独立接口核验\n',
        table(['会话','及时模型','覆盖','E','E/D','实现/未实现/无模型','不可分解非介入'], exposure_rows),
        '\n曝光峰值6进程，使用相同审计口径，评分也未在首模型后全部耗尽；在线四类同样只出现 covered_difference 与 no_new_observation。'
        '它只确认接口及重放一致性，不加入18个正式会话的均值、相关性或胜负重复。\n',
        '## 可复现材料与边界\n',
        '主表位于 `results/soft_phase_score_diagnosis/{screen,exposure}/`：query_outcomes、applied_phase_events、'
        'summary_explanation、score_availability、repeated_direction_costs、session_summary。'
        '逐会话另有完整方向机会 gzip CSV、H/E/D 位图证据、来源哈希、完整目录和所有评分阶段。'
        '行中不适用的 query/atom 字段为空；可从会话来源哈希与逐条 query_record_sha256 回到冻结记录。'
        '原始数据从历史归档只读解包；不重复提交可恢复的大体积 raw 副本，归档已在基点提交中，输入清单绑定字节哈希。\n',
        '新增15个纯离线回归通过；包含提出未采用、采用未实现、Unknown、迟到、摘要重叠、两侧平分、首次观察配对、'
        '评分恢复、事件截断、跨进程身份、未知反事实和第二审计器交叉验证。未运行任何旧全量测试入口。'
        '解析与求值期间用 no_search 禁止 Z3 搜索入口，主分析及独立审计记录搜索尝试均为零。\n',
        '重放命令见结果目录 `README.md`；全部衍生指标在独立目录，不写回任何历史结果。\n',
        '## 后续候选修改（本轮不实施）\n',
        '保持 No-Go，不启动新的60秒或900秒实验，也不据此替换评分器。若单独批准后续受控验证，优先检验摘要能否描述真实剩余缺口，'
        '并把目录成本纳入总预算；扩大目录本身不保证提高覆盖。接口层应优先补足完整遇到/跳过归属与有限事件时序，'
        '避免将“安装成功”当作有效干预。若比较方向排序，应固定介入资格、额度与触发条件，再比较排序；本轮没有正观察平分事件，'
        '不支持仅为这批实例修改平分策略。对相同状态连续 Unknown 的避免重复机制也只能作为局部候选假设，现有证据仅一个方向，'
        '不能据此宣称成本改进。任何这些修改是否提高在线累计覆盖，都需要新的同预算受控实验；本报告没有给出反事实模型或在线收益承诺。\n'
    ]
    report = ROOT/'docs/软相位评分有效性诊断报告.md'
    report.write_text('\n\n'.join(part.strip() for part in parts)+'\n', encoding='utf-8')
    # Bind rendered claims and additional descriptive tables to their inputs.
    hashes = {}
    for stage in ('screen', 'exposure'):
        for p in sorted((root/stage).glob('*.csv')):
            hashes[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    (root/'report_provenance.json').write_text(json.dumps(dict(
        source_analysis_sha256=hashlib.sha256((root/'analysis_summary.json').read_bytes()).hexdigest(),
        source_audit_sha256=hashlib.sha256((root/'independent_audit.json').read_bytes()).hexdigest(),
        report_sha256=hashlib.sha256(report.read_bytes()).hexdigest(),
        table_sha256=hashes, search_calls=0), indent=2)+'\n')
    print('Offline report and descriptive tables written:', report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='results/soft_phase_score_diagnosis')
    build((ROOT/parser.parse_args().out).resolve())
