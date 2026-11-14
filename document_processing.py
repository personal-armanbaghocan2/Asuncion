"""Private durable extraction attempts; original sources remain immutable.

Extraction runs outside transactions. Short transactions claim/finish a run;
source-scoped advisory locks coordinate independent Flask instances.
"""
from contextlib import contextmanager
from hashlib import sha256
import json
import uuid

from flask import abort, session
import durable_ocr_v0_1_0 as durable
import server_sessions_v0_1_0 as sessions

# v6 also preserves explicit unknown/missing catalog categories over sheet names. Existing runs
# and drafts stay unchanged; only a separately requested reprocess uses this.
PARSER_VERSION='ppmp-source-evidence-v6'


@contextmanager
def transaction(api):
    with durable.transaction(api,read_committed=True) as (con,uid):
        con.execute("select set_config('psms.workbook_actor',?,true)",
                    (json.dumps(dict(uid=uid,sid=sessions.token_hash(session.get(sessions.COOKIE_KEY)))),))
        con.execute("SET LOCAL statement_timeout='10s'")
        con.execute("SET LOCAL lock_timeout='5s'")
        yield con,uid


def ready(con):
    row=con.execute("select to_regclass('psms_documents.processing_runs') is not null as ready").fetchone()
    if not row or not row['ready']:
        abort(503,description='The reviewed document-processing installation is required. Existing documents remain preserved.')


def lock(con,uid,digest):
    # Same lock as both existing PPMP upload paths. No table ownership or
    # UPDATE permission on the immutable source table is needed.
    con.execute('select pg_advisory_xact_lock(hashtextextended(?,0))',(f'ppmp-intake:{uid}:{digest}',))


def _claim(api,con,uid,source):
    previous=con.execute("""select *,started_at>clock_timestamp()-interval '5 minutes' as fresh
        from psms_documents.processing_runs where source_id=? and owner_id=?
        order by started_at desc,id desc limit 1""",(str(source['id']),uid)).fetchone()
    if previous and previous['status']=='PROCESSING':
        if previous['fresh']:abort(409,description='This document is already processing. Reopen its saved history shortly.')
        con.execute("""update psms_documents.processing_runs set status='FAILED',completed_at=clock_timestamp(),
            error_code='INTERRUPTED' where id=? and owner_id=? and status='PROCESSING'""",(previous['id'],uid))
    if previous and previous['status']=='COMPLETE' and previous['parser_version']==PARSER_VERSION:
        return None
    run_id=str(uuid.uuid4())
    con.execute('''insert into psms_documents.processing_runs(id,source_id,owner_id,kind,parser_version,source_sha256)
        values(?,?,?,?,?,?)''',(run_id,str(source['id']),uid,source['kind'],PARSER_VERSION,source['sha256']))
    durable.audit(api,con,'Started Document Extraction',source['kind'].upper(),source['id'])
    return run_id


def capture(api,name,ext,content):
    digest=sha256(content).hexdigest()
    with transaction(api) as (con,uid):
        ready(con);lock(con,uid,digest)
        existing=con.execute("""select id from psms_documents.sources where owner_id=?
            and kind='ppmp' and sha256=? order by created_at limit 1""",(uid,digest)).fetchone()
        if existing:return str(existing['id']),None
        state=dict(text='',method='Awaiting extraction',warnings=[],item_count=0,
                   filename=name,intake='automatic-item-routing-v2')
        sid=durable.source(con,uid,'ppmp','MIXED',name,ext,content,state)
        source=dict(id=sid,kind='ppmp',sha256=digest)
        durable.audit(api,con,'Uploaded Private Document','PPMP',sid)
        run_id=_claim(api,con,uid,source)
    return sid,run_id


def begin_existing(api,sid):
    with transaction(api) as (con,uid):
        ready(con)
        source=con.execute("""select id,kind,filename,extension,content,sha256,extraction
            from psms_documents.sources where id=? and owner_id=? and kind='ppmp'""",(str(sid),uid)).fetchone()
        if not source:abort(404)
        source=dict(source);source['id']=str(source['id'])
        if sha256(bytes(source['content'])).hexdigest()!=source['sha256']:
            abort(409,description='Original document integrity requires owner review. Nothing was replaced.')
        lock(con,uid,source['sha256'])
        run_id=_claim(api,con,uid,source)
    return source,run_id


def current_run(con,uid,sid,run_id):
    row=con.execute('''select * from psms_documents.processing_runs
        where id=? and source_id=? and owner_id=? for update''',(run_id,str(sid),uid)).fetchone()
    if not row or row['status']!='PROCESSING':
        abort(409,description='This extraction attempt is no longer current. Reopen the saved document.')
    return row


def complete(con,uid,run_id,state,summary):
    status='COMPLETE' if summary['total'] and not summary.get('uncertain_regions') else 'INCOMPLETE'
    result=con.execute('''update psms_documents.processing_runs set status=?,completed_at=clock_timestamp(),
        extraction=?::jsonb,summary=?::jsonb where id=? and owner_id=? and status='PROCESSING' ''',
        (status,durable.payload(state),durable.payload(summary),run_id,uid))
    if result.rowcount!=1:abort(409)
    return status


def failed(api,sid,run_id):
    if not run_id:return
    try:
        with transaction(api) as (con,uid):
            result=con.execute("""update psms_documents.processing_runs set status='FAILED',completed_at=clock_timestamp(),
                error_code='EXTRACTION_FAILED' where id=? and source_id=? and owner_id=? and status='PROCESSING'""",
                (run_id,str(sid),uid))
            if result.rowcount==1:durable.audit(api,con,'Document Extraction Failed','PPMP',sid)
    except Exception:
        # A revoked session cannot write completion data. A later authorized
        # retry marks an expired lease interrupted; never bypass authentication.
        pass


def latest(api,sid,fallback=None):
    from account_profiles import connection
    with connection(api) as (con,uid):
        ready(con)
        row=con.execute('''select extraction,status,summary,started_at,completed_at,parser_version
            from psms_documents.processing_runs where source_id=? and owner_id=?
            order by started_at desc,id desc limit 1''',(str(sid),uid)).fetchone()
        retained=None
        if row and not row['extraction']:
            retained=con.execute("""select extraction from psms_documents.processing_runs where source_id=? and owner_id=?
                and status in ('COMPLETE','INCOMPLETE') and extraction<>'{}'::jsonb
                order by started_at desc,id desc limit 1""",(str(sid),uid)).fetchone()
    if not row:return dict(fallback or {}),None
    return dict(row['extraction'] or (retained or {}).get('extraction') or fallback or {}),dict(row)
