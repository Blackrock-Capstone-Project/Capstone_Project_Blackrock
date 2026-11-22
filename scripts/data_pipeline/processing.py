"""Offline standardization and versioned, all-or-nothing processed publication."""
from pathlib import Path
import gzip
import io
import os
import uuid

import numpy as np
import pandas as pd

from .common import PROCESS_VERSION, PipelineError, digest, read_csv, read_json, save_json, close_time, sessions
from .dictionary import field_dictionary
from .events import half_days, masked_sessions
from .quality import CHECKPOINT_COLUMNS, VENUE_COLUMNS, security_grids
from . import report as reporting
from . import splits


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


def verify_table(path, keys, expected_rows, check_duplicates=True):
    # Partition ordering is not assumed; check duplicates across chunk boundaries too.
    seen, rows = set(), 0
    for d in read_csv(path, chunksize=250000, dtype={k:str for k in keys}):
        if not set(keys).issubset(d):
            raise PipelineError('Processed schema missing keys')
        if d[keys].isna().any().any() or d.duplicated(keys).any():
            raise PipelineError('Processed table has missing/duplicate keys')
        if check_duplicates:
            current = set(d[keys].itertuples(index=False,name=None))
            if seen.intersection(current):
                raise PipelineError('Processed table has duplicate keys across chunks')
            seen.update(current)
        rows += len(d)
    if rows != expected_rows:
        raise PipelineError('Processed row count differs after serialization')


def output_valid(root, fingerprint):
    try:
        manifest = read_json(root/'manifest.json')
        return manifest['fingerprint'] == fingerprint and all(
            digest(root/name) == info['sha256'] for name,info in manifest['files'].items())
    except (OSError,ValueError,KeyError):
        return False


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


class deterministic_gzip(io.TextIOWrapper):
    """Text gzip writer with mtime=0 so identical content is byte-identical across runs."""
    def __init__(self, path):
        self._raw = open(path, 'wb')
        super().__init__(gzip.GzipFile(filename='', mode='wb', fileobj=self._raw, mtime=0),
                         encoding='utf-8', newline='')

    def close(self):
        try:
            super().close()
        finally:
            self._raw.close()  # GzipFile never closes a caller-supplied fileobj


QUALITY_DROP = CHECKPOINT_COLUMNS + ['identity_count', 'taq_count']


def publish(root, frames, history, config, fingerprint, inputs, events):
    processed = root/'processed'
    processed.mkdir(parents=True,exist_ok=True)
    pointer = processed/'wrds_current'
    if pointer.exists() and not pointer.is_symlink():
        raise PipelineError('wrds_current must be a managed symlink')
    if output_valid(pointer,fingerprint):
        return read_json(pointer/'manifest.json') | {'reused':True}
    previous_sample = pointer/'us_wrds_quality'/'research_sample.csv.gz' if pointer.exists() else None
    generation = processed/'.wrds_versions'/uuid.uuid4().hex
    generation.mkdir(parents=True)
    manifest = dict(version=PROCESS_VERSION,fingerprint=fingerprint,files={},config=config,input_partitions=inputs,
        selection_policy=('ex_post_full_history_not_point_in_time_backtest; pit_* fields use only t-1 history; '
                          'research_sample = research_included_expost | research_included_pit (deferred securities excluded)'),
        schema_notes={'volume':'native shares; history conversion via ajexdi_s/ajexdi_t within split_segment_id',
                      'prices':'as quoted; AJEXDI retained',
                      'identity':'effective historical ticker; not official permanent TAQ link',
                      'universe':"Compustat tpci == '0' (current snapshot); REIT/SPAC flags NA",
                      'label':'listing-venue closing cross: NYSE xvol_n, NASDAQ xvol_q; no fallback',
                      'volume_validity':'daily volume and label finite >0',
                      'masked_sessions':'index rebalance execution closes and XNYS half days',
                      'history':'prior ten sessions excluding today; masked slots not backfilled; >=6 valid',
                      'security_exclusion':'abnormal days / expected unmasked listed sessions >5%'})
    def table(name,frame,keys):
        p=generation/name
        p.parent.mkdir(parents=True,exist_ok=True)
        frame.to_csv(p,index=False,compression={'method':'gzip','mtime':0} if name.endswith('.gz') else None)
        verify_table(p,keys,len(frame))
        manifest['files'][name]=dict(rows=len(frame),sha256=digest(p),keys=keys,
                missing_fields={c:int(frame[c].isna().sum()) for c in frame})
    try:
        daily = pd.concat([daily_clean(f) for f in frames['daily']],ignore_index=True)
        daily, split_issues = splits.annotate(daily)
        taq = pd.concat(frames['taq'],ignore_index=True)
        for c in VENUE_COLUMNS + ['auction_volume_all']:
            if c not in taq:
                taq[c] = np.nan
        taq['volume_basis']='native_shares'
        taq['exchange_source']='largest_closing_cross_venue_inference'
        table('us_wrds_daily/daily_clean.csv.gz',daily,['security_id','date'])
        table('us_wrds_taq/daily_summary.csv.gz',taq,['taq_symbol','date'])
        table('us_wrds_taq/labels_clean.csv.gz',taq[['taq_symbol','date','listing_ex','auction_volume','auction_volume_all',
              *VENUE_COLUMNS,'auction_price','n_prints','volume_basis','exchange_source']],['taq_symbol','date'])
        start, end, markets = config['start'], config['common_end'], config['markets']
        masks = masked_sessions(events, start, end, markets)
        closes = {d: close_time(d) for d in sessions(start, end)}
        acc = reporting.Accumulator(sessions(start, end))
        (generation/'us_wrds_quality').mkdir()
        outputs={'us_wrds_quality/stock_day_quality.csv.gz':['security_id','date'],
                 'us_wrds_quality/eligible_stock_days.csv.gz':['security_id','date'],
                 'us_wrds_quality/research_sample.csv.gz':['security_id','date'],
                 'us_wrds_taq/checkpoints_research.csv.gz':['security_id','date','checkpoint']}
        counts={n:0 for n in outputs}
        handles={n:deterministic_gzip(generation/n) for n in outputs}
        written={n:False for n in outputs}
        def emit(name, frame):
            frame.to_csv(handles[name],index=False,header=not written[name])
            written[name]=True
            counts[name]+=len(frame)
        stats=[]
        try:
            # Stream per-security grids; avoid materializing another full historical panel.
            for sid,grid,summary in security_grids(daily,taq,history,start,end,markets,masks):
                stats.append({'security_id':sid,**summary})
                if grid is None:
                    continue
                acc.add(grid)
                quality=grid.drop(columns=[c for c in QUALITY_DROP if c in grid])
                emit('us_wrds_quality/stock_day_quality.csv.gz',quality)
                emit('us_wrds_quality/eligible_stock_days.csv.gz',quality[quality.eligible])
                # Training sample for this study: research inclusion only (deferred securities never enter).
                emit('us_wrds_quality/research_sample.csv.gz',
                     quality[quality.research_included_expost | quality.research_included_pit])
                cp=checkpoint_rows(grid,closes)
                if cp is not None:
                    emit('us_wrds_taq/checkpoints_research.csv.gz',cp)
            for name,keys in outputs.items():
                if not written[name]:
                    pd.DataFrame(columns=keys+['eligible','pit_eligible']).to_csv(handles[name],index=False)
        finally:
            for h in handles.values():
                h.close()
        for name,keys in outputs.items():
            p=generation/name
            verify_table(p,keys,counts[name],check_duplicates=not name.startswith('us_wrds_taq/'))
            manifest['files'][name]=dict(rows=counts[name],sha256=digest(p),keys=keys)
        security=pd.DataFrame(stats,columns=['security_id','expected_days','abnormal_days','abnormal_rate','security_status',
                                             'reason','research_status'])
        table('us_wrds_quality/security_quality.csv',security,['security_id'])
        table('reference/index_events.csv',events,['event_family','execution_close_date'])
        table('reference/field_dictionary.csv',field_dictionary(),['field'])
        report_files = reporting.write(generation, acc, security, taq, split_issues, events, masks,
                                       half_days(start, end), previous_sample,
                                       generation/'us_wrds_quality'/'research_sample.csv.gz')
        for name in report_files:
            manifest['files'][name]=dict(sha256=digest(generation/name))
        manifest['unresolved_securities']=int(security.security_status.eq('unresolved').sum())
        # Unresolved securities are deferred (kept with reasons, re-evaluated next run); they do not block completion.
        manifest['deferred_securities']=int(security.research_status.eq('deferred').sum())
        manifest['deferred_reasons']=security.loc[security.research_status.eq('deferred'),'reason'].value_counts().to_dict()
        manifest['unverified_events']=int(events.verified.ne('true').sum())
        manifest['quality_status']='complete'
        save_json(generation/'manifest.json',manifest)
        # Public directories are stable aliases through one atomically switched pointer.
        for name in ['us_wrds_daily','us_wrds_taq','us_wrds_quality','reference','report']:
            alias=processed/name
            if alias.exists() and not alias.is_symlink():
                # Retain the existing independent WRDS output as a previous local version.
                os.replace(alias,processed/'.wrds_versions'/('pre_pipeline_'+name+'_'+uuid.uuid4().hex))
            tmp=processed/('.alias_'+uuid.uuid4().hex)
            tmp.symlink_to(Path('wrds_current')/name,target_is_directory=True)
            os.replace(tmp,alias)
        tmp=processed/('.current_'+uuid.uuid4().hex)
        tmp.symlink_to(generation.relative_to(processed),target_is_directory=True)
        os.replace(tmp,pointer)
        return manifest | {'reused':False}
    except BaseException:
        # Keep failed generation for diagnosis, but never advertise it as current.
        raise
