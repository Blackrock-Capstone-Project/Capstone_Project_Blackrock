"""Intraday core features (stage g_b): C_b, mu_b, q_b per checkpoint for one security's dense arrays."""
import numpy as np

from .config import CHECKPOINT_LIST, MU_MIN, MU_WINDOW
from .daily import converted_family
from .windows import masked_mean


def intraday_features(a):
    """{checkpoint: (C_b, mu_b, q_b)} dense arrays. C_b is NaN unless applicable and volume_valid."""
    out = {}
    for cp in CHECKPOINT_LIST:
        c = a['cv_' + cp]
        vals, mask = converted_family(a, a['U_cv_' + cp], c, MU_WINDOW, log1p=True)
        mu, _ = masked_mean(vals, mask, MU_MIN)
        c_t = np.where(np.isfinite(c) & (c >= 0), c, np.nan)
        out[cp] = (c_t, mu, np.log1p(c_t) - mu)
    return out
