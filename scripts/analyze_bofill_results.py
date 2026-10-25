#!/usr/bin/env python3
"""Fixed time/model-prefix analysis. No generation, adaptive selection or new runs."""
import argparse, csv, json, math, statistics
from pathlib import Path
from reevaluate_round6_saved_candidates import dump, write_csv, file_hash
TIMES=[1,5,10,30,60,180,300,600,900]
PREFIXES=[2,5,10,50,100,1000]
CONFIGS=['HighDiv_release','plain_clean','choice_compiled']
def stats(values):
    return dict(n=len(values),mean=statistics.mean(values) if values else None,median=statistics.median(values) if values else None)
def number(v,digits=4):return '未完成/未知' if v is None else f'{v:.{digits}f}'
def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','|'+'|'.join(['---']*len(headers))+'|',*['| '+' | '.join(map(str,row))+' |' for row in rows]])
def main():
    p=argparse.ArgumentParser();p.add_argument('out');a=p.parse_args();out=Path(a.out).resolve();plan=json.loads((out/'plan.json').read_text());audit=json.loads((out/'audit_summary.json').read_text());assert audit['passed']
    runs=audit['runs'];timelines={};summaries=[];fixed_time=[];prefix_rows=[];costs=[];query_costs=[];structures=[]
    for r in runs:
        path=out/'audit'/r['run']/'timeline.csv'
        with path.open() as f:trace=[{**x,'index':int(x['index']),'covered':int(x['covered']),'gain':int(x['gain']),'completion':float(x['completion'])} for x in csv.DictReader(f)]
        timelines[r['run']]=trace;identity={k:r[k] for k in ['formula','seed','configuration','run']}
        summaries.append({**identity,**{k:r[k] for k in ['process_wall_seconds','cleanup_seconds','returncode','terminal','distinct','saved_models','positive','covered','total','coverage','first_model_seconds','longest_zero_gain_models','longest_no_growth_seconds','tail_no_growth_seconds','audit_seconds']},**{f'excluded_{k}':v for k,v in r['excluded'].items()}})
        for limit in TIMES:
            if limit>r['seconds']:continue
            prefix=[x for x in trace if x['completion']<=limit];covered=prefix[-1]['covered'] if prefix else 0
            fixed_time.append({**identity,'time_budget':limit,'distinct':len(prefix),'covered':covered,'total':r['total'],'coverage':covered/r['total'] if r['total'] else 0})
        for n in PREFIXES:
            reached=len(trace)>=n;row=trace[n-1] if reached else None
            prefix_rows.append({**identity,'prefix':n,'complete':reached,'available':len(trace),'covered':row['covered'] if row else None,'coverage':row['covered']/r['total'] if row and r['total'] else None,'arrival_seconds':row['completion'] if row else None})
        if r['costs']:
            for stage,value in r['costs']['seconds'].items():costs.append({**identity,'stage':stage,'seconds':value,'known':True})
            costs.append({**identity,'stage':'unattributed_and_exit','seconds':max(0,r['process_wall_seconds']-sum(r['costs']['seconds'].values())),'known':True})
        else:costs.append({**identity,'stage':'internal_stage_costs','seconds':None,'known':False})
        if r['structure']:structures.append({**identity,'stop_reason':r['compiler_stop'],**r['structure']['counts'],**r['structure']['seconds']})
        qp=out/'runs'/r['run']/'raw/queries.jsonl'
        if qp.exists():
            with qp.open() as f:
                for line in f:
                    if not line.endswith('\n'):continue
                    q=json.loads(line);query_costs.append({**identity,'query':q['query'],'start':q['start'],'finished':q['finished'],**q['evidence'],**{k:q[k] for k in ['gain','emitted','duplicate','scoring_seconds','write_seconds','blocking_seconds']}})
    pairs=[]
    for family,rows,value in [('time',fixed_time,'time_budget'),('prefix',prefix_rows,'prefix')]:
        index={(r['formula'],r['seed'],r['configuration'],r[value]):r for r in rows}
        for r in rows:
            if r['configuration']!='choice_compiled':continue
            for baseline in ['plain_clean','HighDiv_release']:
                b=index[(r['formula'],r['seed'],baseline,r[value])];complete=family=='time' or r['complete'] and b['complete']
                delta=r['covered']-b['covered'] if complete else None
                pairs.append(dict(formula=r['formula'],seed=r['seed'],family=family,point=r[value],baseline=baseline,complete=complete,coverage_bits_delta=delta,win_tie_loss=('win' if delta>0 else 'loss' if delta<0 else 'tie') if complete else 'incomplete',arrival_seconds_delta=r['arrival_seconds']-b['arrival_seconds'] if family=='prefix' and complete else None))
    pair_stats=[]
    keys=sorted({(r['formula'],r['family'],r['point'],r['baseline']) for r in pairs})
    for formula,family,point,baseline in keys:
        selected=[r for r in pairs if (r['formula'],r['family'],r['point'],r['baseline'])==(formula,family,point,baseline)]
        pair_stats.append(dict(formula=formula,family=family,point=point,baseline=baseline,**stats([r['coverage_bits_delta'] for r in selected if r['complete']]),wins=sum(r['win_tie_loss']=='win' for r in selected),ties=sum(r['win_tie_loss']=='tie' for r in selected),losses=sum(r['win_tie_loss']=='loss' for r in selected),incomplete=sum(not r['complete'] for r in selected)))
    common=[];aggregate=[]
    for formula in sorted({r['formula'] for r in runs}):
        selected=[r for r in runs if r['formula']==formula];minimum=min(r['covered'] for r in selected)
        for fraction in [.25,.5,.75,1.]:
            target=math.ceil(minimum*fraction)
            for r in selected:
                hit=next((x for x in timelines[r['run']] if target>0 and x['covered']>=target),None)
                common.append(dict(formula=formula,seed=r['seed'],configuration=r['configuration'],fraction_of_minimum_final=fraction,target_covered_bits=target,arrival_seconds=hit['completion'] if hit else None,completed=hit is not None))
        for config in CONFIGS:
            ss=[r for r in selected if r['configuration']==config]
            for metric in ['coverage','covered','distinct','positive','longest_no_growth_seconds','first_model_seconds']:
                aggregate.append(dict(formula=formula,configuration=config,metric=metric,**stats([r[metric] for r in ss if r[metric] is not None])))
    tables={'session_summary.csv':summaries,'time_coverage.csv':fixed_time,'sample_prefix.csv':prefix_rows,'paired_differences.csv':pairs,'paired_statistics.csv':pair_stats,'common_coverage_time.csv':common,'aggregate.csv':aggregate,'costs.csv':costs,'query_costs.csv':query_costs,'structure.csv':structures}
    dest=out/'analysis';dest.mkdir(exist_ok=True)
    for name,rows in tables.items():write_csv(dest/name,rows)
    report=['# bofill 双实例 900 秒实验报告' if plan['seconds']==900 else '# bofill 短程正确性检查（不属于正式性能结论）','',f'归档：`{out.relative_to(Path(__file__).resolve().parents[1])}`。共同预算 {plan["seconds"]:g} 秒，{len(runs)} 次运行。全部原始输出已由冻结 ManualSatisfiesMetric 独立核验，审计没有调用求解器搜索。','', '本轮移除当前投影生成入口及构建依赖，保留历史审计。新增必要二选一常量组的整数选择编码、单位系数等式精确消元及完整原赋值恢复。plain_clean 与 choice_compiled 共用首次 SAT 到枚举结束的一个持久求解器；二者差异才对应编译表示，不能用历史入口数字归因。HighDiv 使用冻结源码、同一 Z3 与明确 -O3 Release，仅增加写出时间戳。','', 'SMT_real 原为 evaluation，现已用于本轮定向开发；结论不代表未接触评测或一般 bofill/LIA。两个实例、三个种子不是普遍性能证明。','']
    for formula in sorted({r['formula'] for r in runs}):
        selected=[r for r in runs if r['formula']==formula];report+=['## '+formula,'',table(['seed','配置','进程秒','不同合法模型','正增益模型','最终 covered/分母','覆盖率','首模型秒','最长无增长秒'],[[r['seed'],r['configuration'],number(r['process_wall_seconds'],3),r['distinct'],r['positive'],f'{r["covered"]}/{r["total"]}',number(r['coverage']*100,4)+'%',number(r['first_model_seconds'],3),number(r['longest_no_growth_seconds'],3)] for r in selected]),'']
        final=[r for r in pair_stats if r['formula']==formula and r['family']=='time' and r['point']==plan['seconds']]
        for p in final:report.append(f'choice_compiled 相对 {p["baseline"]}：最终新增 covered bit 差均值 {number(p["mean"],2)}，中位数 {number(p["median"],2)}；胜/平/负 {p["wins"]}/{p["ties"]}/{p["losses"]}（配对 {p["n"]}）。')
        report+=['',table(['样本前缀','对照','完整配对','未完成','covered差中位数','胜/平/负'],[[p['point'],p['baseline'],p['n'],p['incomplete'],number(p['median'],2),f'{p["wins"]}/{p["ties"]}/{p["losses"]}'] for p in pair_stats if p['formula']==formula and p['family']=='prefix']),'']
        for baseline in ['plain_clean','HighDiv_release']:
            f=next((p for p in final if p['baseline']==baseline),None)
            pp=[p for p in pair_stats if p['formula']==formula and p['family']=='prefix' and p['baseline']==baseline and p['n']]
            if not f:continue
            if f['median']>0 and any(p['median']>0 for p in pp):interpret='同时间覆盖中位数更高，部分共同样本前缀也提高；是否跨种子一致及是否存在早期退步必须以表内各前缀胜负为准，不能概括为全部更快更好。'
            elif f['median']>0:interpret='同时间覆盖中位数提高，同数量前缀未显示正中位数收益；支持吞吐或模型顺序相关收益，不能宣称样本质量同步提升。'
            else:interpret='固定时间覆盖中位数没有严格改善；不能以编译完成、模型数量或个别种子优势替代覆盖目标。'
            report.append(f'相对 {baseline} 的有限判断：{interpret}')
        report.append('')
    report+=['## 成本、结构与证据边界','', '结构与每种子编译开销见 `analysis/structure.csv`；逐查询请求/授予时限、纯 check、Unknown reason、恢复、验证及阻塞成本见 `analysis/query_costs.csv`。所有在线成本均已包含在共同墙钟中，结构导出也不免费；未细分的剩余进程成本单列。HighDiv 冻结程序没有提供的内部重复数、纯 check 与恢复成本为未知，不从写出数反推。','', '原文件、完整原赋值、样本写完时间、查询和结构日志均保留；每个运行的 `audit/model_audit.csv`（位于对应运行子目录）逐条列出身份、接受/排除理由。所有完整保存模型均验证原 F，只有及时、不同模型进入覆盖。C++ 每次 seen0/seen1 指纹和最终完整状态与冻结评测器一致；辅助变量不进入覆盖。','', f'晚到模型 {sum(r["excluded"]["late"] for r in runs)}；缺失时间戳 {sum(r["excluded"]["missing_timestamp"] for r in runs)}；正式重复 {sum(r["excluded"]["duplicate"] for r in runs)}。尾行及其他缺损见各 audit summary。保留失败运行和未完成前缀，不补零、不追加获胜参数。','', '共同覆盖到达时间以每个公式全部运行最终覆盖最小值的 25%/50%/75%/100% 为描述性阈值，原阈值和完整配对记录于 `analysis/common_coverage_time.csv`。最长无增长包含首段与结尾到预算截止的空白，提前结束另有 terminal，不把清理时间当作覆盖时间。','', '本轮只支持所列有限对照，不推断采样均匀、分布不变或全类泛化。未自动新增投影、30秒筛选、其他参数或额外900秒任务。','', '## 复现','', '构建与实验冻结协议：`docs/bofill双实例900秒实验协议.md`；输入、源码、二进制及协议哈希：本归档 `plan.json`；原始日志哈希：各 `audit/*/source_hashes.json`。','', '```bash',f'HIGHDIV_ROOT=/tmp/lia-highdiv-frozen /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/audit_bofill_results.py {out.relative_to(Path(__file__).resolve().parents[1])} --jobs 3',f'HIGHDIV_ROOT=/tmp/lia-highdiv-frozen /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/analyze_bofill_results.py {out.relative_to(Path(__file__).resolve().parents[1])}','```','']
    (out/'报告.md').write_text('\n'.join(report))
    if plan['seconds']==900:(Path(__file__).resolve().parents[1]/'docs/bofill双实例900秒实验报告.md').write_text('\n'.join(report))
    dump(out/'analysis_manifest.json',{'tables':{str(p.relative_to(out)):file_hash(p) for p in sorted(dest.iterdir()) if p.is_file()},'input_audit_sha256':file_hash(out/'audit_summary.json'),'script_sha256':file_hash(Path(__file__))})
    print('Analysis and Chinese report complete:',out,flush=True)
if __name__=='__main__':main()
