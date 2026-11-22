"""Checkpoints, field dictionary, end-to-end outputs, determinism, event rebuild, deferral."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from auction_data import wrds_queries as sources
from auction_data import process_data
from auction_data.build import checkpoint_rows
from auction_data.dictionary import field_dictionary
from auction_data.events import EVENTS_PATH
from auction_data.storage import digest
from auction_data.trading_calendar import close_time
from tests.helpers import history_frame, WrdsEnv, START


class CheckpointAndDictionaryTests(unittest.TestCase):
    def test_half_day_checkpoints(self):
        days = ['2025-11-26', '2025-11-28']
        g = pd.DataFrame({'security_id': 'S', 'date': days, 'match_status': 'unique',
                          'cumvol_10:00': [1., 1.], 'cumvol_12:00': [2., 2.], 'cumvol_14:00': [3., np.nan],
                          'cumvol_15:00': [4., np.nan], 'cumvol_15:30': [5., np.nan], 'cumvol_15:50': [4., np.nan]})
        out = checkpoint_rows(g, {d: close_time(d) for d in days})
        half = out[out.date.eq('2025-11-28')]
        self.assertEqual(half.loc[half.checkpoint_status.eq('applicable'), 'checkpoint'].tolist(), ['10:00', '12:00'])
        self.assertFalse(half.nonmonotone.any())
        self.assertTrue(out[out.date.eq('2025-11-26')].nonmonotone.all())  # 15:30 -> 15:50 decreases
        self.assertEqual(len(out), 12)

    def test_post_close_fields(self):
        f = field_dictionary().set_index('field').available_at
        for name in ['label', 'auction_volume', 'continuous_volume', 'trf_close_volume', 'PX_VOLUME', 'n_prints']:
            self.assertEqual(f[name], 't post-close', name)
        self.assertEqual(f['cumulative_volume'], 't intraday')
        self.assertEqual(f['pit_n_anomaly'], 't-1 close')


class EndToEndTests(WrdsEnv, unittest.TestCase):
    """End-to-end run on mocked WRDS sources."""

    def test_outputs_report_determinism_and_event_rebuild(self):
        self.assertTrue(self.update()['complete'])
        out = self.root / 'processed'
        for name in ['us_wrds_quality/research_sample.csv.gz', 'us_wrds_taq/checkpoints_research.csv.gz',
                     'reference/index_events.csv', 'reference/field_dictionary.csv', 'report/funnel.csv',
                     'report/daily_counts.csv', 'report/daily_counts.png', 'report/summary.json']:
            self.assertTrue((out / name).exists(), name)
        sample = pd.read_csv(out / 'us_wrds_quality/research_sample.csv.gz')
        self.assertIn('pit_eligible', sample)
        self.assertIn('adjustment_factor', sample)
        funnel = pd.read_csv(out / 'report/funnel.csv').set_index('step')
        quality = pd.read_csv(out / 'us_wrds_quality/stock_day_quality.csv.gz')
        self.assertEqual(funnel.loc['eligible_current_day_valid', 'stock_days'], int(quality.eligible.sum()))
        self.assertEqual(funnel.loc['with_listing_boundary', 'stock_days'], len(quality))
        cps = pd.read_csv(out / 'us_wrds_taq/checkpoints_research.csv.gz')
        self.assertEqual(len(cps), 6 * len(quality))
        first = digest(out / 'us_wrds_quality/research_sample.csv.gz')
        # Same input on a copy of the raw data -> byte-identical output.
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / 'Data'
            shutil.copytree(self.root / 'raw', copy / 'raw')
            shutil.copy(self.root / 'pipeline_plan.json', copy / 'pipeline_plan.json')
            self.assertFalse(process_data(copy)['reused'])
            self.assertEqual(digest(copy / 'processed/us_wrds_quality/research_sample.csv.gz'), first)
        # Editing the event list changes the fingerprint and rebuilds.
        events = pd.read_csv(EVENTS_PATH, dtype=str, keep_default_na=False)
        events.loc[len(events)] = ['msci_index_review', 'MSCI', '', '2022-01-14', '2022-01-18', 'NYSE|NASDAQ', 'u', 'false', 'test']
        path = Path(self.temp.name) / 'events.csv'
        events.to_csv(path, index=False)
        result = process_data(self.root, events_path=path)
        self.assertFalse(result['reused'])
        q = pd.read_csv(self.root / 'processed/us_wrds_quality/stock_day_quality.csv.gz', keep_default_na=False)
        self.assertEqual(q.loc[q.date.eq('2022-01-14'), 'exclusion_reason'].tolist(), ['event_day'])
        summary = json.loads((self.root / 'processed/report/summary.json').read_text())
        self.assertEqual(summary['diff_vs_previous']['previous'], 'compared')
        self.assertEqual(summary['diff_vs_previous']['removed'], 1)


class DeferralEndToEndTests(WrdsEnv, unittest.TestCase):
    def test_deferred_security_does_not_block_completion_and_counts_align(self):
        extra = pd.DataFrame({'gvkey': ['000002'], 'iid': ['01'], 'item': ['EXCHG'], 'itemvalue': ['14'],
                              'efffrom': [START], 'effthru': ['9999-12-31']})
        self.mocks[3].side_effect = lambda db, a, b, m: pd.concat([history_frame(a, b), extra], ignore_index=True)
        r = self.update()
        self.assertTrue(r['complete'], r)
        self.assertEqual(r['processing']['quality_status'], 'complete')
        self.assertEqual(r['processing']['deferred_securities'], 1)
        self.assertEqual(r['processing']['deferred_reasons'], {'unknown_security_type': 1})
        out = self.root / 'processed'
        sec = pd.read_csv(out / 'us_wrds_quality/security_quality.csv', dtype={'security_id': str}, keep_default_na=False)
        self.assertEqual(sec.set_index('security_id').loc['000002:01', 'research_status'], 'deferred')
        sample = pd.read_csv(out / 'us_wrds_quality/research_sample.csv.gz', dtype={'security_id': str})
        self.assertNotIn('000002:01', set(sample.security_id))
        self.assertTrue((sample.research_included_expost | sample.research_included_pit).all())
        summary = pd.read_csv(out / 'report/security_status_summary.csv').set_index('category').securities
        self.assertEqual(summary['all_securities'], len(sec))
        self.assertEqual(summary[['non_common_stock', 'outside_window', 'security_type_unknown', 'listing_boundary_missing',
                                  'confirmed_common_stock']].sum(), summary['all_securities'])
        self.assertEqual(summary[['retained', 'excluded_gt_5pct', 'unresolved']].sum(), summary['confirmed_common_stock'])
        funnel = pd.read_csv(out / 'report/funnel.csv').set_index('step')
        self.assertEqual(funnel.loc['confirmed_common_stock', 'securities'], summary['confirmed_common_stock'])


if __name__ == '__main__':
    unittest.main()
