"""Owner-only, server-paged private document history; no source bytes in queries.

These reads reuse the existing session actor bridge. Shared official documents
are linked only through recorded relationships after the private source check.
"""
from datetime import date, datetime
import json
import re
import uuid

from flask import abort, current_app, request, session, url_for
from werkzeug.exceptions import ServiceUnavailable

from account_profiles import connection
import document_processing as processing

PAGE_SIZE = 20
CATEGORIES = ('DBM', 'NON-DBM', 'LIB', 'MIXED', 'UNASSIGNED')
STATUSES = ('PROCESSING', 'COMPLETE', 'INCOMPLETE', 'FAILED', 'NEEDS_REVIEW', 'REVIEWED', 'CONFIRMED')
TYPES = ('.pdf', '.docx', '.xlsx', '.xlsm', '.csv', '.txt', '.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp')
SORTS = {
    'newest': 'created_at DESC,id DESC', 'oldest': 'created_at ASC,id ASC',
    'name_asc': 'lower(filename) ASC,id ASC', 'name_desc': 'lower(filename) DESC,id DESC',
    'modified_newest': 'modified_sort DESC,id DESC', 'modified_oldest': 'modified_sort ASC,id ASC',
    'type': 'extension ASC,created_at DESC,id DESC',
    'category': 'category ASC,created_at DESC,id DESC',
    'status': 'processing_status ASC,review_status ASC,created_at DESC,id DESC',
}
GROUPS = ('category', 'month', 'type', 'status')


def _object(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            result = json.loads(value)
            return result if isinstance(result, dict) else {}
        except (ValueError, TypeError):
            pass
    return {}


def _filters():
    uid = session.get('user_id')
    if type(uid) is not int or uid <= 0:
        abort(403)
    result = {}
    for name in ('q', 'sort', 'category', 'type', 'status', 'from', 'to', 'uploader', 'group', 'page'):
        key = 'h_' + name
        if len(request.args.getlist(key)) > 1:
            abort(400, description='Choose one value for each document history filter.')
        result[key] = request.args.get(key, '').strip()
    result['h_sort'] = result['h_sort'] or 'newest'
    result['h_page'] = result['h_page'] or '1'
    if (len(result['h_q']) > 200 or result['h_sort'] not in SORTS
            or result['h_category'] not in ('', *CATEGORIES)
            or result['h_status'] not in ('', *STATUSES)
            or result['h_group'] not in ('', *GROUPS)
            or not re.fullmatch(r'[1-9][0-9]{0,8}', result['h_page'])):
        abort(400, description='Choose valid document history filters.')
    if result['h_type']:
        result['h_type'] = '.' + result['h_type'].lower().lstrip('.')
        if result['h_type'] not in TYPES:
            abort(400, description='Choose a supported document type.')
    if result['h_uploader'] and result['h_uploader'] != str(uid):
        abort(403)
    result['h_uploader'] = str(uid)
    for key in ('h_from', 'h_to'):
        if result[key]:
            try:
                if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', result[key]):
                    raise ValueError()
                date.fromisoformat(result[key])
            except ValueError:
                abort(400, description='Choose valid document history dates.')
    if result['h_from'] and result['h_to'] and result['h_from'] > result['h_to']:
        abort(400, description='The document history end date must follow its start date.')
    return result


def _legacy_sort(column):
    """Normalize recorded legacy timestamps for sorting without unsafe casts.

    Legacy draft dates are text. Unknown text remains displayed as recorded and
    does not replace a known source/run timestamp in the modified-date ordering.
    """
    months = "ARRAY['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']"
    return f"""CASE WHEN {column} ~ '^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) [0-3][0-9], [0-9]{{4}} (0[1-9]|1[0-2]):[0-5][0-9] (AM|PM)$'
      THEN substring({column},9,4)||'-'||lpad(array_position({months},substring({column},1,3))::text,2,'0')||'-'||substring({column},5,2)||' '||
        lpad(((substring({column},14,2)::int%12)+CASE WHEN right({column},2)='PM' THEN 12 ELSE 0 END)::text,2,'0')||':'||substring({column},17,2)||':00'
      WHEN {column} ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}[ T][0-9]{{2}}:[0-9]{{2}}' THEN replace(left({column},19),'T',' ')
      ELSE '' END"""


def _json_count(payload, key):
    return f"""CASE WHEN jsonb_typeof({payload}->'{key}')='number'
      AND {payload}->>'{key}' ~ '^[0-9]{{1,6}}$' THEN ({payload}->>'{key}')::int ELSE 0 END"""


def _array_count(payload, key):
    return f"""(SELECT count(*) FROM jsonb_array_elements(CASE WHEN jsonb_typeof({payload}->'{key}')='array'
      THEN {payload}->'{key}' ELSE '[]'::jsonb END) entry(value) WHERE jsonb_typeof(entry.value)='object')"""


def _source_category(value):
    return f"CASE WHEN {value}->>'source_category' IN ('DBM','NON-DBM','LIB') THEN {value}->>'source_category' ELSE 'UNASSIGNED' END"


def _group_stats(value):
    category = _source_category(value)
    # Source item numbers restart across category sections. Preserve that scope
    # when supplier quotations describe the same numbered item more than once.
    identity = f"""CASE
      WHEN jsonb_typeof({value}->'source_item_no') IN ('string','number') AND btrim({value}->>'source_item_no')<>''
        THEN ({category})||':number:'||lower(regexp_replace({value}->>'source_item_no','[^[:alnum:]]','','g'))
      WHEN jsonb_typeof({value}->'item')='string' AND btrim({value}->>'item')<>''
        THEN ({category})||':name:'||lower(regexp_replace({value}->>'item','[^[:alnum:]]','','g')) END"""
    columns = [f'count(DISTINCT {identity}) AS total', 'count(*) AS row_count']
    for name, label in (('dbm', 'DBM'), ('non_dbm', 'NON-DBM'), ('lib', 'LIB'), ('unclassified', 'UNASSIGNED')):
        columns.append(f"count(DISTINCT {identity}) FILTER(WHERE ({category})='{label}') AS {name}")
    return ','.join(columns)


def _ppmp_catalog_sql():
    """PPMP history needs no AOQ draft/quotation privileges or JSON expansion."""
    legacy=_legacy_sort('d.updated_at')
    return f"""WITH owned AS (
      SELECT s.id,s.owner_id,s.kind,s.filename,s.extension,s.category AS source_category,s.created_at
      FROM psms_documents.sources s WHERE s.owner_id=? AND s.kind=?
    ), catalog AS (
      SELECT s.*,p.*,r.status AS run_status,
        greatest(to_char(s.created_at,'YYYY-MM-DD HH24:MI:SS'),coalesce(p.draft_modified,''),
          coalesce(to_char(coalesce(r.completed_at,r.started_at),'YYYY-MM-DD HH24:MI:SS'),'')) AS modified_sort
      FROM owned s LEFT JOIN LATERAL (
        SELECT count(*) FILTER(WHERE d.status<>'CANCELLED') AS total,
          count(*) FILTER(WHERE d.status NOT IN ('CANCELLED','CATEGORY_REVIEW') AND d.ppmp_category='DBM') AS dbm,
          count(*) FILTER(WHERE d.status NOT IN ('CANCELLED','CATEGORY_REVIEW') AND d.ppmp_category='NON-DBM') AS non_dbm,
          count(*) FILTER(WHERE d.status NOT IN ('CANCELLED','CATEGORY_REVIEW') AND d.ppmp_category='LIB') AS lib,
          count(*) FILTER(WHERE d.status<>'CANCELLED' AND (d.status='CATEGORY_REVIEW' OR d.ppmp_category NOT IN ('DBM','NON-DBM','LIB'))) AS unclassified,
          count(*) FILTER(WHERE d.status NOT IN ('CANCELLED','CATEGORY_REVIEW') AND (d.reviewed OR d.converted OR d.status IN ('REVIEWED','CONVERTED_TO_PPMP'))) AS reviewed,
          count(*) FILTER(WHERE d.status NOT IN ('CANCELLED','CATEGORY_REVIEW') AND (d.converted OR d.status='CONVERTED_TO_PPMP')) AS converted,
          max({legacy}) AS draft_modified,
          string_agg(concat_ws(' ',d.temp_code,official.no),' ') AS search_references
        FROM public.ppmp_ocr_drafts d LEFT JOIN public.ppmp official ON official.id=d.official_ppmp_id
        WHERE d.source_id=s.id AND d.owner_id=s.owner_id
      ) p ON true LEFT JOIN LATERAL (
        SELECT status,started_at,completed_at FROM psms_documents.processing_runs
        WHERE source_id=s.id AND owner_id=s.owner_id ORDER BY started_at DESC,id DESC LIMIT 1
      ) r ON true
    ), labeled AS (
      SELECT c.*,CASE WHEN (CASE WHEN dbm>0 THEN 1 ELSE 0 END+CASE WHEN non_dbm>0 THEN 1 ELSE 0 END+
        CASE WHEN lib>0 THEN 1 ELSE 0 END+CASE WHEN unclassified>0 THEN 1 ELSE 0 END)>1 THEN 'MIXED'
        WHEN dbm>0 THEN 'DBM' WHEN non_dbm>0 THEN 'NON-DBM' WHEN lib>0 THEN 'LIB' ELSE 'UNASSIGNED' END AS category,
        coalesce(run_status,CASE WHEN total>0 THEN 'COMPLETE' ELSE 'INCOMPLETE' END) AS processing_status,
        CASE WHEN total>0 AND converted=total THEN 'CONFIRMED'
          WHEN total>0 AND reviewed=total THEN 'REVIEWED' ELSE 'NEEDS_REVIEW' END AS review_status
      FROM catalog c
    ) SELECT * FROM labeled"""


def _catalog_sql(kind=None):
    if kind=='ppmp':return _ppmp_catalog_sql()
    legacy = _legacy_sort('d.updated_at')
    item_count = _json_count('a.payload', 'procurement_item_count')
    quote_count = _json_count('a.payload', 'quotation_count')
    offers = _array_count('a.payload', 'offer_items')
    source_items = _array_count('a.payload', 'procurement_items')
    suppliers = _array_count('a.payload', 'suppliers')
    fallback_total = f'greatest({item_count},{source_items},{offers})'
    return f"""WITH owned AS (
      SELECT s.id,s.owner_id,s.kind,s.filename,s.extension,s.category AS source_category,s.created_at
      FROM psms_documents.sources s WHERE s.owner_id=? AND s.kind=?
    ), catalog AS (
      SELECT s.*,coalesce(p.total,0) AS ppmp_total,
        CASE WHEN s.kind='aoq' THEN coalesce(source_groups.dbm,0) ELSE coalesce(p.dbm,0) END AS dbm,
        CASE WHEN s.kind='aoq' THEN coalesce(source_groups.non_dbm,0) ELSE coalesce(p.non_dbm,0) END AS non_dbm,
        CASE WHEN s.kind='aoq' THEN coalesce(source_groups.lib,0) ELSE coalesce(p.lib,0) END AS lib,
        CASE WHEN s.kind='aoq' THEN CASE WHEN source_groups.total>0 THEN source_groups.unclassified ELSE {fallback_total} END
          ELSE coalesce(p.unclassified,0) END AS unclassified,coalesce(p.reviewed,0) AS reviewed,
        coalesce(p.converted,0) AS converted,a.id AS draft_id,a.status AS aoq_status,
        a.official_aoq_id,coalesce(official.no,'') AS search_references,
        CASE WHEN source_groups.total>0 THEN source_groups.total ELSE {fallback_total} END AS aoq_total,
        greatest({quote_count},coalesce(quotations.row_count,0)) AS quotation_count,
        {suppliers} AS supplier_count,r.status AS run_status,
        greatest(to_char(s.created_at,'YYYY-MM-DD HH24:MI:SS'),coalesce(p.modified_sort,''),
          coalesce(to_char(a.updated_at,'YYYY-MM-DD HH24:MI:SS'),''),
          coalesce(to_char(coalesce(r.completed_at,r.started_at),'YYYY-MM-DD HH24:MI:SS'),'')) AS modified_sort
      FROM owned s
      LEFT JOIN LATERAL (
        SELECT count(*) FILTER(WHERE d.status<>'CANCELLED') AS total,
          count(*) FILTER(WHERE d.status NOT IN ('CANCELLED','CATEGORY_REVIEW') AND d.ppmp_category='DBM') AS dbm,
          count(*) FILTER(WHERE d.status NOT IN ('CANCELLED','CATEGORY_REVIEW') AND d.ppmp_category='NON-DBM') AS non_dbm,
          count(*) FILTER(WHERE d.status NOT IN ('CANCELLED','CATEGORY_REVIEW') AND d.ppmp_category='LIB') AS lib,
          count(*) FILTER(WHERE d.status<>'CANCELLED' AND (d.status='CATEGORY_REVIEW' OR d.ppmp_category NOT IN ('DBM','NON-DBM','LIB'))) AS unclassified,
          count(*) FILTER(WHERE d.status NOT IN ('CANCELLED','CATEGORY_REVIEW') AND (d.reviewed OR d.converted OR d.status IN ('REVIEWED','CONVERTED_TO_PPMP'))) AS reviewed,
          count(*) FILTER(WHERE d.status NOT IN ('CANCELLED','CATEGORY_REVIEW') AND (d.converted OR d.status='CONVERTED_TO_PPMP')) AS converted,
          max({legacy}) AS modified_sort
        FROM public.ppmp_ocr_drafts d WHERE d.source_id=s.id AND d.owner_id=s.owner_id AND s.kind='ppmp'
      ) p ON true
      LEFT JOIN LATERAL (
        SELECT d.id,d.payload,d.status,d.official_aoq_id,d.updated_at
        FROM psms_documents.aoq_drafts d WHERE d.source_id=s.id AND d.owner_id=s.owner_id AND s.kind='aoq'
        ORDER BY d.updated_at DESC,d.id DESC LIMIT 1
      ) a ON true
      LEFT JOIN LATERAL (
        SELECT {_group_stats('item.value')}
        FROM jsonb_array_elements(CASE WHEN jsonb_typeof(a.payload->'suppliers')='array'
          THEN a.payload->'suppliers' ELSE '[]'::jsonb END) supplier(value)
        CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(supplier.value->'items')='array'
          THEN supplier.value->'items' ELSE '[]'::jsonb END) item(value)
        WHERE jsonb_typeof(supplier.value)='object' AND jsonb_typeof(item.value)='object'
      ) quotations ON true
      LEFT JOIN LATERAL (
        SELECT {_group_stats('item.value')}
        FROM jsonb_array_elements(CASE WHEN jsonb_typeof(a.payload->'procurement_items')='array'
          THEN a.payload->'procurement_items' ELSE '[]'::jsonb END) item(value)
        WHERE jsonb_typeof(item.value)='object'
      ) procurement_groups ON true
      LEFT JOIN LATERAL (
        SELECT {_group_stats('item.value')}
        FROM jsonb_array_elements(CASE WHEN jsonb_typeof(a.payload->'offer_items')='array'
          THEN a.payload->'offer_items' ELSE '[]'::jsonb END) item(value)
        WHERE jsonb_typeof(item.value)='object'
      ) offer_groups ON true
      LEFT JOIN LATERAL (
        SELECT preferred.* FROM (
          SELECT 0 AS priority,procurement_groups.* WHERE procurement_groups.total>0
          UNION ALL SELECT 1,quotations.* WHERE quotations.total>0
          UNION ALL SELECT 2,offer_groups.*
        ) preferred ORDER BY priority LIMIT 1
      ) source_groups ON true
      LEFT JOIN LATERAL (
        SELECT status,started_at,completed_at FROM psms_documents.processing_runs
        WHERE source_id=s.id AND owner_id=s.owner_id ORDER BY started_at DESC,id DESC LIMIT 1
      ) r ON true
      LEFT JOIN public.aoq official ON official.id=a.official_aoq_id
    ), labeled AS (
      SELECT c.*,CASE WHEN kind='ppmp' THEN ppmp_total ELSE aoq_total END AS total,
        CASE WHEN (CASE WHEN dbm>0 THEN 1 ELSE 0 END+CASE WHEN non_dbm>0 THEN 1 ELSE 0 END+
                CASE WHEN lib>0 THEN 1 ELSE 0 END+CASE WHEN unclassified>0 THEN 1 ELSE 0 END)>1 THEN 'MIXED'
          WHEN dbm>0 THEN 'DBM' WHEN non_dbm>0 THEN 'NON-DBM' WHEN lib>0 THEN 'LIB' ELSE 'UNASSIGNED' END AS category,
        coalesce(run_status,CASE WHEN (kind='ppmp' AND ppmp_total>0) OR (kind='aoq' AND aoq_total>0)
          THEN 'COMPLETE' ELSE 'INCOMPLETE' END) AS processing_status,
        CASE WHEN kind='aoq' THEN CASE WHEN aoq_status='CONFIRMED' THEN 'CONFIRMED' ELSE 'NEEDS_REVIEW' END
          WHEN ppmp_total>0 AND converted=ppmp_total THEN 'CONFIRMED'
          WHEN ppmp_total>0 AND reviewed=ppmp_total THEN 'REVIEWED' ELSE 'NEEDS_REVIEW' END AS review_status
      FROM catalog c
    ) SELECT * FROM labeled"""


def _where(filters):
    clauses = []
    params = []
    for key, column in (('h_type', 'extension'),):
        if filters[key]:
            clauses.append(column + '=?')
            params.append(filters[key])
    if filters['h_q']:
        clauses.append("strpos(lower(concat_ws(' ',filename,id::text,search_references)),lower(?))>0")
        params.append(filters['h_q'])
    category = filters['h_category']
    if category:
        if category in ('DBM', 'NON-DBM', 'LIB'):
            column = {'DBM': 'dbm', 'NON-DBM': 'non_dbm', 'LIB': 'lib'}[category]
            clauses.append(f'({column}>0 OR category=?)')
        elif category == 'UNASSIGNED':
            clauses.append('(unclassified>0 OR category=?)')
        else:
            clauses.append('category=?')
        params.append(category)
    if filters['h_status']:
        clauses.append('(processing_status=? OR review_status=?)')
        params.extend((filters['h_status'], filters['h_status']))
    if filters['h_from']:
        clauses.append('created_at>=?::date')
        params.append(filters['h_from'])
    if filters['h_to']:
        clauses.append("created_at<(?::date+interval '1 day')")
        params.append(filters['h_to'])
    return (' WHERE ' + ' AND '.join(clauses) if clauses else ''), params


def _preserved_args():
    # Flask URL-building controls are never accepted from a browser query.
    route_names = set(request.view_args or {})
    return [(key, value) for key, value in request.args.items(multi=True)
            if not key.startswith(('_', 'h_')) and key != 'endpoint' and key not in route_names]


def _page_url(filters, page):
    values = dict(_preserved_args())
    values.update(filters)
    values['h_page'] = page
    values.update(request.view_args or {})
    return url_for(request.endpoint, **values) + '#document-history'


def _decorate(row, group):
    row = dict(row)
    row['id'] = str(row['id'])
    row['uploader'] = session.get('username') or 'Your account'
    row['modified_at'] = row.get('modified_sort') or row.get('created_at')
    row['DBM'] = int(row.get('dbm') or 0)
    row['NON-DBM'] = int(row.get('non_dbm') or 0)
    row['LIB'] = int(row.get('lib') or 0)
    row['total'] = int(row.get('total') or 0)
    row['unclassified'] = int(row.get('unclassified') or 0)
    row['needs_classification'] = row['unclassified']
    row['detail_url'] = url_for('document_history_detail', sid=row['id'])
    row['download_url'] = url_for('document_history_source', sid=row['id'])
    row['reopen_url'] = row['detail_url']
    if row['kind'] == 'ppmp':
        row['reopen_url'] = url_for('ppmp_intake_review', sid=row['id'])
    elif row.get('draft_id') and row.get('aoq_status') == 'REVIEW':
        row['reopen_url'] = url_for('aoq', draft_id=str(row['draft_id']))
    elif row.get('official_aoq_id'):
        row['reopen_url'] = url_for('aoq_review', id=row['official_aoq_id'])
    row['official_links'] = []
    row['workflow_stage'] = ('Official document created' if row['review_status'] == 'CONFIRMED'
                             else 'Item review completed' if row['review_status'] == 'REVIEWED'
                             else 'Extraction ' + row['processing_status'].lower() if row['processing_status'] != 'COMPLETE'
                             else 'Human review required')
    if group == 'month':
        row['group_label'] = str(row.get('created_at') or '')[:7]
    elif group == 'type':
        row['group_label'] = row.get('extension') or 'Unknown type'
    elif group == 'status':
        row['group_label'] = row['processing_status'] + ' / ' + row['review_status']
    else:
        row['group_label'] = row['category'] if group else 'Saved documents'
    return row


def listing(api, kind):
    if kind not in ('ppmp', 'aoq'):
        abort(400)
    filters = _filters()
    page = int(filters['h_page'])
    result = dict(rows=[], page=page, has_next=False, total=0, filters=filters,
                  group=filters['h_group'], unavailable=False, groups=[], kind=kind,
                  types=TYPES, statuses=STATUSES, categories=CATEGORIES,
                  uploader=session.get('username') or 'Your account',
                  preserved_args=_preserved_args())
    result['previous_url'] = _page_url(filters, page - 1) if page > 1 else None
    result['next_url'] = None
    reset_values = dict(_preserved_args())
    reset_values.update(request.view_args or {})
    result['reset_url'] = url_for(request.endpoint, **reset_values) + '#document-history'
    sql = _catalog_sql(kind)
    where, params = _where(filters)
    stage='readiness'
    try:
        with connection(api) as (con, uid):
            processing.ready(con)
            stage='count'
            count = con.execute('SELECT count(*) AS total FROM (' + sql + where + ') history_count', (uid, kind, *params)).fetchone()
            result['total'] = int(count['total']) if count else 0
            stage='page'
            rows = con.execute(sql + where + ' ORDER BY ' + SORTS[filters['h_sort']] + ' LIMIT 21 OFFSET ?',
                               (uid, kind, *params, (page - 1) * PAGE_SIZE)).fetchall()
            result['has_next'] = len(rows) > PAGE_SIZE
            result['rows'] = [_decorate(row, result['group']) for row in rows[:PAGE_SIZE]]
            if kind == 'ppmp' and result['rows']:
                stage='official_links'
                official = con.execute('''SELECT d.source_id,p.id,p.no,p.status FROM public.ppmp_ocr_drafts d
                    JOIN public.ppmp p ON p.id=d.official_ppmp_id WHERE d.owner_id=?
                    AND d.source_id=ANY(?::uuid[]) ORDER BY p.id LIMIT 100''',
                    (uid, [row['id'] for row in result['rows']])).fetchall()
                by_source = {row['id']: row for row in result['rows']}
                for record in official:
                    parent = by_source.get(str(record['source_id']))
                    if parent is not None:
                        parent['official_links'].append(dict(label=record['no'] or 'PPMP', status=record['status'],
                            url=url_for('ppmp_review', id=record['id'])))
    except ServiceUnavailable as error:
        reference=uuid.uuid4().hex[:16]
        cause=error
        sqlstate='unavailable'
        for _ in range(6):
            value=getattr(cause,'sqlstate',None)
            if isinstance(value,str) and re.fullmatch(r'[0-9A-Z]{5}',value):sqlstate=value;break
            cause=getattr(cause,'__context__',None)
            if cause is None:break
        current_app.logger.warning('Document history failed kind=%s stage=%s sqlstate=%s reference=%s',
                                   kind,stage,sqlstate,reference)
        result.update(unavailable=True, reference=reference,rows=[], groups=[], has_next=False, total=0)
    if result['has_next']:
        result['next_url'] = _page_url(filters, page + 1)
    groups = {}
    for row in result['rows']:
        groups.setdefault(row['group_label'], []).append(row)
    result['groups'] = [dict(label=label, rows=rows) for label, rows in groups.items()]
    return result


_WORKFLOW = """WITH owned_ppmp AS (
 SELECT official_ppmp_id AS id FROM public.ppmp_ocr_drafts WHERE source_id=? AND owner_id=? AND official_ppmp_id IS NOT NULL
), owned_aoq AS (
 SELECT official_aoq_id AS id,pr_id FROM psms_documents.aoq_drafts WHERE source_id=? AND owner_id=?
), lp AS (
 SELECT p.id,p.no,p.status,p.created_at FROM public.ppmp p WHERE p.id IN (SELECT id FROM owned_ppmp)
 OR p.id IN (SELECT r.ppmp_id FROM public.pr r WHERE r.id IN (SELECT pr_id FROM owned_aoq))
 OR p.id IN (SELECT a.ppmp_id FROM public.app_plan a JOIN public.pr r ON r.app_id=a.id WHERE r.id IN (SELECT pr_id FROM owned_aoq))
), la AS (
 SELECT a.id,a.no,a.status,a.created_at FROM public.app_plan a WHERE a.ppmp_id IN (SELECT id FROM owned_ppmp)
 OR a.id IN (SELECT app_id FROM public.pr WHERE id IN (SELECT pr_id FROM owned_aoq))
), lr AS (
 SELECT r.id,r.no,r.status,r.created_at FROM public.pr r WHERE r.ppmp_id IN (SELECT id FROM owned_ppmp)
 OR r.app_id IN (SELECT id FROM la WHERE EXISTS(SELECT 1 FROM owned_ppmp)) OR r.id IN (SELECT pr_id FROM owned_aoq)
), lq AS (
 SELECT a.id,a.no,a.status,a.created_at FROM public.aoq a WHERE a.id IN (SELECT id FROM owned_aoq)
 OR (EXISTS(SELECT 1 FROM owned_ppmp) AND a.pr_id IN (SELECT id FROM lr))
), lo AS (
 SELECT p.id,p.no,p.status,p.created_at FROM public.po p WHERE p.aoq_id IN (SELECT id FROM lq)
), li AS (
 SELECT i.id,i.item_code AS no,i.status,i.created_at FROM public.inventory i WHERE i.source_po IN (SELECT no FROM lo)
), linked AS (
 SELECT 1 AS stage,'ppmp' AS module,id,no,status,created_at FROM lp UNION ALL
 SELECT 2,'app',id,no,status,created_at FROM la UNION ALL
 SELECT 3,'pr',id,no,status,created_at FROM lr UNION ALL
 SELECT 4,'aoq',id,no,status,created_at FROM lq UNION ALL
 SELECT 5,'po',id,no,status,created_at FROM lo UNION ALL
 SELECT 6,'nlp',id,no,status,created_at FROM public.nlp WHERE po_id IN (SELECT id FROM lo) UNION ALL
 SELECT 7,'iar',id,no,status,created_at FROM public.iar WHERE po_id IN (SELECT id FROM lo) UNION ALL
 SELECT 8,'inventory',id,no,status,created_at FROM li UNION ALL
 SELECT 9,'ris',id,no,status,created_at FROM public.ris_ics WHERE no IN (
   SELECT document_no FROM public.inventory_transactions WHERE inventory_id IN (SELECT id FROM li))
) SELECT * FROM linked ORDER BY stage,id LIMIT 101"""


def _event_key(event):
    value = event.get('at')
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S.%f')
    text = str(value or '')
    try:
        return datetime.strptime(text, '%b %d, %Y %I:%M %p').strftime('%Y-%m-%d %H:%M:%S.%f')
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M:%S.%f')
        except ValueError:
            return ''


def detail(api, sid):
    try:
        sid = str(uuid.UUID(str(sid)))
    except (ValueError, TypeError, AttributeError):
        abort(404)
    result = dict(source=None, extraction={}, run=None, runs=[], events=[], links=[], unavailable=False, history=[],
                  events_truncated=False, links_truncated=False)
    with connection(api) as (con, uid):
        source = con.execute('''SELECT id,owner_id,kind,category,filename,extension,sha256,created_at,extraction
            FROM psms_documents.sources WHERE id=? AND owner_id=? AND kind IN ('ppmp','aoq')''', (sid, uid)).fetchone()
        if not source:
            abort(404)
        result['source'] = dict(source)
        result['source']['id'] = str(source['id'])
        result['extraction'] = _object(source['extraction'])
        result['events'].append(dict(at=source['created_at'], action='Original document uploaded', module=source['kind'].upper(), reference=sid))
        try:
            processing.ready(con)
        except ServiceUnavailable:
            result['unavailable'] = True
        if not result['unavailable']:
            runs = con.execute('''SELECT id,status,parser_version,source_sha256,started_at,completed_at,summary,error_code
                FROM psms_documents.processing_runs WHERE source_id=? AND owner_id=?
                ORDER BY started_at DESC,id DESC LIMIT 300''', (sid, uid)).fetchall()
            result['runs'] = [dict(run) for run in runs]
            if runs:
                latest = con.execute('''SELECT id,status,parser_version,started_at,completed_at,extraction,summary,error_code
                    FROM psms_documents.processing_runs WHERE source_id=? AND owner_id=? ORDER BY started_at DESC,id DESC LIMIT 1''', (sid, uid)).fetchone()
                result['run'] = dict(latest)
                result['extraction'] = _object(latest['extraction']) or result['extraction']
                if not _object(latest['extraction']):
                    retained=con.execute("""SELECT extraction FROM psms_documents.processing_runs
                        WHERE source_id=? AND owner_id=? AND status IN ('COMPLETE','INCOMPLETE')
                        AND extraction<>'{}'::jsonb ORDER BY started_at DESC,id DESC LIMIT 1""",(sid,uid)).fetchone()
                    if retained:result['extraction']=_object(retained['extraction']) or result['extraction']
            for run in runs:
                result['events'].append(dict(at=run['started_at'], action='Extraction started', module=source['kind'].upper(),
                    reference=str(run['id']), parser_version=run['parser_version']))
                if run['completed_at'] is not None:
                    result['events'].append(dict(at=run['completed_at'], action='Extraction ' + run['status'].lower(),
                        module=source['kind'].upper(), reference=str(run['id']), error_code=run['error_code']))
        draft_order = 'greatest(' + _legacy_sort('reviewed_at') + ',' + _legacy_sort('converted_at') + ') DESC,id DESC'
        drafts = con.execute('''SELECT id,temp_code,status,ppmp_category,reviewed_at,converted_at,official_ppmp_id
            FROM public.ppmp_ocr_drafts WHERE source_id=? AND owner_id=? ORDER BY ''' + draft_order + ' LIMIT 300', (sid, uid)).fetchall()
        references = {sid}
        for draft in drafts:
            if draft['temp_code']:
                references.add(draft['temp_code'])
            for field, action in (('reviewed_at', 'Item review recorded'), ('converted_at', 'Official PPMP created')):
                if draft[field]:
                    result['events'].append(dict(at=draft[field], action=action, module='PPMP', reference=draft['temp_code']))
        aoq_drafts = con.execute('''SELECT id,status,created_at,updated_at FROM psms_documents.aoq_drafts
            WHERE source_id=? AND owner_id=? ORDER BY updated_at DESC,id DESC LIMIT 300''', (sid, uid)).fetchall()
        for draft in aoq_drafts:
            reference = str(draft['id'])
            references.add(reference)
            result['events'].append(dict(at=draft['created_at'], action='Private AOQ draft saved', module='AOQ', reference=reference))
            if draft['updated_at'] and draft['updated_at'] != draft['created_at']:
                action = {'CONFIRMED': 'AOQ confirmation recorded', 'CANCELLED': 'AOQ draft cancellation recorded'}.get(
                    draft['status'], 'Private AOQ draft last updated')
                result['events'].append(dict(at=draft['updated_at'], action=action, module='AOQ', reference=reference))
        # Project only recorded history metadata; never transfer before/after
        # item payloads merely to construct a timeline.
        changes = con.execute('''SELECT d.temp_code,h.value->>'at' AS at,h.value->>'by' AS actor,
                h.value->>'revision' AS revision,h.value->>'previous_category' AS previous_category,h.value->>'category' AS category
            FROM public.ppmp_ocr_drafts d CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(d.confidence_json::jsonb->'_review_history')='array'
                THEN d.confidence_json::jsonb->'_review_history' ELSE '[]'::jsonb END) h(value)
            WHERE d.source_id=? AND d.owner_id=? ORDER BY ''' + _legacy_sort("h.value->>'at'") + ' DESC,d.id DESC LIMIT 300', (sid, uid)).fetchall()
        result['history'] = [dict(change) for change in changes]
        for change in changes:
            if change['temp_code']:
                references.add(change['temp_code'])
            if change['at']:
                result['events'].append(dict(at=change['at'], action='Item correction and category recorded', module='PPMP',
                    reference=change['temp_code'], actor=change['actor'], revision=change['revision'],
                    previous_category=change['previous_category'], category=change['category']))
        assignments = con.execute('''SELECT d.temp_code,
                d.confidence_json::jsonb->'_category_assignment'->>'at' AS at,
                d.confidence_json::jsonb->'_category_assignment'->>'by' AS actor,
                d.confidence_json::jsonb->'_category_assignment'->>'category' AS category,
                d.confidence_json::jsonb->'_category_assignment'->>'evidence' AS evidence
            FROM public.ppmp_ocr_drafts d WHERE d.source_id=? AND d.owner_id=?
                AND jsonb_typeof(d.confidence_json::jsonb->'_category_assignment')='object'
                AND d.confidence_json::jsonb->'_category_assignment'->>'method'='automatic'
                AND nullif(d.confidence_json::jsonb->'_category_assignment'->>'at','') IS NOT NULL
            ORDER BY ''' + _legacy_sort("d.confidence_json::jsonb->'_category_assignment'->>'at'") + ' DESC,d.id DESC LIMIT 300', (sid, uid)).fetchall()
        for assignment in assignments:
            if assignment['temp_code']:
                references.add(assignment['temp_code'])
            result['events'].append(dict(at=assignment['at'], action='Automatic category assignment recorded',
                module='PPMP', reference=assignment['temp_code'], actor=assignment['actor'],
                category=assignment['category'], evidence=assignment['evidence']))
        endpoints = {'ppmp': 'ppmp_review', 'app': 'app_review', 'pr': 'pr_review', 'aoq': 'aoq_review',
                     'po': 'po_review', 'nlp': 'nlp_review', 'iar': 'iar_review', 'ris': 'ris_ics_review'}
        linked = con.execute(_WORKFLOW, (sid, uid, sid, uid)).fetchall()
        result['links_truncated'] = len(linked) > 100
        for record in linked[:100]:
            link = dict(record)
            link['label'] = record['no'] or record['module'].upper()
            link['url'] = (url_for('inventory', q=record['no']) if record['module'] == 'inventory'
                           else url_for(endpoints[record['module']], id=record['id']))
            result['links'].append(link)
            if record['no']:
                references.add(record['no'])
        audit = con.execute('''SELECT dt,"user" AS actor,action,module,ref FROM public.audit_trail
            WHERE ref=ANY(?::text[]) AND module=ANY(?::text[]) ORDER BY ''' + _legacy_sort('dt') + ' DESC,id DESC LIMIT 300',
            (sorted(references), ['PPMP', 'APP', 'PR', 'AOQ', 'PO', 'NLP', 'IAR', 'Inventory', 'RIS/ICS'])).fetchall()
        for entry in audit:
            if entry['dt']:
                result['events'].append(dict(at=entry['dt'], action=entry['action'], actor=entry['actor'],
                    module=entry['module'], reference=entry['ref']))
    result['events_truncated'] = len(result['events']) > 300
    newest = sorted(result['events'], key=_event_key, reverse=True)[:300]
    result['events'] = sorted(newest, key=_event_key)
    for event in result['events']:
        event.setdefault('actor', None)
    return result
