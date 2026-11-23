"""Real-data acceptance for the published feature version (skipped unless AUCTION_FEATURES_REALDATA=1).

50 securities (splits, unresolved splits, missing factors, transfers, relisting gaps, random):
  1. recomputing the sample alone equals the published full build bitwise (isolation, determinism);
  2. truncating the panel at two dates leaves every earlier feature and profile bitwise unchanged;
  3. the row-by-row reference matches: NaN positions equal, integer columns equal, floats |d| <= 1e-12 + 1e-12|ref|.
"""
import os
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from auction_data.settings import ROOT
from auction_features.build import build_tables, load_table
from auction_features.panel import calendar_sessions, load_panel
from auction_data.storage import read_json
from tests.feature_helpers import reference

DATA = ROOT / 'Data'
ENABLED = os.environ.get('AUCTION_FEATURES_REALDATA') == '1' and (DATA / 'features' / 'current').exists()
CUTS = ['2024-06-03', '2025-02-14']
REL = 1e-12


def pick_sample(panel, n=50, seed=7):
    rng = np.random.default_rng(seed)
    chosen = []

    def take(ids, k):
        ids = [i for i in sorted(set(ids)) if i not in chosen]
        chosen.extend(rng.choice(ids, size=min(k, len(ids)), replace=False).tolist() if ids else [])

    take(panel.loc[panel.split_status.eq('split_applied'), 'security_id'], 10)
    take(panel.loc[panel.split_status.eq('split_unresolved'), 'security_id'], 5)
    take(panel.loc[panel.split_status.eq('factor_missing'), 'security_id'], 5)
    take(panel.groupby('security_id').market.nunique().loc[lambda s: s > 1].index, 10)
    cal, sidx = calendar_sessions(panel)
    gap = pd.Series(sidx).groupby(panel.security_id.to_numpy()).apply(lambda s: (np.diff(s.to_numpy()) > 1).any())
    take(gap[gap].index, 5)
    take(panel.security_id.unique(), n - len(chosen))
    return sorted(chosen)


@unittest.skipUnless(ENABLED, 'set AUCTION_FEATURES_REALDATA=1 with a published feature version')
class RealDataAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.version = (DATA / 'features' / 'current').resolve()
        manifest = read_json(cls.version / 'manifest.json')
        full = load_panel(Path(manifest['processed']['dir']))
        cls.sample = pick_sample(full)
        cls.panel = full[full.security_id.isin(cls.sample)].reset_index(drop=True)
        del full
        cls.published = {}
        for name in ['daily', 'intraday', 'profiles']:
            t = load_table(cls.version, name)
            cls.published[name] = t[t.security_id.isin(cls.sample)].reset_index(drop=True)
        cls.recomputed = build_tables(cls.panel, workers=4)

    def test_sample_recompute_equals_published(self):
        for name, df in self.recomputed.items():
            pd.testing.assert_frame_equal(df.reset_index(drop=True), self.published[name], check_exact=True)

    def test_truncation(self):
        keys = ['security_id', 'month']
        for cut in CUTS:
            t = build_tables(self.panel[self.panel.date < cut].reset_index(drop=True), workers=4)
            for name in ['daily', 'intraday']:
                a = self.recomputed[name]
                b = t[name]
                pd.testing.assert_frame_equal(a[a.date < cut].reset_index(drop=True),
                                              b[b.date < cut].reset_index(drop=True), check_exact=True)
            a = self.recomputed['profiles']
            a = a[a.m_date < cut].reset_index(drop=True)
            pd.testing.assert_frame_equal(a, t['profiles'].merge(a[keys], on=keys), check_exact=True)

    def test_reference(self):
        cal, _ = calendar_sessions(self.panel)
        ref_d, ref_i, ref_p = reference(self.panel, cal)
        d = self.recomputed['daily'].set_index(['security_id', 'date'])
        exact = {'a_last_age', 'd_last_age', 'a_n22', 'interval_id'}
        worst = 0.0
        for key, rec in ref_d.items():
            for c, y in rec.items():
                x = d.at[key, c]
                self.assertEqual(np.isnan(x), np.isnan(y), (key, c))
                if not np.isnan(y):
                    if c in exact:
                        self.assertEqual(x, y, (key, c))
                    else:
                        err = abs(x - y)
                        worst = max(worst, err / (1 + abs(y)))
                        self.assertLessEqual(err, REL + REL * abs(y), (key, c))
        mu = self.recomputed['intraday'].query("checkpoint == '14:00'").set_index(['security_id', 'date']).mu_b
        for key, y in ref_i.items():
            if key in mu.index:
                x = mu[key]
                self.assertEqual(np.isnan(x), np.isnan(y), key)
                if not np.isnan(y):
                    self.assertLessEqual(abs(x - y), REL + REL * abs(y), key)
        prof = self.recomputed['profiles'].set_index(['security_id', 'month'])
        for key, rec in ref_p.items():
            row = prof.loc[key]
            self.assertEqual(row.profile_status, rec['profile_status'], key)
            for c in ['n_A', 'n_e', 'n_p']:
                if c in rec:
                    self.assertEqual(row[c], rec[c], (key, c))
            for c in ['pf_lshare', 'pf_dev_std', 'pf_dev_ac1']:
                if c in rec:
                    self.assertLessEqual(abs(row[c] - rec[c]), REL + REL * abs(rec[c]), (key, c))
        print(f'\nreference: {len(ref_d)} daily rows, {len(ref_p)} profiles, worst relative float error {worst:.2e}')


if __name__ == '__main__':
    unittest.main()
