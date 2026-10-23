#!/usr/bin/env python3
"""Transfer a fixed-size queue to more workers without restarting running samplers.

Only the old Python controller is stopped/replaced. Sampler process groups and
their outputs remain in place. Inherited exit codes may be unavailable to the
new parent; this is recorded explicitly instead of inventing an observed code.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import ctypes
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write(path, value):
    temporary=path.with_name(path.name+".tmp")
    temporary.write_text(json.dumps(value,indent=2,ensure_ascii=False)+"\n")
    temporary.replace(path)

def identity(job):
    return job["batch"],job["instance"],job["seed"]

def process_info(pid):
    try:
        data=Path(f"/proc/{pid}/stat").read_text().rsplit(")",1)[1].split()
        args=Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        return dict(pid=pid,state=data[0],parent=int(data[1]),ticks=int(data[19]),
                    exit_status=int(data[49]) if len(data)>49 else None,
                    command=[os.fsdecode(a) for a in args if a])
    except (FileNotFoundError,ProcessLookupError):
        return None

def birth_seconds(path):
    buffer=ctypes.create_string_buffer(256)
    libc=ctypes.CDLL(None,use_errno=True)
    status=libc.statx(-100,os.fsencode(path),0,0x800,buffer)
    if status or not struct.unpack_from("I",buffer.raw,0)[0]&0x800:
        return None
    seconds,nanos=struct.unpack_from("qI",buffer.raw,80)
    return seconds+nanos*1e-9

def read_journal(path, backup):
    data=path.read_bytes() if path.exists() else b""
    rows=[];offset=0
    for line in data.splitlines(keepends=True):
        try:
            if not line.endswith(b"\n"):break
            rows.append(json.loads(line));offset+=len(line)
        except json.JSONDecodeError:
            break
    if offset!=len(data):
        backup.write_bytes(data)
        path.write_bytes(data[:offset])
    return rows

def infer_result(folder, code, timed_out):
    if timed_out:return "timeout_partial"
    if code is not None:return "ok" if code==0 else "error"
    try:
        summary=json.loads((folder/"summary.json").read_text())
        if summary["status"] in ("sample_limit", "unsat", "initial_unknown",
                                 "time_limit_without_model", "exploration_exhausted", "time_limit"):
            return "ok"
    except (FileNotFoundError,json.JSONDecodeError,KeyError):
        pass
    return "error"

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output",type=Path)
    parser.add_argument("--takeover-pid",type=int,required=True)
    parser.add_argument("--jobs",type=int,default=15)
    args=parser.parse_args()
    if not 1<=args.jobs<=15:parser.error("jobs must be 1..15")
    root=args.output.resolve()
    plan=json.loads((root/"plan.json").read_text())
    if (root/"COMPLETE.json").exists():parser.error("batch already complete")
    controller_lock=(root/"controller.lock").open("w")
    fcntl.flock(controller_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    original=process_info(args.takeover_pid)
    if not original or not any(Path(a).name=="run_round2.py" for a in original["command"]):
        parser.error("takeover PID is not the original round-two controller")
    command=original["command"]
    if Path(command[command.index("--output")+1]).resolve()!=root:
        parser.error("controller belongs to another output directory")
    manifests={j["batch"]:json.loads((root/j["batch"]/"manifest.json").read_text()) for j in plan["queue"]}
    for manifest in manifests.values():
        if digest(Path(manifest["binary"]))!=manifest["binary_sha256"]:
            parser.error("frozen executable changed")
    migration=root/"migration_to_15"
    migration.mkdir(exist_ok=False)
    (migration/"queue_before.jsonl").write_bytes((root/"queue.jsonl").read_bytes())
    inherited={};fds={};old_stopped=False;old_replaced=False
    try:
        # Freeze only the dispatcher to make a consistent queue snapshot. All
        # solver processes run in independent sessions and continue executing.
        os.kill(args.takeover_pid,signal.SIGSTOP);old_stopped=True
        for _ in range(100):
            current=process_info(args.takeover_pid)
            if current and current["state"] in ("T","t"):break
            time.sleep(.001)
        else:raise RuntimeError("controller did not stop")
        by_output={str((root/j["batch"]/j["run"]).resolve()):j for j in plan["queue"]}
        now_wall=time.time();now_mono=time.monotonic()
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():continue
            proc=process_info(int(entry.name))
            if not proc or proc["parent"]!=args.takeover_pid:continue
            if "-o" not in proc["command"]:
                # A just-forked child can still be between fork and exec.
                for _ in range(100):
                    time.sleep(.001);proc=process_info(int(entry.name))
                    if not proc or "-o" in proc["command"]:break
            if not proc or "-o" not in proc["command"]:continue
            destination=str(Path(proc["command"][proc["command"].index("-o")+1]).resolve())
            if destination not in by_output:raise RuntimeError("unexpected controller child")
            job=by_output[destination];key=identity(job);folder=Path(destination)
            birth=birth_seconds(folder/"process.log")
            if birth is None:
                age=time.clock_gettime(time.CLOCK_BOOTTIME)-proc["ticks"]/os.sysconf("SC_CLK_TCK")
                birth=now_wall-age;origin="kernel_start_tick"
            else:origin="process_log_birth_time"
            # The original function records start immediately before opening this
            # log. Reserve 10 ms conservatively; do not extend an inherited budget.
            reconstructed=birth-.010
            proc.update(started_utc=datetime.fromtimestamp(reconstructed,timezone.utc).isoformat(),
                start_monotonic=now_mono-(now_wall-reconstructed),timing_origin=origin,
                conservative_start_adjustment_seconds=.010,job=job)
            inherited[key]=proc
            try:fds[key]=os.pidfd_open(proc["pid"])
            except ProcessLookupError:fds[key]=None
        known={}
        for row in read_journal(root/"queue.jsonl",migration/"partial_queue.jsonl"):
            known[identity(row)]=row
        for job in plan["queue"]:
            path=root/job["batch"]/job["run"]/"process.json"
            if path.exists():
                try:
                    row=json.loads(path.read_text());known[identity(row)]=row
                except json.JSONDecodeError:
                    (migration/(job["batch"]+"_"+job["instance"]+".partial_process.json")).write_bytes(path.read_bytes())
        for key in known:
            inherited.pop(key,None)
            fd=fds.pop(key,None)
            if fd is not None:os.close(fd)
        event=dict(changed_utc=datetime.now(timezone.utc).isoformat(),old_workers=plan["workers"],
            new_workers=args.jobs,reason="user requested 15 workers; keep running samples uninterrupted",
            old_controller_pid=args.takeover_pid,new_controller_pid=os.getpid(),
            inherited_processes=list(inherited.values()),completed_at_handoff=len(known),
            timing_note="Inherited controller start reconstructed from log birth or kernel start tick minus 10 ms; original native sampler timer unchanged; original controller timestamp unavailable.")
        write(migration/"handoff.json",event)
        # SIGKILL targets the old controller only, so its SIGTERM handler cannot
        # kill child process groups. Children retain their PIDs and native timers.
        os.kill(args.takeover_pid,signal.SIGKILL);old_replaced=True
    finally:
        if old_stopped and not old_replaced:
            try:os.kill(args.takeover_pid,signal.SIGCONT)
            except ProcessLookupError:pass
    with (root/"concurrency_changes.jsonl").open("a") as f:
        f.write(json.dumps(event,ensure_ascii=False)+"\n")
    write(root/"runtime.json",dict(workers=args.jobs,controller_pid=os.getpid(),changed_utc=event["changed_utc"]))
    journals={name:(root/name/"runs.jsonl").open("a") for name in manifests}
    existing_batch={}
    for name in manifests:
        rows=read_journal(root/name/"runs.jsonl",migration/(name+"_partial_journal.jsonl"))
        existing_batch[name]={identity(r) for r in rows}
    existing_queue={identity(r) for r in read_journal(root/"queue.jsonl",migration/"partial_queue_after.jsonl")}
    stopped=threading.Event();lock=threading.Lock();active={p["pid"]:p["ticks"] for p in inherited.values()}
    def cancel(*_):
        stopped.set()
        with lock:
            for pid,ticks in active.items():
                proc=process_info(pid)
                if proc and proc["ticks"]==ticks:
                    try:os.killpg(pid,signal.SIGKILL)
                    except ProcessLookupError:pass
    signal.signal(signal.SIGTERM,cancel);signal.signal(signal.SIGINT,cancel)
    def run(job):
        key=identity(job);folder=root/job["batch"]/job["run"]
        proc=inherited.get(key)
        if stopped.is_set():return dict(**job,status="cancelled",elapsed=0,returncode=None)
        if proc:
            start=proc["start_monotonic"];utc=proc["started_utc"];code=None;timed_out=False
            fd=fds[key];poll=select.poll()
            if fd is not None:poll.register(fd,select.POLLIN)
            while True:
                remaining=plan["seconds"]-(time.monotonic()-start)
                if fd is None or poll.poll(max(0,int(min(.2,max(0,remaining))*1000))):
                    last=process_info(proc["pid"])
                    if last and last["state"]=="Z" and last["exit_status"] is not None:
                        code=os.waitstatus_to_exitcode(last["exit_status"])
                    break
                if remaining<=0:
                    last=process_info(proc["pid"])
                    if last and last["ticks"]==proc["ticks"]:
                        try:os.killpg(proc["pid"],signal.SIGKILL)
                        except ProcessLookupError:pass
                    timed_out=True;code=-signal.SIGKILL
                    poll.poll(1000);break
            if fd is not None:os.close(fd)
            with lock:active.pop(proc["pid"],None)
            status=infer_result(folder,code,timed_out)
            extra=dict(inherited_pid=proc["pid"],timing_origin=proc["timing_origin"],
                       conservative_start_adjustment_seconds=.010,
                       exit_code_observed=code is not None,
                       outcome_source="wait_status" if code is not None else "sampler_summary")
        else:
            # A folder created just before controller replacement can be reused
            # only if no sampler artifacts exist. Never run an existing output.
            if folder.exists() and any(p.name!="process.log" or p.stat().st_size for p in folder.iterdir()):
                summary=json.loads((folder/"summary.json").read_text()) if (folder/"summary.json").exists() else {}
                result=dict(**job,status=infer_result(folder,None,False),elapsed=summary.get("elapsed",0),
                    returncode=None,started_utc=datetime.fromtimestamp(birth_seconds(folder/"process.log") or time.time(),timezone.utc).isoformat(),
                    finished_utc=datetime.now(timezone.utc).isoformat(),outcome_source="completed_during_handoff",
                    exit_code_observed=False,timing_origin="process_log_birth_time")
                write(folder/"process.json",result);return result
            folder.mkdir(parents=True,exist_ok=True)
            env=dict(os.environ,OMP_NUM_THREADS="1",OPENBLAS_NUM_THREADS="1",MKL_NUM_THREADS="1")
            start=time.monotonic();utc=datetime.now(timezone.utc).isoformat();status="ok"
            with (folder/"process.log").open("wb") as log:
                child=subprocess.Popen(job["command"],cwd=folder,start_new_session=True,
                    env=env,stdout=log,stderr=subprocess.STDOUT)
                info=process_info(child.pid)
                with lock:active[child.pid]=info["ticks"] if info else -1
                try:
                    code=child.wait(timeout=max(.001,plan["seconds"]-(time.monotonic()-start)))
                    if code:status="error"
                except subprocess.TimeoutExpired:
                    try:os.killpg(child.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    code=child.wait();status="timeout_partial"
                finally:
                    with lock:active.pop(child.pid,None)
            extra=dict(timing_origin="controller_monotonic",exit_code_observed=True)
        result=dict(**job,status="cancelled" if stopped.is_set() else status,elapsed=time.monotonic()-start,
                    returncode=code,started_utc=utc,finished_utc=datetime.now(timezone.utc).isoformat(),**extra)
        write(folder/"process.json",result);return result
    pending=[j for j in plan["queue"] if identity(j) not in known]
    pending.sort(key=lambda j:identity(j) not in inherited)
    with (root/"queue.jsonl").open("a") as journal:
        def record(row):
            key=identity(row);line=json.dumps(row)+"\n"
            if key not in existing_queue:
                journal.write(line);journal.flush();existing_queue.add(key)
            if key not in existing_batch[row["batch"]]:
                journals[row["batch"]].write(line);journals[row["batch"]].flush()
                existing_batch[row["batch"]].add(key)
            known[key]=row
        for row in list(known.values()):record(row)
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for future in as_completed([pool.submit(run,j) for j in pending]):
                result=future.result();record(result)
                print(f'{len(known)}/{len(plan["queue"])} {result["batch"]} {result["instance"]}: '
                      f'{result["status"]} {result["elapsed"]:.2f}s',flush=True)
    for name,journal in journals.items():
        journal.close();folder=root/name
        write(folder/"COMPLETE.json",dict(runs=sum(r["batch"]==name for r in known.values()),
            cancelled=stopped.is_set(),manifest_sha256=digest(folder/"manifest.json"),
            journal_sha256=digest(folder/"runs.jsonl")))
    write(root/"COMPLETE.json",dict(runs=len(known),cancelled=stopped.is_set(),
        plan_sha256=digest(root/"plan.json"),queue_sha256=digest(root/"queue.jsonl"),
        errors=sum(r["status"]=="error" for r in known.values()),effective_workers=args.jobs,
        concurrency_changes_sha256=digest(root/"concurrency_changes.jsonl")))
    if stopped.is_set() or any(r["status"]=="error" for r in known.values()):
        raise SystemExit("run errors retained; inspect before comparison")

if __name__=="__main__":main()
