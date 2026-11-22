"""Market-wide excluded sessions: verified index-rebalance closes and XNYS half days."""
from pathlib import Path

import pandas as pd

from .settings import PipelineError
from .storage import digest
from .trading_calendar import half_days, sessions


EVENTS_PATH = Path(__file__).resolve().parent / 'reference' / 'index_events.csv'


EVENT_COLUMNS = ['event_family', 'index', 'announce_date', 'execution_close_date', 'effective_date',
                 'markets', 'source_url', 'verified', 'notes']


EVENT_FAMILIES = {'sp_quarterly', 'nasdaq100_annual', 'russell_reconstitution', 'msci_index_review'}


def load_events(path=EVENTS_PATH):
    events = pd.read_csv(path, dtype=str, keep_default_na=False)
    if list(events.columns) != EVENT_COLUMNS:
        raise PipelineError('Index event list has unexpected columns')
    if not events.event_family.isin(EVENT_FAMILIES).all():
        raise PipelineError('Index event list has unknown event family')
    if not events.verified.isin(['true', 'false']).all():
        raise PipelineError('Index event verified flag must be true/false')
    days = events.execution_close_date
    if days.eq('').any():
        raise PipelineError('Index event missing execution close date')
    valid = set(sessions(days.min(), days.max()))
    bad = sorted(set(days) - valid)
    if bad:
        raise PipelineError(f'Index event execution dates are not XNYS sessions: {bad}')
    markets = events.markets.str.split('|')
    if not markets.map(lambda m: set(m) <= {'NYSE', 'NASDAQ'} and len(m) > 0).all():
        raise PipelineError('Index event markets must be NYSE/NASDAQ separated by |')
    return events


def masked_sessions(events, start, end, markets):
    """One row per (date, market); an index event outranks a half day, multiple events count once."""
    rows = []
    for e in events.itertuples():
        if start <= e.execution_close_date <= end:
            rows += [(e.execution_close_date, m, 'event_day') for m in e.markets.split('|') if m in markets]
    for d in half_days(start, end):
        rows += [(d, m, 'half_day') for m in markets]
    mask = pd.DataFrame(rows, columns=['date', 'market', 'mask_reason'])
    mask['rank'] = mask.mask_reason.map({'event_day': 0, 'half_day': 1})
    return (mask.sort_values(['date', 'market', 'rank']).drop_duplicates(['date', 'market'])
                .drop(columns='rank').reset_index(drop=True))


def events_fingerprint(path=EVENTS_PATH):
    return digest(path)
