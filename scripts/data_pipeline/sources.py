"""WRDS source adapters. No source substitution or current-universe filtering."""
import datetime as dt
import re

import pandas as pd

from .common import MARKETS, PipelineError, RAW_VERSION, sessions, close_time
from .auth import QueryError
from .taq_sql import day_sql, finalize, CHECKPOINTS

DAILY = 'comp_na_daily_all.secd'
HISTORY = 'comp_na_daily_all.sec_idhist'
DAILY_COLUMNS = ['gvkey','iid','datadate','tic','cusip','curcdd','prccd','prcod','prchd','prcld',
                 'cshtrd','cshoc','ajexdi','prcstd','tpci','adrrc']


def month_ranges(start, end):
    first, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    while first <= last:
        nxt = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        stop = min(last, nxt - dt.timedelta(days=1))
        yield str(first), str(stop)
        first = nxt


class SourceDiscoveryError(PipelineError):
    def __init__(self, operation, exc=None, message=None):
        self.detail = {'operation': operation}
        if isinstance(exc, QueryError):
            self.detail.update(exc.diagnostics())
        else:
            self.detail.update(error_type='source_discovery_error', sqlstate=None,
                               message=message or 'Source discovery failed after login; no credentials were recorded.')
        super().__init__(self.detail['message'])


def probe(db, operation, sql, params=None):
    try:
        return db.query(sql, params)
    except Exception as exc:
        raise SourceDiscoveryError(operation, exc) from None


def discover(db, start, end, markets):
    """Dates advertised by sources, restricted to caller's completed sessions later."""
    daily = probe(db, 'compustat_historical_dates', f'''SELECT DISTINCT d.datadate AS date FROM {DAILY} d
        JOIN {HISTORY} e ON d.gvkey=e.gvkey AND d.iid=e.iid AND e.item='EXCHG'
        AND e.itemvalue IN %s AND d.datadate>=e.efffrom
        AND (e.effthru IS NULL OR d.datadate<=e.effthru)
        WHERE d.curcdd='USD' AND d.datadate BETWEEN %s AND %s ORDER BY date''',
        (tuple(MARKETS[m] for m in markets), start, end))
    daily_dates = sorted(daily.date.astype(str).str[:10].tolist())
    tables = probe(db, 'taq_table_inventory', '''SELECT n.nspname AS schemaname, c.relname AS tablename
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname LIKE 'taqm_%%' AND c.relname LIKE 'ctm_%%'
        AND c.relkind IN ('r','p','v','m','f') ''')
    dates = []
    for row in tables.itertuples():
        match = re.fullmatch(r'ctm_(\d{8})', row.tablename)
        if match and row.schemaname == 'taqm_' + match[1][:4]:
            try:
                d = dt.datetime.strptime(match[1], '%Y%m%d').date().isoformat()
            except ValueError:
                continue
            if start <= d <= end:
                dates.append(d)
    if not daily_dates or not dates:
        raise SourceDiscoveryError('compustat_dates' if not daily_dates else 'taq_dates',
            message='No source dates advertised in requested interval; verify requested dates, publication and source access.')
    # A listed table is not proof of SELECT permission.
    for year in sorted({d[:4] for d in dates}):
        sample = min(d for d in dates if d.startswith(year)).replace('-', '')
        probe(db, 'taq_select_permission_' + year, f'SELECT sym_root, sym_suffix, time_m, size, price, ex, tr_scond, tr_corr FROM taqm_{year}.ctm_{sample} LIMIT 0')
    return {'daily': daily_dates, 'taq': sorted(set(dates))}


def download_daily(db, start, end, markets):
    columns = ', '.join('d.' + c for c in DAILY_COLUMNS)
    return db.query(f'''SELECT {columns}, e.itemvalue AS historical_exchg, t.itemvalue AS historical_tic
        FROM {DAILY} d JOIN {HISTORY} e
        ON d.gvkey=e.gvkey AND d.iid=e.iid AND e.item='EXCHG'
        AND e.itemvalue IN %s AND d.datadate>=e.efffrom
        AND (e.effthru IS NULL OR d.datadate<=e.effthru)
        LEFT JOIN {HISTORY} t ON d.gvkey=t.gvkey AND d.iid=t.iid AND t.item='TIC'
        AND d.datadate>=t.efffrom AND (t.effthru IS NULL OR d.datadate<=t.effthru)
        WHERE d.datadate BETWEEN %s AND %s AND d.curcdd='USD'
        ORDER BY d.gvkey,d.iid,d.datadate''', (tuple(MARKETS[m] for m in markets), start, end))


def download_history(db, start, end, markets):
    return db.query(f'''SELECT gvkey, iid, item, itemvalue, efffrom, effthru FROM {HISTORY}
        WHERE item IN ('EXCHG','TIC') AND efffrom<=%s
        AND (effthru IS NULL OR effthru>=%s)
        AND (gvkey,iid) IN (SELECT gvkey,iid FROM {HISTORY}
          WHERE item='EXCHG' AND itemvalue IN %s AND efffrom<=%s
          AND (effthru IS NULL OR effthru>=%s))''',
        (end, start, tuple(MARKETS[m] for m in markets), end, start))


def download_taq(db, day):
    frame = db.query(day_sql(day.replace('-', '')))
    frame.insert(0, 'date', day)
    frame = finalize(frame)
    frame['taq_symbol'] = frame.sym_root + frame.sym_suffix.fillna('').map(lambda v: '/' + v if v else '')
    return frame


def validate(frame, kind, start, end, markets):
    if frame.empty:
        raise PipelineError('Empty expected source chunk; not a successful checkpoint')
    if kind == 'history':
        required = {'gvkey','iid','item','itemvalue','efffrom','effthru'}
        if not required.issubset(frame) or frame[['gvkey','iid','item','efffrom']].isna().any().any():
            raise PipelineError('Invalid historical identifier schema')
        return
    date = 'datadate' if kind == 'daily' else 'date'
    keys = ['gvkey','iid',date] if kind == 'daily' else ['taq_symbol','date']
    required = set(DAILY_COLUMNS + ['historical_exchg','historical_tic']) if kind == 'daily' else {
        'taq_symbol','date','listing_ex','auction_volume','auction_price','continuous_volume','n_prints',
        'trf_close_volume', *['cumvol_' + cp for cp in CHECKPOINTS if cp + ':00' <= close_time(start)]}
    if not required.issubset(frame):
        raise PipelineError('Source chunk missing required columns')
    frame[date] = pd.to_datetime(frame[date], errors='raise').dt.strftime('%Y-%m-%d')
    if frame[keys].isna().any().any() or frame.duplicated(keys).any():
        raise PipelineError('Source chunk has missing/duplicate security-day keys')
    if not frame[date].between(start,end).all():
        raise PipelineError('Source chunk has out-of-range dates')
    if set(frame[date]) != set(sessions(start, end)):
        raise PipelineError('Source chunk has missing/unexpected trading sessions')
    if kind == 'daily' and not frame.historical_exchg.astype(str).isin([MARKETS[m] for m in markets]).all():
        raise PipelineError('Unexpected historical market')
    # Volume anomalies remain observations for the separate quality layer.


def request(kind, start, end, markets):
    return dict(source={'daily': DAILY, 'taq':'wrds_taq_ctm', 'history':HISTORY}[kind],
                table=DAILY if kind == 'daily' else HISTORY if kind == 'history' else f'taqm_{start[:4]}.ctm_{start.replace("-", "")}',
                start=start,end=end,markets=sorted(markets),query_version=RAW_VERSION)
