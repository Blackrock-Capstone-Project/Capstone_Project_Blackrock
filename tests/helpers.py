"""Shared synthetic WRDS data and the mocked-WRDS test environment."""
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from auction_data import wrds_queries as sources
from auction_data import splits, update_data
from auction_data.build import daily_clean
from auction_data.identity import security_grids
from auction_data.storage import digest
from auction_data.trading_calendar import close_time, sessions


def daily_frame(start,end,markets=('NYSE','NASDAQ')):
    dates=sessions(start,end)
    d=pd.DataFrame({c:np.ones(len(dates)) for c in sources.DAILY_COLUMNS})
    d['gvkey']='000001';d['iid']='01';d['datadate']=dates
    d['historical_exchg']='11' if 'NYSE' in markets else '14'
    d['historical_tic']='NA';d['tic']='NA';d['cusip']='001234567';d['tpci']='0'
    d['curcdd']='USD';d['prcstd']=3.;d['cshtrd']=1000.;d['cshoc']=500.;d['prccd']=10.
    d['adrrc']=np.nan
    return d


def taq_frame(day):
    d=pd.DataFrame({'date':[day], 'taq_symbol':['NA'], 'listing_ex':['N'],
        'auction_volume':[100.], 'auction_volume_all':[100.], 'xvol_n':[100.], 'xvol_q':[np.nan],
        'xvol_p':[np.nan], 'xvol_a':[np.nan], 'xvol_z':[np.nan], 'auction_price':[10.], 'continuous_volume':[900.],
        'n_prints':[1], 'trf_close_volume':[0.]})
    for i,cp in enumerate(sources.CHECKPOINTS):
        if cp+':00'<=close_time(day):d['cumvol_'+cp]=100.*i
    return d


def history_frame(start,end,*args):
    return pd.DataFrame({'gvkey':['000001','000001'],'iid':['01','01'],
        'item':['EXCHG','TIC'],'itemvalue':['11','NA'],
        'efffrom':[start,start],'effthru':[end,end]})


class WrdsEnv:
    """Mixin: temporary data dir and mocked WRDS sources (not a TestCase, so never collected itself)."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.start='2022-01-03';self.end='2022-02-01'
        self.patches=[patch.object(sources,'discover',side_effect=lambda db,a,b,m:{'daily':sessions(a,b),'taq':sessions(a,b)}),
            patch.object(sources,'download_daily',side_effect=lambda db,a,b,m:daily_frame(a,b,m)),
            patch.object(sources,'download_taq',side_effect=lambda db,d:taq_frame(d)),
            patch.object(sources,'download_history',side_effect=lambda db,a,b,m:history_frame(a,b))]
        self.mocks=[p.start() for p in self.patches]
        self.addCleanup(lambda:[p.stop() for p in self.patches])
        self.addCleanup(self.temp.cleanup)

    def update(self,**kwargs):
        return update_data(self.start,kwargs.pop('end',self.end),data_dir=self.root,
            connection=object(),refresh_sessions=kwargs.pop('refresh_sessions',0),**kwargs)


START, END = '2022-01-03', '2022-02-01'


DATES = sessions(START, END)


def daily_for(ticker='NA', tpci='0', exch='11', gvkey='000001', dates=DATES):
    raw = daily_frame(dates[0], dates[-1])
    raw = raw[raw.datadate.isin(dates)].copy()
    raw['gvkey'] = gvkey; raw['historical_tic'] = ticker; raw['tic'] = ticker
    raw['tpci'] = tpci; raw['historical_exchg'] = exch
    return splits.annotate(daily_clean(raw))[0]


def history(gvkey='000001', ticker='NA', exch=(('11', START, '9999-12-31'),)):
    rows = [(gvkey, '01', 'EXCHG', e, a, b) for e, a, b in exch]
    rows.append((gvkey, '01', 'TIC', ticker, START, '9999-12-31'))
    return pd.DataFrame(rows, columns=['gvkey', 'iid', 'item', 'itemvalue', 'efffrom', 'effthru'])


def taq_for(symbol='NA', venue='xvol_n', dates=DATES, **override):
    t = pd.concat([taq_frame(d) for d in dates], ignore_index=True)
    t['taq_symbol'] = symbol
    t[['xvol_n', 'xvol_q']] = np.nan
    t[venue] = 100.
    for k, v in override.items():
        t[k] = v
    return t


def grid(daily, taq, hist, masks=None, end=END):
    return [(sid, g, s) for sid, g, s in security_grids(daily, taq, hist, START, end, ['NYSE', 'NASDAQ'], masks)]


def tree_digest(root):
    return {str(p.relative_to(root)): digest(p) for p in sorted(Path(root).rglob('*')) if p.is_file() and not p.is_symlink()}
