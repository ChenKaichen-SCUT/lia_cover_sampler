#!/usr/bin/env python3
"""Pre-frozen R9 groups with barriers. Never selects targets from new outcomes."""
import argparse,json,hashlib,os,subprocess,time,signal,threading,tarfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
ENV={k:'1' for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','VECLIB_MAXIMUM_THREADS','BLIS_NUM_THREADS')}
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,x):Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def run(job,binary,out,barrier):
 dest=out/'raw'/job['id'];dest.parent.mkdir(parents=True,exist_ok=True)
 if job['stage']=='A':cmd=[str(binary),job['formula'],str(job['group']),str(job['alternative']),str(job['timeout_ms']),str(job['seed']),str(dest)]
 else:
  cmd=[str(binary),job['formula'],job['mode'],str(job['seconds']),str(job['seed']),str(dest)]
  if job['kind']=='mechanism':cmd += [job['prefix_values'],job['seed_values']]
 record=dict(job,command=cmd,output=str(dest.relative_to(out)));expired=threading.Event()
 with dest.with_suffix('.stdout').open('wb') as stdout,dest.with_suffix('.stderr').open('wb') as stderr:
  barrier.wait();record['start_utc']=datetime.now(timezone.utc).isoformat();start=time.monotonic()
  child=subprocess.Popen(cmd,stdout=stdout,stderr=stderr,stdin=subprocess.DEVNULL,env={**os.environ,**ENV},start_new_session=True)
  def kill():
   try:os.killpg(child.pid,signal.SIGKILL);expired.set()
   except ProcessLookupError:pass
  timer=threading.Timer(job['seconds']+30,kill);timer.start()
  try:_,status,usage=os.wait4(child.pid,0);child.returncode=os.waitstatus_to_exitcode(status)
  finally:timer.cancel()
 record.update(end_utc=datetime.now(timezone.utc).isoformat(),exitcode=child.returncode,watchdog=expired.is_set(),process_seconds=time.monotonic()-start,cpu_seconds=usage.ru_utime+usage.ru_stime,hashes={str(p.relative_to(dest)):digest(p) for p in dest.glob('*') if p.is_file()})
 if (dest/'summary.json').exists():record['summary']={k:v for k,v in json.loads((dest/'summary.json').read_text()).items() if k!='coverage'}
 write(dest.with_suffix('.run.json'),record);return record

def main():
 p=argparse.ArgumentParser();p.add_argument('--stage',choices=['A','B','C'],required=True);p.add_argument('--binary',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 subprocess.run(['git','diff','--exit-code','HEAD','--'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
 if a.stage=='C':assert json.loads((ROOT/'results/round9_mechanism/main_10s/decision.json').read_text())['enter_C'],'mechanism gate not met'
 out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);(out/'raw').mkdir();groups=[];inputs=[]
 if a.stage=='A':
  selection=ROOT/'results/round9_diagnostic/diagnostic_set.json';s=json.loads(selection.read_text());inputs+=[selection];formula=s['formula'];inputs.append(Path(formula))
  for alt in s['alternatives']:
   for seed in (0,1):
    groups.append([dict(alt,formula=formula,instance=Path(formula).name,state='single_alternative',seed=seed,timeout_ms=ms,seconds=ms/1000.+1,kind='diagnostic',split=alt['kind'],id=f"g-{alt['group']}-a-{alt['alternative']}/seed-{seed}/ms-{ms}") for ms in (50,200,1000,2000)])
 elif a.stage=='B':
  manifest=ROOT/'results/round3_snapshots/states/manifest.json';inputs.append(manifest)
  states=[s for s in json.loads(manifest.read_text())['snapshots'] if s['prefix'] in (1,10) and s['instance']=='SMT_random_LIA_ex14600_2600_100.smt2'];assert len(states)==2
  for s in states:
   assert s['split']=='development' and digest(s['formula'])==s['input_sha256'];d=Path(s['folder']);inputs.append(Path(s['formula']))
   for name,key in [('prefix.values','prefix_sha256'),('seed.values','seed_sha256'),('coverage.json','coverage_sha256'),('assignments.json','assignments_sha256')]:assert digest(d/name)==s[key];inputs.append(d/name)
   for seed in (0,1,2):
    modes=['plain_fixed','macro_budget_fixed','macro_fair_retry'];modes=modes[seed:]+modes[:seed]
    groups.append([dict(instance=s['instance'],state=s['id'],formula=s['formula'],prefix_values=str(d/'prefix.values'),seed_values=str(d/'seed.values'),seed=seed,mode=m,kind='mechanism',seconds=10,split='development',id=f"{s['id']}/seed-{seed}/{m}") for m in modes])
 else:
  for i,c in enumerate(json.loads((ROOT/'benchmarks/manifest.json').read_text())):
   if c['split']!='development':continue
   formula=ROOT/'benchmarks'/c['path'];inputs.append(formula)
   for seed in (0,1,2):
    modes=['micro_plain','micro_macro'] if (i+seed)%2==0 else ['micro_macro','micro_plain']
    groups.append([dict(instance=c['path'],state=c['path'],formula=str(formula),seed=seed,mode=m,kind='online',seconds=30,split='development',id=f"{c['path']}/seed-{seed}/{m}") for m in modes])
 waves=[];per_wave=1 if a.stage=='A' else 2 if a.stage=='B' else 3
 for i in range(0,len(groups),per_wave):
  wave=[]
  for gi,g in enumerate(groups[i:i+per_wave],i):
   for job in g:job.update(stage=a.stage,wave=len(waves),group_id=gi);wave.append(job)
  waves.append(wave)
 cpp=sorted(list((ROOT/'src').glob('*.cpp'))+list((ROOT/'include').glob('*.hpp')));source=hashlib.sha256(''.join(digest(p) for p in cpp).encode()).hexdigest()
 paths=cpp+inputs+[ROOT/'CMakeLists.txt',ROOT/'docs/第九轮实验协议.md',Path(__file__),a.binary.resolve(),ROOT/'scripts/audit_round9.py',ROOT/'scripts/analyze_round9.py',ROOT/'scripts/analyze_early_coverage.py',ROOT/'python/coverage_differential_test.py',ROOT/'scripts/audit_macro_pairs.py',ROOT/'scripts/reevaluate_round6_saved_candidates.py',ROOT/'python/validation.py']
 frozen=Path(os.environ['HIGHDIV_ROOT']);metric=frozen/'HighDiv/my_scripts/calc_metric.py';lib=frozen/'venv/lib/libz3.so';paths += [metric,lib]
 assert digest(metric)=='a51cdd069321cd32becf8eeb5e606cb71fbab16ae3e0231f68963eaadf493f04';assert digest(lib)=='c66acd3c4743444ce90b255820a88a65cbeaa4bd13808ed59ac7be91721967ca'
 plan=dict(base='af9b2c6',stage=a.stage,commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),created_utc=datetime.now(timezone.utc).isoformat(),source_sha256=source,tasks=[j for w in waves for j in w],waves=waves,max_concurrency=max(map(len,waves)),environment=ENV,host=os.uname().nodename,hashes={str(p):digest(p) for p in paths},protocol='complete group barriers; no overlapping audits or builds')
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
