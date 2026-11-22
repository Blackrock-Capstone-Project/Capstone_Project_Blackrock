"""Interactive WRDS source permission/date check; does not download data."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.data_pipeline.auth import connect, Database
from scripts.data_pipeline.sources import discover
from scripts.data_pipeline.common import PipelineError, completed_sessions
import pandas as pd


def main():
    conn = None
    try:
        conn = connect()
        end = completed_sessions('2022-01-03', pd.Timestamp.now(tz='America/New_York').date().isoformat())[-1]
        result = discover(Database(conn), '2022-01-03', end, ['NYSE','NASDAQ'])
        print({key: {'first': min(dates), 'last': max(dates), 'published_sessions': len(dates)} for key,dates in result.items()})
        return 0
    except PipelineError:
        print('WRDS login/source check failed; verify your account, Duo approval and source permissions.')
        return 2
    finally:
        if conn is not None:
            conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
