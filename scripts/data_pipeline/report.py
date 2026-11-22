"""Quality report: daily retained counts, filter funnel, label reconciliation, splits, events, PIT gap, diff."""
import json

import numpy as np
import pandas as pd

from .quality import EXCLUSION_ORDER, VENUE_COLUMNS


class Accumulator:
    """Streaming per-security tallies; never holds the full panel."""

    def __init__(self, dates):
        self.dates = list(dates)
        self.pos = {d: i for i, d in enumerate(self.dates)}
        n = len(self.dates)
        self.daily = {k: np.zeros(n, dtype=np.int64) for k in
                      ['listed', 'event_day', 'half_day', 'expected', 'valid', 'retained_expost', 'retained_pit']}
        self.reasons = dict.fromkeys(EXCLUSION_ORDER + ['eligible'], 0)
        self.reason_securities = {k: set() for k in self.reasons}
        self.reconciliation = {}
        self.label_status = {}
        self.pit = dict(expected=0, expost_only=0, pit_only=0, both=0, pit_rate_block_on_retained=0)

    def add(self, g):
        idx = g.date.map(self.pos).to_numpy()
        for key, mask in [('listed', np.ones(len(g), bool)), ('event_day', g.mask_reason.eq('event_day')),
                          ('half_day', g.mask_reason.eq('half_day')), ('expected', g.expected),
                          ('valid', g.valid_day), ('retained_expost', g.eligible), ('retained_pit', g.pit_eligible)]:
            np.add.at(self.daily[key], idx[np.asarray(mask, bool)], 1)
        reason = g.exclusion_reason.where(g.exclusion_reason.ne(''), 'eligible')
        for r, n in reason.value_counts().items():
            self.reasons[r] += int(n)
            self.reason_securities[r].add(g.security_id.iloc[0])
        e = g[g.expected]
        for col, store in [('venue_reconciliation', self.reconciliation), ('label_status', self.label_status)]:
            for k, n in e[col].value_counts().items():
                store[k] = store.get(k, 0) + int(n)
        self.pit['expected'] += len(e)
        self.pit['expost_only'] += int((e.eligible & ~e.pit_eligible).sum())
        self.pit['pit_only'] += int((~e.eligible & e.pit_eligible).sum())
        self.pit['both'] += int((e.eligible & e.pit_eligible).sum())
        rate_block = e.valid_day & e.prior_10_valid_days.ge(6) & ~e.pit_eligible & e.security_status.eq('retained')
        self.pit['pit_rate_block_on_retained'] += int(rate_block.sum())


def funnel(acc, security):
    s = security.security_status
    reason = security.reason.fillna('')
    in_window = ~s.eq('outside_window') & ~reason.eq('missing_effective_listing_market_boundary')
    rows = [('all_securities', len(security), None),
            ('listed_in_window', int(in_window.sum()), None),
            ('confirmed_common_stock', int((in_window & ~s.eq('non_common_stock') & ~reason.isin(
                ['unknown_security_type', 'multiple_security_types'])).sum()), None),
            ('with_listing_boundary', int(reason.eq('').sum()), sum(acc.reasons.values()))]
    remaining = sum(acc.reasons.values())
    steps = [('identity_matched', ['ambiguous_identity']),
             ('after_event_and_half_day_mask', ['event_day', 'half_day']),
             ('after_expost_5pct_rule', ['unresolved_security', 'security_abnormal_rate_gt_5pct']),
             ('after_prior_10_window', ['insufficient_prior_valid_days']),
             ('eligible_current_day_valid', ['invalid_current_day'])]
    for name, removed in steps:
        remaining -= sum(acc.reasons[r] for r in removed)
        rows.append((name, None, remaining))
    f = pd.DataFrame(rows, columns=['step', 'securities', 'stock_days'])
    detail = pd.DataFrame([(r, acc.reasons[r], len(acc.reason_securities[r])) for r in acc.reasons],
                          columns=['exclusion_reason', 'stock_days', 'securities_touched'])
    return f, detail


def status_summary(security):
    """Three aligned levels: all -> type groups -> confirmed common stock statuses."""
    s, r = security.security_status, security.reason.fillna('')
    type_unknown = s.eq('unresolved') & r.isin(['unknown_security_type', 'multiple_security_types'])
    other_pre = s.eq('unresolved') & r.eq('missing_effective_listing_market_boundary')
    confirmed = ~s.isin(['non_common_stock', 'outside_window']) & ~type_unknown & ~other_pre
    rows = [(0, 'all_securities', '', len(security)),
            (1, 'non_common_stock', 'all_securities', int(s.eq('non_common_stock').sum())),
            (1, 'outside_window', 'all_securities', int(s.eq('outside_window').sum())),
            (1, 'security_type_unknown', 'all_securities', int(type_unknown.sum())),
            (1, 'listing_boundary_missing', 'all_securities', int(other_pre.sum())),
            (1, 'confirmed_common_stock', 'all_securities', int(confirmed.sum())),
            (2, 'retained', 'confirmed_common_stock', int((confirmed & s.eq('retained')).sum())),
            (2, 'excluded_gt_5pct', 'confirmed_common_stock', int((confirmed & s.eq('excluded')).sum())),
            (2, 'unresolved', 'confirmed_common_stock', int((confirmed & s.eq('unresolved')).sum()))]
    out = pd.DataFrame(rows, columns=['level', 'category', 'parent', 'securities'])
    deferred = security[security.research_status.eq('deferred')].reason.value_counts()
    extra = pd.DataFrame([(3, 'deferred:' + k, 'research_status', int(v)) for k, v in deferred.items()],
                         columns=out.columns)
    return pd.concat([out, extra], ignore_index=True)


def outside_venue(taq):
    xv = taq[VENUE_COLUMNS].apply(pd.to_numeric, errors='coerce')
    mask = xv.isna().all(axis=1) & pd.to_numeric(taq.auction_volume_all, errors='coerce').gt(0)
    out = taq.loc[mask, ['taq_symbol', 'date', 'auction_volume_all', 'auction_price', 'n_prints']]
    summary = (out.groupby('taq_symbol').date.agg(['min', 'max', 'count'])
                  .rename(columns={'min': 'first_date', 'max': 'last_date', 'count': 'days'}).reset_index())
    return summary.sort_values(['days', 'taq_symbol'], ascending=[False, True])


def diff_previous(previous, current):
    cols = ['security_id', 'date', 'eligible', 'pit_eligible']  # present in every version of research_sample
    if previous is None or not previous.exists():
        return dict(previous='none')
    try:
        old = pd.read_csv(previous, usecols=cols, dtype={'security_id': str, 'date': str})
    except ValueError:
        return dict(previous='incompatible_schema')
    new = pd.read_csv(current, usecols=cols, dtype={'security_id': str, 'date': str})
    m = old.merge(new, on=['security_id', 'date'], how='outer', suffixes=('_old', '_new'), indicator=True)
    both = m[m._merge.eq('both')]
    return dict(previous='compared', added=int(m._merge.eq('right_only').sum()),
                removed=int(m._merge.eq('left_only').sum()),
                eligible_changed=int(both.eligible_old.ne(both.eligible_new).sum()),
                pit_eligible_changed=int(both.pit_eligible_old.ne(both.pit_eligible_new).sum()))


def plot_daily(daily, path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    d = daily.copy()
    d['date'] = pd.to_datetime(d.date)
    fig, ax = plt.subplots(figsize=(11, 4.5))
    regular = d.day_type.eq('regular')  # masked sessions are gaps in the line, not drops to zero
    ax.plot(d.date, d.retained_expost.where(regular), lw=1, label='retained (ex-post)')
    ax.plot(d.date, d.retained_pit.where(regular), lw=1, alpha=0.8, label='retained (PIT)')
    for kind, color in [('event_day', 'tab:red'), ('half_day', 'tab:orange')]:
        x = d.loc[d.day_type.eq(kind), 'date']
        ax.scatter(x, np.zeros(len(x)), marker='|', s=200, color=color, label=kind.replace('_', ' ') + ' (excluded)')
    ax.set_ylabel('stocks per session'); ax.set_title('Retained common stocks per session')
    ax.legend(loc='lower right', fontsize=8); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(path, dpi=120, metadata={'Software': None}); plt.close(fig)


def write(generation, acc, security, taq, split_issues, events, masks, half_day_map, previous, current):
    out = generation / 'report'
    out.mkdir(parents=True, exist_ok=True)
    files = []
    def save(name, frame):
        frame.to_csv(out / name, index=False)
        files.append('report/' + name)
    day_type = pd.Series('regular', index=acc.dates)
    event_dates = set(masks.loc[masks.mask_reason.eq('event_day'), 'date'])
    day_type[day_type.index.isin(half_day_map)] = 'half_day'
    day_type[day_type.index.isin(event_dates)] = 'event_day'
    daily = pd.DataFrame({'date': acc.dates, 'day_type': day_type.to_numpy(), **acc.daily,
                          'source_gap': 0})  # processing refuses to run with unverified source partitions
    save('daily_counts.csv', daily)
    plot_daily(daily, out / 'daily_counts.png'); files.append('report/daily_counts.png')
    f, detail = funnel(acc, security)
    save('funnel.csv', f)
    save('exclusion_reasons.csv', detail)
    save('excluded_securities.csv', security[security.security_status.eq('excluded')])
    save('unresolved_securities.csv', security[security.security_status.eq('unresolved')])
    save('security_status_summary.csv', status_summary(security))
    rec = pd.DataFrame(sorted(acc.reconciliation.items()), columns=['venue_reconciliation', 'expected_stock_days'])
    save('label_reconciliation.csv', rec)
    save('label_status.csv', pd.DataFrame(sorted(acc.label_status.items()), columns=['label_status', 'expected_stock_days']))
    save('outside_venue_crosses.csv', outside_venue(taq))
    save('split_issues.csv', split_issues)
    save('unverified_events.csv', events[events.verified.ne('true')])
    summary = dict(deferred_securities=int(security.research_status.eq('deferred').sum()),
                   deferred_reasons=security.loc[security.research_status.eq('deferred'), 'reason'].value_counts().to_dict(),
                   pit_vs_expost=acc.pit,
                   pit_expost_disagreement_share=((acc.pit['expost_only'] + acc.pit['pit_only']) / acc.pit['expected']
                                                  if acc.pit['expected'] else None),
                   masked_sessions={'event_day': int(len(event_dates)), 'half_day': len(half_day_map)},
                   events_total=len(events), events_unverified=int(events.verified.ne('true').sum()),
                   split_issue_counts=split_issues.issue.value_counts().to_dict(),
                   diff_vs_previous=diff_previous(previous, current))
    (out / 'summary.json').write_text(json.dumps(summary, indent=2, default=int) + '\n')
    files.append('report/summary.json')
    return files
