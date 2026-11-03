"""Authenticated polling of durable, role/owner-scoped PostgreSQL revisions."""
import hashlib
import json
from flask import abort, jsonify, request, session
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
    with connection(api) as (con,uid):
        row=con.execute('select psms_sync.read_versions(?::text[]) as versions',(topics,)).fetchone()
    values=row['versions'] if row else {}
    response=jsonify(versions=values)
    response.set_etag(hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest())
    response.headers.update({'Cache-Control':'private, no-store','Vary':'Cookie'})
    response.headers['X-CSRFToken']=generate_csrf()
    return response.make_conditional(request)
