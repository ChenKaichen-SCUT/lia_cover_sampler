#!/usr/bin/env python3
"""Chinese report from immutable audited stage tables; never launches work."""
import argparse,json,csv,statistics
from pathlib import Path
from collections import Counter,defaultdict
from reevaluate_round6_saved_candidates import dump
ROOT=Path(__file__).resolve().parents[1]
def read(p):
    with p.open() as f:return list(csv.DictReader(f))
def short(f):return 'SMT_random' if 'SMT_random' in f else 'SMT_real' if 'SMT_real' in f else 'Bromberger' if 'Bromberger' in f else 'convert'
def fmt(v):return 'NA' if v is None or v=='' else f'{v:.6f}' if isinstance(v,float) else str(v)
def table(rs,cols):return '\n'.join(['| '+' | '.join(label for key,label in cols)+' |','| '+' | '.join('---' for _ in cols)+' |']+['| '+' | '.join(fmt(r.get(key)) for key,label in cols)+' |' for r in rs])
def main():
    p=argparse.ArgumentParser();p.add_argument('out',type=Path);a=p.parse_args();out=a.out;correct=json.loads((out/'correctness/summary.json').read_text());assert correct['passed']
    stages=[s for s in ['smoke','probe','screen'] if (out/s/'audit_summary.json').exists()];audits={s:json.loads((out/s/'audit_summary.json').read_text()) for s in stages};assert all(v['passed'] for v in audits.values());gate=json.loads((out/'probe/gate.json').read_text()) if 'probe' in stages else None;screen=json.loads((out/'screen/screen_gate.json').read_text()) if 'screen' in stages else None
    lines=['# 算术覆盖构造实验报告','',f'基于冻结提交 `0e656558bb0f27388cf4ea2ef7f8d90975392079`，本轮已完成阶段：{", ".join(stages)}。Release/ASan/UBSan 与规定回归的通过凭据保存在 correctness/summary.json。未运行 HighDiv，未启动300/900秒或调参实验。','']
    if gate and not gate['passed']:lines+=['**S2 未通过预先冻结的晋级门槛，停止本 MVP 的扩大实验；84次120秒筛选没有启动。** 这不证明所有算术移动均无效，也不证明未尝试的多列区域没有空间。','']
    elif screen:lines+=['**'+('S3 双目标初步门槛通过，仅建议后续新实例确认；本轮不自动启动下一实验。' if screen['passed'] else 'S3 双目标门槛未通过，保存负结果并停止扩大本 MVP。')+'**','']
    lines+=['## 实现与可比性','', '三配置使用同一新 Release 可执行文件与同一独立 Z3 库。P关闭新算术机制；R均匀选覆盖盲完整候选池（含no-op）；C按新增covered、其次新增观察选择，同分优先no-op。外层为冻结随机1–4位置与Keep/Flip各半，没有改变动作消费时机或查询授时。原生整数移动设施和既有模命中被复用；它们本身不作为新算法贡献。','', 'hook位于原理论最终检查全部通过、即将FC_DONE的位置，非零提交后FC_CONTINUE并正常复检。全部F、当前相关atom真值与完整模型blockers均保留；映射不全、epsilon或不支持运算回退。partial pool / score incomplete不提交移动。正式状态只在及时交付后更新。预测推荐、实际提交与最终赋值分开记录；正常random_update未禁用。','', '新增模块单次2ms软限额及独立会话5%额度，旧相位5%额度原样保留。初始化、映射、失败、评分与诊断均收费。软期限的不可中断操作超支真实记录。分项是包含关系，不能把callback/scoring再与完整hook相加。','', '六个不同物理核、同组同步、CPU/启动次序固定轮换，采样与编译/审计/压缩分开。监督器及超时线程亲和性、逐秒资源记录均保存；一次运行，不替换seed、不追加有利重复。','']
    for stage in stages:
        sd=out/stage;plan=json.loads((sd/'plan.json').read_text());audit=audits[stage];models=sum(r['delivered'] for r in audit['runs']);lines += [f'## {stage}：{len(audit["runs"])} 个 {plan["seconds"]} 秒会话','',f'所有 {models} 个及时正式模型通过冻结 ManualSatisfiesMetric 独立重放：完整赋值合法性、完整seen0/seen1、短路访问、低64位、原分母、去重、blocker、边际累计及截止一致。离线审计搜索调用为0。','']
        init=read(sd/'initialization.csv');groups=defaultdict(list)
        for r in init:groups[r['group']].append(r)
        triples=[rs for rs in groups.values() if len(rs)==3]
        if triples:
            equal=sum(len({r['first_model_id'] for r in rs})==1 for rs in triples);pre=sum(len({r['prefirst_status_grants'] for r in rs})==1 for rs in triples)
            lines += [f'初始化三方首模型身份相同 {equal}/{len(triples)} 组，首模型前状态/授时序列相同 {pre}/{len(triples)} 组。所有差异保留，不能归因于第一正式模型后才启动的算术机制。','']
        rows=[]
        costs={r['run']:r for r in read(sd/'mechanism_costs.csv')}
        for r in audit['runs']:
            c=costs[r['run']];rows.append(dict(formula=short(r['formula']),seed=r['seed'],mode=r['configuration'],models=r['delivered'],covered=r['covered'],total=r['total'],arith=float(c['arith_seconds']),phase=float(c['phase_seconds']),wall=float(c['process_wall_seconds']),hooks=c.get('hook_queries',0),pools=c.get('scored_pools',0),moves=c.get('committed',0),stop=c['arith_stop'] or '无'))
        lines += [table(rows,[('formula','公式'),('seed','seed'),('mode','配置'),('models','正式模型'),('covered','covered bit'),('total','分母'),('wall','进程秒'),('arith','算术总秒'),('phase','相位总秒'),('hooks','hook查询'),('pools','完整评分池'),('moves','提交'),('stop','算术停用')]),'']
        if stage=='smoke':lines+=['S1仅为日志、交付和接入冒烟，每公式一个seed，不以这六次短跑宣布性能优势。','']
        if stage=='probe':
            rows=[r|dict(name=short(r['formula'])) for r in gate['formulas']];lines += [table(rows,[('name','公式'),('hook_queries','hook状态'),('verified_complete_pools','完整独立核对池'),('nontrivial_pools','非单例池'),('strictly_better_noop','优于no-op状态'),('covered_above_random','covered超随机期望状态'),('arith_seconds','算术累计秒')]),'']
            qs=read(sd/'mechanism_queries.csv');by=defaultdict(Counter)
            for r in qs:by[short(r['formula'])][r['reason']]+=1
            lines+=['逐公式退出原因（查询计数是描述性诊断，不是独立实验重复）：','',table([dict(formula=f,reason=k,count=v) for f,c in by.items() for k,v in c.most_common()],[('formula','公式'),('reason','原因'),('count','次数')]),'', '以上最高分只描述已完成的有界池，不是整个区域或公式的覆盖上界。probe总是正式交付原生no-op，影子候选未输出、未阻塞、未更新历史；其成本包含在30秒内，曲线不能冒充P的正式性能。','']
        if stage=='screen':
            ps=read(sd/'paired_summary.csv');lines += [table([r|dict(name=short(r['formula'])) for r in ps if r['metric'] in ['C_120s','C_n10','C_n100','C_n1000']],[('name','公式'),('comparison','比较'),('metric','指标'),('n','完整配对'),('incomplete','未达标'),('mean','均值bit差'),('median','中位差'),('wins','胜'),('ties','平'),('losses','负'),('worst','最差差值')]),'', '完整逐seed差值见 paired_differences.csv；未达前缀为NA，不能用最后模型代替。common_prefix100.csv 单列三配置共同达成的seed集合；全部达成率、达标时间与未完成行在prefix_completion.csv。公式分别解释，跨公式只等权覆盖百分点。','',table(screen['comparisons'],[('comparison','比较'),('passed','门槛'),('equal_formula_endpoint_mean_pp','等权终点百分点'),('positive_endpoint_formulas','终点正中位公式'),('common100_formulas','共同前100合格公式'),('common100_mean_pp','共同前100等权百分点'),('no_prefix_completion_loss','无达成率损失'),('no_severe_or_unknown_regression','早期/终点严重退步检查')]),'']
    lines+=['## 三个问题与决定','']
    if gate:
        for r in gate['formulas']:
            desc='获得非零合法完整候选池' if r['nontrivial_pools'] else '未得到可独立核验的非零完整候选池'
            lines += [f'**{short(r["formula"])} 的自由度证据：** {r["hook_queries"]} 次实际hook，{r["verified_complete_pools"]} 个完成核对的池，{r["nontrivial_pools"]} 个非单例池，{desc}。缺失池需要结合上表退出原因判断；单列无空间与映射/预算失败分别记录。','']
        local=sum(r['covered_above_random'] for r in gate['formulas']);lines += [f'**覆盖选择相对随机的局部价值：** 同状态完整池中共 {local} 个状态的最高covered收益严格超过均匀随机期望。该计数只说明这些固定池上的选择机会；完整策略仍需支付评分成本，不能凭此宣称C在线胜R。','']
    if 'screen' not in stages:lines+=['**固定时间与严格样本数双目标：尚未验证。** S2门槛未满足时不开展S3，不能以冒烟终点或池内最优代替120秒、严格前100的完整配对证据。前1000同样没有得到本轮正式筛选结论。停止扩大当前MVP，保留完整失败/不完整池和成本证据；不通过加时间、换seed、增加候选或多列移动绕过本轮失败。','']
    else:lines+=['**双目标决定：** '+('通过本轮有限开发筛选门槛，可单独提出新实例确认计划。' if screen['passed'] else '未通过全部预定门槛，当前双目标未满足；不启动后续长跑。')+' 四个都是旧开发公式；查询/候选/seed不是新公式，不能包装为泛化显著性证明。','']
    lines+=['## 复现','', '代码、依赖、编译器、实际链接、输入与冻结协议哈希见 correctness/summary.json、各阶段plan.json及依赖manifest。完整原始输出和失败日志通过各阶段archive_manifest.json与分片归档保存，恢复后可离线复算。以下审计不生成模型：','', '```bash']
    for stage in stages:lines += [f'/tmp/lia-highdiv-frozen/venv/bin/python3 scripts/archive_bofill_results.py {out/stage} --restore',f'HIGHDIV_ROOT=/tmp/lia-highdiv-frozen PYTHONPATH=scripts:python /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/audit_arith_coverage.py {out/stage} --jobs 6',f'HIGHDIV_ROOT=/tmp/lia-highdiv-frozen PYTHONPATH=scripts:python /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/analyze_arith_coverage.py {out/stage}']
    lines+=['```','', '重新采样的入口为 run_arith_coverage.py --prepare/--run --stage smoke|probe|screen；需要新目录，screen受S2 gate强制保护，现有目录禁止自动重跑。',''];report='\n'.join(lines);(out/'报告.md').write_text(report);(ROOT/'docs/算术覆盖构造实验报告.md').write_text(report)
    dump(out/'analysis_summary.json',dict(stages=stages,sessions=sum(len(a['runs']) for a in audits.values()),probe_gate=gate,screen_gate=screen,highdiv_rerun=False,long_runs=False,decision='stop' if not screen or not screen['passed'] else 'propose_separate_confirmation_only'))
if __name__=='__main__':main()
