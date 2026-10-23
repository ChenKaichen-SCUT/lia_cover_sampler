#!/usr/bin/env python3
"""Audit the AB adapter's formal sample cap and compare controlled nonbinding limits."""
import argparse,json,re,subprocess,time
from pathlib import Path
from run_iteration import ROOT,digest,write
def main():
    p=argparse.ArgumentParser();p.add_argument('--binary',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);source=ROOT/'src/ab_adapter.cpp';uses=[]
    for number,line in enumerate(source.read_text().splitlines(),1):
        if re.search(r'\bopt\.n\b',line):
            if not re.search(r'(formal.count\s*[<>]=?\s*opt\.n|if\s*\(opt\.n\)|<<\s*opt\.n)',line):raise ValueError('unclassified cap use: '+line)
            uses.append(dict(line=number,text=line.strip(),classification='termination_or_metadata'))
    formula=out/'controlled.smt2';formula.write_text(''.join(f'(declare-const x{i} Int)(assert (and (<= -1 x{i})(<= x{i} 0)))' for i in range(3)))
    trials=[];start=time.monotonic()
    for polish in (False,True):
        for seed in (0,1,2):
            sequences=[]
            for cap in (1000,4294967295):
                folder=out/f'polish-{polish}-seed-{seed}-cap-{cap}'
                cmd=[str(a.binary.resolve()),'-i',str(formula),'-o',str(folder),'-n',str(cap),'-t','3','-s',str(seed),'--output-polish',str(polish).lower()]
                r=subprocess.run(cmd,capture_output=True,text=True,timeout=10);(out/(folder.name+'.log')).write_text(r.stdout+r.stderr)
                if r.returncode:raise RuntimeError('controlled cap run failed')
                sequences.append([json.loads(s)['assignment'] for s in (folder/'models.jsonl').read_text().splitlines()])
            common=0
            for x,y in zip(*sequences):
                if x!=y:break
                common+=1
            trials.append(dict(polish=polish,seed=seed,counts=list(map(len,sequences)),leading_identical=common,full_common_prefix_equal=common==min(map(len,sequences))))
    result=dict(passed=all(r['leading_identical']>=2 for r in trials),code_path_stop_only=True,references=uses,
        source_hashes={str(source):digest(source),str(ROOT/'include/ab_output.hpp'):digest(ROOT/'include/ab_output.hpp')},
        binary_sha256=digest(a.binary),trials=trials,wall_seconds=time.monotonic()-start,
        protocol='actual online time trajectory and unreordered formal unique prefixes; raw generation count is separate; real wall budgets may cause independent repetitions to diverge')
    write(out/'audit.json',result)
    if not result['passed']:raise RuntimeError('cap audit requires investigation')
    print(json.dumps(trials))
if __name__=='__main__':main()
