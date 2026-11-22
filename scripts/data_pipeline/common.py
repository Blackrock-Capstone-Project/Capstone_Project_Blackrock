"""Calendar, atomic storage and validation shared by download and processing."""
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
import datetime as dt
import fcntl
import gzip
import io
import hashlib
import json
import os
import uuid

import exchange_calendars as xc
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW_VERSION = 'wrds-v1'          # raw partition metadata + query_version; bumping forces re-download
PROCESS_VERSION = 'wrds-process-v3'  # processing rules; bumping only rebuilds processed outputs
MARKETS = {'NYSE': '11', 'NASDAQ': '14'}


class PipelineError(RuntimeError):
    """A safe, credential-free public error."""


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


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str, allow_nan=False) + '\n')
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def read_csv(path, **kwargs):
    return pd.read_csv(path, keep_default_na=False, na_values=[''], **kwargs)


@contextmanager
def writer_lock(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.pipeline.lock').open('a') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PipelineError('Another update/process owns this data directory') from None
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def read_partition(path, request=None):
    path = Path(path)
    meta = read_json(str(path) + '.json')
    if meta.get('version') != RAW_VERSION or (request is not None and meta['request'] != request):
        raise PipelineError('Unverified partition version or request')
    if digest(path) != meta['sha256']:
        raise PipelineError('Partition fingerprint mismatch')
    frame = read_csv(path, dtype={'gvkey': str, 'iid': str, 'historical_exchg': str,
                                  'historical_tic': str, 'sym_root': str, 'sym_suffix': str,
                                  'taq_symbol': str, 'cusip': str, 'tpci': str, 'itemvalue': str})
    if len(frame) != meta['rows'] or list(frame.columns) != meta['columns']:
        raise PipelineError('Partition schema/row count mismatch')
    return frame, meta


def write_partition(path, frame, request):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with tmp.open('wb') as binary:
            with gzip.GzipFile(filename='', mode='wb', fileobj=binary, mtime=0) as gz:
                with io.TextIOWrapper(gz, encoding='utf-8', newline='') as text:
                    frame.to_csv(text, index=False)
        disk = read_csv(tmp, compression='gzip')
        if len(disk) != len(frame) or list(disk.columns) != list(frame.columns):
            raise PipelineError('Serialized partition mismatch')
        os.replace(tmp, path)
        save_json(str(path) + '.json', dict(version=RAW_VERSION, request=request, rows=len(frame),
                  columns=list(frame.columns), sha256=digest(path), downloaded_at_utc=dt.datetime.now(dt.timezone.utc).isoformat()))
    finally:
        tmp.unlink(missing_ok=True)
