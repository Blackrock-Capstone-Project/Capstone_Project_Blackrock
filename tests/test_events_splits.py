"""Index-rebalance event list, half days, AJEXDI split conversion (incl. real-data cases)."""
import glob
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from auction_data import splits
from auction_data.build import daily_clean
from auction_data.events import load_events, masked_sessions
from auction_data.settings import PipelineError, ROOT
from auction_data.trading_calendar import half_days


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
        with self.assertRaises(PipelineError):
            load_events(path)


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


if __name__ == '__main__':
    unittest.main()
