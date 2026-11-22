"""Valid days, prior-10/6 window, ex-post 5% rule, strict PIT counters and research inclusion."""
import numpy as np
import pandas as pd


# Fixed funnel order: the first matching reason is the one reported, so a stock-day is counted once.
EXCLUSION_ORDER = ['outside_effective_scope', 'ambiguous_identity', 'event_day', 'half_day', 'unresolved_security',
                   'security_abnormal_rate_gt_5pct', 'insufficient_prior_valid_days', 'invalid_current_day']


def invalid_reason(values):
    v = pd.to_numeric(values, errors='coerce')
    return pd.Series(np.select([v.isna(), ~np.isfinite(v), v.le(0)],
                    ['missing', 'nonfinite', 'nonpositive'], default=''), index=values.index)


def score_grid(grid):
    """Grid MUST already contain every calendar session, sorted, one security.

    Columns: in_listing (or expected), optional mask_reason, PX_VOLUME, label, match_status."""
    d = grid.copy()
    if 'in_listing' not in d:
        d['in_listing'] = d.expected
    if 'mask_reason' not in d:
        d['mask_reason'] = ''
    d['mask_reason'] = d.mask_reason.fillna('')
    d['masked'] = d.mask_reason.ne('')
    d['expected'] = d.in_listing & ~d.masked
    d['daily_volume_issue'] = invalid_reason(d.PX_VOLUME)
    d['label_issue'] = invalid_reason(d.label)
    d['valid_day'] = d.daily_volume_issue.eq('') & d.label_issue.eq('') & d.match_status.eq('unique') & d.expected
    # Calendar window (not rows); masked sessions occupy their slot and are never valid: no backfilling.
    d['prior_10_valid_days'] = d.valid_day.shift(1, fill_value=False).rolling(10, min_periods=1).sum().astype(int)
    anomaly = d.expected & ~d.valid_day
    # Point-in-time counters use only sessions strictly before t.
    d['pit_n_expected'] = d.expected.cumsum().shift(1, fill_value=0).astype(int)
    d['pit_n_anomaly'] = anomaly.cumsum().shift(1, fill_value=0).astype(int)
    pit_rate_ok = d.pit_n_expected.gt(0) & (d.pit_n_anomaly * 20 <= d.pit_n_expected)
    d['pit_eligible'] = d.valid_day & d.prior_10_valid_days.ge(6) & pit_rate_ok
    denominator = int(d.expected.sum())
    numerator = int(anomaly.sum())
    ambiguous = d.match_status.isin(['ambiguous','missing_identity'])
    unresolved_reason = ('zero_denominator' if denominator == 0
                         else 'ambiguous_identity' if (d.expected & d.match_status.eq('ambiguous')).any()
                         else 'ticker_history_gap' if (d.expected & d.match_status.eq('missing_identity')).any()
                         else '')
    status = 'unresolved' if unresolved_reason else 'excluded' if numerator * 20 > denominator else 'retained'
    d['security_status'] = status
    d['eligible'] = d.valid_day & d.prior_10_valid_days.ge(6) & (status == 'retained')
    # Research inclusion for THIS study: unresolved securities are deferred (re-evaluated on every run).
    # pit_eligible itself stays strictly point-in-time and is never rewritten by the full-window status.
    d['research_status'] = research_status(status)
    d['research_included_expost'] = d.eligible
    d['research_included_pit'] = d.pit_eligible & (d.research_status != 'deferred')
    flag = lambda v: pd.Series(v, index=d.index)
    d['exclusion_reason'] = np.select(
        [~d.in_listing, ambiguous, d.mask_reason.eq('event_day'), d.mask_reason.eq('half_day'),
         flag(status == 'unresolved'), flag(status == 'excluded'), d.prior_10_valid_days.lt(6), ~d.valid_day],
        EXCLUSION_ORDER, default='')
    return d[d.in_listing].copy(), dict(expected_days=denominator, abnormal_days=numerator,
            abnormal_rate=numerator/denominator if denominator else None, security_status=status,
            reason=unresolved_reason, research_status=research_status(status))


def research_status(security_status):
    """unresolved -> deferred (kept with reason, re-evaluated next run); non-targets not applicable."""
    return {'unresolved': 'deferred', 'retained': 'in_scope', 'excluded': 'in_scope'}.get(security_status, 'not_applicable')
