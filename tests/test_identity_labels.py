"""Common-stock universe, Compustat<->TAQ symbol mapping, listing-venue label, listing intervals."""
import unittest

import numpy as np

from tests.helpers import START, DATES, daily_for, history, taq_for, grid


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


if __name__ == '__main__':
    unittest.main()
