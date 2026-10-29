import importlib.util,json,pathlib,tempfile,unittest,sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
def module(path,name):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

class ArchiveTests(unittest.TestCase):
    def test_captures_are_market_free(self):
        m=module(ROOT/'scripts/model_learning.py','learning_test')
        rows,_=m.collect_local(ROOT,json.loads((ROOT/'learning/config.json').read_text())['project'])
        for row in rows:
            self.assertEqual(set(row),{'event_id','start','frozen_at','model','probabilities','labels','source','cutoff','key','schema_version','expected_scores'})
            self.assertLess(m.timestamp(row['frozen_at']),m.timestamp(row['start']))

class RecoveryTests(unittest.TestCase):
    def test_bad_point_stats_download_keeps_last_good_file(self):
        from unittest.mock import patch
        from tennis_oracle import data
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/f'atp_matches_{data.date.today().year}.csv';p.write_text('LAST GOOD DATA')
            def bad(url,dest):pathlib.Path(dest).write_text('not,a,valid,dataset\n1,2,3,4\n')
            with patch.object(data,'RAW',pathlib.Path(d)),patch.object(data.urllib.request,'urlretrieve',side_effect=bad):
                data.download_sackmann(tours=['atp'],years=[data.date.today().year])
            self.assertEqual(p.read_text(),'LAST GOOD DATA')
    def test_result_catchup_checkpoints_before_later_failure(self):
        from unittest.mock import patch
        from datetime import date
        from tennis_oracle import data
        with tempfile.TemporaryDirectory() as d:
            with patch.object(data,'ESPN_DIR',pathlib.Path(d)),patch.object(data,'fetch_espn_day',side_effect=[{'events':[]},{'events':[]},RuntimeError('outage')]):
                with self.assertRaises(RuntimeError):data.update_espn_results(date(2026,10,1),date(2026,10,2),step_days=1)
            self.assertEqual(json.loads((pathlib.Path(d)/'results.json').read_text()),{})
