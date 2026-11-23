"""Feature quality report: missingness, history length, outliers, coverage, completeness, split and profile diagnostics.

Completeness of core_required is a review trigger (below COMPLETENESS_REVIEW_BELOW), never a build failure.
"""
from pathlib import Path

import numpy as np
import pandas as pd

from auction_data.storage import save_json

from .config import COMPLETENESS_FROM, COMPLETENESS_REVIEW_BELOW, CORE_REQUIRED, MCAP_JUMP, PROFILES
from .dictionary import feature_dictionary

QUANTILES = [0.001, 0.01, 0.5, 0.99, 0.999]


def _numeric_features(table):
    d = feature_dictionary()
    rows = d[d.tables.str.split('|').map(lambda t: table in t) & d.group.isin(
        ['core', 'diagnostic', 'intraday_core', 'profile', 'profile_ext'])]
    return rows.field.tolist()


def missing_table(daily):
    feats = [c for c in _numeric_features('daily') if c in daily]
    pit = daily.research_included_pit.to_numpy()
    year = daily.date.str[:4]
    rows = []
    for scope, sel in [('all', np.ones(len(daily), bool)), ('pit', pit)]:
        sub = daily.loc[sel, feats + ['market']].assign(year=year[sel])
        grp = sub.groupby(['year', 'market'])
        miss = grp[feats].agg(lambda s: s.isna().mean())
        n = grp.size()
        for (yr, mk), r in miss.iterrows():
            for f in feats:
                rows.append((scope, yr, mk, f, int(n[(yr, mk)]), float(r[f])))
        for f in feats:
            rows.append((scope, 'all', 'all', f, int(sel.sum()), float(daily.loc[sel, f].isna().mean())))
    return pd.DataFrame(rows, columns=['scope', 'year', 'market', 'feature', 'rows', 'missing_rate'])


def outlier_table(df, feats, scope):
    out = []
    for f in feats:
        v = pd.to_numeric(df[f], errors='coerce').to_numpy(float)
        fin = v[np.isfinite(v)]
        rec = dict(scope=scope, feature=f, n=len(v), n_finite=len(fin), n_inf=int(np.isinf(v).sum()))
        if len(fin):
            med = np.median(fin)
            mad = 1.4826 * np.median(np.abs(fin - med))
            rec.update({f'q{q}': float(np.quantile(fin, q)) for q in QUANTILES}, min=float(fin.min()),
                       max=float(fin.max()), robust_z_gt_8=int((np.abs(fin - med) > 8 * mad).sum()) if mad > 0 else 0)
        out.append(rec)
    return pd.DataFrame(out)


def completeness(daily):
    sel = daily.research_included_pit.to_numpy() & (daily.date.to_numpy() >= COMPLETENESS_FROM)
    sub = daily.loc[sel]
    complete = sub.core_required_complete.to_numpy()
    rate = float(complete.mean()) if len(sub) else float('nan')
    bad = sub.loc[~complete]
    # Sessions since the start of the row's continuous market interval.
    age = daily.groupby(['security_id', 'interval_id'], sort=False).cumcount().to_numpy()[sel][~complete]
    reasons = {f'missing_{c}': int(bad[c].isna().sum()) for c in CORE_REQUIRED}
    reasons.update(interval_younger_than_22_sessions=int((age < 22).sum()),
                   a_n22_below_15=int((bad.a_n22 < 15).sum()), a_last_age_missing=int(bad.a_last_age.isna().sum()),
                   factor_unknown=int(bad.factor_unknown.sum()), factor_carried=int(bad.factor_carried.sum()),
                   split_on_t=int(bad.split_on_t.sum()))
    return dict(scope=f'research_included_pit and date >= {COMPLETENESS_FROM}', rows=int(len(sub)),
                complete_rows=int(complete.sum()), rate=rate, review_trigger=COMPLETENESS_REVIEW_BELOW,
                needs_review=bool(rate < COMPLETENESS_REVIEW_BELOW), incomplete_reasons=reasons,
                incomplete_by_year=bad.date.str[:4].value_counts().sort_index().to_dict())


def split_diagnostics(daily):
    pit = daily.research_included_pit
    out = {}
    for c in ['split_on_t', 'factor_carried', 'factor_unknown', 'expost_split_issue_t', 'interval_start']:
        out[c] = dict(all=int(daily[c].sum()), pit=int((daily[c] & pit).sum()))
    sec = daily.security_id.to_numpy()
    same = np.r_[False, sec[1:] == sec[:-1]]
    lm = daily.logmcap_lag1.to_numpy(float)
    diff = np.abs(np.r_[np.nan, np.diff(lm)])
    jump = same & np.isfinite(diff) & (diff > np.log(MCAP_JUMP))
    split = daily.split_on_t.to_numpy()
    near = np.r_[False, split[:-1]] | np.r_[False, False, split[:-2]]  # split on t-1 or t-2 moves mcap_{t-1}
    out['logmcap_jumps'] = dict(all=int(jump.sum()), near_split=int((jump & near).sum()),
                                threshold=f'|delta log mcap| > ln({MCAP_JUMP})')
    return out


def profile_tables(profiles):
    cov = (profiles.groupby(['month', 'market', 'profile_status']).size().rename('securities').reset_index())
    ok = profiles[profiles.profile_status.eq('ok')]
    diag = {}
    for a, b in [('pf_dev_std', 'pf_dev_std_plain'), ('pf_dev_ac1', 'pf_dev_ac1_plain'),
                 ('pf_dev_ac1', 'pf_dev_ac1_spearman')]:
        diag[f'corr({a},{b})'] = float(ok[[a, b]].corr().iloc[0, 1]) if len(ok) > 2 else None
    # Test-retest across non-overlapping 126-session windows: month m versus month m+6.
    o = ok.assign(ord=pd.PeriodIndex(ok.month, freq='M').astype('int64'))
    later = o.assign(ord=o['ord'] - 6)
    pair = o.merge(later, on=['security_id', 'ord'], suffixes=('', '_6m'))
    retest = {p: (float(pair[[p, p + '_6m']].corr().iloc[0, 1]) if len(pair) > 2 else None) for p in PROFILES}
    corr = ok[PROFILES + ['pf_dollar_auc']].corr()
    early = profiles[profiles.month.between('2022-04', '2022-07')]
    early_missing = (early.assign(missing=~early.profile_status.eq('ok')).groupby(['month', 'market']).missing.mean()
                     .rename('missing_rate').reset_index())
    return cov, dict(robust_vs_plain=diag, retest_6m=retest, retest_pairs=int(len(pair)),
                     early_missing=early_missing.to_dict('records')), corr


def _clean(v):
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return None if not np.isfinite(v) else float(v)
    return v


def write_report(out_dir, daily, intraday, profiles):
    """Write report files into out_dir (a new directory); return the summary dict."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=False)
    missing_table(daily).to_csv(out / 'missing.csv', index=False)
    pit = daily[daily.research_included_pit]
    hist = pd.concat([pit[c].value_counts(dropna=False).rename('rows').rename_axis('value').reset_index()
                      .assign(column=c) for c in ['a_n22', 'd_n22', 'a_last_age', 'd_last_age']], ignore_index=True)
    hist.to_csv(out / 'history_length.csv', index=False)
    feats_i = ['C_b', 'mu_b', 'q_b']
    pd.concat([outlier_table(pit, _numeric_features('daily'), 'daily_pit'),
               outlier_table(intraday, feats_i, 'intraday_all'),
               outlier_table(profiles[profiles.profile_status.eq('ok')], _numeric_features('profiles'), 'profiles_ok')],
              ignore_index=True).to_csv(out / 'outliers.csv', index=False)
    cov = (daily.groupby('date').agg(pit_rows=('research_included_pit', 'sum'),
                                     core_complete_pit_rows=('core_required_complete',
                                                             lambda s: int((s & daily.loc[s.index, 'research_included_pit']).sum())))
           .reset_index())
    cov.to_csv(out / 'coverage_daily.csv', index=False)
    comp = completeness(daily)
    splits = split_diagnostics(daily)
    pcov, pdiag, pcorr = profile_tables(profiles)
    pcov.to_csv(out / 'profile_coverage.csv', index=False)
    pcorr.to_csv(out / 'profile_correlation.csv')
    intr = intraday.groupby('checkpoint').agg(rows=('q_b', 'size'), q_missing=('q_b', lambda s: float(s.isna().mean())),
                                              C_missing=('C_b', lambda s: float(s.isna().mean()))).reset_index()
    intr.to_csv(out / 'intraday_coverage.csv', index=False)
    summary = dict(rows=dict(daily=len(daily), intraday=len(intraday), profiles=len(profiles),
                             pit=int(daily.research_included_pit.sum())),
                   completeness=comp, split_and_factor=splits, profiles=pdiag,
                   profile_status=profiles.profile_status.value_counts().to_dict())
    summary = _clean(summary)
    save_json(out / 'summary.json', summary)
    return summary
