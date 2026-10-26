#!/usr/bin/env python3
"""Sequential frozen stages; never overlap sampling, audits or builds.

This is an experiment controller, not a sampler strategy. A failure stops the
workflow with evidence intact. Publication is explicit via --push-results.
"""
import argparse,json,os,subprocess,sys,time,traceback
from datetime import datetime,timezone
from pathlib import Path
from run_native_feedback_pairs import ROOT,dump,sha
from run_native_delivery_pairs import BRANCH

PY='/tmp/lia-highdiv-frozen/venv/bin/python3'

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=ROOT/'results/native_delivery');p.add_argument('--push-results',action='store_true');p.add_argument('--resume',action='store_true');p.add_argument('--threads',type=int,default=15);p.add_argument('--wait-for-audit-pid',type=int);a=p.parse_args()
    os.chdir(ROOT);out=a.out.resolve();out.mkdir(parents=True,exist_ok=a.resume)
    if a.wait_for_audit_pid:
        while (Path('/proc')/str(a.wait_for_audit_pid)).exists():time.sleep(1)
        assert (out/'screen_120s/audit_summary.json').exists(),'audit failed; no automatic retry'
    validation=ROOT/'results/native_delivery_validation/summary_concurrency15.json'
    if not validation.exists():validation=ROOT/'results/native_delivery_validation/summary.json'
    proof=json.loads(validation.read_text());assert proof['passed'],'all regressions must pass first'
    for name,wanted in proof['frozen_files'].items():assert sha(ROOT/name)==wanted,'changed after validation: '+name
    assert subprocess.check_output(['git','branch','--show-current'],text=True).strip()==BRANCH
    assert not subprocess.check_output(['git','status','--short','--untracked-files=no'],text=True).strip(),'freeze implementation before experiments'
    started=time.monotonic();commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip();implementation=json.loads((out/'exposure_30s/plan.json').read_text())['git_commit'] if a.resume else commit
    base=dict(pid=os.getpid(),started_utc=datetime.now(timezone.utc).isoformat(),implementation_commit=implementation,controller_commit=commit,validation_sha256=sha(validation),automatic_parameter_changes=False,highdiv_run=False,requested_threads=a.threads,max_parallel_sampling=(a.threads//4)*4)
    env=dict(os.environ,HIGHDIV_ROOT='/tmp/lia-highdiv-frozen',PYTHONPATH='scripts:python')
    def progress(stage,**extra):dump(out/'workflow_progress.json',base|dict(stage=stage,elapsed_seconds=time.monotonic()-started,updated_utc=datetime.now(timezone.utc).isoformat())|extra)
    def stage(name,kind,decision=None):
        target=out/name;command=[PY,'scripts/run_native_delivery_pairs.py','--out',str(target),'--stage',kind,'--postprocess','--threads',str(a.threads)]
        if decision:command+=['--decision',str(decision)]
        progress(kind,command=command)
        if a.resume and target.exists():
            plan=json.loads((target/'plan.json').read_text());results=json.loads((target/'process_results.json').read_text())
            assert len(results)==plan['total_sessions'] and all(r['returncode']==0 for r in results),'cannot resume an incomplete or failed sampling stage'
            # The handover has already collected every process. Do not generate
            # another model, overwrite its raw logs, or repeat a completed audit.
            for label,marker,cmd in [('audit','audit_summary.json',[PY,'scripts/audit_native_delivery_results.py',str(target),'--jobs','15']),('analysis','analysis/summary.json',[PY,'scripts/analyze_native_delivery_results.py',str(target)]),('archive','archive_manifest.json',[PY,'scripts/archive_bofill_results.py',str(target)])]:
                if not (target/marker).exists():
                    progress(kind+'_'+label)
                    with (target/(label+'.log')).open('w') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,env=env,check=True)
            dump(target/'progress.json',dict(status='audit_analysis_archive_complete',sessions=len(results)))
        else:
            with (out/(kind+'_workflow.log')).open('w') as f:subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,env=env,check=True)
        assert json.loads((target/'progress.json').read_text())['status']=='audit_analysis_archive_complete'
        assert json.loads((target/'audit_summary.json').read_text())['passed']
        return target
    try:
        exposure=stage('exposure_30s','exposure')
        # Exposure is a correctness/exposure gate only, never a coverage filter.
        run_rows=json.loads((exposure/'audit_summary.json').read_text())['runs']
        exposure_check=dict(passed=True,all_sessions_audited=len(run_rows)==8,
            later_positions_reached=sum(sum(r['counts'].get('reached_'+str(k),0) for k in [2,3,4]) for r in run_rows),
            selection_rule='No performance-based cancellation or parameter adjustment; missing later opportunities are an applicability limitation.')
        assert exposure_check['all_sessions_audited'];dump(out/'exposure_check.json',exposure_check)
        screen=stage('screen_120s','screen');decision_path=screen/'analysis/decision.json';decision=json.loads(decision_path.read_text())
        stages=[exposure,screen]
        if decision['enter_900']:
            amended=json.loads(decision_path.read_text())
            for name,wanted in amended['frozen_files'].items():
                current=sha(ROOT/name)
                if current!=wanted:
                    assert name=='scripts/run_native_delivery_pairs.py','algorithm/analysis changed after screening: '+name
                    amended['frozen_files'][name]=current
            amended.update(concurrency_authority='explicit user 15-thread amendment; sampler/coverage/selection unchanged',parent_decision_sha256=sha(decision_path))
            decision_path=screen/'analysis/decision_concurrency15.json';dump(decision_path,amended)
            stages.append(stage('long_900s','long',decision_path))
        progress('report',enter_900=decision['enter_900'],selected=decision['selected'])
        # Complete raw logs are archived without changing or deleting originals.
        # Hash every deliverable except live status and this final self-manifest.
        evidence={str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and 'runs' not in p.relative_to(out).parts and p.name!='workflow_progress.json'}
        dump(out/'evidence_manifest.json',dict(files=evidence,implementation_commit=base['implementation_commit'],validation_sha256=sha(validation)))
        report=['# 有界交付与干预位置实验报告','',
            f"实现与参数冻结提交 `{base['implementation_commit']}`；协议先于性能数据提交。Release、ASan/UBSan、原生钩子与适用回归的结果见 `results/native_delivery_validation/summary.json`。初次旧冻结控制器的身份测试正确拒绝新研究树，测试已改为同时检查拒绝语义与历史库/二进制未改；原控制器未放宽。",'',
            '用户随后明确将资源上限改为15线程。后续运行器允许三个完整四配置组并行（最多12路采样，每组四个不同物理核，组间可能共享SMT），不拆配对组。筛选接管时已接近结束，实际日志重建的采样峰值仍为4，不能把请求上限写成实测15路；随后独立审计实际使用15个工作进程。详见并发修订及原始gate/GNU time收据。没有重跑已完成会话、HighDiv或改算法参数。', '',
            '900秒决定：'+(f"通过预冻结筛选，仅统一配置 `{decision['selected']}` 与基线使用未见seed3/4完成一次验证。" if decision['enter_900'] else '未满足预冻结跨公式条件，停止，不启动900秒验证。'),'',
            '统计单位是公式/seed会话；候选、批次不是独立重复。两个公式均为已接触开发实例，不能宣称独立测试集泛化。历史HighDiv只作描述性辅助，不是本轮同条件对照。','',
            '以下按阶段保留全部结果。完整双轨迹、逐查询/批次成本、NA前缀、观察差与来源哈希分别在各阶段audit、analysis和archive_manifest；归档可用 `scripts/archive_bofill_results.py <阶段目录> --restore` 恢复。各阶段报告中的相对CSV路径指对应阶段目录。','']
        for target in stages:
            stage_text=(target/'报告.md').read_text()
            if target==screen and (target/'concurrency_amendment.json').exists():stage_text=stage_text.replace('四物理核同步配对，组间屏障；','四配置组内同步，实际并发与接管说明见concurrency_amendment.json；')
            if target.name=='long_900s':stage_text=stage_text.replace('四物理核同步配对，组间屏障；','完整配对组并行，线程上限15，组内同步；')
            report += [f'<!-- source: {target.relative_to(ROOT)}/报告.md -->','',stage_text,'']
        report += ['## 可重放命令','', '依赖和构建见 `docs/生成与交付状态分离说明.md`。参数不能按公式修改。已有目录不能作为新实验输出目录。','', '```bash',
                   'HIGHDIV_ROOT=/tmp/lia-highdiv-frozen PYTHONPATH=scripts:python /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/run_native_delivery_workflow.py --out results/native_delivery_replay', '```', '',
                   '以上命令会生成新的实验，只有明确需要重放时运行。本次已完成证据的只读复核：','', '```bash',
                   *[f'{PY} scripts/archive_bofill_results.py {target.relative_to(ROOT)} --restore' for target in stages],
                   *[f'HIGHDIV_ROOT=/tmp/lia-highdiv-frozen {PY} scripts/audit_native_delivery_results.py {target.relative_to(ROOT)} --jobs 15' for target in stages], '```','']
        doc=ROOT/'docs/有界交付与干预位置实验报告.md';doc.write_text('\n'.join(report));(out/'报告.md').write_text(doc.read_text())
        progress('complete',enter_900=decision['enter_900'],selected=decision['selected'],report=str(doc.relative_to(ROOT)),publication='requested' if a.push_results else 'not requested')
        if a.push_results:
            with (out/'publication.log').open('w') as f:
                subprocess.run(['git','add','--',str(out.relative_to(ROOT)),str(doc.relative_to(ROOT))],stdout=f,stderr=subprocess.STDOUT,check=True)
                subprocess.run(['git','commit','-m','Archive frozen native position and bounded delivery experiments'],stdout=f,stderr=subprocess.STDOUT,check=True)
                subprocess.run(['git','push','-u','origin',BRANCH],stdout=f,stderr=subprocess.STDOUT,check=True)
            # This local receipt is intentionally ignored: it records the hash of
            # the result commit without requiring a self-referential extra commit.
            dump(out/'publication_receipt.local.json',dict(pushed=True,commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),branch=BRANCH,completed_utc=datetime.now(timezone.utc).isoformat()))
    except Exception as exc:
        progress('failed',error=str(exc),automatic_retry=False,traceback=traceback.format_exc());raise

if __name__=='__main__':main()
