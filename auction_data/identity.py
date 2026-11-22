"""Compustat <-> TAQ identity: symbol keys, security type, per-security listing grid and matching.

Never silently substitutes observed listing bounds; yields one scored grid per security.
"""
import numpy as np
import pandas as pd

from .eligibility import research_status, score_grid
from .labels import label_columns
from .settings import CHECKPOINT_COLUMNS, COMMON_STOCK_TPCI, MARKETS, VENUE_COLUMNS
from .trading_calendar import sessions


def symbol_key(symbols, types=None):
    s = symbols.fillna('').str.strip().str.upper()
    if types is not None:
        preferred = types.isin(['1','G','S'])
        s.loc[preferred] = s.loc[preferred].str.replace(r'\.P([A-Z]+)$', r'/PR\1', regex=True)
    return s.str.replace('.', '/', regex=False)


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
