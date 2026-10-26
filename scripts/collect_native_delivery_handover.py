#!/usr/bin/env python3
"""Recover completed process receipts after a user-requested scheduler handover.

Never starts or kills a sampler. The old coordinators were paused, while their
sampler children ran to completion. GNU time and /proc supply exit evidence.
"""
import json,os,signal,time
from pathlib import Path
from run_native_feedback_pairs import ROOT,dump,sha

def elapsed(text):
    line=next(x for x in text.splitlines() if 'Elapsed (wall clock)' in x)
    parts=line.rsplit(': ',1)[1].split(':');value=0.
    for part in parts:value=60*value+float(part)
    return value

def main():
    root=ROOT/'results/native_delivery';out=root/'screen_120s';plan=json.loads((out/'plan.json').read_text());handover=json.loads((root/'scheduler_handover.json').read_text())
    # This check executes before any update to the controller source. Algorithms,
    # original plan, and all original completed process receipts stay untouched.
    for p,wanted in plan['files'].items():assert sha(ROOT/p)==wanted,'changed during sampling: '+p
    results=[];recovered=[]
    for run in sorted((out/'runs').iterdir()):
        if (run/'process_result.json').exists():results.append(json.loads((run/'process_result.json').read_text()));continue
        spec=json.loads((run/'spec.json').read_text());gate=json.loads((run/'gate.json').read_text());pid=json.loads((run/'ready.json').read_text())['pid'];proc=Path(f'/proc/{pid}/stat')
        if proc.exists():assert proc.read_text().split(') ',1)[1].split()[0]=='Z','sampler still active; do not collect yet'
        text=(run/'resource.txt').read_text();code=int(next(x for x in text.splitlines() if 'Exit status:' in x).rsplit(':',1)[1]);assert code==0,'nonzero historical process exit'
        duration=elapsed(text)+(gate['exec_monotonic_ns']-gate['start_monotonic_ns'])/1e9
        r=spec|dict(start_monotonic_ns=gate['start_monotonic_ns'],process_wall_seconds=duration,returncode=code,termination_sent_seconds=None,hard_kill=False,cleanup_after_deadline_seconds=max(0,duration-spec['seconds']),time_source='GNU time elapsed (0.01s resolution) plus recorded gate delay',completion_observed_seconds=(time.monotonic_ns()-gate['start_monotonic_ns'])/1e9,scheduler_handover=True)
        dump(run/'process_result.json',r);results.append(r);recovered.append(run.name)
    assert len(results)==24 and len({(r['formula'],r['seed'],r['configuration']) for r in results})==24
    # Kill only the two paused controller PIDs after every sampler has exited.
    for pid in handover['paused_controllers']:
        p=Path(f'/proc/{pid}/cmdline')
        if p.exists():
            assert b'run_native_delivery_' in p.read_bytes(),'controller PID reused'
            os.kill(pid,signal.SIGKILL)
    dump(out/'process_results.json',results)
    events=[]
    for r in results:
        start=r['start_monotonic_ns']/1e9;events.extend([(start,1),(start+r['process_wall_seconds'],-1)])
    active=peak=0
    for _,delta in sorted(events):active+=delta;peak=max(peak,active)
    amendment=dict(authority='User request: 使用15线程并行完成',thread_budget=15,full_quadruplet_slots=3,effective_max_sampling=12,observed_peak_sessions=peak,hardware='16 logical CPUs / 8 physical cores; three quadruplets may share SMT siblings',preserved_original_plan_sha256=sha(out/'plan.json'),protected_frozen_files_verified=True,recovered_processes=recovered,no_sampler_restarted=True,no_sampler_interrupted=True,existing_results_unchanged=True,notes='Groups 0-3 completed under the initial schedule. Group 5 was launched by the replacement coordinator; actual overlap is determined from gate and GNU time evidence, not from requested concurrency. Handover took long enough that this screen retained an observed peak of four samplers. It is not a uniform 15-thread rerun; all subsequent offline audit uses up to 15 workers.')
    dump(out/'concurrency_amendment.json',amendment);dump(out/'progress.json',dict(status='sampling_complete',sessions=24,completed_groups=6,queued_groups=0,concurrency_amendment='concurrency_amendment.json'))
    dump(root/'workflow_progress.json',dict(stage='scheduler_handover_complete',sampling_complete=24,thread_budget=15,observed_peak_sessions=peak,remaining='independent audit, analysis, archive and conditional frozen validation'))
    print(json.dumps(amendment,ensure_ascii=False))

if __name__=='__main__':main()
