"""Compute, publish (immutable versions), reuse and verify the feature panel.

Layout: Data/features/.versions/<id>/{daily/<MARKET>/<YEAR>.parquet, intraday/<MARKET>/<YEAR>.parquet,
profiles/profiles.parquet, feature_dictionary.csv, manifest.json, report/}; Data/features/current -> version.
A version is written to .versions/.tmp-<uuid>, checked, renamed, then current is switched atomically.
Nothing ever writes into a published version directory again.
"""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import hashlib
import json
import os
import subprocess
import uuid

import numpy as np
import pandas as pd

from auction_data.storage import read_json, save_json, writer_lock

from . import report as reporting
from .config import CHECKPOINT_LIST, CORE_REQUIRED, FEATURE_VERSION, MARKET_NAMES, config_dict
from .daily import calendar_flags, daily_features
from .dictionary import check_columns, feature_dictionary, table_fields
from .intraday import intraday_features
from .panel import (CP_COLUMNS, calendar_sessions, dense_security, input_identity, load_panel, recheck_identity,
                    resolve_processed, security_arrays)
from .profiles import FLOAT_FIELDS, month_starts, profile_at

PACKAGE = Path(__file__).resolve().parent
KEYS = {'daily': ['security_id', 'date'], 'intraday': ['security_id', 'date', 'checkpoint'],
        'profiles': ['security_id', 'month']}
DAILY_OUT = ['a_last', 'a_last_age', 'a_ma5', 'a_ma22', 'd_last', 'd_last_age', 'd_ma22', 'logmcap_lag1',
             'a_lag1', 'd_lag1', 'a_n22', 'd_n22']
PASSTHROUGH = ['label', 'label_status', 'valid_day', 'mask_reason', 'pit_eligible', 'research_status',
               'research_included_pit', 'research_included_expost', 'split_status', 'security_status',
               'exclusion_reason']


# ---------------------------------------------------------------- per-security computation

def compute_security(rows, sidx, session_month, month_start):
    """Row-aligned feature arrays and monthly profile records for one security (pure, no IO)."""
    a = dense_security(rows, sidx)
    pos = a['pos']
    d = daily_features(a)
    out = {k: d[k][pos] for k in DAILY_OUT}
    for k in ['split_on_t', 'factor_carried', 'factor_unknown', 'interval_start']:
        out[k] = a[k][pos]
    out['interval_id'] = a['interval'][pos] + 1
    for cp, (c, mu, q) in intraday_features(a).items():
        out['C|' + cp], out['mu|' + cp], out['q|' + cp] = c[pos], mu[pos], q[pos]
    records = []
    for mo in np.unique(session_month[sidx]):
        rec = profile_at(a, d['a_ma22'], int(month_start[mo] - sidx[0]))
        rec['month_ord'] = int(mo)
        records.append(rec)
    return out, records


def _compute_chunk(payload):
    arrays, sidx, bounds, session_month, month_start = payload
    parts, profiles = [], []
    for k, (lo, hi) in enumerate(bounds):
        rows = {name: v[lo:hi] for name, v in arrays.items()}
        out, recs = compute_security(rows, sidx[lo:hi], session_month, month_start)
        parts.append(out)
        for r in recs:
            r['sec'] = k
        profiles.append(recs)
    merged = {name: np.concatenate([p[name] for p in parts]) for name in parts[0]}
    return merged, profiles


def compute_all(panel, sidx, session_month, month_start, workers=None):
    """Run compute_security over every security (parallel by contiguous chunks); order is preserved."""
    arrays = security_arrays(panel)
    sid = panel.security_id.to_numpy()
    starts = np.flatnonzero(np.r_[True, sid[1:] != sid[:-1]])
    ends = np.r_[starts[1:], len(sid)]
    workers = workers or max(1, min(12, (os.cpu_count() or 2) - 2))
    n_chunks = max(1, min(len(starts), workers * 6))
    chunks = np.array_split(np.arange(len(starts)), n_chunks)
    payloads = []
    for ch in chunks:
        lo, hi = starts[ch[0]], ends[ch[-1]]
        bounds = [(starts[i] - lo, ends[i] - lo) for i in ch]
        payloads.append(({k: v[lo:hi] for k, v in arrays.items()}, sidx[lo:hi], bounds, session_month, month_start))
    if workers == 1:
        results = [_compute_chunk(p) for p in payloads]
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_compute_chunk, payloads))
    out = {name: np.concatenate([r[0][name] for r in results]) for name in results[0][0]}
    profiles = []
    for ch, (_, recs) in zip(chunks, results):
        for k, sec_recs in enumerate(recs):
            for r in sec_recs:
                r['sec'] = int(ch[k])
            profiles.extend(sec_recs)
    return out, profiles, starts


# ---------------------------------------------------------------- tables

def build_tables(panel, workers=None):
    """daily, intraday and profiles DataFrames (sorted by keys, columns in dictionary order)."""
    cal, sidx = calendar_sessions(panel)
    flags = calendar_flags(cal)
    m_idx, m_keys = month_starts(cal)
    session_month = np.searchsorted(np.array(m_idx), np.arange(len(cal)), side='right') - 1
    out, profile_recs, starts = compute_all(panel, sidx, session_month, np.array(m_idx), workers)

    daily = pd.DataFrame({'security_id': panel.security_id.to_numpy(), 'date': panel.date.to_numpy(),
                          'market': panel.market.to_numpy(), 'ticker': panel.ticker.to_numpy(),
                          'interval_id': out['interval_id'].astype(np.int64),
                          'interval_start': out['interval_start'].astype(bool)})
    daily['y'] = np.where(panel.valid_day.to_numpy(), np.log1p(panel.label.to_numpy(float)), np.nan)
    for c in PASSTHROUGH:
        daily[c] = panel[c].to_numpy()
    for c in ['split_on_t', 'factor_carried', 'factor_unknown']:
        daily[c] = out[c].astype(bool)
    daily['expost_split_issue_t'] = panel.split_status.isin(['split_unresolved', 'factor_missing']).to_numpy()
    for c in DAILY_OUT:
        daily[c] = out[c]
    for c in ['dow', 'month_end', 'quarter_end', 'opex']:
        daily[c] = flags[c].to_numpy()[sidx]
    daily['core_required_complete'] = daily[CORE_REQUIRED].notna().all(axis=1)
    daily = daily[table_fields('daily')]

    rows = np.flatnonzero(panel.cp_row.to_numpy())
    n_cp = len(CHECKPOINT_LIST)
    rep = np.repeat(rows, n_cp)
    intraday = pd.DataFrame({'security_id': panel.security_id.to_numpy()[rep], 'date': panel.date.to_numpy()[rep],
                             'checkpoint': np.tile(np.array(CHECKPOINT_LIST, dtype=object), len(rows)),
                             'market': panel.market.to_numpy()[rep]})
    for tag, name in [('C', 'C_b'), ('mu', 'mu_b'), ('q', 'q_b')]:
        intraday[name] = np.stack([out[f'{tag}|{cp}'][rows] for cp in CHECKPOINT_LIST], axis=1).ravel()
    intraday = intraday[table_fields('intraday')]

    sec_ids = panel.security_id.to_numpy()[starts]
    prof = pd.DataFrame(profile_recs)
    prof['security_id'] = sec_ids[prof.sec.to_numpy()]
    prof['month'] = np.array(m_keys)[prof.month_ord.to_numpy()]
    prof['m_date'] = np.array(cal)[np.array(m_idx)[prof.month_ord.to_numpy()]]
    prof['market'] = prof.market.map(MARKET_NAMES).fillna('')
    iv = prof.interval_id.to_numpy(np.int64)
    prof['interval_id'] = np.where(iv >= 0, iv + 1, 0)  # 0 = not listed at m
    for c in ['n_A', 'n_e', 'n_p']:
        prof[c] = prof[c].astype(np.int64)
    for c in FLOAT_FIELDS:
        prof[c] = prof[c].astype(float)
    prof = prof.sort_values(KEYS['profiles'], kind='mergesort').reset_index(drop=True)
    prof = prof[table_fields('profiles')]
    for name, frame in [('daily', daily), ('intraday', intraday), ('profiles', prof)]:
        check_columns(name, frame.columns)
    return dict(daily=daily, intraday=intraday, profiles=prof)


# ---------------------------------------------------------------- hashing, writing, reading

def frame_hash(df):
    h = hashlib.sha256()
    h.update(json.dumps([list(df.columns), [str(t) for t in df.dtypes]]).encode())
    h.update(pd.util.hash_pandas_object(df, index=False).to_numpy().tobytes())
    return h.hexdigest()


def partitions(name, df):
    """(relative path, frame) pairs; frames keep the key order of df."""
    if name == 'profiles':
        return [('profiles/profiles.parquet', df)]
    year = df.date.str[:4].to_numpy()
    market = df.market.to_numpy()
    out = []
    for m in sorted(set(market)):
        for y in sorted(set(year[market == m])):
            sel = (market == m) & (year == y)
            out.append((f'{name}/{m}/{y}.parquet', df.loc[sel].reset_index(drop=True)))
    return out


def write_table(root, name, df):
    """Write partitions; return file records and the table content hash (over partition hashes)."""
    files = {}
    for rel, part in partitions(name, df):
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        part.to_parquet(path, index=False, compression='zstd')
        back = pd.read_parquet(path)
        h = frame_hash(back)
        if h != frame_hash(part) or back.duplicated(KEYS[name]).any():
            raise ValueError(f'Read-back check failed for {rel}')
        files[rel] = dict(rows=len(part), content_hash=h)
    table = hashlib.sha256(''.join(files[k]['content_hash'] for k in sorted(files)).encode()).hexdigest()
    return files, table


def load_table(version_dir, name):
    """Concatenate a published table's files (sorted by path, then by keys)."""
    paths = sorted((Path(version_dir) / name).rglob('*.parquet'))
    df = pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)
    return df.sort_values(KEYS[name], kind='mergesort').reset_index(drop=True)


# ---------------------------------------------------------------- versioning

def code_digest():
    h = hashlib.sha256()
    for p in sorted(PACKAGE.glob('*.py')):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def git_state():
    root = PACKAGE.parent
    try:
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True, text=True,
                                check=True).stdout.strip()
        dirty = bool(subprocess.run(['git', 'status', '--porcelain'], cwd=root, capture_output=True, text=True,
                                    check=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = None, None
    return dict(commit=commit, dirty=dirty)


def fingerprint(identity, code=None):
    payload = dict(processed_fingerprint=identity['fingerprint'], inputs=identity['inputs'],
                   feature_version=FEATURE_VERSION, config=config_dict(), code=code or code_digest())
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def features_root(data_dir):
    return Path(data_dir) / 'features'


def switch_current(root, version_dir):
    pointer = root / 'current'
    target = Path('.versions') / version_dir.name
    if pointer.is_symlink() and Path(os.readlink(pointer)) == target:
        return
    if pointer.exists() and not pointer.is_symlink():
        raise ValueError('Data/features/current must be a managed symlink')
    tmp = root / ('.current_' + uuid.uuid4().hex)
    tmp.symlink_to(target, target_is_directory=True)
    os.replace(tmp, pointer)


def build(data_dir, processed_dir=None, workers=None):
    """Build or reuse the feature version for the pinned processed input; returns the manifest."""
    processed = resolve_processed(data_dir, processed_dir)
    root = features_root(data_dir)
    with writer_lock(root):
        identity = input_identity(processed)
        code = code_digest()  # computed once: the manifest records exactly what the fingerprint used
        fp = fingerprint(identity, code)
        final = root / '.versions' / fp[:16]
        if (final / 'manifest.json').is_file():
            manifest = read_json(final / 'manifest.json')
            if manifest.get('fingerprint') == fp:
                switch_current(root, final)
                return manifest | {'reused': True, 'version_dir': str(final)}
            raise ValueError(f'{final} exists with a different fingerprint')
        tmp = root / '.versions' / ('.tmp-' + uuid.uuid4().hex)
        tmp.mkdir(parents=True)
        panel = load_panel(processed)
        tables = build_tables(panel, workers)
        del panel
        manifest = dict(feature_version=FEATURE_VERSION, fingerprint=fp, processed=identity, config=config_dict(),
                        code_digest=code, git=git_state(), files={}, tables={})
        for name, df in tables.items():
            files, table_hash = write_table(tmp, name, df)
            manifest['files'].update(files)
            manifest['tables'][name] = dict(rows=len(df), keys=KEYS[name], content_hash=table_hash)
        feature_dictionary().to_csv(tmp / 'feature_dictionary.csv', index=False)
        manifest['report'] = reporting.write_report(tmp / 'report', tables['daily'], tables['intraday'],
                                                    tables['profiles'])
        recheck_identity(identity)
        save_json(tmp / 'manifest.json', manifest)
        os.rename(tmp, final)
        switch_current(root, final)
        return manifest | {'reused': False, 'version_dir': str(final)}


def current_version(data_dir):
    pointer = features_root(data_dir) / 'current'
    if not pointer.is_symlink():
        raise ValueError('No current feature version')
    return pointer.resolve()


def verify(version_dir):
    """Read-only check of a published version: files, row counts, keys, content hashes, dictionary."""
    version_dir = Path(version_dir)
    manifest = read_json(version_dir / 'manifest.json')
    problems = []
    for rel, rec in manifest['files'].items():
        path = version_dir / rel
        if not path.is_file():
            problems.append(f'missing {rel}')
            continue
        df = pd.read_parquet(path)
        name = rel.split('/')[0]
        if len(df) != rec['rows'] or frame_hash(df) != rec['content_hash'] or df.duplicated(KEYS[name]).any():
            problems.append(f'content mismatch {rel}')
        try:
            check_columns(name, df.columns)
        except ValueError as e:
            problems.append(str(e))
    for name, rec in manifest['tables'].items():
        parts = sorted(k for k in manifest['files'] if k.startswith(name + '/'))
        joined = hashlib.sha256(''.join(manifest['files'][k]['content_hash'] for k in parts).encode()).hexdigest()
        if joined != rec['content_hash'] or sum(manifest['files'][k]['rows'] for k in parts) != rec['rows']:
            problems.append(f'table hash mismatch {name}')
    return dict(version_dir=str(version_dir), ok=not problems, problems=problems)
