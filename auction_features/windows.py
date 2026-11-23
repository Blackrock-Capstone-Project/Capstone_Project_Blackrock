"""Pure window functions on one security's dense session array (one slot per XNYS session, no IO).

Row t of a window matrix holds slots t-w .. t-1 (column w-1 is t-1); slots before the array start are empty.
Masked, unlisted or otherwise unusable slots occupy their position and are never backfilled.
"""
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view


def window_matrix(x, w, fill):
    x = np.asarray(x)
    padded = np.concatenate([np.full(w, fill, dtype=x.dtype), x])
    return sliding_window_view(padded, w)[:len(x)]


def _last_index(finite, strict):
    idx = np.where(finite, np.arange(len(finite)), -1)
    np.maximum.accumulate(idx, out=idx)
    if strict:
        idx = np.concatenate([[-1], idx[:-1]])
    return idx


def ffill_before(x):
    """Last finite value strictly before each slot (NaN if none)."""
    x = np.asarray(x, dtype=float)
    idx = _last_index(np.isfinite(x), strict=True)
    out = np.full(len(x), np.nan)
    out[idx >= 0] = x[idx[idx >= 0]]
    return out


def history_mask(usable, seg, seg_ref, interval, w):
    """Usable slots for each t: family condition, same forecast-time segment, same continuous market interval."""
    m = window_matrix(np.asarray(usable, bool), w, False)
    m = m & (window_matrix(np.asarray(seg, float), w, np.nan) == np.asarray(seg_ref, float)[:, None])
    iv = np.asarray(interval, np.int64)
    return m & (window_matrix(iv, w, -1) == iv[:, None]) & (iv[:, None] >= 0)


def converted(scaled, f_t, w, log1p):
    """log1p or log of (value_s * f_s) / f_t for the w prior slots; scaled = value * f_s (NaN if f_s unknown)."""
    r = window_matrix(np.asarray(scaled, float), w, np.nan) / np.asarray(f_t, float)[:, None]
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.log1p(r) if log1p else np.log(r)


def masked_mean(vals, mask, min_periods):
    cnt = mask.sum(axis=1)
    total = np.where(mask, vals, 0.0).sum(axis=1)
    with np.errstate(divide='ignore', invalid='ignore'):
        out = total / cnt
    out[cnt < min_periods] = np.nan
    return out, cnt


def last_valid(vals, mask):
    """Rightmost usable value in each row and its age in sessions (1 = t-1)."""
    n, w = mask.shape
    rev = mask[:, ::-1]
    found = rev.any(axis=1)
    k = rev.argmax(axis=1)
    out = vals[np.arange(n), w - 1 - k].astype(float)
    age = (k + 1).astype(float)
    out[~found] = np.nan
    age[~found] = np.nan
    return out, age


def strict_lag1(vals, mask):
    return np.where(mask[:, -1], vals[:, -1], np.nan).astype(float)
