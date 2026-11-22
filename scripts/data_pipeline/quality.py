"""Sample eligibility (ex-post and point-in-time); never silently substitutes observed listing bounds."""
import numpy as np
import pandas as pd

from .common import MARKETS, sessions

COMMON_STOCK_TPCI = '0'
VENUE_COLUMNS = ['xvol_n', 'xvol_q', 'xvol_p', 'xvol_a', 'xvol_z']
LISTING_VENUE = {'NYSE': ('xvol_n', 'N'), 'NASDAQ': ('xvol_q', 'Q')}
CHECKPOINT_COLUMNS = ['cumvol_10:00', 'cumvol_12:00', 'cumvol_14:00', 'cumvol_15:00', 'cumvol_15:30', 'cumvol_15:50']
# Fixed funnel order: the first matching reason is the one reported, so a stock-day is counted once.
EXCLUSION_ORDER = ['outside_effective_scope', 'ambiguous_identity', 'event_day', 'half_day', 'unresolved_security',
                   'security_abnormal_rate_gt_5pct', 'insufficient_prior_valid_days', 'invalid_current_day']


def symbol_key(symbols, types=None):
    s = symbols.fillna('').str.strip().str.upper()
    if types is not None:
        preferred = types.isin(['1','G','S'])
        s.loc[preferred] = s.loc[preferred].str.replace(r'\.P([A-Z]+)$', r'/PR\1', regex=True)
    return s.str.replace('.', '/', regex=False)


def invalid_reason(values):
    v = pd.to_numeric(values, errors='coerce')
    return pd.Series(np.select([v.isna(), ~np.isfinite(v), v.le(0)],
                    ['missing', 'nonfinite', 'nonpositive'], default=''), index=values.index)


def label_columns(grid):
    """Listing-venue closing-cross label; never falls back to another venue or the largest cross."""
    g = grid
    for c in VENUE_COLUMNS + ['auction_volume_all']:
        if c not in g:
            g[c] = np.nan
    xv = g[VENUE_COLUMNS].apply(pd.to_numeric, errors='coerce')
    venue = g.market.map({m: v[0] for m, v in LISTING_VENUE.items()})
    letter = g.market.map({m: v[1] for m, v in LISTING_VENUE.items()})
    label = pd.Series(np.nan, index=g.index)
    for col in ['xvol_n', 'xvol_q']:
        rows = venue.eq(col)
        label.loc[rows] = xv.loc[rows, col]
    g['label'] = label
    any_venue = xv.notna().any(axis=1)
    has_record = g.taq_count.notna() if 'taq_count' in g else pd.Series(True, index=g.index)
    outside = ~any_venue & pd.to_numeric(g.auction_volume_all, errors='coerce').gt(0)
    g['label_status'] = np.select([~has_record, label.notna(), outside],
                                  ['no_taq_record', 'ok', 'cross_outside_known_venues'],
                                  default='listing_venue_cross_missing')
    other = any_venue & label.isna()
    largest = g.listing_ex if 'listing_ex' in g else pd.Series('', index=g.index)
    conflict = other | (label.notna() & largest.notna() & largest.ne(letter))
    g['venue_reconciliation'] = np.select(
        [g.match_status.isin(['ambiguous', 'missing_identity']), conflict, label.notna()],
        ['identity_unresolved', 'venue_conflict', 'consistent'], default='listing_cross_missing')
    return g


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


def security_type(observed):
    types = observed.security_type_code.dropna().astype(str).unique()
    if len(types) == 0:
        return None, 'unknown_security_type'
    if len(types) > 1:
        return None, 'multiple_security_types'
    if types[0] != COMMON_STOCK_TPCI:
        return types[0], 'non_common_stock:' + types[0]
    return types[0], ''


def security_grids(daily, taq, history, start, end, markets, masks=None):
    """Yield (security_id, grid or None, summary). masks: DataFrame date, market, mask_reason."""
    dates = sessions(start,end)
    history = history.copy()
    history['security_id'] = history.gvkey + ':' + history.iid
    history['efffrom'] = history.efffrom.astype('string').str[:10]
    history['effthru'] = history.effthru.astype('string').str[:10].fillna('9999-12-31')
    daily = daily[daily.date.between(start,end)]
    taq = taq[taq.date.between(start,end)].copy()
    taq['taq_symbol'] = taq.taq_symbol.str.upper()
    for c in VENUE_COLUMNS + ['auction_volume_all'] + CHECKPOINT_COLUMNS:
        if c not in taq:
            taq[c] = np.nan
    if masks is None:
        masks = pd.DataFrame(columns=['date', 'market', 'mask_reason'])
    # Require unique symbols on each date in BOTH sources; no punctuation-stripping aliases.
    known = daily[['security_id','date','ticker','security_type_code']].copy()
    known['symbol_key'] = symbol_key(known.ticker, known.security_type_code)
    counts = known.groupby(['date','symbol_key']).security_id.nunique().rename('identity_count')
    taq = taq.merge(counts, left_on=['date','taq_symbol'], right_index=True, how='left')
    taq['taq_count'] = taq.groupby(['date','taq_symbol']).taq_symbol.transform('size')
    hgroups = history.groupby('security_id', sort=False)
    dgroups = {sid: group for sid, group in daily.groupby('security_id', sort=False)}
    tg = {sym: group for sym, group in taq.groupby('taq_symbol', sort=False)}
    taq_columns = (['date','taq_symbol','auction_volume','listing_ex','auction_volume_all','identity_count','taq_count']
                   + VENUE_COLUMNS + CHECKPOINT_COLUMNS)
    obs_columns = ['date','PX_VOLUME','security_type_code','adjustment_factor','split_segment_id','split_status']
    ids = sorted(set(dgroups) | set(history.security_id))
    for sid in ids:
        observed = dgroups.get(sid, daily.iloc[:0])
        h = hgroups.get_group(sid) if sid in hgroups.groups else history.iloc[:0]
        exch = h[h.item.eq('EXCHG') & h.itemvalue.isin([MARKETS[m] for m in markets])]
        empty = dict(expected_days=0, abnormal_days=0, abnormal_rate=None)
        if exch.empty:
            yield sid, None, empty | dict(security_status='unresolved', reason='missing_effective_listing_market_boundary',
                                          research_status='deferred')
            continue
        if not (exch.efffrom.le(end) & exch.effthru.ge(start)).any():
            # Listed on a target market only outside the processing window: not part of this sample.
            yield sid, None, empty | dict(security_status='outside_window', reason='no_listing_interval_in_range',
                                          research_status='not_applicable')
            continue
        tpci, type_reason = security_type(observed)
        if type_reason:
            status = 'non_common_stock' if tpci else 'unresolved'
            yield sid, None, empty | dict(security_status=status, reason=type_reason, research_status=research_status(status))
            continue
        grid = pd.DataFrame({'date':dates})
        grid['security_id'] = sid
        grid['in_listing'] = False
        grid['market'] = ''
        grid['ticker'] = ''
        grid['history_count'] = 0
        for r in exch.itertuples():
            mask = grid.date.between(r.efffrom,r.effthru)
            grid.loc[mask,'in_listing'] = True
            grid.loc[mask,'market'] = {'11':'NYSE','14':'NASDAQ'}[r.itemvalue]
            grid.loc[mask,'history_count'] += 1
        for r in h[h.item.eq('TIC')].itertuples():
            mask = grid.date.between(r.efffrom,r.effthru)
            # Multiple TIC intervals are ambiguity even when one happens to match.
            occupied = mask & grid.ticker.ne('')
            grid.loc[occupied,'history_count'] += 1
            grid.loc[mask,'ticker'] = r.itemvalue
        grid = grid.merge(masks, on=['date','market'], how='left', validate='many_to_one')
        grid['mask_reason'] = grid.mask_reason.fillna('')
        cols = [c for c in obs_columns if c in observed]
        grid = grid.merge(observed[cols],on='date',how='left',validate='one_to_one')
        grid['security_type_code'] = grid.security_type_code.fillna(tpci)
        grid['taq_symbol'] = symbol_key(grid.ticker, grid.security_type_code)
        relevant = [tg[s] for s in grid.taq_symbol.unique() if s in tg]
        labels = pd.concat(relevant, ignore_index=True) if relevant else taq.iloc[:0]
        # Duplicate TAQ identities cannot be used for training; aggregate only to diagnose.
        labels = labels.drop_duplicates(['date','taq_symbol'])
        grid = grid.merge(labels[taq_columns], on=['date','taq_symbol'],how='left',validate='many_to_one')
        grid['match_status'] = 'unmatched'
        ambiguous = grid.history_count.gt(1) | grid.identity_count.gt(1) | grid.taq_count.gt(1)
        grid.loc[grid.taq_count.eq(1) & grid.identity_count.eq(1) & ~ambiguous,'match_status'] = 'unique'
        grid.loc[ambiguous,'match_status'] = 'ambiguous'
        grid.loc[grid.in_listing & grid.ticker.eq(''),'match_status'] = 'missing_identity'
        grid = label_columns(grid)
        scored, summary = score_grid(grid)
        yield sid, scored, summary
