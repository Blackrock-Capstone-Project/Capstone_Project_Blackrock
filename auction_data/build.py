"""Build processed tables from verified raw frames: daily observations, per-security grids, checkpoints."""
import numpy as np
import pandas as pd

from . import report as reporting
from . import splits
from .dictionary import field_dictionary
from .events import masked_sessions
from .identity import security_grids
from .publish import Generation, reuse_existing
from .settings import CHECKPOINT_COLUMNS, PROCESS_VERSION, VENUE_COLUMNS
from .trading_calendar import close_time, half_days, sessions


def listed_shares(d):
    shares = d.cshoc.copy()
    adr = d.tpci.isin(['F','S'])
    shares.loc[adr] = d.loc[adr,'cshoc'] / d.loc[adr,'adrrc'].where(d.loc[adr,'adrrc'] > 0)
    return shares


def daily_clean(d):
    shares = listed_shares(d)
    bid_ask = d.prcstd.eq(4)
    return pd.DataFrame({
        'security_id':d.gvkey+':'+d.iid,'gvkey':d.gvkey,'iid':d.iid,'cusip':d.cusip,
        'date':d.datadate,'ticker':d.historical_tic,'market':d.historical_exchg.map({'11':'NYSE','14':'NASDAQ'}),
        'currency':d.curcdd,'PX_OPEN':d.prcod,'PX_HIGH':d.prchd.mask(bid_ask),'PX_LOW':d.prcld.mask(bid_ask),
        'source_prchd':d.prchd,'source_prcld':d.prcld,'closing_ask':d.prchd.where(bid_ask),
        'closing_bid':d.prcld.where(bid_ask),'PX_LAST':d.prccd,'PX_VOLUME':d.cshtrd,
        'shares_outstanding':shares,'source_cshoc':d.cshoc,'adr_ratio':d.adrrc,
        'CUR_MKT_CAP':d.prccd*shares,'adjustment_factor':d.ajexdi,'price_standard_code':d.prcstd,
        'security_type_code':d.tpci,'is_reit':pd.NA,'is_spac':pd.NA,'daily_source':'wrds_compustat_na_secd',
        'volume_basis':'compustat_reported_shares','price_basis':'as_quoted',
        'close_times_volume_usd':d.prccd*d.cshtrd})


def checkpoint_rows(grid, closes):
    """Long checkpoint table for uniquely matched listed stock-days; after-close checkpoints kept as not_applicable."""
    g = grid[grid.match_status.eq('unique')]
    if g.empty:
        return None
    values = g[CHECKPOINT_COLUMNS].apply(pd.to_numeric, errors='coerce').to_numpy(float)
    cps = [c[7:] for c in CHECKPOINT_COLUMNS]
    close = g.date.map(closes).to_numpy()
    applicable = np.array([[cp + ':00' <= c for cp in cps] for c in close], dtype=bool)
    values = np.where(applicable, values, np.nan)
    with np.errstate(invalid='ignore'):
        nonmonotone = (np.diff(values, axis=1) < 0).any(axis=1)
    n = len(g)
    out = pd.DataFrame({'security_id': np.repeat(g.security_id.to_numpy(), 6),
                        'date': np.repeat(g.date.to_numpy(), 6),
                        'checkpoint': np.tile(cps, n),
                        'cumulative_volume': values.ravel(),
                        'checkpoint_status': np.where(applicable.ravel(), 'applicable', 'not_applicable')})
    v = out.cumulative_volume
    out['volume_valid'] = out.checkpoint_status.eq('applicable') & np.isfinite(v) & v.ge(0)
    out['nonmonotone'] = np.repeat(nonmonotone, 6)
    return out


QUALITY_DROP = CHECKPOINT_COLUMNS + ['identity_count', 'taq_count']
STREAMED = {'us_wrds_quality/stock_day_quality.csv.gz': ['security_id', 'date'],
            'us_wrds_quality/eligible_stock_days.csv.gz': ['security_id', 'date'],
            'us_wrds_quality/research_sample.csv.gz': ['security_id', 'date'],
            'us_wrds_taq/checkpoints_research.csv.gz': ['security_id', 'date', 'checkpoint']}
SELECTION_POLICY = ('ex_post_full_history_not_point_in_time_backtest; pit_* fields use only t-1 history; '
                    'research_sample = research_included_expost | research_included_pit (deferred securities excluded)')
SCHEMA_NOTES = {'volume': 'native shares; history conversion via ajexdi_s/ajexdi_t within split_segment_id',
                'prices': 'as quoted; AJEXDI retained',
                'identity': 'effective historical ticker; not official permanent TAQ link',
                'universe': "Compustat tpci == '0' (current snapshot); REIT/SPAC flags NA",
                'label': 'listing-venue closing cross: NYSE xvol_n, NASDAQ xvol_q; no fallback',
                'volume_validity': 'daily volume and label finite >0',
                'masked_sessions': 'index rebalance execution closes and XNYS half days',
                'history': 'prior ten sessions excluding today; masked slots not backfilled; >=6 valid',
                'security_exclusion': 'abnormal days / expected unmasked listed sessions >5%'}


def build_outputs(root, frames, history, config, fingerprint, inputs, events):
    """Build every processed table from verified raw frames and publish them as one new version."""
    reused = reuse_existing(root, fingerprint)
    if reused is not None:
        return reused
    manifest = dict(version=PROCESS_VERSION, fingerprint=fingerprint, files={}, config=config,
                    input_partitions=inputs, selection_policy=SELECTION_POLICY, schema_notes=SCHEMA_NOTES)
    g = Generation(root, manifest)
    daily = pd.concat([daily_clean(f) for f in frames['daily']], ignore_index=True)
    daily, split_issues = splits.annotate(daily)
    taq = pd.concat(frames['taq'], ignore_index=True)
    for c in VENUE_COLUMNS + ['auction_volume_all']:
        if c not in taq:
            taq[c] = np.nan
    taq['volume_basis'] = 'native_shares'
    taq['exchange_source'] = 'largest_closing_cross_venue_inference'
    g.table('us_wrds_daily/daily_clean.csv.gz', daily, ['security_id', 'date'])
    g.table('us_wrds_taq/daily_summary.csv.gz', taq, ['taq_symbol', 'date'])
    g.table('us_wrds_taq/labels_clean.csv.gz', taq[['taq_symbol', 'date', 'listing_ex', 'auction_volume', 'auction_volume_all',
            *VENUE_COLUMNS, 'auction_price', 'n_prints', 'volume_basis', 'exchange_source']], ['taq_symbol', 'date'])
    start, end, markets = config['start'], config['common_end'], config['markets']
    masks = masked_sessions(events, start, end, markets)
    closes = {d: close_time(d) for d in sessions(start, end)}
    acc = reporting.Accumulator(sessions(start, end))
    out = g.streams(STREAMED)
    stats = []
    try:
        # Stream per-security grids; avoid materializing another full historical panel.
        for sid, grid, summary in security_grids(daily, taq, history, start, end, markets, masks):
            stats.append({'security_id': sid, **summary})
            if grid is None:
                continue
            acc.add(grid)
            quality = grid.drop(columns=[c for c in QUALITY_DROP if c in grid])
            out.emit('us_wrds_quality/stock_day_quality.csv.gz', quality)
            out.emit('us_wrds_quality/eligible_stock_days.csv.gz', quality[quality.eligible])
            # Training sample for this study: research inclusion only (deferred securities never enter).
            out.emit('us_wrds_quality/research_sample.csv.gz',
                     quality[quality.research_included_expost | quality.research_included_pit])
            cp = checkpoint_rows(grid, closes)
            if cp is not None:
                out.emit('us_wrds_taq/checkpoints_research.csv.gz', cp)
        out.write_missing_headers()
    finally:
        out.close()
    # Checkpoint keys are unique by construction (one security streamed at a time); skip the full key-set check.
    out.verify(skip_duplicate_check={'us_wrds_taq/checkpoints_research.csv.gz'})
    security = pd.DataFrame(stats, columns=['security_id', 'expected_days', 'abnormal_days', 'abnormal_rate',
                                            'security_status', 'reason', 'research_status'])
    g.table('us_wrds_quality/security_quality.csv', security, ['security_id'])
    g.table('reference/index_events.csv', events, ['event_family', 'execution_close_date'])
    g.table('reference/field_dictionary.csv', field_dictionary(), ['field'])
    for name in reporting.write(g.dir, acc, security, taq, split_issues, events, masks, half_days(start, end),
                                g.previous_sample, g.dir/'us_wrds_quality'/'research_sample.csv.gz'):
        g.record(name)
    manifest['unresolved_securities'] = int(security.security_status.eq('unresolved').sum())
    # Unresolved securities are deferred (kept with reasons, re-evaluated next run); they do not block completion.
    manifest['deferred_securities'] = int(security.research_status.eq('deferred').sum())
    manifest['deferred_reasons'] = security.loc[security.research_status.eq('deferred'), 'reason'].value_counts().to_dict()
    manifest['unverified_events'] = int(events.verified.ne('true').sum())
    manifest['quality_status'] = 'complete'
    return g.switch()
