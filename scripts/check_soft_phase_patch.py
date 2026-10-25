#!/usr/bin/env python3
"""Static no-side-effect guard, paired native trace check, bounded event audit."""
import argparse,hashlib,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FILES=['src/smt/smt_context.h','src/smt/smt_context.cpp','src/solver/solver.h','src/smt/smt_solver.cpp','src/api/z3_api.h','src/api/api_solver.cpp']
def main():
    a=argparse.ArgumentParser();a.add_argument('--patched',required=True);a.add_argument('--out',required=True);v=a.parse_args();out=Path(v.out);out.mkdir(parents=True,exist_ok=True)
    old=Path('/tmp/lia-highdiv-frozen/z3');new=Path(v.patched).resolve();before=(old/'src/smt/smt_context.cpp').read_text();after=(new/'src/smt/smt_context.cpp').read_text()
    # Actual original functions are unchanged except the one guarded phase hook.
    b=before[before.index('bool context::guess('):before.index('void context::update_phase_cache_counter(')]
    c=after[after.index('bool context::guess('):after.index('void context::update_phase_cache_counter(')]
    c=c.replace('    if (m_soft_active) soft_phase_decision(var, is_pos);\n','')
    assert len(b)>1000 and b==c,'native guess/decide changed outside guarded hook'
    region=after[after.index('void context::soft_phase_clear'):after.index('bool context::guess(')]
    for token in ['m_random','ensure_internalized','assert_expr','assign(','next_case_split','push_scope','add_clause']:
        assert token not in region, 'native hook contains active side effect: '+token
    logs=[]
    for label,prefix in [('frozen',Path('/tmp/lia-highdiv-frozen/venv')),('patched',new/'install')]:
        binary=(out/('native_off_'+label)).resolve();cmd=['g++','-std=c++20','-O2','-I'+str(prefix/'include'),str(ROOT/'tests/native_hook_off_probe.cpp'),'-L'+str(prefix/'lib'),'-Wl,-rpath,'+str(prefix/'lib'),'-lz3','-o',str(binary)]
        subprocess.run(cmd,check=True);r=subprocess.run([str(binary)],check=True,capture_output=True,text=True);(out/(label+'_decisions.txt')).write_text(r.stdout);logs.append(r.stdout);binary.unlink()
    assert logs[0]==logs[1],'hook-off native decisions/models differ'
    (out/'hook_off_check.json').write_text(json.dumps(dict(passed=True,decision_trace_lines=sum(s.startswith('decision ') for s in logs[0].splitlines()),trace_sha256=hashlib.sha256(logs[0].encode()).hexdigest(),native_rng_check='native RNG call sites and guess/decide unchanged; exact decision trace under PS_RANDOM, seeds 0/1/2; hidden RNG draw counter is not exposed and not claimed measured',fixed_rlimit=100000),indent=2)+'\n')
if __name__=='__main__':main()
