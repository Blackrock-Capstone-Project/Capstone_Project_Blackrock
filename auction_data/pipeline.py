"""Public entry points: update (download + process), process (offline), audit (raw completeness)."""
from pathlib import Path
import datetime as dt
import hashlib
import json
import traceback

import pandas as pd

from . import wrds_queries as sources
from .build import build_outputs
from .events import EVENTS_PATH, events_fingerprint, load_events
from .settings import PROCESS_VERSION, RAW_VERSION, ROOT, PipelineError
from .storage import digest, read_json, read_partition, save_json, write_partition, writer_lock
from .trading_calendar import completed_sessions, sessions
from .wrds_auth import Database, LoginCancelled, connect, runtime_credentials


def configuration(start, end, markets, refresh_sessions, now=None):
    start=dt.date.fromisoformat(start).isoformat()
    end=dt.date.fromisoformat(end).isoformat() if end else pd.Timestamp.now(tz='America/New_York').date().isoformat()
    markets=tuple(sorted(set(markets)))
    if start>end or not markets or not set(markets).issubset(sources.MARKETS) or refresh_sessions<0:
        raise ValueError('Invalid interval, markets or refresh_sessions')
    completed=completed_sessions(start,end,now)
    if not completed:
        raise ValueError('No completed trading sessions in request')
    return dict(start=start,end=end,markets=list(markets),refresh_sessions=refresh_sessions,
                completed_sessions=completed,version=RAW_VERSION)


def plan_chunks(root, config, availability):
    chunks=[]
    requested=set(config['completed_sessions'])
    for kind in ['daily','taq']:
        available=sorted(requested.intersection(availability[kind]))
        if not available:
            raise PipelineError('Source has no completed advertised sessions')
        # Enumerate ALL expected chunks, including unavailable dates before latest release.
        through=available[-1]
        expected=[d for d in config['completed_sessions'] if d<=through]
        if kind=='daily':
            ranges=sources.month_ranges(config['start'],through)
        else:
            ranges=((d,d) for d in expected)
        for first,last in ranges:
            needed=sessions(first,last)
            if not needed:
                continue
            path=Path('raw/wrds/daily/months')/(first[:7].replace('-','')+'.csv.gz') if kind=='daily' else Path('raw/taq/days')/(first.replace('-','')+'.csv.gz')
            # Extend/re-fetch the union, including previously stored partial-month history.
            chunk_markets=list(config['markets'])
            if kind=='daily':
                try:
                    old=read_json(str(root/path)+'.json')['request']
                    if old.get('source',old.get('table'))==sources.DAILY:
                        first=min(first,old['start'])
                        last=max(last,old['end'])
                        old_markets=old.get('markets') or [m for m,code in sources.MARKETS.items() if code in old.get('exchanges',[])]
                        chunk_markets=sorted(set(chunk_markets).union(old_markets))
                except (OSError,ValueError,KeyError):
                    pass
            req=sources.request(kind,first,last,chunk_markets)
            chunks.append(dict(kind=kind,path=str(path),request=req,status='expected',
                expected_sessions=sessions(first,last),advertised_sessions=[d for d in available if first<=d<=last]))
    chunks.append(dict(kind='history',path='raw/wrds/daily/history.csv.gz',
        request=sources.request('history',config['start'],max(d['request']['end'] for d in chunks),config['markets']),
        status='expected',expected_sessions=[]))
    return chunks


def execute_chunks(db, root, config, chunks, refresh_start=None):
    for chunk in chunks:
        req=chunk['request']; kind=chunk['kind']; path=root/chunk['path']
        try:
            published=sorted(set(d for c in chunks if c['kind']==kind for d in c.get('advertised_sessions',[])))
            recent=set(published[-config['refresh_sessions']:]) if config['refresh_sessions'] else set()
            force=kind=='history' or bool(recent.intersection(chunk['expected_sessions'])) or (
                refresh_start is not None and req['end']>=refresh_start)
            if not force:
                try:
                    frame,meta=read_partition(path,req)
                    sources.validate(frame,kind,req['start'],req['end'],req['markets'])
                    chunk.update(status='skipped',rows=len(frame),sha256=meta['sha256'],validation='passed')
                    continue
                except (OSError,ValueError,KeyError,PipelineError):
                    pass
            if kind!='history' and not set(chunk['expected_sessions']).issubset(chunk['advertised_sessions']):
                # Old history outside the request may have been unioned; re-query is valid,
                # but internal absent dates inside requested coverage remain explicit gaps.
                absent=set(chunk['expected_sessions'])-set(chunk['advertised_sessions'])
                if any(config['start']<=d<=config['end'] for d in absent):
                    raise PipelineError('Source has missing advertised trading sessions inside a chunk')
            if kind=='daily':
                frame=sources.download_daily(db,req['start'],req['end'],req['markets'])
            elif kind=='taq':
                frame=sources.download_taq(db,req['start'])
            else:
                frame=sources.download_history(db,req['start'],req['end'],config['markets'])
            sources.validate(frame,kind,req['start'],req['end'],req['markets'])
            write_partition(path,frame,req)
            chunk.update(status='downloaded',rows=len(frame),sha256=digest(path),validation='passed')
        except Exception:
            chunk.update(status='failed',validation='failed',error='Chunk unavailable, empty, invalid or query failed; retry/check source permission')
        # Persist progress after EVERY chunk; interruption cannot hide an expected block.
        checkpoint=read_json(root/'pipeline_plan.json')
        checkpoint.update(chunks=chunks,complete=False)
        save_json(root/'pipeline_plan.json',checkpoint)


def _audit(root):
    plan=read_json(root/'pipeline_plan.json')
    frames={'daily':[],'taq':[],'history':[]}
    failures=[]
    inputs=[]
    for chunk in plan['chunks']:
        try:
            if chunk['status'] not in ['skipped','downloaded']:
                raise PipelineError('Unfinished chunk')
            d,m=read_partition(root/chunk['path'],chunk['request'])
            r=chunk['request']
            sources.validate(d,chunk['kind'],r['start'],r['end'],r['markets'])
            frames[chunk['kind']].append(d)
            inputs.append((chunk['path'],m['sha256'],r))
        except Exception:
            failures.append(dict(path=chunk['path'],status='failed_validation'))
    # Check expected chunk coverage independently of stored chunk status and date extrema.
    expected=plan_chunks(root,plan['config'],plan['availability'])
    actual={(c['kind'],c['path']) for c in plan['chunks']}
    for c in expected:
        if (c['kind'],c['path']) not in actual:
            failures.append(dict(path=c['path'],status='missing_entire_chunk'))
    for kind,datecol in [('daily','datadate'),('taq','date')]:
        dates=set().union(*(set(f[datecol]) for f in frames[kind])) if frames[kind] else set()
        published=set(plan['availability'][kind]).intersection(plan['config']['completed_sessions'])
        if not published or not set(sessions(plan['config']['start'],max(published))).issubset(dates):
            failures.append(dict(path=kind,status='missing_source_sessions'))
    report=dict(complete=not failures,failures=failures,input_partitions=len(inputs))
    return report,frames,plan,inputs


def audit_data(data_dir=ROOT/'Data'):
    root=Path(data_dir)
    try:
        report,_,_,_=_audit(root)
        return report
    except Exception:
        return dict(complete=False,failures=[{'status':'missing_or_invalid_plan'}])


def _process(root, events_path=EVENTS_PATH):
    events=load_events(events_path)
    audit,frames,plan,inputs=_audit(root)
    if not audit['complete']:
        raise PipelineError('Raw data incomplete/unverified; run update to repair before processing')
    config=plan['config'].copy()
    config['common_end']=min(max(plan['availability'][k]) for k in ['daily','taq'])
    for kind,datecol in [('daily','datadate'),('taq','date')]:
        frames[kind]=[f[f[datecol].between(config['start'],config['end'])].copy() for f in frames[kind]]
    frames['daily']=[f[f.historical_exchg.isin([sources.MARKETS[m] for m in config['markets']])].copy() for f in frames['daily']]
    # Rule inputs (event list) are part of the fingerprint: editing them rebuilds everything.
    fingerprint=hashlib.sha256(json.dumps(dict(inputs=inputs,config=config,processing_version=PROCESS_VERSION,
        events_sha256=events_fingerprint(events_path)),sort_keys=True).encode()).hexdigest()
    return build_outputs(root,frames,pd.concat(frames['history'],ignore_index=True),config,fingerprint,inputs,events)


def process_data(data_dir=ROOT/'Data', events_path=EVENTS_PATH):
    root=Path(data_dir)
    with writer_lock(root):
        return _process(root, events_path)


def update_data(start='2022-01-03', end=None, markets=('NYSE','NASDAQ'), refresh_sessions=5,
                *, data_dir=ROOT/'Data', connection=None, credentials_provider=None,
                username=None, use_pgpass=False, host='wrds-pgdata.wharton.upenn.edu',port=9737,
                connect_timeout=60, refresh_start=None, download_only=False, now=None):
    """Update historical partitions, then publish datasets. Supplied connections remain caller-owned.

    Returns a credential-free structured report. Raises ValueError for invalid config.
    Failed source/processing operations return complete=False and preserve valid outputs.
    """
    config=configuration(start,end,markets,refresh_sessions,now)
    if refresh_start is not None:
        refresh_start=dt.date.fromisoformat(refresh_start).isoformat()
    root=Path(data_dir)
    owned=connection is None
    db=None
    credentials_cache={}
    report=dict(complete=False,config=config,sources={},processing=None,stage='authentication')
    with writer_lock(root):
        try:
            def credentials():
                if not credentials_cache:
                    credentials_cache.update(dict(credentials_provider()) if credentials_provider else runtime_credentials(username,use_pgpass))
                return credentials_cache
            def login():
                return connect(credentials_provider=credentials,username=username,use_pgpass=use_pgpass,
                               host=host,port=port,connect_timeout=connect_timeout)
            db=Database(login() if owned else connection,reconnect=login if owned else None)
            report['stage']='source_discovery'
            availability=sources.discover(db,start,config['completed_sessions'][-1],config['markets'])
            available={k:sorted(set(v).intersection(config['completed_sessions'])) for k,v in availability.items()}
            report['stage']='planning'
            chunks=plan_chunks(root,config,available)
            for kind,dates in available.items():
                report['sources'][kind]=dict(first=dates[0],last=dates[-1],
                    missing_sessions=sorted(set(sessions(start,dates[-1]))-set(dates)),
                    pending_sessions=[d for d in sessions(start,config['end']) if d>dates[-1]])
            plan=dict(config=config,availability=available,chunks=chunks,complete=False)
            save_json(root/'pipeline_plan.json',plan)
            report['stage']='download'
            execute_chunks(db,root,config,chunks,refresh_start)
            plan['chunks']=chunks
            save_json(root/'pipeline_plan.json',plan)
            audit,_,_,_=_audit(root)
            plan['complete']=audit['complete']
            save_json(root/'pipeline_plan.json',plan)
            report['audit']=audit
            report['chunks']=[{k:c[k] for k in ['kind','path','status','validation','rows'] if k in c} for c in chunks]
            report['common_end']=min(dates[-1] for dates in available.values())
            if audit['complete'] and not download_only:
                report['stage']='processing'
                report['processing']=_process(root)
            pending=any(s['pending_sessions'] or s['missing_sessions'] for s in report['sources'].values())
            report['complete']=audit['complete'] and not pending and (download_only or report['processing']['quality_status']=='complete')
            report['status']='complete' if report['complete'] else 'partial_or_pending'
        except sources.SourceDiscoveryError as exc:
            report.update(status='failed',error=exc.detail['message'],failure_detail=exc.detail)
        except (KeyboardInterrupt,EOFError,LoginCancelled):
            report.update(status='cancelled',error='Update cancelled; validated partitions remain available')
        except Exception as exc:
            report.update(status='failed',error='WRDS pipeline failed at the reported stage; verify authentication/source permissions or repair local data/output paths')
            categories = {TypeError:'client_type_error', ValueError:'client_value_error',
                          AttributeError:'client_attribute_error', KeyError:'client_key_error', IndexError:'client_index_error'}
            report['failure_detail'] = {
                'error_type': categories.get(type(exc),'unexpected_error'),
                'location': [{'function':f.name,'line':f.lineno,'module':Path(f.filename).name}
                    for f in traceback.extract_tb(exc.__traceback__)
                    if Path(f.filename).parent == Path(__file__).parent],
            }
        finally:
            credentials_cache.clear()
            if owned and db is not None:
                try:
                    db.connection.close()
                except Exception:
                    pass
        save_json(root/'update_report.json',report)
    return report
