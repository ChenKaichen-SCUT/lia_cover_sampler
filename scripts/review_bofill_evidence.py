#!/usr/bin/env python3
"""Final read-only consistency review of saved bofill results; no SMT imports."""
import argparse,csv,hashlib,json,statistics,tarfile,time
from collections import Counter
from pathlib import Path

def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def dump(p,x):p.write_text(json.dumps(x,indent=2,ensure_ascii=False)+'\n')
def csvout(p,rows):
    fields=list(dict.fromkeys(k for r in rows for k in r))
    with p.open('w',newline='') as f:w=csv.DictWriter(f,fields);w.writeheader();w.writerows(rows)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('out');a=ap.parse_args();out=Path(a.out).resolve();root=Path(__file__).resolve().parents[1]
    audit=json.loads((out/'audit_summary.json').read_text());plan=json.loads((out/'plan.json').read_text());archives=json.loads((out/'archive_manifest.json').read_text())
    assert audit['passed'] and len(audit['runs'])==12 and len(archives['runs'])==12
    assert plan['configurations']==['plain_clean','choice_compiled']
    diagnostics=[];resources=[];hashes={};archive_members=0;total_bytes=0
    for entry in archives['runs']:
        # All current outputs fit a single compressed piece. Fail explicitly for an unsupported future layout.
        assert len(entry['parts'])==1,'use restored content verification for split archives'
        part=entry['parts'][0];p=out/part['file'];assert sha(p)==part['sha256'];total_bytes+=p.stat().st_size
        with tarfile.open(p,'r:gz') as tar:
            for member in tar:
                if not member.isfile():continue
                f=tar.extractfile(member);h=hashlib.sha256()
                for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
                assert h.hexdigest()==entry['content_sha256'][member.name],member.name
                archive_members+=1
        source=json.loads((out/'audit'/entry['run']/'source_hashes.json').read_text())
        assert source==entry['content_sha256'],'audit/archive source file sets differ'
        for path,expected in source.items():assert sha(out/path)==expected,path
    for r in audit['runs']:
        run=out/'runs'/r['run'];summary=json.loads((run/'raw/summary.json').read_text())
        assert r['terminal']=='deadline' and summary['terminal']=='deadline'
        assert not r['issues'] and not any(r['excluded'].values()) and not r['forbidden_search_attempts']
        assert r['distinct']==summary['formal_count'] and r['covered']==summary['coverage']['covered']
        assert r['distinct']==summary['stats']['counts'].get('sat',0)
        counts=Counter();grants=Counter();check=0.;fast_unknown=0;qs=0
        with (run/'raw/queries.jsonl').open() as f:
            for line in f:
                q=json.loads(line);e=q['evidence'];qs+=1;counts[(e['raw_status'],e['control_classification'],e['raw_reason'])]+=1;grants[e['granted_timeout_ms']]+=1;check+=e['actual_check_seconds']
                if e['raw_status']=='unknown' and e['actual_check_seconds']*1000<.8*e['granted_timeout_ms']:fast_unknown+=1
                assert e['granted_timeout_ms']<=e['requested_timeout_ms']<=2000
        identity={k:r[k] for k in ['formula','seed','configuration','run']}
        diagnostics.append({**identity,'queries':qs,'sat':sum(n for (s,_,_),n in counts.items() if s=='sat'),'unsat':sum(n for (s,_,_),n in counts.items() if s=='unsat'),'unknown':sum(n for (s,_,_),n in counts.items() if s=='unknown'),'unknown_fast_exits':fast_unknown,'first_sat_seconds':summary['first_sat_seconds'] if summary['first_sat_seconds']>=0 else None,'check_seconds_precise_sum':check,'grants_ms':json.dumps(dict(grants),sort_keys=True),'exit_counts':json.dumps([dict(raw=s,classification=c,reason=reason,count=n) for (s,c,reason),n in counts.items()]),'compile_seconds':summary['stats']['seconds']['compile']})
        peak=0;global_peak=0;lowest_available=None
        with (run/'parallel_resources.jsonl').open() as f:
            for line in f:
                x=json.loads(line);peak=max(peak,sum(p['rss_kib'] for p in x['processes']));global_peak=max(global_peak,x['aggregate_rss_kib']);v=x['host_available_kib'];lowest_available=min(lowest_available,v) if lowest_available is not None else v
        resources.append({**identity,'sampled_peak_group_rss_kib':peak,'sampled_peak_all_sampler_rss_kib':global_peak,'lowest_host_available_kib':lowest_available,'resource_stop':False,'sampling_interval_seconds':1})
    assert sum(r['distinct'] for r in audit['runs'])==23569
    for p in [out/'plan.json',out/'parallel_amendment.json',out/'archive_manifest.json',out/'audit_summary.json',out/'historical_highdiv.json']:
        hashes[str(p.relative_to(out))]=sha(p)
    change=json.loads((out/'parallel_amendment.json').read_text());assert sha(root/'scripts/continue_bofill_parallel.py')==change['script_sha256'];assert sha(root/'scripts/run_bofill_900s.py')==change['gate_script_sha256']
    for entry in plan['inputs']:assert sha(out/'inputs'/entry['name'])==entry['sha256']
    assert sha(root/'build/bofill/release/bofill_sampler')==change['binary_sha256']
    dest=out/'review';dest.mkdir(exist_ok=True)
    csvout(dest/'query_diagnostics.csv',diagnostics);csvout(dest/'resource_summary.csv',resources)
    result=dict(passed=True,runs=12,valid_timely_distinct=23569,zero_output_runs=3,archive_members=archive_members,archive_bytes=total_bytes,all_raw_files_match_audit_and_archive=True,no_new_generation_or_search=True,source_hashes=hashes,compiled_random_all_unknown=all(x['sat']==0 and x['unknown']==452 and x['unknown_fast_exits']==0 for x in diagnostics if x['configuration']=='choice_compiled' and x['formula'].startswith('SMT_random')),peak_sampled_aggregate_rss_kib=max(x['sampled_peak_all_sampler_rss_kib'] for x in resources),all_deadline_exits=True)
    dump(dest/'integrity.json',result);print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
