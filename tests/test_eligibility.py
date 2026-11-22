"""Valid days, prior-10/6 window with masking, ex-post 5% rule, strict PIT, research deferral."""
import unittest

import numpy as np
import pandas as pd

from auction_data.build import daily_clean
from auction_data.eligibility import score_grid
from auction_data.identity import security_grids
from auction_data.trading_calendar import sessions
from tests.helpers import daily_frame, taq_frame, history_frame, DATES, daily_for, history, taq_for, grid


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


if __name__ == '__main__':
    unittest.main()
