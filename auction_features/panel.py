"""Pinned processed input and the per-security history panel.

Usability per family (spec feature-panel):
  U_A auction  = valid_day (label and volume > 0, unique identity, unmasked, listed)
  U_V volume   = listed, unmasked, PX_VOLUME finite > 0
  U_C checkpt  = applicable, volume_valid, unmasked
  U_P price    = PX_LAST and shares_outstanding finite > 0 (masked sessions allowed; strict t-1 only)
U_A/U_V/U_C history must lie in the same continuous market interval as t and in seg_ref_t, the last known
split_segment_id at or before t-1. Values are converted with f_t (assumption A1); a missing f_t carries the last
known factor (factor_carried); with no known factor, converted features are NaN (factor_unknown).
"""
from pathlib import Path

import numpy as np
import pandas as pd

from auction_data.storage import digest, read_json
from auction_data.trading_calendar import sessions

from .config import CHECKPOINT_LIST, INPUT_FILES, MARKET_CODES
from .windows import ffill_before

QUALITY_COLUMNS = ['date', 'security_id', 'market', 'ticker', 'mask_reason', 'PX_VOLUME', 'adjustment_factor',
                   'split_segment_id', 'split_status', 'label', 'label_status', 'valid_day', 'pit_eligible',
                   'research_status', 'research_included_pit', 'research_included_expost', 'security_status',
                   'exclusion_reason']
TEXT = ['security_id', 'market', 'ticker', 'mask_reason', 'split_status', 'label_status', 'research_status',
        'security_status', 'exclusion_reason']
BOOLS = ['valid_day', 'pit_eligible', 'research_included_pit', 'research_included_expost']
CP_COLUMNS = ['cv_' + cp for cp in CHECKPOINT_LIST]


# ---------------------------------------------------------------- pinned input

def resolve_processed(data_dir=None, processed_dir=None):
    """Resolve the processed version directory once (wrds_current is followed to its real target)."""
    if processed_dir is not None:
        path = Path(processed_dir).resolve()
    else:
        path = (Path(data_dir) / 'processed' / 'wrds_current').resolve()
    if not (path / 'manifest.json').is_file():
        raise ValueError(f'No processed manifest in {path}')
    return path


def input_identity(processed):
    """Processed version, fingerprint and input digests verified against the processed manifest."""
    processed = Path(processed)
    m = read_json(processed / 'manifest.json')
    digests = {}
    for name in INPUT_FILES:
        expected = m['files'][name]['sha256']
        actual = digest(processed / name)
        if actual != expected:
            raise ValueError(f'Input digest mismatch for {name}')
        digests[name] = actual
    return dict(dir=str(processed), version=m['version'], fingerprint=m['fingerprint'],
                manifest_sha256=digest(processed / 'manifest.json'), common_end=m['config']['common_end'],
                inputs=digests)


def recheck_identity(identity):
    """Fail if the pinned processed version changed while building."""
    again = input_identity(identity['dir'])
    if again != identity:
        raise ValueError('Processed input changed during the feature build')


# ---------------------------------------------------------------- loading

def _read(path, usecols, dtype=None):
    return pd.read_csv(path, usecols=usecols, dtype=dtype, keep_default_na=False, na_values=[''])


def load_panel(processed):
    """One row per stock_day_quality row with prices, shares and wide checkpoint volumes; sorted by security, date."""
    processed = Path(processed)
    q = _read(processed / INPUT_FILES[0], QUALITY_COLUMNS, dtype={c: str for c in TEXT})
    for c in TEXT:
        if c != 'security_id':
            q[c] = q[c].fillna('')
    for c in BOOLS:
        q[c] = q[c].astype(bool)
    d = _read(processed / INPUT_FILES[1], ['security_id', 'date', 'PX_LAST', 'shares_outstanding'],
              dtype={'security_id': str})
    q = q.merge(d, on=['security_id', 'date'], how='left', validate='one_to_one')
    del d
    cp = _read(processed / INPUT_FILES[2],
               ['security_id', 'date', 'checkpoint', 'cumulative_volume', 'checkpoint_status', 'volume_valid'],
               dtype={'security_id': str, 'checkpoint': str, 'checkpoint_status': str, 'volume_valid': str})
    q = q.merge(checkpoint_wide(cp), on=['security_id', 'date'], how='left', validate='one_to_one')
    q['cp_row'] = q.cp_row.astype('boolean').fillna(False).astype(bool)
    return q.sort_values(['security_id', 'date'], kind='mergesort').reset_index(drop=True)


def checkpoint_wide(cp):
    """Wide checkpoint volumes; a value is kept only when applicable and volume_valid."""
    valid = cp.checkpoint_status.eq('applicable') & cp.volume_valid.eq('True')
    cp = cp.assign(value=cp.cumulative_volume.where(valid))
    wide = cp.pivot(index=['security_id', 'date'], columns='checkpoint', values='value')
    wide = wide.reindex(columns=CHECKPOINT_LIST)
    wide.columns = CP_COLUMNS
    wide['cp_row'] = True
    return wide.reset_index()


def calendar_sessions(panel, extra_days=60):
    """Session list covering the panel (plus future sessions for calendar flags) and each row's session index."""
    first, last = panel.date.min(), panel.date.max()
    start = first[:8] + '01'  # month starts must be real first sessions even if the data starts mid-month
    end = (pd.Timestamp(last) + pd.Timedelta(days=extra_days)).strftime('%Y-%m-%d')
    cal = sessions(start, end)
    sidx = pd.Index(cal).get_indexer(panel.date)
    if (sidx < 0).any():
        raise ValueError('Panel contains non-session dates')
    return cal, sidx


def security_arrays(panel):
    """Numeric column arrays used by the per-security computation (no object columns)."""
    out = dict(label=panel.label.to_numpy(float), pxv=panel.PX_VOLUME.to_numpy(float),
               f=panel.adjustment_factor.to_numpy(float), seg=panel.split_segment_id.to_numpy(float),
               masked=panel.mask_reason.ne('').to_numpy(), valid=panel.valid_day.to_numpy(bool),
               market=panel.market.map(MARKET_CODES).fillna(-1).astype(np.int64).to_numpy(),
               price=panel.PX_LAST.to_numpy(float), shares=panel.shares_outstanding.to_numpy(float),
               cp_row=panel.cp_row.to_numpy(bool))
    for c in CP_COLUMNS:
        out[c] = panel[c].to_numpy(float)
    return out


# ---------------------------------------------------------------- dense per-security arrays

def dense_security(rows, sidx):
    """Dense arrays (one slot per session from the first to the last listed session) for one security."""
    pos = sidx - sidx[0]
    n = int(pos[-1]) + 1

    def put(values, fill, dtype=float):
        out = np.full(n, fill, dtype=dtype)
        out[pos] = values
        return out

    listed = put(True, False, bool)
    masked = put(rows['masked'], True, bool)
    market = put(rows['market'], -1, np.int64)
    f = put(rows['f'], np.nan)
    f = np.where(np.isfinite(f) & (f > 0), f, np.nan)
    pxv = put(rows['pxv'], np.nan)
    price = put(rows['price'], np.nan)
    shares = put(rows['shares'], np.nan)
    a = dict(pos=pos, n=n, listed=listed, masked=masked, market=market, f=f, seg=put(rows['seg'], np.nan),
             label=put(rows['label'], np.nan), pxv=pxv, price=price, shares=shares)
    # Continuous market interval: a new interval after an unlisted session or a market change.
    prev_listed = np.concatenate([[False], listed[:-1]])
    prev_market = np.concatenate([[-1], market[:-1]])
    start = listed & (~prev_listed | (market != prev_market))
    interval = np.cumsum(start) - 1
    a['interval'] = np.where(listed, interval, -1).astype(np.int64)
    a['interval_start'] = start
    a['U_A'] = put(rows['valid'], False, bool)
    a['U_V'] = listed & ~masked & np.isfinite(pxv) & (pxv > 0)
    a['U_P'] = listed & np.isfinite(price) & (price > 0) & np.isfinite(shares) & (shares > 0)
    # Forecast-time quantities, known before the open of t.
    a['seg_ref'] = ffill_before(a['seg'])
    a['f_prev'] = ffill_before(f)
    a['f_t'] = np.where(np.isfinite(f), f, a['f_prev'])
    a['factor_carried'] = ~np.isfinite(f) & np.isfinite(a['f_prev'])
    a['factor_unknown'] = ~np.isfinite(a['f_t'])
    a['split_on_t'] = np.isfinite(f) & np.isfinite(a['f_prev']) & (f != a['f_prev'])
    for cp in CP_COLUMNS:
        v = put(rows[cp], np.nan)
        a[cp] = v
        a['U_' + cp] = listed & ~masked & np.isfinite(v) & (v >= 0)
    return a
