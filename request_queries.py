"""One short-lived read connection per GET; no cross-request record/auth cache."""
from copy import deepcopy
from flask import current_app, g, has_request_context, request


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
        else:con=factory()
        rows=con.execute(sql,params).fetchall()
        result=rows[0] if one and rows else None if one else rows
        if shared and one and key is not None and len(cache)<128:
            values=list(result.values()) if isinstance(result,dict) else []
            if (not any(isinstance(v,(dict,list,memoryview,bytearray)) for v in values)
                    and sum(len(v) if isinstance(v,(str,bytes)) else 64 for v in values)<65536):
                cache[key]=deepcopy(result)
        return result
    finally:
        if con is not None and not shared:con.close()


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
