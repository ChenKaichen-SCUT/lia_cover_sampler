#!/usr/bin/env python3
"""Verify immutable experiment identities and all formal audit bindings before publication."""
import json,sys
from pathlib import Path
from datetime import datetime,timezone
from run_iteration import ROOT,digest,write
from audit_iteration import bindings

def main():
    state=json.loads((ROOT/'results/round4_dev/workflow.json').read_text())
    assert state['status']=='screen_not_promoted'
    build=json.loads(Path(state['frozen_build']).read_text())
    assert digest(state['frozen_build'])==state['frozen_build_sha256']
    assert all(digest(ROOT/p)==sha for p,sha in build['source_hashes'].items())
    assert all(digest(b['path'])==b['sha256'] for b in build['binaries'].values())
    assert digest(ROOT/'build/lia_cover_sampler')==build['binaries']['lia_cover_sampler']['sha256']
    assert digest(ROOT/'scripts/analyze_round4.py')==state['analysis_sha256']
    old=json.loads((ROOT/'results/round2_builds/baseline_hashes.json').read_text())
    assert all(digest(ROOT/p)==sha for p,sha in old.items())
    screen=Path(state['screen']);plan=json.loads((screen/'plan.json').read_text());done=json.loads((screen/'COMPLETE.json').read_text())
    assert done['runs']==18 and not done['cancelled'] and not done['errors']
    assert digest(screen/'plan.json')==done['plan_sha256'] and digest(screen/'queue.jsonl')==done['queue_sha256']
    assert digest(screen/'resources.json')==done['resources_sha256']
    assert plan['workers']==12 and plan['seconds']==120 and plan['seeds']==[0,1,2]
    assert plan['configs']==json.loads(Path(state['configs']).read_text())
    assert digest(state['configs'])==state['configs_sha256']
    batches=[];total=0
    for name in plan['configs']:
        folder=screen/name;jobs=[json.loads(s) for s in (folder/'runs.jsonl').read_text().splitlines()]
        complete=json.loads((folder/'COMPLETE.json').read_text())
        assert len(jobs)==complete['runs']==9
        assert digest(folder/'manifest.json')==complete['manifest_sha256']
        assert digest(folder/'runs.jsonl')==complete['journal_sha256']
        for job in jobs:
            path=folder/'audit'/job['instance']/f'seed-{job["seed"]}.json'
            bound=json.loads(path.with_suffix('.binding.json').read_text())
            assert bound['bindings']==bindings(folder,job,120)
            assert bound['report_sha256']==digest(path)
            audit=json.loads(path.read_text());total+=audit['unique_valid_in_budget']
            assert all(audit['counts'][k]==0 for k in ('invalid','unknown','malformed','duplicate','coverage_mismatch','after_deadline','timestamp_missing'))
        batches.append(dict(configuration=name,runs=len(jobs),content_bindings_verified=True))
    raw=json.loads((screen/'STATE_AUDIT_COMPLETE.json').read_text());assert len(raw['rows'])==18
    for row in raw['rows']:
        folder=screen/row['configuration']/'raw'/row['instance']/f'seed-{row["seed"]}'
        assert all(digest(folder/p)==sha for p,sha in row['hashes'].items())
        assert row['checker_sha256']==digest(ROOT/'scripts/audit_ab_states.py')
        assert row['all_raw_valid'] and row['processed_prefix_observations_preserved']
    diag=Path(state['diagnosis']);dc=json.loads((diag/'COMPLETE.json').read_text())
    assert dc['runs']==324 and dc['all_returned_models_audited'] and dc['probe_sha256']==build['binaries']['snapshot_probe']['sha256']
    assert digest(dc['snapshot_manifest'])==dc['snapshot_manifest_sha256']
    for snap in json.loads(Path(dc['snapshot_manifest']).read_text())['snapshots']:
        assert digest(snap['formula'])==snap['input_sha256']
        for file,key in [('prefix.values','prefix_sha256'),('seed.values','seed_sha256'),('coverage.json','coverage_sha256'),('assignments.json','assignments_sha256')]:
            assert digest(Path(snap['folder'])/file)==snap[key]
    replay=Path(state['replay']);rc=json.loads((replay/'COMPLETE.json').read_text())
    assert rc['runs']==9 and rc['all_valid'] and rc['all_preserved']
    assert digest(replay/'plan.json')==rc['plan_sha256'] and digest(replay/'runs.jsonl')==rc['journal_sha256']
    for row in json.loads((replay/'analysis.json').read_text())['rows']:
        assert digest(row['source'])==row['source_sha256'] and row['binary_sha256']==build['binaries']['ab_replay']['sha256']
    promotion=json.loads((screen/'promotion.json').read_text());assert not promotion['approved'] and not promotion['automatic_long_runs']
    assert digest(screen/'analysis/analysis.json')==promotion['analysis_sha256']
    output=dict(checked_utc=datetime.now(timezone.utc).isoformat(),sampling_source_and_binaries_unchanged=True,
        promotion_rules_unchanged=True,baseline_paths_verified=len(old),batches=batches,valid_formal_samples=total,
        raw_formal_runs_verified=18,pending_raw_models=sum(r['pending_raw_models'] for r in raw['rows']),
        incomplete_formal_lines=sum(r['incomplete_formal_lines'] for r in raw['rows']),
        diagnosis_runs=dc['runs'],fixed_prefix_runs=rc['runs'],decision='not promoted',
        failed_checks=[k for k,v in promotion['checks'].items() if not v],
        formal_audit_bindings='Every report SHA and all input, metric, validator, solver and checker hashes verified',
        tail_policy='Original deadline-truncated row preserved and excluded; incomplete transaction still fails the frozen gate')
    write(ROOT/'results/round4_dev/builds/publication_verification.json',output)
    print(json.dumps(output,indent=2))
if __name__=='__main__':main()
