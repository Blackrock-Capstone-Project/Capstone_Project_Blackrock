import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
import psycopg2

from scripts.data_pipeline import update_data, process_data, audit_data
from scripts.data_pipeline import auth, sources
from scripts.data_pipeline.common import (PipelineError, completed_sessions, close_time, sessions,
    writer_lock, read_json, read_partition, save_json, write_partition)
from scripts.data_pipeline.pipeline import configuration, plan_chunks
from scripts.data_pipeline.quality import score_grid, security_grids
from scripts.data_pipeline.taq_sql import day_sql


def daily_frame(start,end,markets=('NYSE','NASDAQ')):
    dates=sessions(start,end)
    d=pd.DataFrame({c:np.ones(len(dates)) for c in sources.DAILY_COLUMNS})
    d['gvkey']='000001';d['iid']='01';d['datadate']=dates
    d['historical_exchg']='11' if 'NYSE' in markets else '14'
    d['historical_tic']='NA';d['tic']='NA';d['cusip']='001234567';d['tpci']='0'
    d['curcdd']='USD';d['prcstd']=3.;d['cshtrd']=1000.;d['cshoc']=500.;d['prccd']=10.
    d['adrrc']=np.nan
    return d


def taq_frame(day):
    d=pd.DataFrame({'date':[day], 'taq_symbol':['NA'], 'listing_ex':['N'],
        'auction_volume':[100.], 'auction_volume_all':[100.], 'xvol_n':[100.], 'xvol_q':[np.nan],
        'xvol_p':[np.nan], 'xvol_a':[np.nan], 'xvol_z':[np.nan], 'auction_price':[10.], 'continuous_volume':[900.],
        'n_prints':[1], 'trf_close_volume':[0.]})
    for i,cp in enumerate(sources.CHECKPOINTS):
        if cp+':00'<=close_time(day):d['cumvol_'+cp]=100.*i
    return d


def history_frame(start,end,*args):
    return pd.DataFrame({'gvkey':['000001','000001'],'iid':['01','01'],
        'item':['EXCHG','TIC'],'itemvalue':['11','NA'],
        'efffrom':[start,start],'effthru':[end,end]})


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.start='2022-01-03';self.end='2022-02-01'
        self.patches=[patch.object(sources,'discover',side_effect=lambda db,a,b,m:{'daily':sessions(a,b),'taq':sessions(a,b)}),
            patch.object(sources,'download_daily',side_effect=lambda db,a,b,m:daily_frame(a,b,m)),
            patch.object(sources,'download_taq',side_effect=lambda db,d:taq_frame(d)),
            patch.object(sources,'download_history',side_effect=lambda db,a,b,m:history_frame(a,b))]
        self.mocks=[p.start() for p in self.patches]
        self.addCleanup(lambda:[p.stop() for p in self.patches])
        self.addCleanup(self.temp.cleanup)

    def update(self,**kwargs):
        return update_data(self.start,kwargs.pop('end',self.end),data_dir=self.root,
            connection=object(),refresh_sessions=kwargs.pop('refresh_sessions',0),**kwargs)

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
        with patch('scripts.data_pipeline.pipeline.runtime_credentials',side_effect=auth.LoginCancelled('cancelled')):
            result=update_data(self.start,self.end,data_dir=self.root)
            self.assertEqual(result['status'],'cancelled')
        conn=unittest.mock.MagicMock()
        with patch('scripts.data_pipeline.pipeline.connect',return_value=conn):
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
        with patch('scripts.data_pipeline.processing.verify_table',side_effect=PipelineError('synthetic')):
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
        result=subprocess.run([sys.executable,'-m','scripts.data_pipeline','audit','--data-dir',str(self.root)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),audit_data(self.root))

    def test_nyse_scope_and_bad_config(self):
        self.assertTrue(self.update(markets=('NYSE',))['complete'])
        self.assertEqual(self.mocks[1].call_args[0][-1],['NYSE'])
        with self.assertRaises(ValueError):self.update(markets=('LSE',))


class QualityTests(unittest.TestCase):
    def grid(self,n=100):
        return pd.DataFrame({'date':range(n),'expected':True,'PX_VOLUME':100.,'label':10.,'match_status':'unique'})

    def test_threshold_and_either_volume(self):
        for count,status in [(5,'retained'),(6,'excluded')]:
            g=self.grid();g.loc[:count-1,'label']=0
            q,s=score_grid(g)
            self.assertEqual(s['security_status'],status)
            self.assertEqual(s['expected_days'],100)
            self.assertEqual(s['abnormal_days'],count)
        for col in ['PX_VOLUME','label']:
            for value in [0,-1,np.nan,np.inf,-np.inf]:
                g=self.grid();g.loc[20,col]=value
                q,s=score_grid(g)
                self.assertFalse(q.loc[20,'valid_day'])
                self.assertEqual(s['abnormal_days'],1)

    def test_window_current_day_short_history_and_order(self):
        g=self.grid()
        g.loc[0:4,'label']=0
        q,s=score_grid(g)
        self.assertEqual(q.loc[10,'prior_10_valid_days'],5)
        self.assertEqual(q.loc[11,'prior_10_valid_days'],6)
        self.assertFalse(q.loc[10,'eligible']);self.assertTrue(q.loc[11,'eligible'])
        original=q.loc[20,'prior_10_valid_days']
        g.loc[20,'PX_VOLUME']=0
        changed,s=score_grid(g)
        self.assertEqual(changed.loc[20,'prior_10_valid_days'],original)
        self.assertFalse(changed.eligible.any()) # 6/100 means entire stock removed, before row filtering
        q,_=score_grid(self.grid(6))
        self.assertFalse(q.eligible.any())

    def test_boundaries_missing_records_ambiguous_and_unresolved(self):
        start,end='2022-01-03','2022-02-01'
        raw=daily_frame(start,end)
        from scripts.data_pipeline.processing import daily_clean
        daily=daily_clean(raw)
        taq=pd.concat([taq_frame(d) for d in sessions(start,end)],ignore_index=True)
        hist=history_frame('2022-01-05','2022-01-28')
        # A missing row inside effective boundaries must still count.
        daily=daily[daily.date.ne('2022-01-10')]
        sid,q,s=next(security_grids(daily,taq,hist,start,end,['NYSE']))
        self.assertEqual(q.date.min(),'2022-01-05');self.assertEqual(q.date.max(),'2022-01-28')
        self.assertFalse(q.loc[q.date.eq('2022-01-10'),'valid_day'].iloc[0])
        self.assertEqual(s['expected_days'],len(sessions('2022-01-05','2022-01-28')))
        sid,q,s=next(security_grids(daily,taq,hist.iloc[:0],start,end,['NYSE']))
        self.assertIsNone(q);self.assertEqual(s['security_status'],'unresolved')
        hist=pd.concat([hist,hist.iloc[:1]],ignore_index=True)
        sid,q,s=next(security_grids(daily,taq,hist,start,end,['NYSE']))
        self.assertEqual(s['security_status'],'unresolved')


class AuthenticationAndCalendarTests(unittest.TestCase):
    def test_no_tty_and_cancel(self):
        with patch('sys.stdin.isatty',return_value=False):
            with self.assertRaises(PipelineError):auth.connect()
        with patch('sys.stdin.isatty',return_value=True),patch('builtins.input',side_effect=EOFError):
            with self.assertRaises(PipelineError):auth.connect()

    def test_native_connection_and_no_saved_password(self):
        with patch('psycopg2.connect') as connector:
            conn=auth.connect(credentials_provider=lambda:{'user':'synthetic','password':'synthetic-secret'})
            self.assertTrue(conn.autocommit)
            self.assertEqual(connector.call_args.kwargs['password'],'synthetic-secret')
        with patch('psycopg2.connect',side_effect=psycopg2.OperationalError('password=secret user=private')) as connector:
            with self.assertRaises(PipelineError) as e:
                auth.connect(credentials_provider=lambda:{'user':'synthetic','password':'secret'})
            self.assertNotIn('secret',str(e.exception));self.assertEqual(connector.call_count,2)

    def test_db_retry_and_sanitize_permission(self):
        conn=unittest.mock.MagicMock()
        conn.cursor.side_effect=psycopg2.OperationalError('private password')
        replacement=unittest.mock.MagicMock()
        cur=replacement.cursor.return_value.__enter__.return_value
        cur.description=[('x',)];cur.fetchall.return_value=[(1,)]
        reconnect=unittest.mock.Mock(return_value=replacement)
        self.assertEqual(auth.Database(conn,reconnect).query('select 1').x.tolist(),[1])
        reconnect.assert_called_once()
        conn.cursor.side_effect=psycopg2.ProgrammingError('denied private')
        with self.assertRaises(PipelineError) as e:auth.Database(conn).query('select 1')
        self.assertNotIn('private',str(e.exception))

    def test_source_discovery_and_query_contract(self):
        db=unittest.mock.Mock()
        def query(sql,params=None):
            if 'SELECT DISTINCT' in sql:
                return pd.DataFrame({'date':['2022-01-03']})
            if 'pg_catalog.pg_class' in sql:
                self.assertIn("'f'",sql);self.assertIn("'v'",sql)
                return pd.DataFrame({'schemaname':['taqm_2022'],'tablename':['ctm_20220103']})
            return pd.DataFrame()
        db.query.side_effect=query
        self.assertEqual(sources.discover(db,'2022-01-03','2022-01-03',['NYSE']),
                         {'daily':['2022-01-03'],'taq':['2022-01-03']})
        sources.download_daily(db,'2022-01-03','2022-01-03',['NYSE'])
        sql,params=db.query.call_args.args
        self.assertEqual(params[0],('11',));self.assertIn('sec_idhist',sql)
        d=daily_frame('2022-01-03','2022-01-03')
        with self.assertRaises(PipelineError):
            sources.validate(pd.concat([d,d]),'daily','2022-01-03','2022-01-03',['NYSE'])

    def test_year_boundary_calendar_dates_and_holiday_only_intervals(self):
        self.assertEqual(len(sessions('2022-12-01','2022-12-31')),21)
        january=sessions('2023-01-01','2023-01-31')
        self.assertEqual(january[0],'2023-01-03')
        self.assertEqual(len(january),20)
        self.assertEqual(sessions('2023-01-01','2023-01-02'),[])
        self.assertEqual(sessions('2022-12-31','2023-01-02'),[])
        self.assertEqual(sessions('2022-12-30','2023-01-03'),['2022-12-30','2023-01-03'])

    def test_full_2022_to_2026_chunk_plan(self):
        config=configuration('2022-01-03','2026-10-16',('NYSE','NASDAQ'),0)
        dates=config['completed_sessions']
        self.assertEqual(len(dates),1202)
        with tempfile.TemporaryDirectory() as tmp:
            chunks=plan_chunks(Path(tmp),config,{'daily':dates,'taq':dates})
        daily=[c for c in chunks if c['kind']=='daily']
        taq=[c for c in chunks if c['kind']=='taq']
        self.assertEqual(len(daily),58)
        self.assertEqual(len(taq),1202)
        self.assertEqual(set(d for c in daily for d in c['expected_sessions']),set(dates))
        self.assertEqual(sum(len(c['expected_sessions']) for c in daily),1202)
        self.assertEqual(len(chunks),58+1202+1)

    def test_calendar_half_day_unfinished_and_sql(self):
        self.assertNotIn('2025-01-09',sessions('2025-01-08','2025-01-10'))
        self.assertEqual(close_time('2026-11-27'),'13:00:00')
        self.assertEqual(completed_sessions('2026-11-27','2026-11-27',now='2026-11-27T17:00:00Z'),[])
        self.assertEqual(completed_sessions('2026-11-27','2026-11-27',now='2026-11-27T18:31:00Z'),['2026-11-27'])
        sql=day_sql('20261127')
        self.assertIn('13:05:00',sql);self.assertNotIn('cumvol_15:50',sql)


if __name__=='__main__':unittest.main()
