#!/usr/bin/env python3
"""Frozen R11 three-mode groups; every outcome retained, no automatic retries."""
import argparse,json,hashlib,os,subprocess,threading,tarfile
from pathlib import Path
from datetime import datetime,timezone
from concurrent.futures import ThreadPoolExecutor,as_completed
from run_round9 import run,digest,write,ENV
ROOT=Path(__file__).resolve().parents[1]
MODES=['plain_only','plain_extra','projection_fixed']
def main():
 p=argparse.ArgumentParser();p.add_argument('--stage',choices=['B','C'],required=True);p.add_argument('--binary',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 subprocess.run(['git','diff','--exit-code','HEAD','--'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
 if a.stage=='C':assert json.loads((ROOT/'results/round11_projection/mechanism/AUDIT.json').read_text())['all_passed']
 out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);(out/'raw').mkdir();groups=[];inputs=[]
 manifest=ROOT/'results/round3_snapshots/states/manifest.json';inputs.append(manifest)
 states=[s for s in json.loads(manifest.read_text())['snapshots'] if s['split']=='development' and s['prefix']==1]
 assert len(states)==3
 def add(instance,state,formula,pv,sv,seed,seconds,split):
  modes=MODES[seed%3:]+MODES[:seed%3]
  if a.stage=="B":modes=[m for m in modes if m!="plain_only"]
  groups.append([dict(instance=instance,state=state,formula=str(formula),prefix_values=str(pv),seed_values=str(sv),seed=seed,mode=m,kind='mechanism',seconds=seconds,split=split,id=f'{state}/seed-{seed}/{m}') for m in modes])
 for s in states:
  d=Path(s['folder']);assert digest(s['formula'])==s['input_sha256'];inputs.append(Path(s['formula']))
  for name,key in [('prefix.values','prefix_sha256'),('seed.values','seed_sha256'),('coverage.json','coverage_sha256'),('assignments.json','assignments_sha256')]:assert digest(d/name)==s[key];inputs.append(d/name)
  for seed in ((0,) if a.stage=='B' else (0,1)):add(s['instance'],s['id'],s['formula'],d/'prefix.values',d/'seed.values',seed,{'B':.75,'C':10}[a.stage],'development')
 waves=[]
 for i in range(0,len(groups),3 if a.stage=="B" else 2):
  wave=[]
  for gi,g in enumerate(groups[i:i+(3 if a.stage=="B" else 2)],i):
   for job in g:job.update(stage=a.stage,wave=len(waves),group_id=gi);wave.append(job)
  waves.append(wave)
 cpp=sorted(list((ROOT/'src').glob('*.cpp'))+list((ROOT/'include').glob('*.hpp')));source=hashlib.sha256(''.join(digest(p) for p in cpp).encode()).hexdigest()
 paths=cpp+inputs+[ROOT/'CMakeLists.txt',ROOT/'docs/第十一轮实验协议.md',Path(__file__),a.binary.resolve()]+[ROOT/'scripts'/s for s in ('run_round9.py','audit_projection_fix.py','audit_projection_component.py','audit_projection_fix_inputs.py','analyze_projection_fix.py','reevaluate_round6_saved_candidates.py')]+[ROOT/'python/validation.py']
 frozen=Path(os.environ['HIGHDIV_ROOT']);metric=frozen/'HighDiv/my_scripts/calc_metric.py';lib=frozen/'venv/lib/libz3.so';paths +=[metric,lib]
 assert digest(metric)=='a51cdd069321cd32becf8eeb5e606cb71fbab16ae3e0231f68963eaadf493f04';assert digest(lib)=='c66acd3c4743444ce90b255820a88a65cbeaa4bd13808ed59ac7be91721967ca'
 plan=dict(base='833bca1',stage=a.stage,commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),created_utc=datetime.now(timezone.utc).isoformat(),source_sha256=source,tasks=[j for w in waves for j in w],waves=waves,max_concurrency=max(map(len,waves)),environment=ENV,host=os.uname().nodename,hashes={str(p):digest(p) for p in paths},protocol='three complete two-mode component groups' if a.stage=='B' else 'two complete three-mode groups, barriers between waves; no concurrent audits/builds')
 write(out/'plan.json',plan);records=[]
 with (out/'runs.jsonl').open('w') as journal:
  for wave in waves:
   barrier=threading.Barrier(len(wave))
   with ThreadPoolExecutor(max_workers=len(wave)) as pool:
    for f in as_completed([pool.submit(run,j,a.binary.resolve(),out,barrier) for j in wave]):
     r=f.result();records.append(r);journal.write(json.dumps(r)+'\n');journal.flush();print(r['id'],r['exitcode'],flush=True)
   if any(r['exitcode']!=0 or r['watchdog'] for r in records):break
 with tarfile.open(out/'raw.tar.gz','w:gz') as archive:archive.add(out/'raw',arcname='raw')
 write(out/'archive.json',dict(sha256=digest(out/'raw.tar.gz'),files={str(p.relative_to(out)):digest(p) for p in (out/'raw').rglob('*') if p.is_file()}))
 write(out/'COMPLETE.json',dict(runs=len(records),failed=sum(r['exitcode']!=0 for r in records),plan_sha256=digest(out/'plan.json'),journal_sha256=digest(out/'runs.jsonl')))
if __name__=='__main__':main()
