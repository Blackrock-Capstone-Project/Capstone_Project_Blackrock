"""auction_features: windows, usability, intervals, splits, factors, naming, metadata, profiles, invariance, reference."""
import unittest

import numpy as np
import pandas as pd

from auction_data import splits
from auction_features.build import PASSTHROUGH, build_tables
from auction_features.config import CORE_REQUIRED
from auction_features.dictionary import check_columns
from auction_features.windows import last_valid, masked_mean, window_matrix
from tests.feature_helpers import CAL, reference, synth_panel

FEATURES = ['a_last', 'a_last_age', 'a_ma5', 'a_ma22', 'd_last', 'd_last_age', 'd_ma22', 'logmcap_lag1', 'a_lag1',
            'd_lag1', 'a_n22', 'd_n22', 'dow', 'month_end', 'quarter_end', 'opex', 'split_on_t', 'factor_carried',
            'factor_unknown', 'interval_id', 'interval_start', 'core_required_complete']
REL = 1e-12


def tables(panel):
    return build_tables(panel, workers=1)


def resegment(p):
    a, _ = splits.annotate(p.assign(source_cshoc=p.shares_outstanding)[
        ['security_id', 'date', 'adjustment_factor', 'PX_LAST', 'source_cshoc', 'ticker']])
    p = p.copy()
    p['split_status'] = a.split_status.to_numpy()
    p['split_segment_id'] = a.split_segment_id.astype(float).to_numpy()
    return p


def at(df, sid, i):
    return df[(df.security_id == sid) & (df.date == CAL[i])].iloc[0]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.panel = synth_panel()
        cls.t = tables(cls.panel)
        cls.daily = cls.t['daily']

    def assertFramesBitwise(self, a, b):
        pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True), check_exact=True)


class WindowFunctionTests(unittest.TestCase):
    def test_window_matrix_and_last_valid(self):
        m = window_matrix(np.arange(5.), 3, np.nan)
        self.assertTrue(np.isnan(m[0]).all())
        self.assertEqual(m[4].tolist(), [1., 2., 3.])
        vals = np.array([[1., 2., 3.], [4., 5., 6.]])
        mask = np.array([[True, False, False], [False, False, False]])
        v, age = last_valid(vals, mask)
        self.assertEqual((v[0], age[0]), (1., 3.))
        self.assertTrue(np.isnan(v[1]) and np.isnan(age[1]))

    def test_masked_mean_min_periods(self):
        vals = np.ones((2, 22))
        mask = np.zeros((2, 22), bool)
        mask[0, :14] = True
        mask[1, :15] = True
        out, cnt = masked_mean(vals, mask, 15)
        self.assertTrue(np.isnan(out[0]))
        self.assertEqual((out[1], cnt[1]), (1.0, 15))


class ReferenceTests(Base):
    def test_matches_row_by_row_reference(self):
        ref_d, ref_i, ref_p = reference(self.panel)
        d = self.daily.set_index(['security_id', 'date'])
        exact = {'a_last_age', 'd_last_age', 'a_n22', 'interval_id'}
        for key, rec in ref_d.items():
            for c, y in rec.items():
                x = d.at[key, c]
                self.assertEqual(np.isnan(x), np.isnan(y), (key, c))
                if not np.isnan(y):
                    if c in exact:
                        self.assertEqual(x, y, (key, c))
                    else:
                        self.assertLessEqual(abs(x - y), REL + REL * abs(y), (key, c))
        mu = self.t['intraday'].query("checkpoint == '14:00'").set_index(['security_id', 'date']).mu_b
        for key, y in ref_i.items():
            if key in mu.index:
                x = mu[key]
                self.assertEqual(np.isnan(x), np.isnan(y), key)
                if not np.isnan(y):
                    self.assertLessEqual(abs(x - y), REL + REL * abs(y), key)
        prof = self.t['profiles'].set_index(['security_id', 'month'])
        for key, rec in ref_p.items():
            row = prof.loc[key]
            self.assertEqual(row.profile_status, rec['profile_status'], key)
            for c in ['n_A', 'n_e', 'n_p']:
                if c in rec:
                    self.assertEqual(row[c], rec[c], (key, c))
            for c in ['pf_lshare', 'pf_dev_std', 'pf_dev_ac1']:
                if c in rec:
                    self.assertLessEqual(abs(row[c] - rec[c]), REL + REL * abs(rec[c]), (key, c))
        self.assertGreater((self.t['profiles'].profile_status == 'ok').sum(), 10)


class UsabilityTests(Base):
    def test_columns_match_dictionary(self):
        for name, df in self.t.items():
            check_columns(name, df.columns)
        self.assertEqual(len(self.daily), len(self.panel))
        self.assertFalse(self.daily.duplicated(['security_id', 'date']).any())
        self.assertEqual(len(self.t['intraday']), 6 * int(self.panel.cp_row.sum()))

    def test_metadata_passthrough_equals_input(self):
        d = self.daily.set_index(['security_id', 'date'])
        p = self.panel.set_index(['security_id', 'date']).loc[d.index]
        for c in PASSTHROUGH:
            pd.testing.assert_series_equal(d[c], p[c], check_names=False)
        y = np.where(p.valid_day, np.log1p(p.label), np.nan)
        np.testing.assert_array_equal(d.y.to_numpy(), y)
        self.assertTrue((d.core_required_complete == d[CORE_REQUIRED].notna().all(axis=1)).all())
        self.assertTrue((d.expost_split_issue_t == p.split_status.isin(['split_unresolved', 'factor_missing'])).all())

    def test_inclusion_flags_do_not_change_features(self):
        p = self.panel.copy()
        rng = np.random.default_rng(1)
        for c in ['research_included_pit', 'research_included_expost', 'pit_eligible']:
            p[c] = rng.random(len(p)) < 0.5
        p['research_status'] = 'deferred'
        p['security_status'] = 'excluded'
        t = tables(p)
        self.assertFramesBitwise(t['daily'][FEATURES], self.daily[FEATURES])
        self.assertFramesBitwise(t['profiles'], self.t['profiles'])

    def test_masked_slots_occupy_window(self):
        for sid in ['000000:01', '000001:01', '000003:01']:
            r = at(self.daily, sid, 42)          # sessions 40 and 41 are event days
            self.assertTrue(np.isnan(r.a_lag1) and np.isnan(r.d_lag1))
            self.assertGreaterEqual(r.a_last_age, 3)
            self.assertLessEqual(r.a_n22, 20)
        r = at(self.daily, '000000:01', 41)
        self.assertFalse(np.isnan(r.logmcap_lag1))  # prices usable on the masked session 40

    def test_min_periods_relation(self):
        d = self.daily
        self.assertTrue(((d.a_n22 >= 15) == d.a_ma22.notna()).all())
        self.assertTrue(((d.d_n22 >= 15) == d.d_ma22.notna()).all())

    def test_isolation_between_securities(self):
        p = self.panel[self.panel.security_id != '000002:01'].copy()
        q = p.copy()
        q.loc[q.security_id == '000001:01', ['label', 'PX_VOLUME']] *= 7
        a, b = tables(p)['daily'], tables(q)['daily']
        keep = a.security_id != '000001:01'
        self.assertFramesBitwise(a.loc[keep, FEATURES], self.daily.loc[self.daily.security_id.isin(
            a.loc[keep, 'security_id'].unique()), FEATURES])
        self.assertFramesBitwise(a.loc[keep, FEATURES], b.loc[keep, FEATURES])

    def test_transfer_return_and_relisting_intervals(self):
        d = self.daily
        self.assertTrue(at(d, '000001:01', 100).interval_start)
        self.assertEqual(at(d, '000001:01', 100).interval_id, 2)
        self.assertLessEqual(at(d, '000001:01', 101).a_n22, 1)
        self.assertEqual(at(d, '000001:01', 180).interval_id, 3)      # back to NYSE: new interval
        self.assertLessEqual(at(d, '000001:01', 181).a_n22, 1)        # first NYSE interval not reused
        self.assertTrue(at(d, '000002:01', 120).interval_start)       # relisting after a gap
        self.assertLessEqual(at(d, '000002:01', 121).a_n22, 1)


class SplitAndFactorTests(Base):
    def test_two_for_one_split_on_t(self):
        r = at(self.daily, '000003:01', 120)
        self.assertTrue(r.split_on_t)
        s = self.panel[(self.panel.security_id == '000003:01') & self.panel.valid_day &
                       (self.panel.date < CAL[120])].iloc[-1]
        self.assertAlmostEqual(r.a_last, np.log1p(s.label * 2.0), places=12)

    def test_unresolved_split_on_t_is_diagnostic_only(self):
        r = at(self.daily, '000000:01', 150)
        self.assertTrue(r.expost_split_issue_t)
        p = self.panel.copy()
        p.loc[(p.security_id == '000000:01') & (p.date == CAL[150]), 'split_status'] = 'ok'
        self.assertFramesBitwise(tables(p)['daily'][FEATURES], self.daily[FEATURES])
        self.assertLessEqual(at(self.daily, '000000:01', 155).a_n22, 5)  # break at 150 cuts history for t > 150

    def test_missing_factor_carried_and_unknown(self):
        self.assertTrue(at(self.daily, '000003:01', 60).factor_carried)
        p = self.panel.copy()
        p.loc[p.security_id == '000004:01', 'adjustment_factor'] = np.nan
        d = tables(resegment(p))['daily'].query("security_id == '000004:01'")
        self.assertTrue(d.factor_unknown.all())
        self.assertTrue(d[['a_ma22', 'd_ma22', 'a_last']].isna().all().all())
        self.assertTrue(d.logmcap_lag1.iloc[1:].notna().all())

    def test_missing_shares_not_carried(self):
        self.assertTrue(np.isnan(at(self.daily, '000000:01', 31).logmcap_lag1))
        self.assertFalse(np.isnan(at(self.daily, '000000:01', 32).logmcap_lag1))

    def test_consistent_split_invariance(self):
        sid = '000004:01'
        q = self.panel.copy()
        rows = (q.security_id == sid).to_numpy() & (q.date < CAL[160]).to_numpy()
        for c in ['label', 'PX_VOLUME'] + [c for c in q if c.startswith('cv_')]:
            q.loc[rows, c] = q.loc[rows, c] / 2
        q.loc[rows, 'PX_LAST'] *= 2
        q.loc[rows, 'shares_outstanding'] /= 2
        q.loc[q.security_id == sid, 'adjustment_factor'] = np.where(rows[q.security_id == sid], 2.0, 1.0)
        q = resegment(q)
        b, s = self.t, tables(q)
        late = (b['daily'].security_id == sid) & (b['daily'].date >= CAL[160])
        for c in ['a_ma22', 'a_ma5', 'a_last', 'd_ma22', 'd_last', 'logmcap_lag1']:
            np.testing.assert_array_equal(b['daily'].loc[late, c].to_numpy(), s['daily'].loc[late, c].to_numpy())
        x = b['profiles'].query("security_id == @sid and profile_status == 'ok'").set_index('month')
        y = s['profiles'].query("security_id == @sid").set_index('month').loc[x.index]
        self.assertGreater(len(x), 0)
        self.assertTrue((y.profile_status == 'ok').all())
        np.testing.assert_allclose(y.pf_lshare, x.pf_lshare, rtol=REL, atol=REL)
        np.testing.assert_allclose(y.pf_dollar_auc, x.pf_dollar_auc, rtol=REL, atol=REL)
        for c in ['pf_dev_std', 'pf_dev_ac1']:   # ln basis: exact up to floating-point rounding
            np.testing.assert_allclose(y[c], x[c], rtol=REL, atol=REL)


class InvarianceTests(Base):
    def test_truncation_and_future_perturbation(self):
        cut = CAL[170]
        trunc = tables(self.panel[self.panel.date < cut])
        pert = self.panel.copy()
        fut = pert.date >= cut
        pert.loc[fut, ['label', 'PX_VOLUME', 'PX_LAST', 'shares_outstanding']] *= 3.3
        pert.loc[fut, 'adjustment_factor'] = 5.0
        pert.loc[fut, 'mask_reason'] = 'event_day'
        pert.loc[fut, 'market'] = 'NASDAQ'
        pert.loc[fut, 'split_segment_id'] = 99.0
        pert = tables(pert)
        for other in (trunc, pert):
            for name in ['daily', 'intraday']:
                a = self.t[name][self.t[name].date < cut]
                b = other[name][other[name].date < cut]
                self.assertFramesBitwise(a.drop(columns=PASSTHROUGH + ['y'], errors='ignore'),
                                         b.drop(columns=PASSTHROUGH + ['y'], errors='ignore'))
            keys = ['security_id', 'month']
            a = self.t['profiles'][self.t['profiles'].m_date < cut]
            self.assertFramesBitwise(a, other['profiles'].merge(a[keys], on=keys))

    def test_same_day_independence(self):
        p = self.panel.copy()
        sel = p.date == CAL[140]
        p.loc[sel, ['label', 'PX_VOLUME', 'PX_LAST', 'shares_outstanding']] *= 9
        d = tables(p)['daily']
        a = self.daily[self.daily.date == CAL[140]]
        b = d[d.date == CAL[140]]
        self.assertFramesBitwise(a[FEATURES], b[FEATURES])
        self.assertFalse(np.allclose(a.y.dropna(), b.y.dropna()))


class ProfileRuleTests(Base):
    def test_panel_starting_mid_month(self):
        start = CAL[45]                       # not the first session of its month
        self.assertEqual(CAL[44][:7], start[:7])
        t = tables(self.panel[self.panel.date >= start].reset_index(drop=True))
        first = t['profiles'][t['profiles'].month == start[:7]]
        self.assertTrue((first.profile_status == 'not_listed').all())
        self.assertTrue((first.m_date < start).all())
        a = self.daily[self.daily.date >= start]
        for c in ['dow', 'month_end', 'quarter_end', 'opex']:
            np.testing.assert_array_equal(a[c].to_numpy(), t['daily'][c].to_numpy())

    def test_new_listing_needs_history(self):
        prof = self.t['profiles'].query("security_id == '000004:01'").sort_values('month')
        first_ok = prof[prof.profile_status == 'ok'].month.min()
        listed = CAL.index(self.panel.query("security_id == '000004:01'").date.min())
        first_ok_idx = next(i for i, d in enumerate(CAL) if d[:7] == first_ok)
        self.assertGreaterEqual(first_ok_idx - listed, 75)
        self.assertTrue((prof[prof.month < first_ok].profile_status.isin(['insufficient_history', 'not_listed'])).all())

    def test_interval_break_status(self):
        prof = self.t['profiles'].query("security_id == '000001:01'").set_index('month')
        self.assertIn('interval_break', set(prof.profile_status))
        self.assertTrue((prof.loc[prof.profile_status != 'ok', ['pf_lshare', 'pf_dev_std', 'pf_dev_ac1']]
                         .isna().all().all()))

    def test_degenerate_dispersion(self):
        p = self.panel.copy()
        sid = '000003:01'
        sel = p.security_id == sid
        p.loc[sel, 'label'] = 1000.0
        p.loc[sel, 'adjustment_factor'] = 1.0
        p.loc[sel, 'valid_day'] = p.loc[sel, 'mask_reason'].eq('')
        prof = tables(resegment(p))['profiles'].query("security_id == @sid")
        self.assertIn('degenerate_dispersion', set(prof.profile_status))
        self.assertNotIn('ok', set(prof.profile_status))


if __name__ == '__main__':
    unittest.main()
