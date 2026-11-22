"""Atomic files, raw partitions with metadata, fingerprints, writer lock and table verification."""
from contextlib import contextmanager
from pathlib import Path
import datetime as dt
import fcntl
import gzip
import io
import hashlib
import json
import os
import uuid

import pandas as pd

from .settings import RAW_VERSION, PipelineError


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


class deterministic_gzip(io.TextIOWrapper):
    """Text gzip writer with mtime=0 so identical content is byte-identical across runs."""
    def __init__(self, path):
        self._raw = open(path, 'wb')
        super().__init__(gzip.GzipFile(filename='', mode='wb', fileobj=self._raw, mtime=0),
                         encoding='utf-8', newline='')

    def close(self):
        try:
            super().close()
        finally:
            self._raw.close()  # GzipFile never closes a caller-supplied fileobj


def verify_table(path, keys, expected_rows, check_duplicates=True):
    # Partition ordering is not assumed; check duplicates across chunk boundaries too.
    seen, rows = set(), 0
    for d in read_csv(path, chunksize=250000, dtype={k:str for k in keys}):
        if not set(keys).issubset(d):
            raise PipelineError('Processed schema missing keys')
        if d[keys].isna().any().any() or d.duplicated(keys).any():
            raise PipelineError('Processed table has missing/duplicate keys')
        if check_duplicates:
            current = set(d[keys].itertuples(index=False,name=None))
            if seen.intersection(current):
                raise PipelineError('Processed table has duplicate keys across chunks')
            seen.update(current)
        rows += len(d)
    if rows != expected_rows:
        raise PipelineError('Processed row count differs after serialization')


def output_valid(root, fingerprint):
    try:
        manifest = read_json(root/'manifest.json')
        return manifest['fingerprint'] == fingerprint and all(
            digest(root/name) == info['sha256'] for name,info in manifest['files'].items())
    except (OSError,ValueError,KeyError):
        return False
