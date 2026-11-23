"""python -m auction_features build|report|verify

build   pin the processed version, build or reuse a feature version, switch Data/features/current
report  recompute the quality report of a published version into Data/features/reports/<id>/<n>/ (version untouched)
verify  read-only check of a published version (files, rows, keys, content hashes, dictionary)
"""
import argparse
import json
from pathlib import Path

from auction_data.settings import ROOT

from .build import build, current_version, load_table, verify
from .report import write_report


def report(data_dir, version_dir):
    version_dir = Path(version_dir)
    base = Path(data_dir) / 'features' / 'reports' / version_dir.name
    n = len(list(base.glob('*'))) + 1 if base.exists() else 1
    out = base / f'{n:03d}'
    tables = {name: load_table(version_dir, name) for name in ['daily', 'intraday', 'profiles']}
    summary = write_report(out, tables['daily'], tables['intraday'], tables['profiles'])
    return dict(report_dir=str(out), completeness=summary['completeness'])


def main(argv=None):
    p = argparse.ArgumentParser(prog='python -m auction_features', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['build', 'report', 'verify'])
    p.add_argument('--data-dir', type=Path, default=ROOT / 'Data')
    p.add_argument('--processed-dir', type=Path, help='build: pin this processed version directory')
    p.add_argument('--version-dir', type=Path, help='report/verify: a published version (default: current)')
    p.add_argument('--workers', type=int)
    args = p.parse_args(argv)
    if args.command == 'build':
        m = build(args.data_dir, args.processed_dir, args.workers)
        result = dict(reused=m['reused'], version_dir=m['version_dir'], fingerprint=m['fingerprint'],
                      tables=m['tables'], completeness=m['report']['completeness'])
        ok = True
    else:
        version = args.version_dir or current_version(args.data_dir)
        result = verify(version) if args.command == 'verify' else report(args.data_dir, version)
        ok = result.get('ok', True)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if ok else 2


if __name__ == '__main__':
    raise SystemExit(main())
