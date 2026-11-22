"""update / process / audit runs on mocked WRDS: resume, refresh, failures, privacy, lock, CLI."""
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

import pandas as pd
import psycopg2

from auction_data import wrds_auth as auth
from auction_data import wrds_queries as sources
from auction_data import audit_data, process_data, update_data
from auction_data.build import daily_clean
from auction_data.settings import PipelineError
from auction_data.storage import read_json, read_partition, save_json, writer_lock
from auction_data.trading_calendar import sessions
from tests.helpers import daily_frame, taq_frame, history_frame, WrdsEnv


class PipelineTests(WrdsEnv, unittest.TestCase):
    def test_end_to_end_no_bloomberg_and_reuse(self):
        r=self.update()
        self.assertTrue(r['complete'],r)
        self.assertTrue(audit_data(self.root)['complete'])
        out=self.root/'processed'
        d=pd.read_csv(out/'us_wrds_daily/daily_clean.csv.gz',keep_default_na=False)
        self.assertEqual(set(d.ticker),{'NA'})
        self.assertEqual(set(d.CUR_MKT_CAP),{5000.})
        q=pd.read_csv(out/'us_wrds_quality/stock_day_quality.csv.gz')
        self.assertEqual(q.prior_10_valid_days.tolist()[:7],[0,1,2,3,4,5,6])
        self.assertEqual(int(q.eligible.sum()),len(q)-6)
        n=self.mocks[1].call_count
        r=self.update()
        self.assertTrue(r['complete'],r)
        self.assertEqual(self.mocks[1].call_count,n)
        self.assertTrue(r['processing']['reused'])
        self.assertTrue(process_data(self.root)['reused'])
        self.assertFalse((self.root/'raw/bloomberg').exists())

    def test_hole_corruption_legacy_and_refresh(self):
        self.assertTrue(self.update()['complete'])
        path=self.root/'raw/taq/days/20220104.csv.gz'
        path.unlink()
        damaged=self.root/'raw/taq/days/20220105.csv.gz'
        damaged.write_bytes(b'corrupt')
        legacy=self.root/'raw/taq/days/20220106.csv.gz.json';legacy.unlink()
        before=self.mocks[2].call_count
        self.assertTrue(self.update()['complete'])
        self.assertEqual(self.mocks[2].call_count-before,3)
        before=self.mocks[2].call_count
        self.assertTrue(self.update(refresh_sessions=5)['complete'])
        self.assertEqual(self.mocks[2].call_count-before,5)

    def test_partial_month_union(self):
        self.assertTrue(self.update(end='2022-01-10')['complete'])
        self.assertTrue(self.update(end='2022-01-14')['complete'])
        f,m=read_partition(self.root/'raw/wrds/daily/months/202201.csv.gz')
        self.assertEqual(set(f.datadate),set(sessions(self.start,'2022-01-14')))
        self.assertEqual(m['request']['start'],self.start)

    def test_narrow_request_preserves_previous_month_history(self):
        self.assertTrue(self.update()['complete'])
        previous=self.root/'raw/wrds/daily/months/202201.csv.gz'
        original,_=read_partition(previous)
        # A later small-interval smoke test must not truncate the full January partition.
        result=update_data('2022-01-10','2022-01-14',data_dir=self.root,
            connection=object(),refresh_sessions=0,markets=('NYSE',))
        self.assertTrue(result['complete'],result)
        current,meta=read_partition(previous)
        self.assertEqual(set(current.datadate),set(original.datadate))
        self.assertEqual(meta['request']['markets'],['NASDAQ','NYSE'])

    def test_owned_login_cancel_and_credentials_not_in_report(self):
        with patch('auction_data.pipeline.runtime_credentials',side_effect=auth.LoginCancelled('cancelled')):
            result=update_data(self.start,self.end,data_dir=self.root)
            self.assertEqual(result['status'],'cancelled')
        conn=unittest.mock.MagicMock()
        with patch('auction_data.pipeline.connect',return_value=conn):
            result=update_data(self.start,self.end,data_dir=self.root,
                credentials_provider=lambda:{'user':'synthetic','password':'fake-password'})
        self.assertTrue(result['complete'],result)
        conn.close.assert_called_once()
        self.assertNotIn('fake-password',json.dumps(result))

    def test_failure_keeps_old_generation_and_recovery(self):
        self.assertTrue(self.update()['complete'])
        pointer=(self.root/'processed/wrds_current').readlink()
        self.mocks[2].side_effect=lambda db,d:pd.DataFrame()
        r=self.update(refresh_sessions=1)
        self.assertFalse(r['complete'])
        self.assertEqual((self.root/'processed/wrds_current').readlink(),pointer)
        self.assertFalse(audit_data(self.root)['complete'])
        with self.assertRaises(PipelineError):process_data(self.root)
        self.mocks[2].side_effect=lambda db,d:taq_frame(d)
        self.assertTrue(self.update()['complete'])

    def test_processing_failure_keeps_old_pointer(self):
        self.assertTrue(self.update()['complete'])
        pointer=(self.root/'processed/wrds_current').readlink()
        with patch('auction_data.publish.verify_table',side_effect=PipelineError('synthetic')):
            self.mocks[1].side_effect=lambda db,a,b,m:daily_frame(a,b,m).assign(cshtrd=1001.)
            self.assertFalse(self.update(refresh_sessions=1)['complete'])
        self.assertEqual((self.root/'processed/wrds_current').readlink(),pointer)

    def test_missing_entire_chunk_and_inside_block(self):
        self.assertTrue(self.update()['complete'])
        plan=read_json(self.root/'pipeline_plan.json')
        plan['chunks']=[c for c in plan['chunks'] if not c['path'].endswith('20220104.csv.gz')]
        save_json(self.root/'pipeline_plan.json',plan)
        audit=audit_data(self.root)
        self.assertFalse(audit['complete'])
        self.assertIn('missing_entire_chunk',{f['status'] for f in audit['failures']})
        self.mocks[1].side_effect=lambda db,a,b,m:daily_frame(a,b,m).iloc[1:]
        self.assertFalse(self.update(refresh_sessions=1)['complete'])

    def test_publication_lag_and_internal_advertised_gap(self):
        dates=sessions(self.start,self.end)
        self.mocks[0].side_effect=lambda *args:{'daily':dates,'taq':dates[:-1]}
        r=self.update()
        self.assertFalse(r['complete'])
        self.assertEqual(r['common_end'],dates[-2])
        self.assertEqual(r['sources']['taq']['pending_sessions'],[dates[-1]])
        self.mocks[0].side_effect=lambda *args:{'daily':dates[:2]+dates[3:],'taq':dates}
        r=self.update(refresh_sessions=5)
        self.assertFalse(r['complete'])
        self.assertEqual(r['sources']['daily']['missing_sessions'],[dates[2]])

    def test_sentinel_history_date_and_revised_global_exclusion(self):
        self.mocks[3].side_effect=lambda db,a,b,m:history_frame(a,'9999-12-31')
        self.assertTrue(self.update()['complete'])
        self.mocks[2].side_effect=lambda db,d:taq_frame(d).assign(xvol_n=0. if d in sessions(self.start,self.end)[:2] else 100.)
        result=self.update(refresh_sessions=100)
        self.assertTrue(result['complete'],result)
        q=pd.read_csv(self.root/'processed/us_wrds_quality/security_quality.csv')
        self.assertEqual(q.security_status.iloc[0],'excluded')
        eligible=pd.read_csv(self.root/'processed/us_wrds_quality/eligible_stock_days.csv.gz')
        self.assertTrue(eligible.empty)

    def test_source_permission_diagnostic_survives_without_credentials(self):
        class PermissionDenied(psycopg2.ProgrammingError):
            @property
            def pgcode(self):
                return '42501'
        query_error = auth.QueryError(PermissionDenied('user=private password=secret'))
        self.mocks[0].side_effect = sources.SourceDiscoveryError('taq_select_permission_2022',query_error)
        result = self.update()
        self.assertEqual(result['stage'],'source_discovery')
        self.assertEqual(result['failure_detail']['sqlstate'],'42501')
        self.assertEqual(result['failure_detail']['operation'],'taq_select_permission_2022')
        self.assertIn('login itself succeeded',result['error'])
        self.assertNotIn('private',json.dumps(result))
        self.assertNotIn('secret',json.dumps(result))

    def test_source_failure_does_not_leak(self):
        self.mocks[0].side_effect=Exception('secret-user secret-password host=private')
        r=self.update()
        self.assertFalse(r['complete'])
        self.assertNotIn('secret',json.dumps(r))
        self.assertNotIn('secret',(self.root/'update_report.json').read_text())

    def test_lock_and_cli_audit_equivalence(self):
        with writer_lock(self.root):
            with self.assertRaises(PipelineError):self.update()
        self.assertTrue(self.update()['complete'])
        result=subprocess.run([sys.executable,'-m','auction_data','audit','--data-dir',str(self.root)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),audit_data(self.root))

    def test_nyse_scope_and_bad_config(self):
        self.assertTrue(self.update(markets=('NYSE',))['complete'])
        self.assertEqual(self.mocks[1].call_args[0][-1],['NYSE'])
        with self.assertRaises(ValueError):self.update(markets=('LSE',))


if __name__ == '__main__':
    unittest.main()
