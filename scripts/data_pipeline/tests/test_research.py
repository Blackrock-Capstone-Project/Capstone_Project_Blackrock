"""Tests for the closing-auction-processing change: universe, label, masking, PIT, splits, checkpoints, report."""
import glob
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from scripts.data_pipeline import process_data, sources
from scripts.data_pipeline import common, splits
from scripts.data_pipeline.common import RAW_VERSION, PROCESS_VERSION, ROOT, digest, read_partition, sessions, write_partition
from scripts.data_pipeline.dictionary import field_dictionary
from scripts.data_pipeline.events import EVENTS_PATH, half_days, load_events, masked_sessions
from scripts.data_pipeline.processing import checkpoint_rows, daily_clean
from scripts.data_pipeline.quality import score_grid, security_grids
from scripts.data_pipeline.tests import test_pipeline as tp
from scripts.data_pipeline.tests.test_pipeline import daily_frame, history_frame, taq_frame

START, END = '2022-01-03', '2022-02-01'
DATES = sessions(START, END)


def daily_for(ticker='NA', tpci='0', exch='11', gvkey='000001', dates=DATES):
    raw = daily_frame(dates[0], dates[-1])
    raw = raw[raw.datadate.isin(dates)].copy()
    raw['gvkey'] = gvkey; raw['historical_tic'] = ticker; raw['tic'] = ticker
    raw['tpci'] = tpci; raw['historical_exchg'] = exch
    return splits.annotate(daily_clean(raw))[0]


def history(gvkey='000001', ticker='NA', exch=(('11', START, '9999-12-31'),)):
    rows = [(gvkey, '01', 'EXCHG', e, a, b) for e, a, b in exch]
    rows.append((gvkey, '01', 'TIC', ticker, START, '9999-12-31'))
    return pd.DataFrame(rows, columns=['gvkey', 'iid', 'item', 'itemvalue', 'efffrom', 'effthru'])


def taq_for(symbol='NA', venue='xvol_n', dates=DATES, **override):
    t = pd.concat([taq_frame(d) for d in dates], ignore_index=True)
    t['taq_symbol'] = symbol
    t[['xvol_n', 'xvol_q']] = np.nan
    t[venue] = 100.
    for k, v in override.items():
        t[k] = v
    return t


def grid(daily, taq, hist, masks=None, end=END):
    return [(sid, g, s) for sid, g, s in security_grids(daily, taq, hist, START, end, ['NYSE', 'NASDAQ'], masks)]


class VersionTests(unittest.TestCase):
    def test_raw_version_unchanged_and_independent_of_process_version(self):
        self.assertEqual(RAW_VERSION, 'wrds-v1')
        self.assertNotEqual(PROCESS_VERSION, RAW_VERSION)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'x.csv.gz'
            write_partition(path, pd.DataFrame({'a': [1]}), {'k': 1})
            self.assertEqual(json.loads(Path(str(path) + '.json').read_text())['version'], 'wrds-v1')
            with patch.object(common, 'PROCESS_VERSION', 'wrds-process-v999'):
                frame, _ = read_partition(path, {'k': 1})
            self.assertEqual(len(frame), 1)


class EventTests(unittest.TestCase):
    def write_events(self, rows):
        tmp = tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False)
        pd.DataFrame(rows, columns=['event_family', 'index', 'announce_date', 'execution_close_date', 'effective_date',
                                    'markets', 'source_url', 'verified', 'notes']).to_csv(tmp.name, index=False)
        self.addCleanup(Path(tmp.name).unlink)
        return tmp.name

    def test_repository_list_loads(self):
        e = load_events()
        self.assertGreaterEqual(len(e), 40)
        self.assertIn('2026-06-18', set(e.execution_close_date))   # Juneteenth-shifted S&P rebalance
        self.assertIn('2025-08-26', set(e.execution_close_date))   # MSCI not at month end

    def test_same_day_events_masked_once_and_half_day(self):
        path = self.write_events([
            ['sp_quarterly', 'S&P', '', '2022-12-16', '2022-12-19', 'NYSE|NASDAQ', 'u', 'true', ''],
            ['nasdaq100_annual', 'NDX', '', '2022-12-16', '2022-12-19', 'NYSE|NASDAQ', 'u', 'true', '']])
        m = masked_sessions(load_events(path), '2022-11-01', '2022-12-31', ['NYSE', 'NASDAQ'])
        self.assertEqual(len(m[m.date.eq('2022-12-16')]), 2)  # one row per market
        self.assertEqual(set(m.loc[m.date.eq('2022-11-25'), 'mask_reason']), {'half_day'})
        self.assertEqual(half_days('2025-11-01', '2025-11-30'), {'2025-11-28': '13:00:00'})

    def test_non_session_rejected(self):
        path = self.write_events([['sp_quarterly', 'S&P', '', '2026-06-19', '2026-06-22', 'NYSE', 'u', 'true', '']])
        with self.assertRaises(common.PipelineError):
            load_events(path)


class UniverseAndLabelTests(unittest.TestCase):
    def test_class_share_mapping(self):
        out = grid(daily_for('BRK.B'), taq_for('BRK/B'), history(ticker='BRK.B'))
        sid, g, s = out[0]
        self.assertTrue(g.match_status.eq('unique').all())
        self.assertTrue(g.label.eq(100).all())

    def test_non_common_excluded(self):
        for code in ['R', '%', 'F', '7']:
            sid, g, s = grid(daily_for(tpci=code), taq_for(), history())[0]
            self.assertIsNone(g)
            self.assertEqual(s['security_status'], 'non_common_stock')
            self.assertEqual(s['reason'], 'non_common_stock:' + code)

    def test_other_venue_is_missing_label_not_fallback(self):
        t = taq_for(venue='xvol_p', listing_ex='P')
        sid, g, s = grid(daily_for(), t, history())[0]
        self.assertTrue(g.label.isna().all())
        self.assertTrue(g.label_status.eq('listing_venue_cross_missing').all())
        self.assertTrue(g.venue_reconciliation.eq('venue_conflict').all())
        self.assertTrue(g.match_status.eq('unique').all())
        self.assertEqual(s['security_status'], 'excluded')

    def test_cross_outside_known_venues(self):
        t = taq_for(); t[['xvol_n']] = np.nan; t['auction_volume_all'] = 500.
        sid, g, s = grid(daily_for(), t, history())[0]
        self.assertTrue(g.label_status.eq('cross_outside_known_venues').all())

    def test_transfer_between_target_markets(self):
        cut = DATES[10]
        daily = daily_for()
        daily.loc[daily.date >= cut, 'market'] = 'NASDAQ'
        t = taq_for()
        after = t.date >= cut
        t.loc[after, 'xvol_n'] = np.nan; t.loc[after, 'xvol_q'] = 100.
        hist = history(exch=(('11', START, DATES[9]), ('14', cut, '9999-12-31')))
        sid, g, s = grid(daily, t, hist)[0]
        self.assertTrue(g.label.eq(100).all())
        self.assertTrue(g.valid_day.all())
        self.assertEqual(g.loc[g.date.eq(DATES[12]), 'prior_10_valid_days'].iloc[0], 10)  # window spans the transfer

    def test_move_to_other_exchange_ends_interval(self):
        cut = DATES[15]
        t = taq_for(); t.loc[t.date >= cut, 'xvol_n'] = np.nan; t.loc[t.date >= cut, 'auction_volume_all'] = 300.
        daily = daily_for(dates=[d for d in DATES if d < cut])  # Compustat N/Q download stops at the move
        hist = history(exch=(('11', START, DATES[14]), ('24', cut, '9999-12-31')))
        sid, g, s = grid(daily, t, hist)[0]
        self.assertEqual(g.date.max(), DATES[14])
        self.assertEqual(s['expected_days'], 15)
        self.assertEqual(s['abnormal_days'], 0)
        self.assertEqual(s['security_status'], 'retained')


class MaskingAndPitTests(unittest.TestCase):
    def frame(self, n=100):
        return pd.DataFrame({'date': range(n), 'in_listing': True, 'mask_reason': '', 'PX_VOLUME': 100.,
                             'label': 10., 'match_status': 'unique', 'security_id': 'S'})

    def test_event_occupies_window_slot(self):
        g = self.frame()
        g.loc[45, 'mask_reason'] = 'event_day'
        g.loc[[41, 42, 43], 'label'] = 0      # window for t=50 is 40..49: 1 masked, 3 invalid -> 6 valid
        q, s = score_grid(g)
        self.assertEqual(q.loc[50, 'prior_10_valid_days'], 6)
        self.assertTrue(q.loc[50, 'eligible'])
        g.loc[44, 'label'] = 0                # -> 5 valid, no backfill from earlier sessions
        q, s = score_grid(g)
        self.assertEqual(q.loc[50, 'prior_10_valid_days'], 5)
        self.assertFalse(q.loc[50, 'eligible'])
        self.assertEqual(q.loc[50, 'exclusion_reason'], 'insufficient_prior_valid_days')

    def test_masked_day_not_in_denominator_and_reported_once(self):
        g = self.frame(100)
        g.loc[30, 'mask_reason'] = 'event_day'; g.loc[30, 'label'] = 0
        q, s = score_grid(g)
        self.assertEqual(s['expected_days'], 99)
        self.assertEqual(s['abnormal_days'], 0)
        self.assertEqual(q.loc[30, 'exclusion_reason'], 'event_day')
        self.assertFalse(q.loc[30, 'eligible'] or q.loc[30, 'pit_eligible'])

    def test_pit_uses_only_past(self):
        g = self.frame(100)
        base, _ = score_grid(g)
        g.loc[70:99, 'label'] = 0
        later, s = score_grid(g)
        self.assertEqual(s['security_status'], 'excluded')
        cols = ['pit_n_expected', 'pit_n_anomaly', 'pit_eligible']
        pd.testing.assert_frame_equal(base.loc[:69, cols], later.loc[:69, cols])
        self.assertTrue(later.loc[20:69, 'pit_eligible'].all())
        self.assertFalse(later.eligible.any())

    def test_pit_small_denominator_and_first_day(self):
        g = self.frame(30)
        g.loc[0, 'label'] = 0
        q, _ = score_grid(g)
        self.assertEqual(q.loc[0, 'pit_n_expected'], 0)
        self.assertFalse(q.loc[0, 'pit_eligible'])
        self.assertFalse(q.loc[11, 'pit_eligible'])   # 1/11 > 5%
        self.assertTrue(q.loc[20, 'pit_eligible'])    # 1/20 == 5%
        self.assertFalse(q.loc[19, 'pit_eligible'])   # 1/19 > 5%


class SplitTests(unittest.TestCase):
    def series(self, factors, prices):
        n = len(factors)
        return pd.DataFrame({'security_id': 'S', 'date': [f'2024-06-{i + 1:02d}' for i in range(n)], 'ticker': 'X',
                             'adjustment_factor': factors, 'PX_LAST': prices, 'source_cshoc': 1000.})

    def test_nvda_direction(self):
        self.assertAlmostEqual(splits.to_basis(40.9e6, 10, 1), 409e6)
        self.assertAlmostEqual(splits.to_basis(40.9e6, 10, 1), splits.to_basis(40.9e6, 20, 2))  # later split cancels

    def test_consistent_and_inconsistent_factor(self):
        d, issues = splits.annotate(self.series([10, 10, 1, 1], [1200., 1210., 121., 122.]))
        self.assertEqual(d.split_status.tolist(), ['ok', 'ok', 'split_applied', 'ok'])
        self.assertEqual(d.split_segment_id.nunique(), 1)
        d, issues = splits.annotate(self.series([10, 10, 1, 1], [300., 300., 100., 100.]))
        self.assertEqual(d.split_status.tolist()[2], 'split_unresolved')
        self.assertEqual(d.split_segment_id.tolist(), [1, 1, 2, 2])
        self.assertIn('split_unresolved', set(issues.issue))

    def test_missing_factor_breaks_segment(self):
        d, _ = splits.annotate(self.series([1, np.nan, 1], [10., 10., 10.]))
        self.assertEqual(d.split_status.tolist(), ['ok', 'factor_missing', 'ok'])
        self.assertEqual(d.split_segment_id.tolist(), [1, 2, 3])

    @unittest.skipUnless(glob.glob(str(ROOT / 'Data/raw/wrds/daily/months/2024*.csv.gz')), 'real WRDS data not present')
    def test_real_split_and_reverse_split_events(self):
        cases = {('NVDA', '2024-06-10'): 'split_applied', ('AVGO', '2024-07-15'): 'split_applied',
                 ('WMT', '2024-02-26'): 'split_applied', ('CMG', '2024-06-26'): 'split_applied',
                 ('SIRI', '2024-09-10'): 'split_applied',   # 1-for-10 reverse split
                 ('AMCR', '2026-01-15'): 'split_applied',   # 1-for-5 reverse split
                 ('HON', '2026-06-29'): 'split_unresolved'}  # spin-off factor: price unchanged, not a share split
        months = sorted({d[:7].replace('-', '') for _, d in cases})
        neighbours = set()
        for m in months:
            y, mo = int(m[:4]), int(m[4:])
            neighbours |= {m, f'{y - (mo == 1)}{(mo - 2) % 12 + 1:02d}'}
        files = [ROOT / f'Data/raw/wrds/daily/months/{m}.csv.gz' for m in sorted(neighbours)]
        raw = pd.concat([pd.read_csv(f, dtype={'gvkey': str, 'iid': str, 'historical_exchg': str, 'historical_tic': str,
                                               'tpci': str, 'cusip': str}) for f in files if f.exists()])
        raw = raw[raw.historical_tic.isin({t for t, _ in cases})]
        d, _ = splits.annotate(daily_clean(raw))
        for (tic, day), expected in cases.items():
            row = d[(d.ticker == tic) & (d.date == day) & d.security_type_code.eq('0')]
            self.assertEqual(row.split_status.tolist(), [expected], (tic, day))
        nvda = d[(d.ticker == 'NVDA')].set_index('date')
        converted = splits.to_basis(nvda.PX_VOLUME['2024-06-07'], nvda.adjustment_factor['2024-06-07'],
                                    nvda.adjustment_factor['2024-06-12'])
        self.assertAlmostEqual(converted / nvda.PX_VOLUME['2024-06-07'], 10.0)


class CheckpointAndDictionaryTests(unittest.TestCase):
    def test_half_day_checkpoints(self):
        days = ['2025-11-26', '2025-11-28']
        g = pd.DataFrame({'security_id': 'S', 'date': days, 'match_status': 'unique',
                          'cumvol_10:00': [1., 1.], 'cumvol_12:00': [2., 2.], 'cumvol_14:00': [3., np.nan],
                          'cumvol_15:00': [4., np.nan], 'cumvol_15:30': [5., np.nan], 'cumvol_15:50': [4., np.nan]})
        out = checkpoint_rows(g, {d: common.close_time(d) for d in days})
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


class EndToEndTests(tp.PipelineTests):
    """Reuses PipelineTests' mocked WRDS sources; only the extra tests below are new."""

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


# Only run the new end-to-end test here; the inherited ones already run in test_pipeline.
for _name in [n for n in dir(tp.PipelineTests) if n.startswith('test_')]:
    setattr(EndToEndTests, _name, None)


if __name__ == '__main__':
    unittest.main()


class DeferralTests(unittest.TestCase):
    def test_ticker_gap_is_deferred_but_strict_pit_kept(self):
        hist = history()
        hist.loc[hist.item.eq('TIC'), 'efffrom'] = DATES[1]   # EXCHG starts before TIC -> 1 day without identity
        sid, g, s = grid(daily_for(), taq_for(), hist)[0]
        self.assertEqual((s['security_status'], s['reason'], s['research_status']),
                         ('unresolved', 'ticker_history_gap', 'deferred'))
        self.assertTrue(g.pit_eligible.any())                  # strict PIT definition untouched
        self.assertFalse(g.research_included_pit.any())        # but not used in this study
        self.assertFalse(g.research_included_expost.any())


class DeferralEndToEndTests(tp.PipelineTests):
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


for _name in [n for n in dir(tp.PipelineTests) if n.startswith('test_')]:
    setattr(DeferralEndToEndTests, _name, None)
