"""One short-lived read connection per GET; no cross-request record/auth cache."""
from copy import deepcopy
from flask import abort, current_app, g, has_request_context, request, session
import json
import server_sessions_v0_1_0 as sessions


def _bind_actor(con):
    """Bind only the server-validated request identity, transaction locally.

    Public/unauthenticated reads retain their old behavior. Private RLS still
    validates the active account/session in PostgreSQL; this grants no privilege.
    No row lock, write, commit, persistent connection state or client role claim.
    """
    if not has_request_context():return
    account=getattr(g,'psms_account',None);uid=session.get('user_id')
    if (session.get('logged_in') is not True or type(uid) is not int or uid<=0
            or not account or account.get('id')!=uid or account.get('status')!='Active'
            or account.get('role') not in ('Admin','Staff','Inspector')):return
    digest=sessions.token_hash(session.get(sessions.COOKIE_KEY))
    if not digest:abort(403)
    con.execute('SET TRANSACTION READ ONLY')
    con.execute("select set_config('psms.workbook_actor',?,true)",
                (json.dumps(dict(uid=uid,sid=digest)),))


def read(factory,sql,params=(),one=False):
    shared=has_request_context() and request.method in ('GET','HEAD') and str(sql).lstrip().lower().startswith('select ')
    key=None
    if shared and one:
        try:key=(sql,tuple(params));hash(key)
        except TypeError:key=None
        cache=g.setdefault('_psms_read_cache',{})
        if key is not None and key in cache:return deepcopy(cache[key])
    con=None
    try:
        if shared:
            con=getattr(g,'_psms_read_connection',None)
            if con is None:
                con=factory();g._psms_read_connection=con
                con.execute('SET TRANSACTION READ ONLY')
                con.execute("SET LOCAL statement_timeout = '10s'")
                _bind_actor(con)
        else:
            con=factory()
            _bind_actor(con)
        rows=con.execute(sql,params).fetchall()
        result=rows[0] if one and rows else None if one else rows
        if shared and one and key is not None and len(cache)<128:
            values=list(result.values()) if isinstance(result,dict) else []
            if (not any(isinstance(v,(dict,list,memoryview,bytearray)) for v in values)
                    and sum(len(v) if isinstance(v,(str,bytes)) else 64 for v in values)<65536):
                cache[key]=deepcopy(result)
        return result
    finally:
        if con is not None and not shared:
            try:con.rollback()
            finally:con.close()


def close(error=None):
    if not has_request_context():return
    con=g.pop('_psms_read_connection',None)
    g.pop('_psms_read_cache',None)
    if con is not None:
        failed=False
        try:con.rollback()
        except Exception:failed=True
        try:con.close()
        except Exception:failed=True
        if failed:current_app.logger.warning('Request read connection cleanup failed [READ_CLEANUP_FAILED].')
