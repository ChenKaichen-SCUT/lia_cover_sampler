#!/usr/bin/env python3
"""Recover a detached controller exit without repeating completed diagnostic queries."""
import json,os,sys,subprocess,time,signal
from pathlib import Path
from run_bofill_diagnosis import ROOT
from run_bofill_900s import dump,sha,gate

def wall_from_resource(path):
    text=path.read_text();assert 'Exit status: 0' in text
    line=next(l for l in text.splitlines() if 'Elapsed (wall clock)' in l)
    parts=line.rsplit(': ',1)[1].strip().split(':');total=0.
    for part in parts:total=total*60+float(part)
    return total

def resume(out):
    out=Path(out).resolve();meta=json.loads((out/'plan.json').read_text());assert sha(out/'plan.json')==(out/'plan.sha256').read_text().strip()
    for name,digest in meta['files'].items():assert sha(ROOT/name)==digest,name
    os.sched_setaffinity(0,{8});results=[];recovered=[]
    for batch in sorted({r['batch'] for r in meta['runs']}):
        specs=[r for r in meta['runs'] if r['batch']==batch]
        exists=[(out/r['directory']).exists() for r in specs]
        if any(exists):
            assert all(exists),'partial batch needs manual review'
            for spec in specs:
                d=out/spec['directory'];summary=json.loads((d/'raw/summary.json').read_text());assert (d/'resource.txt').exists()
                if (d/'process_result.json').exists():r=json.loads((d/'process_result.json').read_text())
                else:
                    wall=wall_from_resource(d/'resource.txt');g=json.loads((d/'gate.json').read_text())
                    r=dict(**spec,returncode=0,process_wall_seconds=wall,start_monotonic_ns=g['start_monotonic_ns'],wall_source='GNU time elapsed, 0.01s precision; controller terminated before polling',controller_exit_recovered=True,wall_rounding_seconds=.01)
                    dump(d/'process_result.json',r);recovered.append(spec['directory'])
                results.append(r)
            continue
        running=[]
        for spec in specs:
            d=out/spec['directory'];d.mkdir(parents=True);dump(d/'spec.json',spec);log=(d/'process.log').open('wb')
            child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--gate',str(d/'spec.json')],stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            running.append((spec,d,child,log))
        end=time.monotonic()+20
        while not all((d/'ready.json').exists() for _,d,_,_ in running):
            if time.monotonic()>end or any(p.poll() is not None for _,_,p,_ in running):raise RuntimeError('gate failed')
            time.sleep(.02)
        start=time.monotonic_ns()+100_000_000
        for _,_,p,_ in running:p.stdin.write(f'{start}\n'.encode());p.stdin.flush();p.stdin.close()
        dump(out/'progress.json',dict(batch=batch,status='running',completed=len(results),total=len(meta['runs']),resumed=True))
        print(f'batch {batch}: '+', '.join('{mode}/{seed}/{control}'.format(**s[0]) for s in running),flush=True)
        pending=list(running);terminated=set()
        while pending:
            for item in pending[:]:
                spec,d,p,log=item;elapsed=(time.monotonic_ns()-start)/1e9;code=p.poll()
                if code is not None:
                    log.close();r=dict(**spec,returncode=code,process_wall_seconds=elapsed,start_monotonic_ns=start);dump(d/'process_result.json',r);results.append(r);pending.remove(item)
                    if code!=0:raise RuntimeError(f'probe failed {d}: {code}')
                elif elapsed>spec['seconds']+2:os.killpg(p.pid,signal.SIGKILL)
                elif elapsed>spec['seconds']+.25 and p.pid not in terminated:os.killpg(p.pid,signal.SIGTERM);terminated.add(p.pid)
            time.sleep(.02)
        dump(out/'process_results.json',results)
    dump(out/'controller_recovery.json',dict(recovered=recovered,repeated_sessions=0,script_sha256=sha(__file__),reason='original detached controller disappeared; completed probe evidence retained',cost_evidence='first eight original polling; next four GNU time; remaining normal polling'))
    dump(out/'progress.json',dict(status='complete',completed=len(results),total=len(meta['runs']),resumed=True));dump(out/'process_results.json',results)
if __name__=='__main__':
    if sys.argv[1]=='--gate':gate(sys.argv[2])
    else:resume(sys.argv[1])
