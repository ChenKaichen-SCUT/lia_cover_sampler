#!/usr/bin/env python3
"""Audit all sample-limit code uses and compare controlled online prefixes; never reorder samples."""
import argparse,json,re,subprocess,time
from pathlib import Path
from run_iteration import ROOT,digest,write

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'results/round3_dev/prefix_audit')
    p.add_argument('--binary',type=Path,default=ROOT/'build/lia_cover_sampler');a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False)
    sources=[ROOT/'src/main.cpp',Path('/tmp/lia-control-diagnose/src/main.cpp')]
    references=[]
    for source in sources:
        for number,line in enumerate(source.read_text().splitlines(),1):
            if not re.search(r'\bopt\.n\b',line):continue
            # Nonzero n only guards output/loop termination; metadata does not feed search.
            recognized=bool(re.search(r'(count\s*[<>]=?\s*opt\.n|if\s*\(opt\.n\)|<<\s*opt\.n)',line))
            if not recognized:raise ValueError(f'unclassified sample-cap use: {source}:{number}: {line}')
            references.append(dict(source=str(source),line=number,text=line.strip(),classification='termination_or_metadata'))
    formula=a.output/'controlled.smt2'
    formula.write_text(''.join(f'(declare-const x{i} Int)(assert (and (<= -1 x{i})(<= x{i} 0)))' for i in range(6)))
    trials=[];started=time.monotonic()
    for seed in (0,1,2):
        sequences=[]
        for cap in (1000,4294967295):
            out=a.output/f'seed-{seed}-cap-{cap}'
            command=[str(a.binary.resolve()),'-i',str(formula),'-o',str(out),'-n',str(cap),'-t','0.4','-s',str(seed)]
            result=subprocess.run(command,capture_output=True,text=True,timeout=10)
            (a.output/f'seed-{seed}-cap-{cap}.log').write_text(result.stdout+'\n'+result.stderr)
            if result.returncode:raise RuntimeError('controlled sample-cap run failed')
            models=[json.loads(s) for s in (out/'models.jsonl').read_text().splitlines()]
            sequences.append([r['assignment'] for r in models])
        common=0
        for x,y in zip(*sequences):
            if x!=y:break
            common+=1
        trials.append(dict(seed=seed,counts=[len(s) for s in sequences],identical_leading_models=common,
            full_common_prefix_equal=common==min(map(len,sequences))))
    report=dict(passed=all(t['identical_leading_models']>=2 for t in trials),code_path_stop_only=True,references=references,
        source_hashes={str(s):digest(s) for s in sources},binary_sha256=digest(a.binary),trials=trials,wall_seconds=time.monotonic()-started,
        conclusion='1000 and UINT_MAX are both nonzero; cap only stops output, never seeds RNG or allocates solver/action budgets. Controlled prefixes support the code-path audit. Real wall-clock solver deadlines can still cause independently rerun trajectories to diverge.',
        allowed_protocol='one timed run plus its actual unreordered first k distinct valid outputs; missing k is incomplete')
    write(a.output/'audit.json',report)
    if not report['passed']:raise SystemExit('inspect prefix behavior before adopting unified trajectories')
    print(json.dumps(report['trials']))
if __name__=='__main__':main()
