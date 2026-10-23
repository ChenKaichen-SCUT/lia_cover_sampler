#!/usr/bin/env python3
"""Offline, integer-exact attribution of frozen round-four output prefixes.

No sampler is invoked. Every complete raw/formal prefix is reevaluated with the
frozen original wire metric. Incomplete final lines remain separate evidence.
"""
import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
import hashlib
import json
import os
from pathlib import Path
from statistics import mean, median
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
BASE = '89e71b83adec3bdd065f7a00ef9d629cdf95c439'
PREFIXES = (1, 2, 5, 10, 50, 100, 1000)
FILES = ('raw_generator.jsonl', 'models.jsonl', 'emission.csv', 'output_transactions.jsonl')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')


def csvfile(path, rows):
    if not rows:
        Path(path).write_text('')
        return
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with Path(path).open('w', newline='') as f:
        writer = csv.DictWriter(f, fields)
        writer.writeheader()
        writer.writerows(rows)


def complete_lines(path):
    """A newline is the commit boundary, even for otherwise valid JSON at EOF."""
    data = Path(path).read_bytes()
    lines = data.splitlines(keepends=True)
    complete = [line.decode() for line in lines if line.endswith(b'\n')]
    incomplete = [line for line in lines if not line.endswith(b'\n')]
    assert len(incomplete) <= 1
    tail = incomplete[0] if incomplete else b''
    return complete, dict(incomplete_lines=len(incomplete), incomplete_bytes=len(tail),
                         incomplete_sha256=hashlib.sha256(tail).hexdigest() if tail else None,
                         byte_offset=len(data)-len(tail) if tail else None)


def read_records(path):
    lines, tail = complete_lines(path)
    # Complete malformed JSON is an error, never treated as an interrupted tail.
    return [json.loads(line) for line in lines], tail


def assignment_key(row):
    return tuple(sorted((v['name'], v['sort'], str(v['value'])) for v in row['assignment']))


def telescope(formal_covered, raw_covered, reference_covered, total):
    direct = formal_covered - raw_covered
    if reference_covered is None:
        return dict(direct_bits=direct, direct_pp=100*direct/total,
                    sequence_bits=None, sequence_pp=None, total_bits_delta=None,
                    total_pp=None, identity_verified=False)
    sequence = raw_covered - reference_covered
    difference = formal_covered - reference_covered
    assert direct + sequence == difference  # Integer bits, before percent conversion.
    return dict(direct_bits=direct, sequence_bits=sequence, total_bits_delta=difference,
                direct_pp=100*direct/total, sequence_pp=100*sequence/total,
                total_pp=100*difference/total, identity_verified=True)


def prefix_rows(formal, raw, reference, total):
    rows = []
    for k, output in enumerate(formal, 1):
        j = output['raw_generator_index'] + 1
        if not 0 < j <= len(raw):
            raise ValueError('formal output references a missing raw record')
        ref = reference[k-1] if k <= len(reference) else None
        equal_raw = reference[j-1] if j <= len(reference) else None
        row = dict(k=k, raw_prefix=j, skipped_raw_before_prefix=j-k,
                   formal_covered=output['covered'], raw_covered=raw[j-1]['covered'],
                   reference_k_covered=ref['covered'] if ref else None,
                   reference_k_available=ref is not None,
                   formal_seconds=output['seconds'], raw_seconds=raw[j-1]['seconds'],
                   reference_k_seconds=ref['seconds'] if ref else None,
                   transaction_complete=output.get('transaction_complete', True),
                   replaced=output.get('replaced', False), total=total)
        row.update(telescope(output['covered'], raw[j-1]['covered'],
                             ref['covered'] if ref else None, total))
        # A SECOND comparison, with reference prefix j. Never substitute it for k.
        row.update(reference_j_available=equal_raw is not None,
                   reference_j_covered=equal_raw['covered'] if equal_raw else None,
                   same_raw_count_sequence_bits=(raw[j-1]['covered']-equal_raw['covered']) if equal_raw else None,
                   same_raw_count_total_bits=(output['covered']-equal_raw['covered']) if equal_raw else None)
        if equal_raw:
            assert row['direct_bits'] + row['same_raw_count_sequence_bits'] == row['same_raw_count_total_bits']
        rows.append(row)
    return rows


def time_rows(formal, raw, reference, total, deadline):
    rows = []
    for t in sorted(set([x for x in (5, 10, 30, 60, 120) if x <= deadline] + [deadline])):
        k = bisect_right([r['seconds'] for r in formal], t)
        n = bisect_right([r['seconds'] for r in reference], t)
        j = formal[k-1]['raw_generator_index']+1 if k else 0
        a = formal[k-1]['covered'] if k else 0
        b = raw[j-1]['covered'] if j else 0
        c = reference[n-1]['covered'] if n else 0
        row = dict(seconds=t, formal_prefix=k, raw_prefix=j, reference_prefix=n,
                   formal_covered=a, raw_covered=b, reference_covered=c, total=total,
                   raw_models_generated_by_time=bisect_right([r['seconds'] for r in raw], t),
                   transaction_complete=formal[k-1]['transaction_complete'] if k else True)
        row.update(telescope(a, b, c, total))
        rows.append(row)
    return rows


def evaluator(formula):
    sys.path.insert(0, str(ROOT/'python'))
    from coverage_differential_test import load_metric
    metric = load_metric()
    class Incremental(metric.WireCoverageStatistics):
        def __init__(self):
            super().__init__()
            self.covered = 0
        def evaluate_node(self, key, value):
            before = self._storage[key]
            super().evaluate_node(key, value)
            after = self._storage[key]
            self.covered += self.node_count(*after)-self.node_count(*before)
    stat = Incremental()
    evaluation = metric.ManualSatisfiesMetric(formula.read_text(), stat)
    total = sum(stat.node_total(k, v[2]) for k, v in stat._storage.items())
    def add(row):
        sample = [(v['name'], v['value'] if v['sort']=='Bool' else int(v['value'])) for v in row['assignment']]
        assert evaluation.count_sample(sample), 'frozen evaluator rejected original formula'
        return stat.covered
    return stat, add, total


def inspect_run(root, configuration, job, deadline):
    folder = root/configuration/job['run']
    sources = {str(folder/f): digest(folder/f) for f in FILES}
    rows, tails = {}, {}
    for f in FILES:
        if f.endswith('.jsonl'):
            rows[f], tails[f] = read_records(folder/f)
        else:
            lines, tails[f] = complete_lines(folder/f)
            rows[f] = [tuple(line.strip().split(',')) for line in lines]
    raw, formal, transactions = (rows[f] for f in ('raw_generator.jsonl','models.jsonl','output_transactions.jsonl'))
    assert all(not t['incomplete_lines'] or job['status']=='timeout_partial' for t in tails.values())
    assert [r['raw_generator_index'] for r in raw] == list(range(len(raw)))
    assert [r['index'] for r in formal] == list(range(len(formal)))
    assert [r['emitted_index'] for r in formal] == list(range(len(formal)))
    assert [r['raw_generator_index'] for r in transactions] == list(range(len(transactions)))
    assert len(transactions) <= len(raw)
    raw_indices = [r['raw_generator_index'] for r in formal]
    assert raw_indices == sorted(set(raw_indices))
    timestamps = {int(k):float(t) for k,t in rows['emission.csv']}
    assert len(timestamps)==len(rows['emission.csv'])
    # A fully serialized model with no complete emission stamp is not a timed output.
    missing_stamps = [r['index'] for r in formal if r['index'] not in timestamps]
    after_deadline = [r['index'] for r in formal if timestamps.get(r['index'],float('inf'))>deadline and r['index'] in timestamps]
    assert not missing_stamps and not after_deadline, (missing_stamps,after_deadline)
    assert set(timestamps)==set(range(len(formal))), 'orphan timestamps require explicit investigation'
    assert list(timestamps.values())==sorted(timestamps.values())
    assert [r['raw_generator_elapsed'] for r in raw]==sorted(r['raw_generator_elapsed'] for r in raw)
    original_audit = root/configuration/'audit'/job['instance']/f'seed-{job["seed"]}.json'
    original_state = folder/'state_audit.json'
    state = json.loads(original_state.read_text())
    audit = json.loads(original_audit.read_text())
    for f, sha in state['hashes'].items():
        assert digest(folder/f)==sha, 'existing state audit does not bind current evidence'
    assert audit['unique_valid_in_budget']==len(formal)
    assert not any(v for k,v in audit['counts'].items() if k not in ('valid','incomplete_lines'))
    formula = ROOT/'benchmarks'/job['instance']
    assert digest(formula)==job['input_sha256']
    raw_stat, add_raw, total = evaluator(formula)
    formal_stat, add_formal, formal_total = evaluator(formula)
    assert formal_total==total==audit['total']
    raw_curve, formal_curve, skips, pending = [], [], [], []
    formal_seen, raw_seen = set(), set()
    cursor = 0
    for index,r in enumerate(raw):
        key = assignment_key(r)
        assert key not in raw_seen
        raw_seen.add(key)
        covered = add_raw(r)
        assert covered==r['raw_generator_covered'], 'raw coverage mismatch'
        raw_curve.append(dict(covered=covered, seconds=r['raw_generator_elapsed']))
        before = cursor
        while cursor<len(formal) and formal[cursor]['raw_generator_index']==index:
            f = formal[cursor]
            fkey = assignment_key(f)
            assert fkey not in formal_seen
            formal_seen.add(fkey)
            covered = add_formal(f)
            assert covered==f['covered'], 'formal coverage mismatch'
            assert f['total']==total
            assert r['raw_generator_elapsed']<=f['elapsed']<=timestamps[cursor]
            expected = audit['curve'][cursor]
            assert covered==expected['covered'] and timestamps[cursor]==expected['seconds']
            formal_curve.append(dict(raw_generator_index=index, covered=covered,
                seconds=timestamps[cursor], replaced=f['replaced'], transaction_complete=index<len(transactions)))
            cursor+=1
        if index<len(transactions):
            transaction=transactions[index]
            assert transaction['emitted_samples']==cursor
            assert transaction['emitted_covered']==formal_stat.covered
            # Node insertion order is independently identical for the same formula.
            assert list(raw_stat._storage)==list(formal_stat._storage)
            for x,y in zip(raw_stat._storage.values(),formal_stat._storage.values()):
                assert not (int(x[0]) & ~int(y[0])) and not (int(x[1]) & ~int(y[1])), 'raw observations lost'
            if cursor==before:
                skips.append(dict(raw_generator_index=index, raw_prefix=index+1,
                    emitted_prefix=cursor, raw_seconds=r['raw_generator_elapsed'],
                    reason='deduplicated_raw_already_formal' if key in formal_seen else 'completed_transaction_without_emission_deadline_or_unknown',
                    raw_covered=raw_stat.covered, formal_covered=formal_stat.covered))
        else:
            pending.append(dict(raw_generator_index=index, raw_seconds=r['raw_generator_elapsed'],
                has_complete_formal_output=cursor>before, emitted_prefix=cursor,
                raw_covered=raw_stat.covered, formal_covered=formal_stat.covered,
                reason='raw_record_without_completed_transaction'))
    assert cursor==len(formal)
    if configuration=='ab_reference':
        assert not any(r['replaced'] for r in formal)
        for f in formal:
            assert assignment_key(f)==assignment_key(raw[f['raw_generator_index']])
    sources.update({str(p):digest(p) for p in (original_audit,original_state,formula,root/configuration/'runs.jsonl')})
    result=dict(configuration=configuration,instance=job['instance'],seed=job['seed'],
        total=total,raw_models=len(raw),formal_models=len(formal),completed_transactions=len(transactions),
        replaced_models=sum(r['replaced'] for r in formal), tails=tails, dedup_and_other_skips=skips,
        pending_transactions=pending, missing_timestamps=missing_stamps,after_deadline=after_deadline,
        every_complete_prefix_coverage_recomputed=True, completed_transaction_observations_preserved=True,
        hashes=sources)
    return result,raw_curve,formal_curve


def one(payload):
    root,instance,seed,reference_job,polish_job,deadline = payload
    start,cpu=time.monotonic(),time.process_time()
    reference,ref_raw,ref_formal=inspect_run(Path(root),'ab_reference',reference_job,deadline)
    polish,raw,formal=inspect_run(Path(root),'ab_polish',polish_job,deadline)
    assert reference['total']==polish['total']
    rows=prefix_rows(formal,raw,ref_formal,polish['total'])
    assert all(r['direct_bits']>=0 for r in rows if r['transaction_complete'])
    missing=[]
    for k in PREFIXES:
        if k>len(formal) or k>len(ref_formal):
            missing.append(dict(prefix=k,polish_available=k<=len(formal),reference_available=k<=len(ref_formal),
                                polish_models=len(formal),reference_models=len(ref_formal)))
    for r in rows:
        r.update(instance=instance,seed=seed)
    timed=time_rows(formal,raw,ref_formal,polish['total'],deadline)
    for r in timed:r.update(instance=instance,seed=seed)
    identical=0
    # Descriptive divergence evidence; do not infer a cause from its presence.
    ref_records,_=read_records(Path(root)/'ab_reference'/reference_job['run']/'raw_generator.jsonl')
    pol_records,_=read_records(Path(root)/'ab_polish'/polish_job['run']/'raw_generator.jsonl')
    for a,b in zip(ref_records,pol_records):
        if assignment_key(a)!=assignment_key(b):break
        identical+=1
    report=dict(instance=instance,seed=seed,total=polish['total'],
        initial_identical_raw_models=identical,first_different_raw_index=identical if identical<min(len(ref_records),len(pol_records)) else None,
        runs=[reference,polish],missing_requested_prefixes=missing,
        unavailable_reference_k_prefixes=sum(not r['reference_k_available'] for r in rows),
        unavailable_reference_j_prefixes=sum(not r['reference_j_available'] for r in rows),
        prefix_rows=len(rows),integer_identities_verified=sum(r['identity_verified'] for r in rows),
        all_direct_prefix_gains_nonnegative=all(r['direct_bits']>=0 for r in rows),
        wall_seconds=time.monotonic()-start,cpu_seconds=time.process_time()-cpu)
    return report,rows,timed


def summarize(rows,kind):
    grouped=defaultdict(list)
    for row in rows:
        key=row['k'] if kind=='prefix' else row['seconds']
        if kind=='prefix' and key not in PREFIXES:continue
        grouped[key].append(row)
    out=[]
    for budget,group in sorted(grouped.items()):
        valid=[r for r in group if r['identity_verified']]
        for case in ['ALL']+sorted({r['instance'] for r in group}):
            values=[r for r in valid if case=='ALL' or r['instance']==case]
            if not values:continue
            row=dict(metric=kind,budget=budget,instance=case,paired=len(values),
                mean_direct_pp=mean(r['direct_pp'] for r in values),
                mean_sequence_pp=mean(r['sequence_pp'] for r in values),
                mean_total_pp=mean(r['total_pp'] for r in values),
                median_direct_pp=median(r['direct_pp'] for r in values),
                median_sequence_pp=median(r['sequence_pp'] for r in values),
                median_total_pp=median(r['total_pp'] for r in values),
                direct_positive_pairs=sum(r['direct_bits']>0 for r in values),
                direct_zero_pairs=sum(r['direct_bits']==0 for r in values),
                sequence_positive_pairs=sum(r['sequence_bits']>0 for r in values),
                sequence_negative_pairs=sum(r['sequence_bits']<0 for r in values))
            assert abs(row['mean_direct_pp']+row['mean_sequence_pp']-row['mean_total_pp'])<1e-10
            out.append(row)
    return out


def report_chinese(out,reports,summary,bindings):
    aliases={name:('Bromberger' if 'Bromberger' in name else 'SMT_random' if 'SMT_random' in name else 'convert') for name in {r['instance'] for r in reports}}
    lines=['# 第五轮：第四轮既有结果的收益分解','',
      '仅离线读取冻结提交 `89e71b8` 的第四轮已有结果；没有运行采样器、HighDiv 或新的端到端实验。覆盖口径和原始 0/1 观察保留条件保持不变。','',
      '## 定义与审计','',
      '对精修运行的第 k 个实际正式输出，令 j = raw_generator_index + 1。主分解以独立 A/B 的第 k 个正式样本为对照：', '',
      '`C(精修正式前 k 个) − C(对照前 k 个) = [C(精修正式前 k 个) − C(同次运行原始前 j 个)] + [C(同次运行原始前 j 个) − C(对照前 k 个)]`','',
      '第一项是同次原始序列上的直接输出精修净增益；第二项是该原始序列与独立对照的差异，发生去重时也包含 j 与 k 不同的样本计数效应。所有恒等式先在整数覆盖 bit 数上验证，再换算百分点。`prefixes.csv` 另外提供对照前 j 个的等原始数量比较，字段单独命名，绝不代替主比较。','',
      '每条完整的原始与正式样本都用 SHA-256 固定的原 HighDiv 覆盖评测器重新计算所有前缀，并与原始日志、正式日志及第四轮已验证覆盖曲线逐条核对；完成事务额外核验原始观察集合包含于正式观察集合。原始与正式时间均使用各自真实日志，正式输出时刻以刷盘后的 `emission.csv` 为准。源文件哈希和尾行字节信息见 `bindings.json`、`exceptions.json`；本次重新计算不执行 HighDiv 采样。','',
      '## 前 10、50 个样本：退步来自哪一项','',
      '| 前缀 | 案例 | 配对数 | 直接精修增益（百分点） | 原始序列差异（百分点） | 合计（百分点） |','|---:|---|---:|---:|---:|---:|']
    for r in summary:
        if r['metric']=='prefix' and r['budget'] in (10,50):
            lines.append(f"| {r['budget']} | {aliases.get(r['instance'],'全部')} | {r['paired']} | {r['mean_direct_pp']:+.6f} | {r['mean_sequence_pp']:+.6f} | {r['mean_total_pp']:+.6f} |")
    lines += ['', '上述直接项不能解释为内部生成策略的改进：它只描述同一次运行已保存模型经过输出替换后的净覆盖变化。第二项也不能未经干预验证就统称“随机噪声”。第四轮主生成与输出精修状态隔离已受审计，但限时搜索轨迹仍可能受到墙钟预算、系统调度、阶段边界及任务耗时的影响；已有日志只足以定位差异落在原始序列项，不能辨认这些因素各自的因果贡献。第二项为正时，也不得归功于输出精修。', '',
      '## 同时间对比与可归因范围','',
      '在每个给定时间点，分别读取两次运行实际已经完成且带时间戳的正式输出。精修侧仍只使用最后一个正式输出所对应的原始前缀 j，未完成事务中的后续原始模型单独列出。因此第二项在同时间表中包含原始序列及已完成样本数量差异，不能当作等样本质量。','',
      '| 时刻（秒） | 案例 | 直接精修增益（百分点） | 原始序列／数量差异（百分点） | 合计（百分点） |','|---:|---|---:|---:|---:|']
    for r in summary:
        if r['metric']=='time' and r['budget']==120:
            lines.append(f"| 120 | {aliases.get(r['instance'],'全部')} | {r['mean_direct_pp']:+.6f} | {r['mean_sequence_pp']:+.6f} | {r['mean_total_pp']:+.6f} |")
    lines += ['', '## 缺失、跳过与截止尾行','', '| 配置 | 案例 | 种子 | 完整原始／正式 | 完成事务 | 跳过输出 | 未完成事务 | 截断尾行 |','|---|---|---:|---:|---:|---:|---:|---:|']
    for p in reports:
        for r in p['runs']:
            lines.append(f"| {r['configuration']} | {aliases[r['instance']]} | {r['seed']} | {r['raw_models']}/{r['formal_models']} | {r['completed_transactions']} | {len(r['dedup_and_other_skips'])} | {len(r['pending_transactions'])} | {sum(t['incomplete_lines'] for t in r['tails'].values())} |")
    lines += ['', '缺失的参考前缀不会外推或用最近模型填充；已存在的精修前缀仍保存其直接增益，序列项与总差留空。要求检查但双方不足的 1/2/5/10/50/100/1000 前缀逐条保存在 `missing_prefixes.csv`。完整输出、缺少完成事务、无时间戳、超过截止及截断尾行分开统计；尾行不算有效正式样本。','',
      '## 重放命令与产物','', '```bash',
      'HIGHDIV_ROOT=/tmp/lia-highdiv-frozen /tmp/lia-highdiv-frozen/venv/bin/python3 -m unittest discover -s tests -p test_round5_decomposition.py',
      'HIGHDIV_ROOT=/tmp/lia-highdiv-frozen /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/decompose_round4.py --root results/round4_screen/main --output /tmp/round5-decomposition-replay --jobs 9', '```','',
      '输出目录必须不存在，防止覆盖证据。`prefixes.csv` 保存每个案例、种子、实际输出前缀；`focus_prefixes.csv` 提供 10/50 前缀逐例整数 bit 数与百分点；`times.csv` 保存严格同时间分解；`summary.csv` 为配对均值／中位数；`pairs/`、`exceptions.json` 保存逐运行审计、去重与不完整记录；`bindings.json` 绑定所有输入与分析脚本。','']
    (out/'report.md').write_text('\n'.join(lines))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=ROOT/'results/round4_screen/main')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--jobs',type=int,default=9)
    a=p.parse_args();root=a.root.resolve();out=a.output.resolve()
    if out.exists():raise SystemExit('output exists; preserve evidence and choose a fresh directory')
    out.mkdir(parents=True)
    start=time.monotonic()
    plan=json.loads((root/'plan.json').read_text())
    assert plan['seconds']==120 and set(plan['configs'])=={'ab_reference','ab_polish'}
    frozen_files=['python/coverage_differential_test.py','python/validation.py','dependencies/source_audit.json']
    for path in frozen_files:
        old=subprocess.check_output(['git','show',f'{BASE}:{path}'],cwd=ROOT)
        assert hashlib.sha256(old).hexdigest()==digest(ROOT/path), 'frozen evaluator support changed'
    metric=Path(os.environ.get('HIGHDIV_ROOT',ROOT.parent/'HighDiv-master'))/'HighDiv/my_scripts/calc_metric.py'
    expected=json.loads((ROOT/'dependencies/source_audit.json').read_text())['HighDiv/my_scripts/calc_metric.py']['sha256']
    assert digest(metric)==expected
    jobs={}
    for name in ('ab_reference','ab_polish'):
        for line in (root/name/'runs.jsonl').read_text().splitlines():
            job=json.loads(line);jobs[name,job['instance'],job['seed']]=job
    keys=sorted((i,s) for n,i,s in jobs if n=='ab_polish')
    assert len(keys)==9 and len(jobs)==18
    payloads=[(str(root),i,s,jobs['ab_reference',i,s],jobs['ab_polish',i,s],plan['seconds']) for i,s in keys]
    reports,rows,timed=[],[],[]
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:
        for future in as_completed([pool.submit(one,x) for x in payloads]):
            report,prefixes,times=future.result();reports.append(report);rows.extend(prefixes);timed.extend(times)
            write(out/'pairs'/report['instance']/f'seed-{report["seed"]}.json',report)
            print(report['instance'],report['seed'],'prefixes',len(prefixes),flush=True)
    reports.sort(key=lambda r:(r['instance'],r['seed']));rows.sort(key=lambda r:(r['instance'],r['seed'],r['k']));timed.sort(key=lambda r:(r['instance'],r['seed'],r['seconds']))
    summary=summarize(rows,'prefix')+summarize(timed,'time')
    missing=[dict(instance=r['instance'],seed=r['seed'],**x) for r in reports for x in r['missing_requested_prefixes']]
    sources={str(root/'plan.json'):digest(root/'plan.json'),str(metric):digest(metric)}
    for r in reports:
        for run in r['runs']:sources.update(run['hashes'])
    sources.update({str(ROOT/f):digest(ROOT/f) for f in frozen_files})
    assert all(digest(path)==sha for path,sha in sources.items()), 'source changed during analysis'
    bindings=dict(frozen_commit=BASE,source_files=sources,script_sha256=digest(__file__),
        metric='original-wire64-lazy-v1',no_sampling=True,source_rechecked_after_analysis=True)
    csvfile(out/'prefixes.csv',rows);csvfile(out/'focus_prefixes.csv',[r for r in rows if r['k'] in (10,50)])
    csvfile(out/'times.csv',timed);csvfile(out/'summary.csv',summary);csvfile(out/'missing_prefixes.csv',missing)
    write(out/'bindings.json',bindings)
    write(out/'exceptions.json',[dict(instance=r['instance'],seed=r['seed'],
        missing_requested_prefixes=r['missing_requested_prefixes'],
        unavailable_reference_k_prefixes=r['unavailable_reference_k_prefixes'],
        unavailable_reference_j_prefixes=r['unavailable_reference_j_prefixes'],
        runs=[{k:v for k,v in run.items() if k not in ('hashes',)} for run in r['runs']]) for r in reports])
    report_chinese(out,reports,summary,bindings)
    complete=dict(pairs=len(reports),complete_formal_prefixes=len(rows),
        integer_identities_verified=sum(r['identity_verified'] for r in rows),
        unavailable_reference_prefixes=sum(not r['identity_verified'] for r in rows),
        same_time_identities_verified=sum(r['identity_verified'] for r in timed),
        every_complete_prefix_coverage_recomputed=True,
        all_direct_prefix_gains_nonnegative=all(r['direct_bits']>=0 for r in rows),
        wall_seconds=time.monotonic()-start,cpu_seconds=sum(r['cpu_seconds'] for r in reports),
        workers=a.jobs,bindings_sha256=digest(out/'bindings.json'),summary=summary)
    write(out/'COMPLETE.json',complete)
    print(json.dumps({k:v for k,v in complete.items() if k!='summary'}))


if __name__=='__main__':main()
