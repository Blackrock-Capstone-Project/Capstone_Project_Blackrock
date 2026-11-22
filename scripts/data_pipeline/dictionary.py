"""Field dictionary: source, unit, aggregation convention and when each field is available."""
import pandas as pd

T1, INTRA, POST, META = 't-1 close', 't intraday', 't post-close', 'metadata'
TAQ_RULE = "TAQ ctm, tr_corr='00', 09:30 to close"
CROSS_RULE = "condition code contains '6', close-1min to close+5min"

FIELDS = [
    # name, table, source, unit, convention, available_at
    ('security_id', 'all', 'Compustat', 'id', 'GVKEY:IID', META),
    ('date', 'all', 'XNYS calendar', 'date', 'New York trading session', META),
    ('market', 'research', 'Compustat sec_idhist EXCHG', 'enum', 'effective historical listing market (11=NYSE, 14=NASDAQ); LSE reserved', T1),
    ('security_type_code', 'research', 'Compustat tpci', 'code', "current snapshot, not point-in-time; '0' = common stock", META),
    ('is_reit', 'daily', 'not available', 'bool', 'NA until a verified industry source (SIC 6798) is downloaded', META),
    ('is_spac', 'daily', 'not available', 'bool', 'NA until a verified industry source (SIC 6770) is downloaded', META),
    ('PX_VOLUME', 'daily/research', 'Compustat cshtrd', 'native shares', 'reported daily volume', POST),
    ('PX_LAST', 'daily', 'Compustat prccd', 'USD', 'as quoted close', POST),
    ('adjustment_factor', 'daily/research', 'Compustat ajexdi', 'factor', 'cumulative split factor; use ratio ajexdi_s/ajexdi_t within one split_segment_id', T1),
    ('split_segment_id', 'daily/research', 'derived', 'int', 'increments at split_unresolved/factor_missing breaks', T1),
    ('split_status', 'daily/research', 'derived', 'enum', 'ok / split_applied / split_unresolved / factor_missing', T1),
    ('label', 'research', 'TAQ xvol_n or xvol_q', 'native shares', 'closing cross on the listing exchange (NYSE->N, NASDAQ->Q); ' + CROSS_RULE, POST),
    ('label_status', 'research', 'derived', 'enum', 'ok / listing_venue_cross_missing / cross_outside_known_venues / no_taq_record', POST),
    ('venue_reconciliation', 'research', 'derived', 'enum', 'consistent / venue_conflict / listing_cross_missing / identity_unresolved', POST),
    ('xvol_n,xvol_q,xvol_p,xvol_a,xvol_z', 'research/taq', 'TAQ', 'native shares', 'cross volume by venue; ' + CROSS_RULE, POST),
    ('auction_volume_all', 'research/taq', 'TAQ', 'native shares', 'cross volume summed over all venues; ' + CROSS_RULE, POST),
    ('auction_volume', 'research/taq', 'TAQ', 'native shares', 'largest single-venue cross (legacy, reconciliation only)', POST),
    ('listing_ex', 'research/taq', 'TAQ', 'venue code', 'venue of the largest cross (inferred, not official listing)', POST),
    ('auction_price', 'taq', 'TAQ', 'USD', 'MAX price over cross prints across venues', POST),
    ('n_prints', 'taq', 'TAQ', 'count', 'number of cross prints across venues', POST),
    ('continuous_volume', 'taq', 'TAQ', 'shares (fractional)', TAQ_RULE + ', time_m < close; may overlap prints stamped before close (unverified)', POST),
    ('trf_close_volume', 'taq', 'TAQ', 'shares (fractional)', "ex='D' within 30 min after close at price = auction_price", POST),
    ('cumulative_volume', 'checkpoints_research', 'TAQ cumvol_*', 'shares (fractional)', TAQ_RULE + ', time_m < checkpoint', INTRA),
    ('checkpoint_status', 'checkpoints_research', 'XNYS calendar', 'enum', 'applicable / not_applicable (after an early close)', META),
    ('mask_reason', 'research', 'index_events.csv + XNYS', 'enum', 'event_day / half_day; excluded from sample, windows and 5% denominator', META),
    ('valid_day', 'research', 'derived', 'bool', 'PX_VOLUME and label finite > 0, unique identity, not masked', POST),
    ('prior_10_valid_days', 'research', 'derived', 'int', 'valid days in the 10 prior XNYS sessions; masked slots not backfilled', T1),
    ('pit_n_expected', 'research', 'derived', 'int', 'expected sessions before t within listing interval and request', T1),
    ('pit_n_anomaly', 'research', 'derived', 'int', 'anomalous expected sessions before t', T1),
    ('pit_eligible', 'research', 'derived', 'bool', 'STRICT point-in-time: pit rate <= 5%, prior_10_valid_days >= 6, valid_day; '
     'assumes t-1 data available by t; never rewritten by full-window status; do not use alone as the training filter', POST),
    ('research_status', 'research/security_quality', 'derived', 'enum',
     'in_scope / deferred (unresolved: kept with reason, excluded from this study, re-evaluated every run) / not_applicable', META),
    ('research_included_expost', 'research', 'derived', 'bool', 'training filter (ex-post): = eligible', POST),
    ('research_included_pit', 'research', 'derived', 'bool', "training filter (PIT): pit_eligible AND research_status != 'deferred'", POST),
    ('security_status', 'research', 'derived', 'enum', 'ex-post full-history 5% result (uses future data)', POST),
    ('eligible', 'research', 'derived', 'bool', 'ex-post: retained security, prior_10_valid_days >= 6, valid_day', POST),
    ('exclusion_reason', 'research', 'derived', 'enum', 'first reason in fixed funnel order', POST),
]


def field_dictionary():
    return pd.DataFrame(FIELDS, columns=['field', 'table', 'source', 'unit', 'convention', 'available_at'])
