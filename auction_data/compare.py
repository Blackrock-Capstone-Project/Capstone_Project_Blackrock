"""Read-only comparison of two raw data snapshots or two processed versions.

  python -m auction_data compare --old OLD --new NEW [--out DIR]

OLD/NEW are either data directories (containing raw/) -> raw partition comparison, or processed
version directories (containing manifest.json, e.g. Data/processed/.wrds_versions/<id>) -> output comparison.
Nothing under OLD or NEW is modified.
"""
from pathlib import Path
import json

import pandas as pd

from .settings import PipelineError
from .storage import digest, read_csv

RAW_KEYS = {'daily': ['gvkey', 'iid', 'datadate'], 'taq': ['taq_symbol', 'date'],
            'history': ['gvkey', 'iid', 'item', 'efffrom']}
SAMPLE_COLUMNS = ['eligible', 'pit_eligible', 'research_included_expost', 'research_included_pit',
                  'label', 'PX_VOLUME', 'security_status', 'exclusion_reason']
DETAIL_ROWS = 1000  # per partition / table, to keep the detail files small
SKIP_BYTE_COMPARE = {'report/daily_counts.png'}  # rendering depends on local fonts


def _kind(rel):
    return 'taq' if rel.startswith('raw/taq/') else 'history' if rel.endswith('history.csv.gz') else 'daily'


def _inventory(root):
    out = {}
    for meta in sorted(Path(root, 'raw').rglob('*.csv.gz.json')):
        rel = str(meta.relative_to(root))[:-len('.json')]
        info = json.loads(meta.read_text())
        out[rel] = dict(sha256=info.get('sha256'), rows=info.get('rows'), columns=info.get('columns'),
                        downloaded_at_utc=info.get('downloaded_at_utc'), request=info.get('request'))
    return out


def _read(path):
    return read_csv(path, dtype=str)


def diff_frames(old, new, keys, compare_columns=None):
    """Key-level and column-level differences; values compared as stored strings."""
    old = old.drop_duplicates(keys); new = new.drop_duplicates(keys)
    m = old.merge(new, on=keys, how='outer', suffixes=('_old', '_new'), indicator=True)
    both = m[m._merge.eq('both')]
    shared = [c for c in old.columns if c in new.columns and c not in keys]
    if compare_columns is not None:
        shared = [c for c in shared if c in compare_columns]
    changed_by_column, changed_any = {}, pd.Series(False, index=both.index)
    for c in shared:
        a, b = both[c + '_old'], both[c + '_new']
        diff = ~((a == b) | (a.isna() & b.isna()))
        if diff.any():
            changed_by_column[c] = int(diff.sum())
            changed_any |= diff
    detail = both.loc[changed_any, keys + [x for c in changed_by_column for x in (c + '_old', c + '_new')]]
    summary = dict(added=int(m._merge.eq('right_only').sum()), removed=int(m._merge.eq('left_only').sum()),
                   common=len(both), changed_rows=int(changed_any.sum()), changed_by_column=changed_by_column,
                   columns_only_old=sorted(set(old.columns) - set(new.columns)),
                   columns_only_new=sorted(set(new.columns) - set(old.columns)))
    added = m.loc[m._merge.eq('right_only'), keys].assign(change='added')
    removed = m.loc[m._merge.eq('left_only'), keys].assign(change='removed')
    return summary, pd.concat([removed, added, detail.assign(change='changed')], ignore_index=True)


def compare_raw(old_root, new_root):
    a, b = _inventory(old_root), _inventory(new_root)
    result = dict(mode='raw', old=str(old_root), new=str(new_root), partitions={}, counts={})
    details = {}
    for rel in sorted(set(a) | set(b)):
        if rel not in b:
            status = 'only_old'
        elif rel not in a:
            status = 'only_new'
        elif a[rel]['sha256'] == b[rel]['sha256']:
            status = 'identical'
        else:
            status = 'changed'
        result['counts'][status] = result['counts'].get(status, 0) + 1
        if status == 'identical':
            continue
        entry = dict(status=status, old=a.get(rel), new=b.get(rel))
        if status == 'changed':
            s, d = diff_frames(_read(Path(old_root, rel)), _read(Path(new_root, rel)), RAW_KEYS[_kind(rel)])
            entry['diff'] = s
            details[rel] = d
        result['partitions'][rel] = entry
    return result, details


def compare_processed(old_v, new_v):
    ma = json.loads(Path(old_v, 'manifest.json').read_text())
    mb = json.loads(Path(new_v, 'manifest.json').read_text())
    inputs = lambda m: {p: sha for p, sha, *_ in m['input_partitions']}
    ia, ib = inputs(ma), inputs(mb)
    ev = lambda v: digest(Path(v, 'reference/index_events.csv')) if Path(v, 'reference/index_events.csv').exists() else None
    files = {}
    for name in sorted(set(ma['files']) | set(mb['files'])):
        if name in SKIP_BYTE_COMPARE:
            files[name] = 'skipped_rendering_dependent'
        elif name not in mb['files'] or name not in ma['files']:
            files[name] = 'only_old' if name not in mb['files'] else 'only_new'
        else:
            files[name] = 'identical' if ma['files'][name]['sha256'] == mb['files'][name]['sha256'] else 'different'
    result = dict(mode='processed', old=str(old_v), new=str(new_v),
                  version=dict(old=ma['version'], new=mb['version']),
                  fingerprint_equal=ma['fingerprint'] == mb['fingerprint'],
                  config_equal={k: ma['config'].get(k) == mb['config'].get(k) for k in sorted(set(ma['config']) | set(mb['config']))},
                  events_sha256=dict(old=ev(old_v), new=ev(new_v)),
                  input_partitions=dict(changed=sorted(p for p in ia if p in ib and ia[p] != ib[p]),
                                        only_old=sorted(set(ia) - set(ib)), only_new=sorted(set(ib) - set(ia))),
                  files=files)
    details = {}
    sample = 'us_wrds_quality/research_sample.csv.gz'
    if files.get(sample) == 'different':
        s, d = diff_frames(_read(Path(old_v, sample)), _read(Path(new_v, sample)), ['security_id', 'date'], SAMPLE_COLUMNS)
        result['research_sample'] = s
        details[sample] = d
    sec = 'us_wrds_quality/security_quality.csv'
    if files.get(sec) == 'different':
        s, d = diff_frames(_read(Path(old_v, sec)), _read(Path(new_v, sec)), ['security_id'])
        result['security_quality'] = s
        details[sec] = d
    return result, details


def compare(old, new, out=None):
    old, new = Path(old), Path(new)
    if (old / 'manifest.json').exists() and (new / 'manifest.json').exists():
        result, details = compare_processed(old, new)
    elif (old / 'raw').is_dir() and (new / 'raw').is_dir():
        result, details = compare_raw(old, new)
    else:
        raise PipelineError('compare needs two data directories (with raw/) or two processed versions (with manifest.json)')
    if out is not None:
        out = Path(out)
        if out.resolve().is_relative_to(old.resolve()) or out.resolve().is_relative_to(new.resolve()):
            raise PipelineError('--out must be outside the compared directories')
        out.mkdir(parents=True, exist_ok=True)
        (out / 'summary.json').write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str) + '\n')
        for name, frame in details.items():
            frame.head(DETAIL_ROWS).to_csv(out / (name.replace('/', '__') + '.diff.csv'), index=False)
    return result
