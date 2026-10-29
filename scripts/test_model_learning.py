import importlib.util, pathlib, unittest, tempfile, json, math
p=pathlib.Path(__file__).with_name('model_learning.py');spec=importlib.util.spec_from_file_location('learning',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class LearningTests(unittest.TestCase):
    def make(self, event='1',frozen='2026-10-28T12:00Z',start='2026-10-29T12:00Z',p=(.7,.3)):
        return m.record(event,start,frozen,'v1',p,['H','A'],'fixture')
    def test_postgame_missing_and_future_training_rejected(self):
        self.assertIsNone(self.make(frozen='2026-10-30T12:00Z'))
        self.assertIsNone(self.make(frozen=None))
        self.assertIsNone(m.record('1','2026-10-29T12:00Z','2026-10-28T12:00Z','v1',[.5,.5],['H','A'],'x','2026-10-30'))
        with self.assertRaises(ValueError): self.make(p=(float('nan'),.3))
        with self.assertRaises(ValueError): self.make(p=(.9,.3))
    def test_immutable_idempotent_and_timezone_order(self):
        a=self.make(p=(.7028858816018078,.2971141183981923));self.assertEqual(len(m.merge_records([a],[a])),1)
        bad={**a,'probabilities':[.6,.4]}
        with self.assertRaises(ValueError):m.merge_records([bad],[])
        b=self.make(frozen='2026-10-28T09:00-04:00')
        chosen=m.graded([a,b],{'1':{'label':'H'}})
        self.assertEqual(chosen[0]['key'],b['key'])
    def test_metrics_include_losers_and_draw_classes(self):
        a=self.make();b=self.make('2',p=(.2,.8))
        score=m.metrics(m.graded([a,b],{'1':{'label':'A'},'2':{'label':'A'}}))
        self.assertAlmostEqual(score['brier_multiclass_sum'],(.98+.08)/2)
        self.assertEqual(score['winner_accuracy'],.5)
        self.assertEqual(m.calibration_proposal([])['status'],'insufficient_forward_sample')
    def test_adapter_all_sports(self):
        examples={
        'oracle-fc':('data/forecasts/2026/x/v1.json',{'status':'ok','fixture':{'event_id':'1','kickoff_utc':'2026-10-29T12:00Z'},'frozen_at':'2026-10-28T12:00Z','model_version':'v1','simulation':{'p_home':.5,'p_draw':.2,'p_away':.3}}),
        'nba-oracle':('out/predictions.json',{'generatedAt':'2026-10-28T12:00Z','modelVersion':'v1','games':[{'id':'1','tip':'2026-10-29T12:00Z','homeWin':.7,'awayWin':.3}]}),
        'nhl-score-predictor':('data/predictions/2026.json',{'generatedAt':'2026-10-28T12:00Z','model':'v1','games':[{'id':1,'start':'2026-10-29T12:00Z','pHome':.7,'pAway':.3}]}),
        'lucksociety-nfl-dashboard':('out/model.json',{'frozen_at':'2026-10-28T12:00Z','predictions':[{'game':{'game_id':'1','gameday':'2026-10-29','gametime':'20:15'},'res':{'p_home':.7,'p_tie':.01,'p_away':.29}}]}),
        'tennis-oracle':('forecasts/2026/x/v1.json',{'freeze':{'espn_id':'1','pre_match':True,'scheduled_start_utc':'2026-10-29T12:00Z','frozen_at_utc':'2026-10-28T12:00Z'},'players':{'A':{'name':'Alice'},'B':{'name':'Bea'}},'model':{'version':'v1'},'win_prob':{'A':.7,'B':.3}}),
        'lol-scanner':('forecast_log/2026.v2.add1.json',{'frozen_at_utc':'2026-10-28T12:00Z','model_version':'v1','cards':[{'match_id':'1','start_utc':'2026-10-29T12:00Z','sim':{'p_series_a':.7}}]})}
        for project,(name,doc) in examples.items():
            with self.subTest(project=project),tempfile.TemporaryDirectory() as d:
                root=pathlib.Path(d);f=root/name;f.parent.mkdir(parents=True);f.write_text(json.dumps(doc))
                rows,_=m.collect_local(root,project);self.assertEqual(len(rows),1);self.assertEqual(rows[0]['event_id'],'1')
    def test_calibration_uses_heldout_days_and_does_not_autopromote(self):
        from datetime import datetime,timedelta,timezone
        rows=[]
        for i in range(1000):
            day=(datetime(2020,1,1,tzinfo=timezone.utc)+timedelta(days=i//10)).isoformat()
            rows.append({'start':day,'event_id':str(i),'probabilities':[.9,.1],'observed':0 if i%10<6 else 1})
        report=m.calibration_proposal(rows)
        self.assertEqual(report['temperature'],2.)
        self.assertEqual(report['status'],'review_candidate')
        self.assertFalse(report['automatic_live_changes'])
        self.assertGreaterEqual(report['heldout_test']['n'],100)
        for row in rows[600:]:row['observed']=0
        rejected=m.calibration_proposal(rows)
        self.assertEqual(rejected['temperature'],2.)
        self.assertEqual(rejected['status'],'retain_current_model')


class ArtifactTests(unittest.TestCase):
    def test_archive_import_is_idempotent(self):
        import io,os,zipfile
        from unittest.mock import patch
        row=m.record('1','2026-10-29T12:00Z','2026-10-28T12:00Z','v1',[.7,.3],['H','A'],'fixture')
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as z:z.writestr('inbox.jsonl',json.dumps(row)+'\n')
        def api(url,token,**kwargs):
            return buffer.getvalue() if url.endswith('/zip') else json.dumps({'artifacts':[{'name':'learning-1','id':1,'expired':False}]}).encode()
        with patch.dict(os.environ,{'GH_TOKEN':'fake-test-token','GITHUB_REPOSITORY':'test/repo'}),patch.object(m,'api_get',side_effect=api):
            rows,state=m.collect_artifacts({});self.assertEqual(len(rows),1)
            rows,state=m.collect_artifacts(state);self.assertEqual(rows,[])
        self.assertEqual(state['artifact_ids'],[1])

class ScoreTests(unittest.TestCase):
    def test_score_metrics_keep_home_away_orientation(self):
        r=m.record('1','2026-10-29T12:00Z','2026-10-28T12:00Z','v1',[.7,.3],['H','A'],'fixture',expected_scores=[110,100])
        scores=m.metrics(m.graded([r],{'1':{'label':'H','scores':[108,104]}}))
        self.assertEqual(scores['score_mae_per_team'],3)
        self.assertEqual(scores['total_mae'],2)
        self.assertEqual(scores['margin_mae'],6)

if __name__=='__main__':unittest.main()
