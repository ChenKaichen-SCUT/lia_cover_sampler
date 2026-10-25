#!/usr/bin/env python3
"""Deterministic first-ten frozen seed-0 witnesses: evaluation only, never solver search."""
import json, subprocess, shutil, sys, os
from pathlib import Path
from run_bofill_diagnosis import MODES, INPUTS
from run_bofill_900s import sha,dump
from audit_bofill_diagnosis import structure
from reevaluate_round6_saved_candidates import no_search, FrozenCoverage, write_csv
ROOT=Path(__file__).resolve().parents[1]
def main():
    binary=ROOT/'build/bofill_diagnosis/release/bofill_first_sat_probe';out=ROOT/'results/bofill_diagnosis/semantic';out.mkdir(parents=True,exist_ok=True);rows=[];provenance=[]
    for index,(name,expected) in enumerate(INPUTS):
        case='random' if index==0 else 'real';d=out/case;d.mkdir(exist_ok=True);formula=ROOT/'benchmarks'/name;assert sha(formula)==expected
        old=ROOT/'results/bofill_900s/runs'/f'{index*3:02d}_plain_clean/raw';saved=list(map(json.loads,(old/'models.jsonl').read_text().splitlines()))[:10]
        audited=list(__import__('csv').DictReader((ROOT/'results/bofill_900s/audit'/f'{index*3:02d}_plain_clean/model_audit.csv').open()))[:10]
        assert len(saved)==len(audited)==10 and all(r['accepted']=='True' for r in audited)
        source=d/'original.smt2';source.write_bytes(formula.read_bytes());values=d/'saved.values';values.write_text(''.join(' '.join(str(r['value']).lower() for r in model['model'])+'\n' for model in saved))
        (d/'saved_models.jsonl').write_text(''.join(json.dumps(model)+'\n' for model in saved))
        provenance.append(dict(case=case,formula_sha256=expected,source_models=str((old/'models.jsonl').relative_to(ROOT)),source_models_sha256=sha(old/'models.jsonl'),source_audit=str((ROOT/'results/bofill_900s/audit'/f'{index*3:02d}_plain_clean/model_audit.csv').relative_to(ROOT)),first_indices=[m['index'] for m in saved]))
        for mode in MODES:
            target=d/mode;command=[str(binary),str(source),mode,'verify','30','0',str(target),str(values)]
            if not target.exists():subprocess.run(command,check=True,stdout=(d/(mode+'.log')).open('w'),stderr=subprocess.STDOUT)
            dump(target/'command.json',command);records=list(map(json.loads,(target/'roundtrip.jsonl').read_text().splitlines()));assert len(records)==10
            comp=json.loads((target/'structure.json').read_text());export='simplified' if mode=='simplify_only' else 'original_export' if mode=='plain' else mode
            shutil.copyfile(target/'compiled.smt2',d/(export+'.smt2'));dump(d/(export+'_recovery.json'),comp['reconstruction'])
            with no_search() as calls:
                metric=FrozenCoverage(source.read_text());baseline=metric.empty.copy()
                for before,after in zip(saved,records):
                    assert before['model']==after['model'];baseline=metric.apply(baseline,after['model'])
                structural=structure(target/'compiled.smt2',metric.root,comp['reconstruction']);assert not calls
            dump(target/'structural_analysis.json',structural)
            if mode=='full':
                frozen=json.loads((ROOT/'results/bofill_900s/runs'/f'{index*3:02d}_choice_compiled/raw/structure.json').read_text())
                assert comp['reconstruction']==frozen['reconstruction'],'full recovery diverged from frozen'
                assert comp['rewrites']==frozen['rewrites'],'full rewrite order diverged from frozen'
                assert (target/'compiled.smt2').read_bytes()==(ROOT/'results/bofill_900s/runs'/f'{index*3:02d}_choice_compiled/raw/compiled.smt2').read_bytes(),'full IR diverged from frozen'
            rows.append(dict(case=case,mode=mode,witnesses=10,original_valid=True,compiled_valid=True,exact_roundtrip=True,frozen_full_match=mode=='full',remaining_eligible_pivots=structural['remaining_pivot_reasons'].get('eligible_pivot_occurrences',0),operations=comp['statistics']['counts'].get('compiler_operations',0),stop=comp['stop_reason']))
    dump(out/'provenance.json',provenance);write_csv(out/'roundtrip.csv',rows);dump(out/'hashes.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file()});print(json.dumps(rows,indent=2))
if __name__=='__main__':main()
