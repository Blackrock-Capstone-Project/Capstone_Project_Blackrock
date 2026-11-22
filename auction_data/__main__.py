"""python -m auction_data update|process|audit|compare|check"""
import argparse
import json
from pathlib import Path

import pandas as pd

from .compare import compare
from .pipeline import audit_data, process_data, update_data
from .settings import ROOT, PipelineError
from .trading_calendar import completed_sessions
from .wrds_auth import Database, connect
from .wrds_queries import discover


def check(args):
    """Read-only WRDS login, source permission and published-date probe; downloads nothing."""
    conn = None
    try:
        conn = connect(username=args.user, host=args.host, port=args.port, connect_timeout=args.connect_timeout,
                       use_pgpass=args.use_pgpass)
        end = completed_sessions(args.start, args.end or pd.Timestamp.now(tz='America/New_York').date().isoformat())[-1]
        found = discover(Database(conn), args.start, end, args.markets)
        return {k: {'first': min(v), 'last': max(v), 'published_sessions': len(v)} for k, v in found.items()}
    finally:
        if conn is not None:
            conn.close()


def main(argv=None):
    p = argparse.ArgumentParser(prog='python -m auction_data', description=__doc__)
    p.add_argument('command', choices=['update', 'process', 'audit', 'compare', 'check'])
    p.add_argument('--data-dir', type=Path, default=ROOT/'Data')
    p.add_argument('--start', default='2022-01-03')
    p.add_argument('--end')
    p.add_argument('--markets', nargs='+', choices=['NYSE', 'NASDAQ'], default=['NYSE', 'NASDAQ'])
    p.add_argument('--refresh-sessions', type=int, default=5)
    p.add_argument('--refresh-start')
    p.add_argument('--download-only', action='store_true')
    p.add_argument('--user', help='Optional runtime username; prompted by default')
    p.add_argument('--use-pgpass', action='store_true', help='Explicitly opt in to existing local pgpass (never created)')
    p.add_argument('--host', default='wrds-pgdata.wharton.upenn.edu')
    p.add_argument('--port', type=int, default=9737)
    p.add_argument('--connect-timeout', type=int, default=60)
    p.add_argument('--old', type=Path, help='compare: older data dir (with raw/) or processed version dir')
    p.add_argument('--new', type=Path, help='compare: newer data dir or processed version dir')
    p.add_argument('--out', type=Path, help='compare: directory for summary.json and *.diff.csv')
    args = p.parse_args(argv)
    try:
        if args.command == 'update':
            result = update_data(start=args.start, end=args.end, markets=args.markets,
                                 refresh_sessions=args.refresh_sessions, data_dir=args.data_dir,
                                 refresh_start=args.refresh_start, download_only=args.download_only,
                                 username=args.user, use_pgpass=args.use_pgpass, host=args.host, port=args.port,
                                 connect_timeout=args.connect_timeout)
            success = result['complete']
        elif args.command == 'process':
            result = process_data(args.data_dir)
            success = result['quality_status'] == 'complete'
        elif args.command == 'compare':
            if not (args.old and args.new):
                raise ValueError('compare needs --old and --new')
            result = compare(args.old, args.new, args.out)
            success = True
        elif args.command == 'check':
            result = check(args)
            success = True
        else:
            result = audit_data(args.data_dir)
            success = result['complete']
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if success else 2
    except (ValueError, PipelineError):
        print(json.dumps({'complete': False, 'error': 'Invalid configuration, failed login/source check, incomplete '
                          'local data or concurrent writer; inspect update_report.json'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
