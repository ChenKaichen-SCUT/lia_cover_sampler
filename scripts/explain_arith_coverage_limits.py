#!/usr/bin/env python3
"""Offline exit-stage explanation from already audited arithmetic query logs."""
import argparse,csv,hashlib,json,statistics
from collections import defaultdict,Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MARK='\n## 停止原因的分阶段复核\n'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser();p.add_argument('out',type=Path);a=p.parse_args();out=a.out;stage=out/'probe'
    assert json.loads((stage/'audit_summary.json').read_text())['passed']
    rows=defaultdict(list);sources={}
    for d in sorted((stage/'runs').iterdir()):
        s=json.loads((d/'spec.json').read_text());path=d/'raw/arithmetic.jsonl';sources[str(path.relative_to(out))]=sha(path)
        for line in path.read_text().splitlines():
            r=json.loads(line)
            if r['backend'].get('engine_seen'):rows[s['formula']].append((s['seed'],r))
    data=[]
    for formula,values in rows.items():
        rs=[r for _,r in values]
        def numbers(key):return [r['backend'].get(key,0) for r in rs]
        data.append(dict(formula=formula,seeds=json.dumps(sorted({s for s,_ in values})),hook_queries=len(rs),columns_scanned=sum(numbers('columns_scanned')),columns_supported=sum(numbers('columns_supported')),zero_column_scan_queries=sum(n==0 for n in numbers('columns_scanned')),mapping_and_initial_validation_mean_ms=1000*statistics.mean(numbers('mapping_time')),proposal_mean_ms=1000*statistics.mean(numbers('proposal_time')),maximum_hook_ms=1000*max(numbers('hook_seconds')),soft_overrun_queries=sum(r['action_soft_overrun'] for r in rs),reasons=json.dumps(Counter(r['backend']['reason'] for r in rs),sort_keys=True)))
    with (stage/'exit_stage_summary.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,list(data[0]));w.writeheader();w.writerows(data)
    (stage/'exit_stage_sources.json').write_text(json.dumps(dict(search_calls=0,source_sha256=sources,script_sha256=sha(Path(__file__))),indent=2)+'\n')
    lines=[MARK,'', '下表仅复核已保存且完成独立审计的12次probe日志，未新增任何查询。mapping_time涵盖原变量重构和初始原式/相关原子检查；其后才扫描非基列。2ms是软限额，真实超支未截断或删去。','', '| 公式 | hook次数 | 扫描列总数 | 支持列总数 | 映射与初始检查均值ms | 构造均值ms | 最大hook毫秒 |','| --- | --- | --- | --- | --- | --- | --- |']
    for r in data:
        f=r['formula'];name='SMT_random' if 'SMT_random' in f else 'SMT_real' if 'SMT_real' in f else 'Bromberger' if 'Bromberger' in f else 'convert'
        lines.append(f"| {name} | {r['hook_queries']} | {r['columns_scanned']} | {r['columns_supported']} | {r['mapping_and_initial_validation_mean_ms']:.3f} | {r['proposal_mean_ms']:.3f} | {r['maximum_hook_ms']:.3f} |")
    lines+=['', '两个主公式各96次hook均未开始列扫描，全部以pool_incomplete_budget退出。因此，这批数据首先暴露的是有界准备的成本障碍，不能证明主公式没有单列自由度。会话5%额度未耗尽；每次2ms软限额与probe前32个状态上限是不同的限制。convert的8次实际hook均以evaluation_depth退出，属于当前求值支持边界，不能写成没有合法移动。','', 'Bromberger只有14个完整池包含不同于no-op的合法赋值，其中11个状态的最好covered收益优于no-op及均匀随机期望。其余79个完整单例池没有找到支持方向，另3次预算退出。正向局部选择信号仅来自这一公式，未达到“至少两个公式且包含主公式”的门槛。','', '本轮正式R/C冒烟没有提交算术移动；SMT_random的10/6/0个输出及覆盖差异不能归功于算术候选选择。首模型和超时路径已经不同；SMT_real中C终点覆盖也低于两个对照。这些单seed冒烟数据不构成稳定性能证明。','', '决定保持停止：不加预算、不放宽深度、不换seed或列策略来补过门槛。如后续另立研究任务，首先应区分准备成本、原变量映射/深度支持与候选自由度，再设计独立验证；本轮不实现这些改动。','', '补充表的离线重放命令（在报告生成后运行，重复运行会替换本节）：','', '```bash',f'python3 scripts/explain_arith_coverage_limits.py {out}','```','']
    for report in [out/'报告.md',ROOT/'docs/算术覆盖构造实验报告.md']:
        base=report.read_text().split(MARK)[0];report.write_text(base+'\n'+'\n'.join(lines))
    print(json.dumps(dict(formulas=len(data),new_search=False)))
if __name__=='__main__':main()
