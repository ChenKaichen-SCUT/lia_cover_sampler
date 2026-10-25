#!/usr/bin/env python3
"""Detach the authorized round driver from transient tool sessions.
The driver retains a file lock for its whole lifetime. No running job is killed.
"""
import fcntl,json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/soft_phase_validation'
def main():
    OUT.mkdir(parents=True,exist_ok=True)
    lock=os.open(OUT/'controller.lock',os.O_RDWR|os.O_CREAT,0o600)
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('round controller already running; unchanged')
    os.set_inheritable(lock,True)
    pid=os.fork()
    if pid:
        os.close(lock);os.waitpid(pid,0)
        print('Detached round driver launched; results/soft_phase_validation/daemon.json and pipeline_status.json record state.');return
    os.setsid()
    if os.fork():os._exit(0)
    os.chdir(ROOT)
    incoming=os.open('/dev/null',os.O_RDONLY)
    outgoing=os.open(OUT/'pipeline.log',os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    os.dup2(incoming,0);os.dup2(outgoing,1);os.dup2(outgoing,2)
    if incoming>2:os.close(incoming)
    if outgoing>2:os.close(outgoing)
    (OUT/'daemon.json').write_text(json.dumps(dict(pid=os.getpid(),started_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),mechanism='double fork, separate session, PID 1 parent, inherited exclusive flock',command=[sys.executable,'scripts/finish_soft_phase_round.py']),indent=2)+'\n')
    os.execv(sys.executable,[sys.executable,'scripts/finish_soft_phase_round.py'])
if __name__=='__main__':main()
