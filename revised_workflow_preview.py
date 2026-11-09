"""Admin-only inspection of the existing revised UI; never activates procurement.

The platform deployment identity is server configuration, not a browser header.
Only its exact reviewed project hostname can expose these routes. Official
aliases and forwarded hosts cannot select preview mode.
"""
import json
import os
import re
from urllib.parse import parse_qsl, urlsplit

from flask import abort, g, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException

import procurement_comparison_v1 as evidence

PREFIX = '/workflow-preview'
PAGES = ('pr', 'aoq', 'po', 'nlp')
ENDPOINTS = {name: 'workflow_preview_' + name for name in PAGES}
ENDPOINTS.update({name + '_review': 'workflow_preview_' + name + '_review' for name in PAGES})


def deployment_matches():
    host = os.environ.get('VERCEL_URL', '')
    # Immutable deployment URL, excluding project/branch aliases and all custom
    # domains. Do not consult Production environment flags or forwarded headers.
    return (os.environ.get('VERCEL') == '1'
            and re.fullmatch(r'asuncion-[a-z0-9]{9}-arman-a87f\.vercel\.app', host) is not None
            and request.host == host)


def admin():
    account = getattr(g, 'psms_account', None)
    return bool(account and session.get('logged_in') is True
                and type(session.get('user_id')) is int
                and account.get('id') == session['user_id']
                and account.get('status') == 'Active' and account.get('role') == 'Admin')


def available():
    return deployment_matches() and admin()


def active():
    return getattr(g, 'workflow_preview', False) is True


def preview_url(endpoint, **values):
    # Resolve the destination before signing: navigation MACs bind endpoints.
    if active():
        endpoint = ENDPOINTS['pr'] if endpoint == 'dashboard' else ENDPOINTS.get(endpoint, endpoint)
    return url_for(endpoint, **values)


def identifier(value):
    if not re.fullmatch(r'[1-9][0-9]{0,18}', str(value or '')) or int(value) > 9223372036854775807:
        abort(400, description='Select valid source document identifiers.')
    return int(value)


def source_pair(api, pr_id, po_id):
    pr = api['q']("select id,no,status,office,items,app_id,ppmp_id from pr where id=? and status='Approved'",
                  (identifier(pr_id),), one=True)
    po = api['q']("""select p.*,a.pr_id legacy_pr_id,a.status aoq_status,a.no aoq_no
        from po p join aoq a on a.id=p.aoq_id where p.id=?
        and p.status in ('For Review','Approved') and a.status='Approved'""", (identifier(po_id),), one=True)
    if not pr or not po:
        abort(404, description='The selected Approved PR or reviewed PO is unavailable.')
    if any(link is not None and link != pr['id'] for link in (po.get('legacy_pr_id'), po.get('pr_id'))):
        abort(409, description='The selected PR conflicts with the existing official relationship.')
    return dict(pr), dict(po)


def technical(api, pr, po, mapping=None):
    result = evidence.compare(api['parse_items'](pr['items']), api['parse_items'](po['items']), manual_mapping=mapping)
    result.update(semantic_state='NOT_REQUESTED', semantic_candidates=[])
    return result


def nlp_page(api):
    # These are the GET reads and actual template from the revised implementation.
    # JSON projection tolerates the old schema without guessing or adding columns.
    prs = api['q']("select id,no,office,items from pr where status='Approved' order by id desc")
    pos = api['q']("""select p.id,p.no,p.supplier,p.items,a.no aoq_no,
        exists(select 1 from nlp n where n.po_id=p.id) has_verification
        from po p join aoq a on a.id=p.aoq_id
        where p.status='For Review' and a.status='Approved' order by p.id desc""")
    rows = api['q']("""select n.*,p.no po_no,r.no pr_no from nlp n join po p on p.id=n.po_id
        left join aoq a on a.id=p.aoq_id left join pr r on r.id=coalesce(
        (to_jsonb(n)->>'pr_id')::bigint,(to_jsonb(p)->>'pr_id')::bigint,a.pr_id) order by n.id desc""")
    pr = po = comparison = None
    if request.args.get('pr_id') or request.args.get('po_id'):
        pr, po = source_pair(api, request.args.get('pr_id'), request.args.get('po_id'))
        comparison = technical(api, pr, po)
    return render_template('revised_nlp.html', active='nlp', title='PR-PO NLP Verification',
        prs=prs, pos=pos, rows=rows, selected_pr=pr, selected_po=po, comparison=comparison,
        pr_items=api['parse_items'](pr['items']) if pr else [],
        po_items=api['parse_items'](po['items']) if po else [],
        model_state=dict(active=False, message='Inference is disabled in read-only preview. No model was run and no result was saved.'))


def nlp_review(api, id):
    row = api['q']('select * from nlp where id=?', (id,), one=True)
    if not row:
        abort(404)
    if row.get('workflow_version') != 1:
        return api['nlp_review'](id)
    pr, po = source_pair(api, row.get('pr_id'), row.get('po_id'))
    mapping = row.get('review_mapping')
    try:
        details = json.loads(row.get('details') or '{}')
    except (ValueError, TypeError):
        details = {}
    return render_template('revised_nlp_review.html', active='nlp', title='Review PR-PO Verification',
        row=row, pr=pr, po=po, pr_items=api['parse_items'](pr['items']), po_items=api['parse_items'](po['items']),
        comparison=technical(api, pr, po, mapping if isinstance(mapping, list) else None), details=details,
        stale=evidence.fingerprint(pr['items']) != row.get('pr_items_hash')
              or evidence.fingerprint(po['items']) != row.get('po_items_hash'))


def register(app, api):
    host = os.environ.get('VERCEL_URL', '')
    if (os.environ.get('VERCEL') == '1'
            and re.fullmatch(r'asuncion-[a-z0-9]{9}-arman-a87f\.vercel\.app', host)):
        # Trust just the immutable deployment address in addition to the
        # operator's existing hosts. No wildcard or forwarded-host trust.
        if app.config.get('TRUSTED_HOSTS') is not None:
            app.config['TRUSTED_HOSTS'] = [*app.config['TRUSTED_HOSTS'], host]

    @app.before_request
    def preview_boundary():
        is_preview = request.path == PREFIX or request.path.startswith(PREFIX + '/')
        if is_preview:
            if not available():
                abort(404)
            if request.method not in ('GET', 'HEAD'):
                abort(405)
            g.workflow_preview = True

    @app.context_processor
    def preview_context():
        context = dict(workflow_preview=active(), workflow_preview_available=available())
        if active():
            context.update(url_for=preview_url, independent_aoq=True, can_approve=False, can_review=False)
        return context

    @app.after_request
    def retain_preview_navigation(response):
        if active() and response.status_code in (301, 302, 303, 307, 308) and response.location:
            parts = urlsplit(response.location)
            if not parts.scheme and not parts.netloc:
                try:
                    endpoint, values = app.url_map.bind('localhost').match(parts.path, method='GET')
                except HTTPException:
                    return response
                if endpoint in ENDPOINTS:
                    values.update({key: value for key, value in parse_qsl(parts.query) if key != '_nav'})
                    response.location = preview_url(endpoint, **values)
        return response

    def dispatch(page, id=None):
        try:
            if page == 'nlp':
                return nlp_page(api) if id is None else nlp_review(api, id)
            return api[page if id is None else page + '_review'](**({} if id is None else dict(id=id)))
        except HTTPException:
            raise
        except Exception:
            # No driver messages, contents, synthetic records or fake model result.
            return render_template('operation_error.html', active=page, title='Preview unavailable', status=503,
                message='Existing authorized records are temporarily unavailable for this preview. No records or model results were created.'), 503

    for page in PAGES:
        app.add_url_rule(PREFIX + '/' + page, ENDPOINTS[page],
            lambda page=page: dispatch(page), methods=['GET'])
        app.add_url_rule(PREFIX + '/' + page + '/<int:id>/view', ENDPOINTS[page + '_review'],
            lambda id, page=page: dispatch(page, id), methods=['GET'])
