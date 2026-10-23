#!/usr/bin/env python3
"""One shared queue for every configuration, case and budget protocol; no baseline reruns."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = {
    "control_ab": [],
    "schedule": ["--profile", "schedule"],
    "directions": ["--profile", "directions"],
    "polish": ["--profile", "polish"],
    "line": ["--profile", "line"],
    "full": ["--profile", "full"],
    "no_line": ["--profile", "full", "--disable", "line"],
    "no_polish": ["--profile", "full", "--disable", "polish"],
}

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False)+"\n")

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--binary",type=Path,default=ROOT/"build/lia_cover_sampler")
    p.add_argument("--control-binary",type=Path,default=ROOT/"build/round2/control_ab_fixed/lia_cover_sampler")
    p.add_argument("--seconds",type=float,default=900)
    p.add_argument("--jobs",type=int,default=12)
    p.add_argument("--split",choices=["development","all"],default="all")
    p.add_argument("--configs",default=",".join(CONFIGS))
    p.add_argument("--extra",nargs=argparse.REMAINDER,default=[])
    a=p.parse_args()
    if not 1<=a.jobs<=12 or a.seconds<=0:p.error("jobs 1..12 and positive deadline required")
    names=a.configs.split(",")
    if not names or set(names)-CONFIGS.keys():p.error("unknown configuration")
    inputs=json.loads((ROOT/"benchmarks/manifest.json").read_text())
    inputs=[row for row in inputs if a.split=="all" or row["split"]==a.split]
    a.output=a.output.resolve()
    a.output.mkdir(parents=True,exist_ok=False)
    binaries={name:(a.control_binary if name=="control_ab" else a.binary).resolve() for name in names}
    for binary in set(binaries.values()):
        if not binary.is_file():raise FileNotFoundError(binary)
    jobs=[]; batches={}; stamp=datetime.now(timezone.utc).isoformat()
    for name in names:
        for protocol,samples in (("time",4294967295),("count",1000)):
            folder=a.output/(name+"_"+protocol);folder.mkdir()
            manifest=dict(created_utc=stamp,platform=platform.platform(),binary=str(binaries[name]),
                binary_sha256=digest(binaries[name]),jobs=a.jobs,seconds=a.seconds,samples=samples,
                variant="full",configuration=name,protocol=protocol,threads_per_solver=1,
                shared_queue=str(a.output),split=a.split,instances=[],
                deadline="wall time before Popen; SIGKILL at deadline; complete lines preserved")
            for entry in inputs:
                instance=ROOT/"benchmarks"/entry["path"]
                if digest(instance)!=entry["sha256"]:raise ValueError("benchmark hash changed")
                run=Path("raw")/entry["path"]/"seed-0"
                command=[str(binaries[name]),"-i",str(instance),"-o",str(folder/run),
                         "-n",str(samples),"-t",str(a.seconds),"-s","0"]
                command+=CONFIGS[name]
                if name!="control_ab":command+=a.extra
                job=dict(instance=entry["path"],input=str(instance),seed=0,input_sha256=entry["sha256"],
                         run=str(run),command=command,batch=folder.name,configuration=name,protocol=protocol)
                manifest["instances"].append(job);jobs.append(job)
            write(folder/"manifest.json",manifest)
            batches[folder.name]=manifest
    # Initially nine time tasks then three count tasks; completion immediately frees a
    # slot for the next task, independently of configuration or protocol.
    jobs.sort(key=lambda j:(names.index(j["configuration"]),j["protocol"]!="time",j["instance"]))
    write(a.output/"plan.json",dict(created_utc=stamp,workers=a.jobs,seconds=a.seconds,
        configurations=names,split=a.split,extra=a.extra,seed=0,
        source_commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
        queue=jobs,baselines="reuse existing HighDiv and original LIA seed 0; never execute"))
    lock=threading.Lock();active=set();stopped=threading.Event()
    def cancel(*_):
        stopped.set()
        with lock:
            for proc in active:
                try:os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError:pass
    signal.signal(signal.SIGTERM,cancel);signal.signal(signal.SIGINT,cancel)
    def run(job):
        folder=a.output/job["batch"]/job["run"];folder.mkdir(parents=True)
        if stopped.is_set():return dict(**job,status="cancelled",elapsed=0,returncode=None)
        start=time.monotonic();utc=datetime.now(timezone.utc).isoformat()
        env=dict(os.environ,OMP_NUM_THREADS="1",OPENBLAS_NUM_THREADS="1",MKL_NUM_THREADS="1")
        with (folder/"process.log").open("wb") as log:
            with lock:
                proc=subprocess.Popen(job["command"],stdout=log,stderr=subprocess.STDOUT,
                    cwd=folder,start_new_session=True,env=env)
                active.add(proc)
            status="ok"
            try:
                code=proc.wait(timeout=max(.001,a.seconds-(time.monotonic()-start)))
                if code:status="error"
            except subprocess.TimeoutExpired:
                try:os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                code=proc.wait();status="timeout_partial"
            finally:
                with lock:active.discard(proc)
        result=dict(**job,status="cancelled" if stopped.is_set() else status,
                    elapsed=time.monotonic()-start,returncode=code,started_utc=utc,
                    finished_utc=datetime.now(timezone.utc).isoformat())
        write(folder/"process.json",result)
        return result
    completed=[]
    journals={name:(a.output/name/"runs.jsonl").open("w") for name in batches}
    with ThreadPoolExecutor(max_workers=a.jobs) as pool,(a.output/"queue.jsonl").open("w") as queue:
        for future in as_completed([pool.submit(run,j) for j in jobs]):
            result=future.result();completed.append(result)
            line=json.dumps(result)+"\n"
            queue.write(line);queue.flush()
            journals[result["batch"]].write(line);journals[result["batch"]].flush()
            print(f'{len(completed)}/{len(jobs)} {result["batch"]} {result["instance"]}: '
                  f'{result["status"]} {result["elapsed"]:.2f}s',flush=True)
    for name,journal in journals.items():
        journal.close();folder=a.output/name
        write(folder/"COMPLETE.json",dict(runs=sum(r["batch"]==name for r in completed),
            cancelled=stopped.is_set(),manifest_sha256=digest(folder/"manifest.json"),
            journal_sha256=digest(folder/"runs.jsonl")))
    write(a.output/"COMPLETE.json",dict(runs=len(completed),cancelled=stopped.is_set(),
        plan_sha256=digest(a.output/"plan.json"),queue_sha256=digest(a.output/"queue.jsonl"),
        errors=sum(r["status"]=="error" for r in completed)))
    if stopped.is_set() or any(r["status"]=="error" for r in completed):
        raise SystemExit("run errors retained; inspect before comparison")

if __name__=="__main__":main()
