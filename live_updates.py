"""Authenticated polling of durable, role/owner-scoped PostgreSQL revisions."""
import hashlib
import json
import re
import uuid
from flask import abort, jsonify, request, session, current_app
from werkzeug.exceptions import ServiceUnavailable
from account_profiles import connection
from flask_wtf.csrf import generate_csrf

PUBLIC={'ppmp','app','pr','aoq','po','nlp','iar','inventory','ris','dataset','audit'}
TOPICS=PUBLIC|{'documents','account','users'}
PAGE_TOPICS={
    'ppmp':{'ppmp','app','dataset','documents'},'app':{'app','ppmp','pr'},
    'pr':{'pr','app','aoq'},'aoq':{'aoq','pr','po','documents'},'po':{'po','aoq','nlp'},
    'nlp':{'nlp','po','aoq','pr'},'iar':{'iar','po','nlp','inventory'},
    'inventory':{'inventory','iar','ris'},'ris':{'ris','inventory'},
    'reports':PUBLIC,'dashboard':PUBLIC,'audit':{'audit'},'users':{'users'},'account':set()}


def topics_for_page(active):
    topics=set(PAGE_TOPICS.get(active,set()))|{'account'}
    if session.get('role')!='Admin':topics.discard('users')
    return sorted(topics)


def revisions(api):
    raw=request.args.get('topics','account')
    if len(raw)>160:abort(400)
    topics=sorted(set(raw.split(',')))
    if not set(topics)<=TOPICS or len(topics)>14:abort(400)
    if 'users' in topics and session.get('role')!='Admin':abort(403)
    try:
        with connection(api) as (con,uid):
            row=con.execute('select psms_sync.read_versions(?::text[]) as versions',(topics,)).fetchone()
    except ServiceUnavailable as error:
        cause=error;sqlstate='unavailable'
        for _ in range(8):
            value=getattr(cause,'sqlstate',None)
            if isinstance(value,str) and re.fullmatch(r'[0-9A-Z]{5}',value):sqlstate=value;break
            cause=getattr(cause,'__context__',None)
            if cause is None:break
        diagnostic=('SYNC_CONTRACT_UNAVAILABLE' if sqlstate in ('42883','3F000','42P01') else
                    'SYNC_PERMISSION_UNAVAILABLE' if sqlstate=='42501' else 'SYNC_REVISIONS_UNAVAILABLE')
        reference=uuid.uuid4().hex[:16]
        current_app.logger.warning('Revision polling unavailable sqlstate=%s diagnostic=%s reference=%s',sqlstate,diagnostic,reference)
        response=jsonify(error='Automatic record updates are unavailable. Existing edits remain on this page.',
                         diagnostic=diagnostic,reference=reference)
        response.status_code=503
        response.headers.update({'Cache-Control':'private, no-store','Vary':'Cookie',
                                 'X-PSMS-Diagnostic':diagnostic,'X-PSMS-Reference':reference})
        return response
    values=row['versions'] if row else {}
    response=jsonify(versions=values)
    response.set_etag(hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest())
    response.headers.update({'Cache-Control':'private, no-store','Vary':'Cookie'})
    response.headers['X-CSRFToken']=generate_csrf()
    return response.make_conditional(request)
