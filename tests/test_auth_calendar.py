"""Authentication, source discovery contract, XNYS calendar and raw/process version separation."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import psycopg2

from auction_data import wrds_auth as auth
from auction_data import wrds_queries as sources
from auction_data import settings
from auction_data.pipeline import configuration, plan_chunks
from auction_data.settings import PROCESS_VERSION, PipelineError, RAW_VERSION
from auction_data.storage import read_partition, write_partition
from auction_data.trading_calendar import close_time, completed_sessions, sessions
from auction_data.wrds_queries import day_sql
from tests.helpers import daily_frame


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


class VersionTests(unittest.TestCase):
    def test_raw_version_unchanged_and_independent_of_process_version(self):
        self.assertEqual(RAW_VERSION, 'wrds-v1')
        self.assertNotEqual(PROCESS_VERSION, RAW_VERSION)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'x.csv.gz'
            write_partition(path, pd.DataFrame({'a': [1]}), {'k': 1})
            self.assertEqual(json.loads(Path(str(path) + '.json').read_text())['version'], 'wrds-v1')
            with patch.object(settings, 'PROCESS_VERSION', 'wrds-process-v999'):
                frame, _ = read_partition(path, {'k': 1})
            self.assertEqual(len(frame), 1)


if __name__ == '__main__':
    unittest.main()
