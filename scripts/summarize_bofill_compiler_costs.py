#!/usr/bin/env python3
"""Offline stage ledger. Never alters the compiler operation cap or performs search."""
import json
from pathlib import Path
from reevaluate_round6_saved_candidates import write_csv,dump
ROOT=Path(__file__).resolve().parents[1]
def main():
    out=ROOT/'results/bofill_diagnosis/semantic';stages=[];passes=[];summary=[];rewrites=[]
    for case in ['random','real']:
        for mode in ['plain','simplify_only','eq_only','choice_only','full']:
            d=out/case/mode;s=json.loads((d/'structure.json').read_text());t=json.loads((d/'structural_analysis.json').read_text());c=s['statistics']['counts'];base=dict(case=case,mode=mode)
            ops=c.get('compiler_operations',0);exclusive=sum(v['exclusive_seconds'] for v in s['stages'].values())
            assert sum(v['operations'] for v in s['stages'].values())==ops
            for name,v in s['stages'].items():stages.append(dict(**base,stage=name,**v,operation_fraction=v['operations']/ops if ops else 0,exclusive_wall_fraction=v['exclusive_seconds']/exclusive if exclusive else 0))
            for i,r in enumerate(s['candidate_passes']):
                passes.append(dict(**base,pass_index=i,search_seconds=r['search_seconds'],selected=r['selected'],variable_index=r.get('variable_index'),source_clause=r.get('source_clause'),replacement_width=r.get('replacement_width'),references=r.get('references'),estimated_growth=r.get('estimated_growth'),**r['reasons']))
            for i,r in enumerate(s['transactions']):rewrites.append(dict(**base,index=i,**r))
            committed=c.get('unit_equation_eliminations',0);selected=sum(r['selected'] for r in s['candidate_passes'])
            summary.append(dict(**base,operations=ops,stop=s['stop_reason'],interrupted_stage=s['interrupted_stage'],choice_groups=c.get('converted_choice_groups',0),unit_eliminations=committed,complete_candidate_passes=len(s['candidate_passes']),selected_but_uncommitted=selected-committed,remaining_eligible_pivots=t['remaining_pivot_reasons'].get('eligible_pivot_occurrences',0),compile_seconds=s['statistics']['seconds']['compile_seconds'],cpp_compiled_dag=c['compiled_dag_nodes'],reparsed_dag=t['dag_nodes'],or_count=t['or_count'],auxiliary_count=len(t['auxiliary_variables']),aux_arithmetic_expression_sum=sum(r['arithmetic_expression_count'] for r in t['auxiliary_coupling']),aux_constraint_incidence_sum=sum(len(r['clauses']) for r in t['auxiliary_coupling']),largest_necessary_equation_component=max(map(len,t['components']),default=0),remaining_pivot_reasons=json.dumps(t['remaining_pivot_reasons'],sort_keys=True)))
    for name,rows in [('stage_costs',stages),('candidate_search_passes',passes),('rewrite_costs',rewrites),('compiler_summary',summary)]:write_csv(out/(name+'.csv'),rows)
    dump(out/'cost_ledger.json',dict(stage_ops_exact=True,native_z3_node_visit_counts='unavailable; root-call counts recorded',reparsed_dag_note='SMT-LIB roundtrip and wrapper may change AST sharing/counts; CPP original DAG is authoritative for compiler cap',pending_analysis='final retained formula + complete recovery; original eligibility limits, no transformation or search',summary=summary))
if __name__=='__main__':main()
