"""Daily core features (stage f) for one security's dense arrays, plus XNYS calendar flags."""
import numpy as np
import pandas as pd

from .config import A_MA, D_MA, LAST_WINDOW
from .windows import converted, history_mask, last_valid, masked_mean, strict_lag1

A_WINDOW = max(w for w, _ in A_MA.values())
D_WINDOW = max(w for w, _ in D_MA.values())


def converted_family(a, usable, values, window, log1p):
    """Converted history values and the usable-slot mask (missing f_s makes a slot unusable)."""
    vals = converted(values * a['f'], a['f_t'], window, log1p)
    mask = history_mask(usable, a['seg'], a['seg_ref'], a['interval'], window) & np.isfinite(vals)
    return vals, mask


def daily_features(a):
    """Dense arrays: core daily features, strict-lag diagnostics and window counts."""
    out = {}
    vals, mask = converted_family(a, a['U_A'], a['label'], A_WINDOW, log1p=True)
    for name, (w, mp) in A_MA.items():
        out[name], cnt = masked_mean(vals[:, -w:], mask[:, -w:], mp)
        if name == 'a_ma22':
            out['a_n22'] = cnt.astype(float)
    out['a_last'], out['a_last_age'] = last_valid(vals[:, -LAST_WINDOW:], mask[:, -LAST_WINDOW:])
    out['a_lag1'] = strict_lag1(vals, mask)

    vals, mask = converted_family(a, a['U_V'], a['pxv'], D_WINDOW, log1p=False)
    for name, (w, mp) in D_MA.items():
        out[name], cnt = masked_mean(vals[:, -w:], mask[:, -w:], mp)
        if name == 'd_ma22':
            out['d_n22'] = cnt.astype(float)
    out['d_last'], out['d_last_age'] = last_valid(vals[:, -LAST_WINDOW:], mask[:, -LAST_WINDOW:])
    out['d_lag1'] = strict_lag1(vals, mask)

    # Same-day price x shares on t-1; no carry-forward of older shares or prices.
    with np.errstate(divide='ignore', invalid='ignore'):
        mcap = np.where(a['U_P'], np.log(a['price'] * a['shares']), np.nan)
    out['logmcap_lag1'] = np.concatenate([[np.nan], mcap[:-1]])
    return out


def calendar_flags(cal):
    """dow, month_end, quarter_end, opex for every session in cal (cal must extend past the data end)."""
    d = pd.to_datetime(pd.Series(cal))
    nxt = d.shift(-1)
    month_end = nxt.notna() & (nxt.dt.month != d.dt.month)
    quarter_end = month_end & d.dt.month.isin([3, 6, 9, 12])
    opex = np.zeros(len(d), bool)
    for (yr, mo), grp in d.groupby([d.dt.year, d.dt.month]):
        first = pd.Timestamp(year=yr, month=mo, day=1)
        third_friday = first + pd.Timedelta(days=(4 - first.weekday()) % 7 + 14)
        eligible = grp[grp <= third_friday]
        if len(eligible):
            opex[eligible.index[-1]] = True
    return pd.DataFrame({'dow': d.dt.dayofweek.to_numpy(np.int64), 'month_end': month_end.to_numpy(bool),
                         'quarter_end': quarter_end.to_numpy(bool), 'opex': opex})
