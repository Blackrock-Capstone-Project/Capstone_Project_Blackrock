"""Feature version, windows, minimum observations and profile thresholds (spec: openspec/changes/feature-panel)."""
from auction_data.settings import CHECKPOINTS

FEATURE_VERSION = 'features-v1'

MARKET_CODES = {'NYSE': 0, 'NASDAQ': 1}
MARKET_NAMES = {v: k for k, v in MARKET_CODES.items()}
LAST_WINDOW = 5                 # *_last: most recent usable observation within the 5 prior sessions
A_MA = {'a_ma5': (5, 3), 'a_ma22': (22, 15)}
D_MA = {'d_ma22': (22, 15)}
MU_WINDOW, MU_MIN = 20, 12      # intraday mu_b
CHECKPOINT_LIST = list(CHECKPOINTS)

PROFILE = dict(window=126, min_obs=60, min_pairs=50, clip=4.0, mad_scale=1.4826, eps=1e-6)

CORE = ['a_last', 'a_last_age', 'a_ma5', 'a_ma22', 'd_last', 'd_last_age', 'd_ma22', 'logmcap_lag1',
        'dow', 'month_end', 'quarter_end', 'opex']
CORE_REQUIRED = ['a_last', 'a_ma5', 'a_ma22', 'd_last', 'd_ma22', 'logmcap_lag1']
PROFILES = ['pf_lshare', 'pf_dev_std', 'pf_dev_ac1']

COMPLETENESS_FROM = '2022-04-01'
COMPLETENESS_REVIEW_BELOW = 0.995   # review trigger only; never a build failure
MCAP_JUMP = 1.8                     # |delta log market cap| > ln(1.8) counted near split days

INPUT_FILES = ['us_wrds_quality/stock_day_quality.csv.gz', 'us_wrds_daily/daily_clean.csv.gz',
               'us_wrds_taq/checkpoints_research.csv.gz']


def config_dict():
    return dict(feature_version=FEATURE_VERSION, last_window=LAST_WINDOW, a_ma=A_MA, d_ma=D_MA,
                mu=[MU_WINDOW, MU_MIN], checkpoints=CHECKPOINT_LIST, profile=PROFILE, core=CORE,
                core_required=CORE_REQUIRED, profiles=PROFILES, markets=MARKET_CODES,
                completeness_from=COMPLETENESS_FROM, completeness_review_below=COMPLETENESS_REVIEW_BELOW,
                mcap_jump=MCAP_JUMP, input_files=INPUT_FILES)
