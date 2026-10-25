"""Only pure offline regressions. No sampler/optimizer/solver checks."""
import json,sys,unittest,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from diagnose_soft_phase_scores import score,guarantee,gain,explain,category,decode_events,stable_atom,correlations,pack,unpack,run_session,fingerprint,object_hash
from reevaluate_round6_saved_candidates import FrozenCoverage,no_search,dump,file_hash,write_csv

class OfflineScoreTests(unittest.TestCase):
    def test_first_observation_pairing_reintroduces_covered_score(self):
        g=(0,1);self.assertEqual(score(g,(0,0)),(0,1))
        state,d,new,first=gain((0,0),(1,0));self.assertEqual(d,0);self.assertEqual(first,1)
        self.assertEqual(score(g,state),(1,1));self.assertEqual(guarantee(g,state),1)
        state,d,new,first=gain(state,(0,1));self.assertEqual((d,new,first),(1,1,0));self.assertEqual(score(g,state),(0,0))
    def test_overlapping_summaries_use_union(self):
        result,e=explain(0b111,[0b011,0b110],True)
        self.assertEqual(e,0b111);self.assertEqual(result['overlapping_count'],1);self.assertEqual(result['explanation_ratio'],1)
    def test_zero_gain_ratio_na(self):self.assertIsNone(explain(0,[],True)[0]['explanation_ratio'])
    def test_late_gain_not_formal(self):
        r,e=explain(3,[1],False);self.assertEqual(r['explained'],0);self.assertEqual(r['candidate_gain'],2);self.assertIsNone(r['explanation_ratio'])
    def test_all_four_information_classes(self):
        self.assertEqual(category((1,2),(0,2)),'covered_difference')
        self.assertEqual(category((0,3),(0,2)),'observation_only_difference')
        self.assertEqual(category((0,2),(0,2)),'full_tie_positive_observation')
        self.assertEqual(category((0,0),(0,0)),'no_new_observation')
    def test_truncated_nonadoption_cannot_be_inferred_absent(self):
        event=dict(atom_id=7,group=1,conflicts=0,native_truth=True,preferred_truth=True,applied=False)
        h=dict(events=[dict(event,decision=i) for i in range(32)],opportunities=41,same_native=40,applied=1)
        h['events'].append(dict(event,atom_id=8,group=2,native_truth=False,applied=True,decision=41))
        events,missing,complete=decode_events(h);self.assertFalse(complete);self.assertEqual(missing['same_native'],8);self.assertEqual(sum(e['applied'] for e in events),1)
    def test_missing_applied_event_rejected(self):
        with self.assertRaises(ValueError):decode_events(dict(events=[],opportunities=1,applied=1))
    def test_identity_ignores_process_local_ids(self):
        a=dict(formula='f',node=3,expression='(= x 1)',ast_id=1);b=dict(a,ast_id=999)
        self.assertEqual(stable_atom(a['formula'],a['node'],a['expression']),stable_atom(b['formula'],b['node'],b['expression']))
        self.assertNotEqual(stable_atom('f',3,'(= x 1)'),stable_atom('other',3,'(= x 1)'))
    def test_packed_copy_independence(self):
        original=pack([1,3],[0,4]);before=original
        updated,_,_,_=gain(original,pack([0,0],[1,2]));self.assertEqual(original,before);self.assertNotEqual(updated,original);self.assertEqual(unpack(original,2),[(1,0),(3,4)])
    def test_small_correlation_not_reported(self):self.assertIsNone(correlations([(1,2),(2,3)])['pearson'])

    def fixture(self,applied=True,realized=True,late=False):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);out=Path(temp.name);inputs=out/'inputs'/'screen';raw=inputs/'runs'/'r'/'raw';raw.mkdir(parents=True);(inputs/'inputs').mkdir();prior=inputs/'prior_audit'/'r';prior.mkdir(parents=True)
        text='(declare-const x Int)(assert (or (= x 0)(= x 1)))';f=inputs/'inputs'/'f.smt2';f.write_text(text)
        with no_search():
            metric=FrozenCoverage(text);ids=[n.get_id() for n in metric.nodes];node=next(i for i,n in enumerate(metric.nodes) if n.sexpr()=='(= x 1)');group=next(i for i,n in enumerate(metric.nodes) if n.sexpr().startswith('(or '));obs=[];models=[]
            for val in ('0','1'):
                m=[dict(name='x',sort='Int',value=val)];models.append(m);s=metric.apply(metric.empty,m);obs.append(pack([s[k][1] for k in ids],[s[k][0] for k in ids]))
            sides=[]
            for truth,o in enumerate(obs):sides.append(dict(truth=bool(truth),observations=[[i,str(z),str(v)] for i,(z,v) in enumerate(unpack(o,len(ids))) if z or v]))
            dump(raw/'summaries.json',dict(complete=True,summary_nodes=sum(len(s['observations']) for s in sides),reasons={},atoms=[dict(node=node,ast_id=700,group=group,expression=metric.nodes[node].sexpr(),sides=sides)]))
            qs=[];cs=[];ms=[];receipts=[];prior_q=[];prior_p=[];state=(0,0);version=0
            for qid in range(2):
                candidate=models[0 if qid==0 or not realized else 1];o=obs[0 if qid==0 or not realized else 1];after,d,new,first=gain(state,o)
                timely=qid==0 or (realized and not late);scores=[score(g,state) for g in obs];p=[];hook={}
                if qid:
                    p=[dict(node=node,ast_id=700,group=group,truth=True,coverage_version=version,lower_bound=scores[1][0],zero_covered=scores[0][0],one_covered=scores[1][0],zero_observed=scores[0][1],one_observed=scores[1][1])]
                    event=dict(atom_id=700,normalized_id=777,group=group,internal_var=2,negated=False,conflicts=0,decision=1,native_truth=not applied,preferred_truth=True,applied=applied)
                    hook=dict(epoch=1,active=True,installed=1,events=[event],opportunities=1,applied=int(applied),same_native=int(not applied),mapping=[dict(atom_id=700,normalized_id=777,group=group,internal_var=2,negated=False,mapped=True,supported=True)])
                    prior_p.append(dict(query=qid,node=node,actual_override=str(applied),realized=str(realized)))
                finish=.08 if qid==0 else (1.1 if late else .2);completion=finish-.01
                ev=dict(raw_status='sat',raw_reason='',control_classification='sat',budget_clip_reason='none',actual_check_seconds=.01,granted_timeout_ms=50)
                qs.append(dict(query=qid,start=.01 if qid==0 else .1,finished=finish,coverage_version=version,evidence=ev,preferences=p,hook=hook,valid=True,duplicate=bool(qid and not realized),emitted=timely,completion=completion if (timely or late) else -1,gain=d.bit_count() if timely or late else 0))
                cs.append(dict(query=qid,coverage_version=version,validated_seconds=completion-.005,valid=True,model=candidate))
                if timely or late:
                    ms.append(dict(query=qid,index=version+1,validated_seconds=completion-.005,gain=d.bit_count(),observations=new,covered=(after[0]&after[1]).bit_count(),state_fnv1a64=fingerprint(after,len(ids)),model=candidate))
                    receipts.append(dict(query=qid,index=version+1,completion=completion,timely=timely))
                prior_q.append(dict(query=qid,proposed=len(p),mapped=len(p),opportunities=int(bool(qid)),same_native=int(bool(qid) and not applied),applied=int(bool(qid) and applied),already_used=0,override_cap=0,conflict_cap=0))
                if timely:state=after;version+=1
            dump(raw/'summary.json',dict(elapsed=1.2,formal_count=version,coverage=dict(total=metric.total,covered=(state[0]&state[1]).bit_count(),states=[[str(z),str(o)] for z,o in unpack(state,len(ids))])))
        for name,rows in [('queries',qs),('candidates',cs),('models',ms),('commits',receipts)]: (raw/(name+'.jsonl')).write_text(''.join(json.dumps(r)+'\n' for r in rows))
        dump(raw.parent/'spec.json',dict(formula='f.smt2',configuration='coverage_soft',seed=0,seconds=1));dump(raw.parent/'process_result.json',dict(process_wall_seconds=1.2))
        dump(inputs/'plan.json',dict(inputs=[dict(name='f.smt2',sha256=file_hash(f))]));write_csv(prior/'phase_queries.csv',prior_q);write_csv(prior/'phase_realization.csv',prior_p)
        return out,raw
    def test_proposed_but_not_applied(self):
        out,raw=self.fixture(applied=False);s=run_session((str(out),'screen','r'));self.assertEqual(s['all_applied'],0);self.assertEqual(s['explained_applied'],0);self.assertGreater(s['explained_proposed'],0)
    def test_applied_but_unrealized(self):
        out,raw=self.fixture(realized=False);s=run_session((str(out),'screen','r'));self.assertEqual(s['applied_unrealized'],1);self.assertEqual(s['explained_applied'],0)
    def test_late_model_stays_out_of_state(self):
        out,raw=self.fixture(late=True);s=run_session((str(out),'screen','r'));self.assertEqual(s['formal_models'],1);self.assertEqual(s['covered'],0);self.assertEqual(s['applied_realized'],1)
    def test_unknown_no_model_and_counterfactual_unknown(self):
        import csv,gzip
        out,raw=self.fixture();qs=[json.loads(x) for x in (raw/'queries.jsonl').read_text().splitlines()];q=qs[1];q['evidence']['raw_status']='unknown';q['valid']=False;q['emitted']=False;q['completion']=-1;q['gain']=0
        (raw/'queries.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in qs))
        for name in ('candidates','models','commits'):
            p=raw/(name+'.jsonl');p.write_text(p.read_text().splitlines()[0]+'\n')
        summary=json.loads((raw/'summary.json').read_text());m=json.loads((raw/'models.jsonl').read_text())
        with no_search():
            metric=FrozenCoverage((out/'inputs/screen/inputs/f.smt2').read_text());state=metric.apply(metric.empty,m['model']);summary['coverage']=metric.export(state)
        summary['formal_count']=1;dump(raw/'summary.json',summary)
        s=run_session((str(out),'screen','r'));self.assertEqual(s['applied_without_candidate'],1);self.assertEqual(s['applied_unrealized'],0)
        with gzip.open(out/'screen/r/phase_opportunities.csv.gz','rt') as f:rows=list(csv.DictReader(f))
        self.assertTrue(all(r['opposite_generated_model_gain']=='' and r['opposite_query_cost']=='' for r in rows))
        self.assertTrue(all(r['final_direction_realized']=='' for r in rows if r['query']=='1'))
        from audit_soft_phase_score_diagnosis import one
        self.assertTrue(one((str(out),'screen','r'))['passed'])
    def test_second_audit_on_saved_synthetic_assignments(self):
        from audit_soft_phase_score_diagnosis import one
        for options in ({},dict(applied=False),dict(realized=False),dict(late=True)):
            with self.subTest(options=options):
                out,raw=self.fixture(**options)
                run_session((str(out),'screen','r'))
                self.assertTrue(one((str(out),'screen','r'))['passed'])
if __name__=='__main__':unittest.main()
