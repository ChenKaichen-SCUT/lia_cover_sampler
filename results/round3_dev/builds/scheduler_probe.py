from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import subprocess,json,hashlib,time,os,signal
root=Path('/mnt/Space0/SMT_Sampling/lia_cover_sampler');out=root/'results/round3_dev/scheduler_check';out.mkdir()
rows=[r for r in json.loads((root/'benchmarks/manifest.json').read_text()) if r['split']=='development']
configs={'control_ab':(root/'build/round2/control_ab_fixed/lia_cover_sampler',[]),'scheduler_old':(root/'build/round3/scheduler/lia_cover_sampler',['--profile','schedule','--disable','work_conserving']),'scheduler_fixed':(root/'build/round3/scheduler/lia_cover_sampler',['--profile','schedule'])}
jobs=[]
for name,(binary,extra) in configs.items():
 folder=out/name;folder.mkdir();batch=[]
 for row in rows:
  run=Path('raw')/row['path']/'seed-0';cmd=[str(binary),'-i',str(root/'benchmarks'/row['path']),'-o',str(folder/run),'-n','4294967295','-t','30','-s','0',*extra]
  job=dict(instance=row['path'],input_sha256=row['sha256'],run=str(run),seed=0,command=cmd,batch=name);batch.append(job);jobs.append(job)
 (folder/'manifest.json').write_text(json.dumps(dict(phase='scheduler_only_development_check',seconds=30,samples=4294967295,variant='full',binary=str(binary),binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),jobs=9,instances=batch),indent=2))
def run(j):
 folder=out/j['batch']/j['run'];folder.mkdir(parents=True);start=time.monotonic();utc=datetime.now(timezone.utc).isoformat();status='ok'
 with (folder/'process.log').open('wb') as f:
  p=subprocess.Popen(j['command'],stdout=f,stderr=subprocess.STDOUT,start_new_session=True,env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1'))
  try:code=p.wait(timeout=max(.001,30-(time.monotonic()-start)))
  except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);code=p.wait();status='timeout_partial'
  if code and status=='ok':status='error'
 r=dict(**j,status=status,returncode=code,elapsed=time.monotonic()-start,started_utc=utc,finished_utc=datetime.now(timezone.utc).isoformat());(folder/'process.json').write_text(json.dumps(r,indent=2));return r
with ThreadPoolExecutor(max_workers=9) as pool:
 for future in as_completed([pool.submit(run,j) for j in jobs]):
  r=future.result()
  with (out/r['batch']/'runs.jsonl').open('a') as f:f.write(json.dumps(r)+'\n')
  print(r['batch'],r['instance'],r['status'],flush=True)
for name in configs:(out/name/'COMPLETE.json').write_text('{}')
subprocess.run(['/tmp/lia-highdiv-frozen/venv/bin/python3',str(root/'scripts/audit_round2.py'),str(out),'--jobs','9'],cwd=root,env=dict(os.environ,HIGHDIV_ROOT='/tmp/lia-highdiv-frozen'),check=True)
