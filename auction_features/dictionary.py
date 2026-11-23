"""Feature dictionary: the single source for every output column of daily, intraday and profiles tables."""
import pandas as pd

PRE = 'before open of t'
PRE_M = 'before open of m'
POST = 't post-close'
CAL = 'known in advance'
UA = 'U_A valid_day; same continuous market interval; seg_s = seg_ref_t'
UV = 'U_V listed, unmasked, PX_VOLUME>0; same continuous market interval; seg_s = seg_ref_t'
UC = 'U_C applicable, volume_valid, unmasked; same continuous market interval; seg_s = seg_ref_t'
UP = 'U_P PX_LAST>0 (masked allowed) and shares_outstanding>0 on t-1; no carry-forward'
PF = 'U_A in the continuous market interval of m; 126 sessions before m'
CONV = '; converted with f_s/f_t'

# field, tables, stage, group, definition, source, window_min, usable, available_at, assumption_A1
FIELDS = [
    ('security_id', 'daily|intraday|profiles', 'key', 'key', 'GVKEY:IID', 'stock_day_quality', '', '', 'metadata', False),
    ('date', 'daily|intraday', 'key', 'key', 'XNYS session', 'stock_day_quality', '', '', 'metadata', False),
    ('checkpoint', 'intraday', 'key', 'key', 'New York time checkpoint', 'checkpoints_research', '', '', 'metadata', False),
    ('month', 'profiles', 'key', 'key', 'YYYY-MM of the profile', 'XNYS', '', '', 'metadata', False),
    ('m_date', 'profiles', 'key', 'metadata', 'first XNYS session of the month', 'XNYS', '', '', CAL, False),
    ('market', 'daily|intraday|profiles', 'key', 'metadata', 'historical listing market of the row (profiles: of m)',
     'sec_idhist EXCHG', '', '', PRE, False),
    ('ticker', 'daily', 'key', 'metadata', 'historical ticker', 'sec_idhist TIC', '', '', 'metadata', False),
    ('interval_id', 'daily|profiles', 'flag', 'metadata', 'continuous market interval number within the security '
     '(1-based; new interval after an unlisted session or a market change); profiles: interval of m, 0 if not listed', 'sec_idhist', '', '', PRE, False),
    ('interval_start', 'daily', 'flag', 'metadata', 't is the first session of its continuous market interval',
     'sec_idhist', '', '', PRE, False),
    ('y', 'daily', 'target', 'target', 'log1p(label_t) if valid_day_t else NaN', 'label', '', '', POST, False),
    ('label', 'daily', 'target', 'target', 'listing-venue closing cross shares (native, passthrough)', 'stock_day_quality',
     '', '', POST, False),
    ('label_status', 'daily', 'flag', 'metadata', 'passthrough', 'stock_day_quality', '', '', POST, False),
    ('valid_day', 'daily', 'flag', 'metadata', 'passthrough', 'stock_day_quality', '', '', POST, False),
    ('mask_reason', 'daily', 'flag', 'metadata', 'passthrough: event_day / half_day / empty', 'stock_day_quality', '', '',
     CAL, False),
    ('pit_eligible', 'daily', 'flag', 'metadata', 'passthrough (strict PIT, needs valid_day_t)', 'stock_day_quality', '',
     '', POST, False),
    ('research_status', 'daily', 'flag', 'metadata', 'passthrough (deferral is a full-window decision)',
     'stock_day_quality', '', '', 'ex-post', False),
    ('research_included_pit', 'daily', 'flag', 'metadata', 'passthrough; study sample filter', 'stock_day_quality', '',
     '', POST, False),
    ('research_included_expost', 'daily', 'flag', 'metadata', 'passthrough', 'stock_day_quality', '', '', 'ex-post', False),
    ('split_status', 'daily', 'flag', 'metadata', 'passthrough processed split status of t', 'stock_day_quality', '', '',
     POST, False),
    ('security_status', 'daily', 'flag', 'ex_post', 'passthrough ex-post 5% status; report only, never a feature',
     'stock_day_quality', '', '', 'ex-post', False),
    ('exclusion_reason', 'daily', 'flag', 'ex_post', 'passthrough funnel reason; report only, never a feature',
     'stock_day_quality', '', '', 'ex-post', False),
    ('split_on_t', 'daily', 'flag', 'flag', 'f_t differs from the last known factor before t', 'ajexdi', '', '', PRE, True),
    ('factor_carried', 'daily', 'flag', 'flag', 'f_t missing or <=0; last known factor before t used', 'ajexdi', '', '',
     PRE, False),
    ('factor_unknown', 'daily', 'flag', 'flag', 'no known factor at or before t; converted features NaN', 'ajexdi', '',
     '', PRE, False),
    ('expost_split_issue_t', 'daily', 'flag', 'ex_post', 'split_status_t in {split_unresolved, factor_missing}; needs '
     't close; never used for features or samples', 'split_status', '', '', POST, False),
    ('core_required_complete', 'daily', 'flag', 'flag', 'a_last, a_ma5, a_ma22, d_last, d_ma22, logmcap_lag1 all '
     'non-missing', 'derived', '', '', PRE, False),
    ('a_last', 'daily', 'daily', 'core', 'most recent usable log1p(V^A_s f_s/f_t) in the 5 prior sessions' + CONV,
     'label, ajexdi', '5/1', UA, PRE, True),
    ('a_last_age', 'daily', 'daily', 'core', 'sessions between t and the a_last observation (1-5)', 'derived', '5/1',
     UA, PRE, False),
    ('a_ma5', 'daily', 'daily', 'core', 'mean log1p(V^A_s f_s/f_t) over usable slots', 'label, ajexdi', '5/3', UA, PRE, True),
    ('a_ma22', 'daily', 'daily', 'core', 'mean log1p(V^A_s f_s/f_t) over usable slots', 'label, ajexdi', '22/15', UA,
     PRE, True),
    ('d_last', 'daily', 'daily', 'core', 'most recent usable log(PX_VOLUME_s f_s/f_t) in the 5 prior sessions',
     'cshtrd, ajexdi', '5/1', UV, PRE, True),
    ('d_last_age', 'daily', 'daily', 'core', 'sessions between t and the d_last observation (1-5)', 'derived', '5/1',
     UV, PRE, False),
    ('d_ma22', 'daily', 'daily', 'core', 'mean log(PX_VOLUME_s f_s/f_t) over usable slots', 'cshtrd, ajexdi', '22/15',
     UV, PRE, True),
    ('logmcap_lag1', 'daily', 'daily', 'core', 'strict t-1 log(PX_LAST * shares_outstanding)', 'prccd, cshoc, adrrc',
     '1/1', UP, PRE, False),
    ('dow', 'daily', 'daily', 'core', 'day of week 0=Mon (categorical)', 'XNYS', '', '', CAL, False),
    ('month_end', 'daily', 'daily', 'core', 'last XNYS session of the month', 'XNYS', '', '', CAL, False),
    ('quarter_end', 'daily', 'daily', 'core', 'last XNYS session of Mar/Jun/Sep/Dec', 'XNYS', '', '', CAL, False),
    ('opex', 'daily', 'daily', 'core', 'third Friday of the month, or the preceding session if closed', 'XNYS', '', '',
     CAL, False),
    ('a_lag1', 'daily', 'daily', 'diagnostic', 'strict t-1 auction value; NaN if t-1 unusable', 'label, ajexdi', '1/1',
     UA, PRE, True),
    ('d_lag1', 'daily', 'daily', 'diagnostic', 'strict t-1 volume value; NaN if t-1 unusable', 'cshtrd, ajexdi', '1/1',
     UV, PRE, True),
    ('a_n22', 'daily', 'daily', 'diagnostic', 'usable auction slots in the 22 prior sessions', 'derived', '', UA, PRE, False),
    ('d_n22', 'daily', 'daily', 'diagnostic', 'usable volume slots in the 22 prior sessions', 'derived', '', UV, PRE, False),
    ('C_b', 'intraday', 'intraday', 'intraday_core', 'continuous volume 09:30 to checkpoint b on t (native shares); '
     'NaN unless applicable and volume_valid', 'TAQ cumvol', '', 'applicable, volume_valid', 'checkpoint b on t', False),
    ('mu_b', 'intraday', 'intraday', 'intraday_core', 'mean log1p(C_{s,b} f_s/f_t) over usable slots',
     'TAQ cumvol, ajexdi', '20/12', UC, PRE, True),
    ('q_b', 'intraday', 'intraday', 'intraday_core', 'log1p(C_b) - mu_b', 'derived', '', '', 'checkpoint b on t', True),
    ('pf_lshare', 'profiles', 'profile', 'profile', 'mean ln(label_s/PX_VOLUME_s)', 'label, cshtrd', '126/60', PF,
     PRE_M, False),
    ('pf_dev_std', 'profiles', 'profile', 'profile', '1.4826*median|e-median(e)|, e = ln(1+label_s) - a_ma22_s',
     'derived', '126/60', PF, PRE_M, False),
    ('pf_dev_ac1', 'profiles', 'profile', 'profile', 'Pearson (ddof=0) of clipped e on adjacent-session pairs; clip at '
     'median +/- 4*pf_dev_std', 'derived', '126/50 pairs', PF, PRE_M, False),
    ('pf_dollar_auc', 'profiles', 'profile', 'profile_ext', 'mean ln(label_s * PX_LAST_s); optional size extension',
     'label, prccd', '126/60', PF, PRE_M, False),
    ('pf_dev_std_plain', 'profiles', 'profile', 'diagnostic', 'std(e, ddof=1)', 'derived', '', PF, PRE_M, False),
    ('pf_dev_ac1_plain', 'profiles', 'profile', 'diagnostic', 'Pearson of unclipped e on the same pairs', 'derived', '',
     PF, PRE_M, False),
    ('pf_dev_ac1_spearman', 'profiles', 'profile', 'diagnostic', 'Spearman of e on the same pairs', 'derived', '', PF,
     PRE_M, False),
    ('n_A', 'profiles', 'profile', 'diagnostic', 'usable observations in the window and interval', 'derived', '', PF,
     PRE_M, False),
    ('n_e', 'profiles', 'profile', 'diagnostic', 'observations with e', 'derived', '', PF, PRE_M, False),
    ('n_p', 'profiles', 'profile', 'diagnostic', 'adjacent-session e pairs', 'derived', '', PF, PRE_M, False),
    ('profile_status', 'profiles', 'profile', 'flag', 'ok / not_listed / interval_break / insufficient_history / '
     'insufficient_pairs / degenerate_dispersion', 'derived', '', '', PRE_M, False),
]
COLUMNS = ['field', 'tables', 'stage', 'group', 'definition', 'source', 'window_min', 'usable', 'available_at',
           'assumption_A1']


def feature_dictionary():
    return pd.DataFrame(FIELDS, columns=COLUMNS)


def table_fields(table):
    """Ordered output columns of one table, exactly as declared."""
    return [f[0] for f in FIELDS if table in f[1].split('|')]


def check_columns(table, columns):
    expected = table_fields(table)
    if list(columns) != expected:
        missing = sorted(set(expected) - set(columns))
        extra = sorted(set(columns) - set(expected))
        raise ValueError(f'{table} columns do not match the dictionary: missing={missing} extra={extra}')
