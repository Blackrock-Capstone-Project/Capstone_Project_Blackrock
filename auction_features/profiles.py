"""Monthly behaviour profiles (unfitted) for one security; spec feature-panel design D5.

At the first session m of each month, using the 126 sessions before m and only U_A observations in the continuous
market interval of m:
  pf_lshare  = mean ln(label/PX_VOLUME)                              (n_A >= 60)
  e_s        = ln(1+label_s) - a_ma22_s  (a_ma22_s non-missing)        (n_e >= 60)
  pf_dev_std = 1.4826 * median|e - median(e)|
  e~         = clip(e, median(e) +/- 4 * pf_dev_std)
  pf_dev_ac1 = Pearson(e~_s, e~_prev(s)) with ddof=0, prev(s) = immediately preceding session, both in the e set
               (>= 50 pairs)
No standardisation, winsorisation or clustering happens here.
"""
import numpy as np
from scipy.stats import rankdata

from .config import PROFILE

FLOAT_FIELDS = ['pf_lshare', 'pf_dev_std', 'pf_dev_ac1', 'pf_dollar_auc', 'pf_dev_std_plain', 'pf_dev_ac1_plain',
                'pf_dev_ac1_spearman']


def pearson(x, y):
    sx, sy = x.std(), y.std()
    if len(x) < 2 or sx == 0 or sy == 0:
        return np.nan
    return float(((x - x.mean()) * (y - y.mean())).mean() / (sx * sy))


def profile_at(a, a_ma22, pm):
    """Profile record at dense position pm (first session of a month; may be outside the security's range)."""
    p = PROFILE
    rec = {k: np.nan for k in FLOAT_FIELDS}
    rec.update(n_A=0, n_e=0, n_p=0, market=-1, interval_id=-1, profile_status='not_listed')
    if pm < 0 or pm >= a['n'] or not a['listed'][pm]:
        return rec
    interval = int(a['interval'][pm])
    rec['market'] = int(a['market'][pm])
    rec['interval_id'] = interval
    lo = max(0, pm - p['window'])
    idx = np.arange(lo, pm)
    usable = a['U_A'][idx]
    s = idx[usable & (a['interval'][idx] == interval)]
    rec['n_A'] = len(s)
    with np.errstate(divide='ignore', invalid='ignore'):
        e_full = np.log1p(a['label']) - a_ma22
        if len(s) >= p['min_obs']:
            rec['pf_dollar_auc'] = float(np.mean(np.log(a['label'][s] * a['price'][s])))
    ok = s[np.isfinite(e_full[s])]
    in_e = np.zeros(a['n'], bool)
    in_e[ok] = True
    pairs = ok[(ok - 1 >= lo) & in_e[np.maximum(ok - 1, 0)]]
    rec['n_e'], rec['n_p'] = len(ok), len(pairs)
    if len(ok) >= 2:
        rec['pf_dev_std_plain'] = float(np.std(e_full[ok], ddof=1))
    if len(pairs) >= 2:
        rec['pf_dev_ac1_plain'] = pearson(e_full[pairs], e_full[pairs - 1])
        rec['pf_dev_ac1_spearman'] = pearson(rankdata(e_full[pairs]), rankdata(e_full[pairs - 1]))
    if len(s) < p['min_obs']:
        rec['profile_status'] = 'interval_break' if usable.sum() >= p['min_obs'] else 'insufficient_history'
        return rec
    if len(ok) < p['min_obs']:
        rec['profile_status'] = 'insufficient_history'
        return rec
    if len(pairs) < p['min_pairs']:
        rec['profile_status'] = 'insufficient_pairs'
        return rec
    e = e_full[ok]
    med = np.median(e)
    scale = p['mad_scale'] * np.median(np.abs(e - med))
    if scale <= p['eps']:
        rec['profile_status'] = 'degenerate_dispersion'
        return rec
    clipped = np.clip(e_full, med - p['clip'] * scale, med + p['clip'] * scale)
    ac1 = pearson(clipped[pairs], clipped[pairs - 1])
    if not np.isfinite(ac1):
        rec['profile_status'] = 'degenerate_dispersion'
        return rec
    with np.errstate(divide='ignore', invalid='ignore'):
        lshare = float(np.mean(np.log(a['label'][s] / a['pxv'][s])))
    rec.update(pf_lshare=lshare, pf_dev_std=float(scale), pf_dev_ac1=ac1, profile_status='ok')
    return rec


def month_starts(cal):
    """Calendar indices of the first session of each month and the month keys."""
    keys = [d[:7] for d in cal]
    idx = [i for i in range(len(cal)) if i == 0 or keys[i - 1] != keys[i]]
    return idx, [keys[i] for i in idx]
