"""Synthetic panels, a synthetic processed version and a slow row-by-row reference for auction_features tests."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from auction_data import splits
from auction_data.storage import digest
from auction_data.trading_calendar import sessions
from auction_features.config import CHECKPOINT_LIST, INPUT_FILES, PROFILE

CAL = sessions('2023-01-03', '2023-12-29')
MASKED = {40: 'event_day', 41: 'event_day', 90: 'event_day', 130: 'half_day'}


def synth_panel(seed=0):
    """Five securities exercising gaps, transfers, splits, missing factors, masked days and missing labels."""
    rng = np.random.default_rng(seed)
    rows = []
    for k in range(5):
        sid = f'00000{k}:01'
        price, shares = 50.0 + 10 * k, 1e6 * (k + 1)
        level = 8 + k
        for i, d in enumerate(CAL):
            if k == 2 and 100 <= i < 120:          # unlisted gap (relisting later, same market)
                continue
            if k == 4 and i < 60:                  # late listing
                continue
            market = 'NYSE'
            if k == 1 and 100 <= i < 180:          # NYSE -> NASDAQ -> NYSE
                market = 'NASDAQ'
            factor = 1.0
            px = price * float(np.exp(rng.normal(0, 0.01)))
            price = px
            sh = shares
            vol_scale = 1.0
            if k == 3:                             # consistent 2:1 split at session 120
                if i < 120:
                    factor = 2.0
                else:
                    px, vol_scale = px / 2, 2.0
                    sh = shares * 2 if i >= 121 else shares   # shares update one day late
                if i == 60:
                    factor = np.nan                # missing factor
            if k == 0 and i >= 150:                # unexplained factor change (price unchanged) -> unresolved
                factor = 3.0
            label = float(np.exp(level + rng.normal(0, 0.5))) * vol_scale
            if rng.random() < 0.06:
                label = np.nan
            pxv = float(np.exp(level + 2.3 + rng.normal(0, 0.3))) * vol_scale
            if k == 0 and i == 30:
                sh = np.nan                        # missing shares
            mask = MASKED.get(i, '')
            valid = bool(np.isfinite(label) and pxv > 0 and mask == '')
            cv = np.cumsum(rng.uniform(0.1, 0.2, len(CHECKPOINT_LIST))) * pxv
            rec = dict(date=d, security_id=sid, market=market, ticker=f'T{k}', mask_reason=mask, PX_VOLUME=pxv,
                       adjustment_factor=factor, label=label,
                       label_status='ok' if np.isfinite(label) else 'listing_venue_cross_missing',
                       valid_day=valid, pit_eligible=valid, research_status='in_scope',
                       research_included_pit=valid and i > 10, research_included_expost=valid and i > 10,
                       security_status='retained', exclusion_reason='' if valid else 'invalid_current_day',
                       PX_LAST=px, shares_outstanding=sh, cp_row=(i % 7 != 3))
            for cp, v in zip(CHECKPOINT_LIST, cv):
                rec['cv_' + cp] = v if rec['cp_row'] and not (mask == 'half_day' and cp >= '14:00') else np.nan
            rows.append(rec)
    p = pd.DataFrame(rows)
    daily = p.assign(source_cshoc=p.shares_outstanding, ticker=p.ticker)
    annotated, _ = splits.annotate(daily[['security_id', 'date', 'adjustment_factor', 'PX_LAST', 'source_cshoc',
                                          'ticker']])
    p['split_status'] = annotated.split_status.to_numpy()
    p['split_segment_id'] = annotated.split_segment_id.astype(float).to_numpy()
    return p.sort_values(['security_id', 'date'], kind='mergesort').reset_index(drop=True)


# ---------------------------------------------------------------- synthetic processed version

def write_processed(root, panel, version='wrds-process-test'):
    """A processed version directory with the three input tables and a manifest carrying their sha256."""
    root = Path(root)
    vdir = root / 'processed' / '.wrds_versions' / 'v1'
    for rel in INPUT_FILES:
        (vdir / rel).parent.mkdir(parents=True, exist_ok=True)
    gz = {'method': 'gzip', 'mtime': 0}
    q = panel.drop(columns=['PX_LAST', 'shares_outstanding', 'cp_row'] + ['cv_' + c for c in CHECKPOINT_LIST])
    q.to_csv(vdir / INPUT_FILES[0], index=False, compression=gz)
    panel[['security_id', 'date', 'PX_LAST', 'shares_outstanding']].to_csv(vdir / INPUT_FILES[1], index=False,
                                                                          compression=gz)
    cp = panel[panel.cp_row]
    long = []
    for c in CHECKPOINT_LIST:
        v = cp['cv_' + c]
        long.append(pd.DataFrame({'security_id': cp.security_id, 'date': cp.date, 'checkpoint': c,
                                  'cumulative_volume': v,
                                  'checkpoint_status': np.where(v.notna(), 'applicable', 'not_applicable'),
                                  'volume_valid': v.notna()}))
    pd.concat(long).sort_values(['security_id', 'date', 'checkpoint']).to_csv(vdir / INPUT_FILES[2], index=False,
                                                                              compression=gz)
    manifest = dict(version=version, fingerprint='f' * 64, config=dict(common_end=panel.date.max()),
                    files={rel: dict(sha256=digest(vdir / rel)) for rel in INPUT_FILES})
    (vdir / 'manifest.json').write_text(json.dumps(manifest))
    link = root / 'processed' / 'wrds_current'
    link.symlink_to(Path('.wrds_versions') / 'v1', target_is_directory=True)
    return vdir


# ---------------------------------------------------------------- slow reference

def _fin(x):
    return x is not None and np.isfinite(x)


def reference(panel, cal=CAL):
    """Row-by-row reimplementation of the spec: daily core, mu_b and profiles."""
    panel = panel.assign(cv14=panel['cv_14:00'])  # itertuples cannot address 'cv_14:00'
    daily, intr, prof = {}, {}, {}
    month_first = {}
    for i, d in enumerate(cal):
        month_first.setdefault(d[:7], i)
    for sid, g in panel.groupby('security_id', sort=True):
        g = g.sort_values('date')
        idx = {r.date: r for r in g.itertuples()}
        first, last = cal.index(g.date.iloc[0]), cal.index(g.date.iloc[-1])
        seq = cal[first:last + 1]
        interval, cur, prev_listed, prev_m = {}, -1, False, None
        for d in seq:
            if d in idx:
                m = idx[d].market
                if not prev_listed or m != prev_m:
                    cur += 1
                interval[d], prev_listed, prev_m = cur, True, m
            else:
                prev_listed = False

        def f_of(d):
            v = idx[d].adjustment_factor if d in idx else np.nan
            return v if _fin(v) and v > 0 else np.nan

        def seg_ref(i):
            for j in range(i - 1, -1, -1):
                if seq[j] in idx and _fin(idx[seq[j]].split_segment_id):
                    return idx[seq[j]].split_segment_id
            return np.nan

        def f_t(i):
            for j in range(i, -1, -1):
                if _fin(f_of(seq[j])):
                    return f_of(seq[j])
            return np.nan

        def hist(i, w, usable, value, log1p):
            ft, sr, t = f_t(i), seg_ref(i), seq[i]
            vals = []
            for j in range(i - w, i):
                if j < 0 or seq[j] not in idx:
                    vals.append(None)
                    continue
                r = idx[seq[j]]
                fs = f_of(seq[j])
                ok = usable(r) and interval[seq[j]] == interval[t] and r.split_segment_id == sr and _fin(fs) and _fin(ft)
                if ok:
                    x = value(r) * fs / ft
                    vals.append(np.log1p(x) if log1p else np.log(x))
                else:
                    vals.append(None)
            return vals

        def mean_min(vals, mp):
            v = [x for x in vals if x is not None]
            return float(np.mean(v)) if len(v) >= mp else np.nan

        def last5(vals):
            for k, x in enumerate(reversed(vals[-5:])):
                if x is not None:
                    return x, k + 1
            return np.nan, np.nan

        ua = lambda r: bool(r.valid_day)
        uv = lambda r: r.mask_reason == '' and _fin(r.PX_VOLUME) and r.PX_VOLUME > 0
        a22, la22 = {}, {}
        for i, t in enumerate(seq):
            if t not in idx:
                continue
            A = hist(i, 22, ua, lambda r: r.label, True)
            D = hist(i, 22, uv, lambda r: r.PX_VOLUME, False)
            rec = dict(a_ma22=mean_min(A, 15), a_ma5=mean_min(A[-5:], 3), d_ma22=mean_min(D, 15),
                       a_lag1=A[-1] if A[-1] is not None else np.nan, d_lag1=D[-1] if D[-1] is not None else np.nan)
            rec['a_last'], rec['a_last_age'] = last5(A)
            rec['d_last'], rec['d_last_age'] = last5(D)
            rec['a_n22'] = float(sum(x is not None for x in A))
            prev = seq[i - 1] if i > 0 else None
            if prev in idx and _fin(idx[prev].PX_LAST) and idx[prev].PX_LAST > 0 and \
                    _fin(idx[prev].shares_outstanding) and idx[prev].shares_outstanding > 0:
                rec['logmcap_lag1'] = float(np.log(idx[prev].PX_LAST * idx[prev].shares_outstanding))
            else:
                rec['logmcap_lag1'] = np.nan
            rec['interval_id'] = interval[t] + 1
            daily[(sid, t)] = rec
            a22[t] = rec['a_ma22']
            la22[t] = mean_min(hist(i, 22, ua, lambda r: r.label, False), 15)
            C = hist(i, 20, lambda r: r.mask_reason == '' and _fin(r.cv14) and r.cv14 >= 0, lambda r: r.cv14, True)
            intr[(sid, t)] = mean_min(C, 12)
        # profiles
        months = sorted({d[:7] for d in g.date})
        for mo in months:
            gi = month_first[mo]
            pm = gi - first
            key = (sid, mo)
            if pm < 0 or seq[pm] not in idx:
                prof[key] = dict(profile_status='not_listed')
                continue
            m_int = interval[seq[pm]]
            window = [seq[j] for j in range(max(0, pm - PROFILE['window']), pm)]
            S = [d for d in window if d in idx and idx[d].valid_day and interval[d] == m_int]
            any_ok = [d for d in window if d in idx and idx[d].valid_day]
            e = {d: np.log(idx[d].label) - la22[d] for d in S if _fin(la22[d])}
            pairs = [d for d in e if seq.index(d) - 1 >= max(0, pm - PROFILE['window']) and seq[seq.index(d) - 1] in e]
            rec = dict(n_A=len(S), n_e=len(e), n_p=len(pairs))
            if len(S) < 60:
                rec['profile_status'] = 'interval_break' if len(any_ok) >= 60 else 'insufficient_history'
            elif len(e) < 60:
                rec['profile_status'] = 'insufficient_history'
            elif len(pairs) < 50:
                rec['profile_status'] = 'insufficient_pairs'
            else:
                ev = np.array(list(e.values()))
                med = np.median(ev)
                sc = 1.4826 * np.median(np.abs(ev - med))
                clip = {d: min(max(v, med - 4 * sc), med + 4 * sc) for d, v in e.items()}
                x = np.array([clip[d] for d in pairs])
                y = np.array([clip[seq[seq.index(d) - 1]] for d in pairs])
                ac = float(np.mean((x - x.mean()) * (y - y.mean())) / (x.std() * y.std()))
                rec.update(profile_status='ok', pf_dev_std=sc, pf_dev_ac1=ac,
                           pf_lshare=float(np.mean([np.log(idx[d].label / idx[d].PX_VOLUME) for d in S])))
            prof[key] = rec
    return daily, intr, prof
