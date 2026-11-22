"""Split share conversion from Compustat AJEXDI (cumulative adjustment factor, larger before a split).

History volume on day s in day-t share units is vol_s * ajexdi_s / ajexdi_t. Only the RATIO is used, so
splits after the download snapshot (which rescale every AJEXDI by the same constant) cancel out; the
conversion is only valid within one split_segment_id.
"""
import numpy as np
import pandas as pd

PRICE_TOLERANCE = 0.20   # |price ratio / factor ratio - 1| above this -> split_unresolved
JUMP_RATIO = 1.8         # unexplained price or shares-outstanding jump flagged for review only


def to_basis(vol_s, ajexdi_s, ajexdi_t):
    """Convert native shares on day s to day-t share units."""
    return vol_s * ajexdi_s / ajexdi_t


def annotate(daily):
    """Add split_status / split_segment_id per row; return (daily, issues).

    daily needs security_id, date, adjustment_factor, PX_LAST, source_cshoc; rows of one security
    are compared with that security's previous available row."""
    d = daily.reset_index(drop=True).sort_values(['security_id', 'date'])
    f = pd.to_numeric(d.adjustment_factor, errors='coerce')
    px = pd.to_numeric(d.PX_LAST, errors='coerce')
    sh = pd.to_numeric(d.source_cshoc, errors='coerce')
    first = d.security_id.ne(d.security_id.shift())
    f_prev, px_prev, sh_prev = f.shift(), px.shift(), sh.shift()
    bad = ~(np.isfinite(f) & f.gt(0))
    change = ~first & ~bad & ~bad.shift(fill_value=False) & f.ne(f_prev)
    factor_ratio = f_prev / f
    price_ratio = px_prev / px
    consistent = (price_ratio / factor_ratio - 1).abs().le(PRICE_TOLERANCE)
    unresolved = change & ~consistent.fillna(False)
    d['split_status'] = np.select([bad, unresolved, change], ['factor_missing', 'split_unresolved', 'split_applied'],
                                  default='ok')
    # A break starts a new segment: inconsistent factor changes, missing factors and the row after one.
    brk = first | unresolved | bad | bad.shift(fill_value=False)
    d['split_segment_id'] = brk.groupby(d.security_id).cumsum().astype(int)
    log_jump = np.log(JUMP_RATIO)
    with np.errstate(divide='ignore', invalid='ignore'):  # zero prices/shares give inf -> flagged as jumps
        jump = ~first & ~change & ~bad & (np.log(price_ratio).abs().gt(log_jump) | np.log(sh / sh_prev).abs().gt(log_jump))
    issues = d.loc[unresolved | bad | jump, ['security_id', 'date', 'ticker', 'adjustment_factor', 'PX_LAST', 'source_cshoc',
                                             'split_status']].copy()
    issues['factor_ratio'] = factor_ratio[issues.index]
    issues['price_ratio'] = price_ratio[issues.index]
    issues['issue'] = np.select([bad[issues.index], unresolved[issues.index]],
                                ['factor_missing', 'split_unresolved'], default='unexplained_jump')
    return d.sort_index(), issues.reset_index(drop=True)
