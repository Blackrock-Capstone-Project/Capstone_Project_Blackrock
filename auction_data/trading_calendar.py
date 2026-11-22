"""XNYS trading sessions, close times and half days."""
from functools import lru_cache

import exchange_calendars as xc
import pandas as pd


@lru_cache(maxsize=16)
def _calendar(first_year, last_year):
    # sessions_in_range validates against first/last *trading* session, not the
    # requested calendar dates. Padding keeps Jan 1 / Dec 31 holidays in bounds.
    return xc.get_calendar('XNYS', start=f'{first_year - 1}-01-01',
                           end=f'{last_year + 1}-12-31')


def calendar(start, end):
    return _calendar(int(str(start)[:4]), int(str(end)[:4]))


def sessions(start, end):
    if str(start) > str(end):
        return []
    return calendar(start, end).sessions_in_range(start, end).strftime('%Y-%m-%d').tolist()


def close_time(day):
    return calendar(day, day).session_close(day).tz_convert('America/New_York').strftime('%H:%M:%S')


def completed_sessions(start, end, now=None):
    now = pd.Timestamp.now(tz='UTC') if now is None else pd.Timestamp(now)
    if now.tzinfo is None:
        raise ValueError('now must include timezone')
    return [s for s in sessions(start, end) if calendar(start, end).session_close(s) + pd.Timedelta(minutes=30) <= now]


def half_days(start, end):
    """Sessions closing before 16:00 New York time, with their actual close."""
    return {d: close_time(d) for d in sessions(start, end) if close_time(d) < '16:00:00'}
