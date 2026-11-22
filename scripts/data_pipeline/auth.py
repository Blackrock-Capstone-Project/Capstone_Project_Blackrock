"""Interactive native PostgreSQL login. No credentials are persisted or logged."""
import getpass
import re
import sys

from .common import PipelineError


class QueryError(PipelineError):
    """Database diagnostics restricted to public codes, never raw server text."""
    def __init__(self, exc):
        import psycopg2
        code = getattr(exc, 'pgcode', None)
        self.sqlstate = code if isinstance(code, str) and re.fullmatch(r'[0-9A-Z]{5}', code) else None
        categories = [(psycopg2.OperationalError, 'connection_error'),
                      (psycopg2.ProgrammingError, 'query_or_permission_error'),
                      (psycopg2.Error, 'database_error'),
                      (TypeError, 'client_type_error'), (ValueError, 'client_value_error'),
                      (IndexError, 'client_index_error')]
        self.error_type = next((name for cls, name in categories if isinstance(exc, cls)), 'client_error')
        hints = {'42501': 'Source SELECT permission denied; login itself succeeded.',
                 '42P01': 'Requested source table/view is unavailable.',
                 '42703': 'A requested source column is unavailable.',
                 '57014': 'Source query cancelled or timed out.',
                 '08006': 'Database connection interrupted.'}
        self.hint = hints.get(self.sqlstate, 'Database/client query failed after connection; inspect operation and SQLSTATE.')
        super().__init__(self.hint)

    def diagnostics(self):
        return {'error_type': self.error_type, 'sqlstate': self.sqlstate, 'message': self.hint}


class LoginCancelled(PipelineError):
    pass


def runtime_credentials(username=None, use_pgpass=False):
    if not sys.stdin.isatty():
        if use_pgpass and username:
            return {'user': username}
        raise PipelineError('WRDS login requires an interactive terminal or credentials_provider/connection')
    print('WRDS login: complete any required Duo approval on your own device. No credentials will be saved.')
    try:
        user = username or input('Your WRDS username: ').strip()
        result = {'user': user}
        if not use_pgpass:
            result['password'] = getpass.getpass('WRDS password (hidden): ')
        return result
    except (EOFError, KeyboardInterrupt):
        raise LoginCancelled('WRDS login cancelled') from None


def connect(*, credentials_provider=None, username=None, host='wrds-pgdata.wharton.upenn.edu',
            port=9737, connect_timeout=60, attempts=2, use_pgpass=False):
    import psycopg2
    credentials = None
    try:
        credentials = dict(credentials_provider()) if credentials_provider is not None else runtime_credentials(username,use_pgpass)
        if set(credentials) - {'user','password'}:
            raise PipelineError('Credential provider accepts only user/password')
        if not credentials.get('user') or (not use_pgpass and not credentials.get('password')):
            raise PipelineError('WRDS username/password not supplied')
        for attempt in range(max(1, attempts)):
            try:
                conn = psycopg2.connect(host=host, port=port, dbname='wrds', sslmode='require',
                    connect_timeout=connect_timeout, application_name='closing_auction_pipeline',
                    options='-c statement_timeout=900000', **credentials)
                conn.autocommit = True
                return conn
            except psycopg2.OperationalError:
                if attempt + 1 >= max(1, attempts):
                    raise PipelineError('WRDS connection failed; verify account, permissions, network and required Duo approval') from None
    except (EOFError, KeyboardInterrupt):
        raise LoginCancelled('WRDS login cancelled') from None
    except PipelineError:
        raise
    except Exception:
        raise PipelineError('WRDS authentication provider failed') from None
    finally:
        if credentials is not None:
            credentials.clear()


class Database:
    """DB-API reader with a bounded retry; raw database errors never escape."""
    def __init__(self, connection, reconnect=None):
        self.connection, self.reconnect = connection, reconnect

    def query(self, sql, params=None):
        import pandas as pd
        import psycopg2
        for attempt in range(2):
            try:
                with self.connection.cursor() as cur:
                    cur.execute(sql, params)
                    return pd.DataFrame(cur.fetchall(), columns=[c[0] for c in cur.description])
            except Exception as exc:
                try:
                    self.connection.rollback()
                except Exception:
                    pass
                if attempt == 0 and self.reconnect and isinstance(exc, (psycopg2.OperationalError, psycopg2.InterfaceError)):
                    try:
                        self.connection.close()
                    except Exception:
                        pass
                    self.connection = self.reconnect()
                    continue
                raise QueryError(exc) from None
