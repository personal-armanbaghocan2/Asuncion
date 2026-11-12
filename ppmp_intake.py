"""One private PPMP source, item-level category review, existing official workflow."""
import hashlib
import json
import re
import uuid
from io import BytesIO
import time

from flask import abort, flash, g, make_response, redirect, render_template, request, send_file, session, url_for
import durable_ocr_v0_1_0 as durable
import document_processing as processing
from private_document_uploads import validate, extraction_path

CATEGORIES=('DBM','NON-DBM','LIB')


def category(value):
    text=re.sub(r'[\s\u2010-\u2015]+','-',str(value or '').strip().upper())
    if text=='NONDBM':text='NON-DBM'
    return text if text in CATEGORIES else None


def suggest_category(row):
    """Use explicit row, worksheet or scoped document evidence; never item-name guesses."""
    confidence=row.get('field_confidence') or {}
    evidence=confidence.get('_source_evidence') or {}
    candidates=[]
    cells=str(evidence.get('text') or '').split(' | ')
    explicit_columns=False
    for index,label in enumerate(evidence.get('headers') or []):
        if re.sub(r'[^a-z]+',' ',str(label).lower()).strip() in ('category','ppmp category','source category'):
            explicit_columns=True
            value=category(cells[index]) if index<len(cells) else None
            if not value:return None,'source item category is missing or unsupported; manual assignment required'
            candidates.append((value,'item category column'))
    # Explicit item columns take precedence over shared document context.
    if not candidates and not explicit_columns:
        value=category((confidence.get('_document_metadata') or {}).get('category'))
        if value:candidates.append((value,'scoped source category'))
    if not candidates:
        value=category(evidence.get('worksheet'))
        if value:candidates.append((value,'worksheet category'))
    values={value for value,_ in candidates}
    return (next(iter(values)),candidates[0][1]) if len(values)==1 else (None,'manual category assignment required')


def item_identity(row):
    evidence=(row.get('field_confidence') or {}).get('_source_evidence') or {}
    number=str(evidence.get('item_number') or '').strip()
    if number.isdigit():number=str(int(number))
    scope=[str(evidence.get('worksheet') or ''),str(evidence.get('page') or '')]
    anchor=['item',number] if number else ['row',str(evidence.get('row') or ''),str(evidence.get('source') or '')]
    if not number and not evidence.get('row'):
        anchor=['text',str(evidence.get('text') or ''),str(row.get('temp_id') or '')]
    return hashlib.sha256(json.dumps(scope+anchor,ensure_ascii=False).encode()).hexdigest()


def catalog_categories(api,rows):
    """Exact catalog identity only; contradictory matches remain unclassified."""
    names={str((row.get('fields') or {}).get('Item Name') or '').strip().lower()
           for row in rows if suggest_category(row)[0] is None}
    names.discard('')
    if not names:return {}
    records=api['q']('''select id,article,description,unit,sheet_name,raw_data,normalized_data
        from dataset_records where active=1 and
        (lower(btrim(article))=any(?::text[]) or lower(btrim(description))=any(?::text[]))
        order by id limit 501''',(sorted(names),sorted(names)))
    if len(records)>500:return {}
    result={}
    for record in records:
        values=set();explicit=False
        for data in (api['_json_dict'](record.get('raw_data')),api['_json_dict'](record.get('normalized_data'))):
            for key in ('PPMP Category','ppmp_category','Source Category','category','Category'):
                if key in data:
                    explicit=True
                    # None deliberately preserves a missing/unsupported source
                    # category; a worksheet title must not silently replace it.
                    values.add(category(data[key]))
        if not explicit:
            sheet=category(record.get('sheet_name'))
            if sheet:values.add(sheet)
        for name in (record.get('article'),record.get('description')):
            key=str(name or '').strip().lower()
            if key in names:result.setdefault(key,set()).update(values)
    return result


def save_extraction(api,name,ext,content,extracted,structured,*,source_id=None,run_id=None,timings=None):
    """Persist atomically after extraction; repeated source bytes reuse existing drafts."""
    rows=structured.get('rows') or []
    if len(rows)>200:abort(400,description='Review at most 200 items per upload.')
    if not (structured.get('model') or {}).get('active'):
        abort(503,description='The semantic mapper is unavailable. No extraction was saved.')
    digest=hashlib.sha256(content).hexdigest()
    if source_id is None:
        source_id,run_id=processing.capture(api,name,ext,content)
        if run_id is None:return redirect(url_for('ppmp_intake_review',sid=source_id))
    state=dict(text=str(extracted.get('raw_text') or ''),method=extracted.get('extraction_method') or 'native extraction',
        warnings=structured.get('warnings') or [],filename=name,structured=structured,item_count=len(rows),
        extraction_timestamp=api['now'](),intake='automatic-item-routing-v2',
        parser_version=processing.PARSER_VERSION,timings=timings or {})
    catalog=catalog_categories(api,rows)
    summary=dict(total=len(rows),DBM=0,**{'NON-DBM':0},LIB=0,unclassified=0,
                 inserted=0,preserved=0,updated=0,uncertain_regions=len(structured.get('review_candidates') or []))
    g.document_stage='draft_persistence'
    with processing.transaction(api) as (con,uid):
        processing.lock(con,uid,digest)
        processing.current_run(con,uid,source_id,run_id)
        existing=con.execute('''select * from ppmp_ocr_drafts where source_id=? and owner_id=?
            order by id for update''',(str(source_id),uid)).fetchall()
        prior={}
        for draft in existing:
            confidence=api['_json_dict'](draft['confidence_json'])
            key=confidence.get('_extraction_identity') or item_identity(dict(field_confidence=confidence,fields=api['_json_dict'](draft['fields_json'])))
            prior.setdefault(key,[]).append(draft)
        seen=set();identified=[]
        for row_index,row in enumerate(rows):
            evidence=(row.get('field_confidence') or {}).get('_source_evidence') or {}
            key=item_identity(row)
            if key in seen:
                # Repeated item numbers do not erase distinct physical rows.
                key=hashlib.sha256((key+':'+str(evidence.get('row'))+':'+str(row_index)).encode()).hexdigest()
            seen.add(key);identified.append((key,row))
        unmatched_new=seen-set(prior)
        if existing and unmatched_new and (set(prior)-seen or any(len(group)>1 for group in prior.values())):
            # A parser can change row anchors. Never guess which corrected or
            # official draft a new anchor replaces, or silently duplicate it.
            message='Source item identities changed. Existing drafts and corrections were preserved. Reconcile the saved items before reprocessing.'
            response=make_response(render_template('operation_error.html',active='ppmp',title='Source reconciliation required',
                message=message,status=409),409)
            response.headers['Cache-Control']='private, no-store'
            abort(response)
        for key,row in identified:
            fields=dict(row.get('fields') or {})
            confidence=dict(row.get('field_confidence') or {})
            confidence['_original_fields']=dict(fields)
            from offline_ocr_mapper import KEY_TO_LABEL
            project_label=KEY_TO_LABEL['project_type']
            if category(fields.get(project_label)):
                fields[project_label]=''
                confidence[project_label]=dict(score=0,level='Unresolved',source='PPMP category is not procurement project type')
            proposed,reason=suggest_category(row)
            evidence=confidence.get('_source_evidence') or {}
            explicit_headers=[i for i,label in enumerate(evidence.get('headers') or [])
                              if re.sub(r'[^a-z]+',' ',str(label).lower()).strip() in ('category','ppmp category','source category')]
            if not proposed and not explicit_headers:
                matches=catalog.get(str(fields.get('Item Name') or '').strip().lower(),set())
                if len(matches)==1 and next(iter(matches)) in CATEGORIES:
                    proposed,reason=next(iter(matches)),'exact existing procurement catalog evidence'
            confidence['_category_suggestion']=dict(category=proposed,evidence=reason)
            confidence['_category_confirmed']=False
            confidence['_category_assignment']=dict(category=proposed,method='automatic' if proposed else 'unresolved',evidence=reason,at=api['now']())
            confidence['_extraction_identity']=key
            summary[proposed or 'unclassified']+=1
            status='NEEDS_REVIEW' if proposed else 'CATEGORY_REVIEW'
            matches=prior.get(key,[])
            if matches:
                draft=matches[0]
                previous=api['_json_dict'](draft['confidence_json'])
                if (len(matches)>1 or draft['revision']>1 or draft['reviewed'] or draft['converted']
                        or draft['status'] in ('CANCELLED','CONVERTED_TO_PPMP') or previous.get('_category_confirmed')):
                    summary['preserved']+=1;continue
                confidence['_reextraction_history']=[dict(run_id=run_id,at=api['now'](),
                    before_fields=api['_json_dict'](draft['fields_json']),before_category=draft['ppmp_category'])]
                con.execute('''update ppmp_ocr_drafts set ppmp_category=?,fields_json=?,confidence_json=?,
                    status=?,reviewed=false,updated_at=?,revision=revision+1 where id=?''',
                    (proposed or 'UNASSIGNED',durable.payload(fields),durable.payload(confidence),status,api['now'](),draft['id']))
                summary['updated']+=1;continue
            did=con.execute(f'''insert into ppmp_ocr_drafts(upload_token,temp_code,ppmp_category,fields_json,confidence_json,
                average_confidence,source_filename,raw_ocr_text,extraction_method,status,reviewed,created_by,created_at,updated_at,owner_id,source_id)
                values(?,?,?,?,?,?,?,?,?,'{status}',false,?,?,?,?,?) returning id''',
                (str(source_id),'OCR-'+str(uuid.uuid4()),proposed or 'UNASSIGNED',durable.payload(fields),durable.payload(confidence),
                 float(row.get('average_confidence') or 0),name,state['text'],state['method'],session['username'],api['now'](),api['now'](),uid,str(source_id))).fetchone()['id']
            con.execute('update ppmp_ocr_drafts set temp_code=? where id=?',(f'OCR-TEMP-{did:06d}',did))
            summary['inserted']+=1
        result_status=processing.complete(con,uid,run_id,state,summary)
        durable.audit(api,con,'Saved Categorized Extraction Drafts','PPMP',source_id)
    if rows:
        flash(f"{summary['inserted']} draft(s) saved; {summary['updated']} refreshed; {summary['preserved']} preserved. Official review and approval remain separate.",'success')
        if summary['unclassified']:flash(f"{summary['unclassified']} item(s) need classification. Other categorized drafts are already available.",'info')
        if result_status=='INCOMPLETE':flash('Extraction is incomplete. Review the retained uncertain source regions.','info')
    else:flash('Document saved. Extraction incomplete: no reliable procurement items were identified. Reprocess or review the original source.','info')
    return redirect(url_for('ppmp_intake_review',sid=str(source_id)))


def upload(api):
    from nlp_runtime_selection import require_available
    require_available(api['app'].config)
    g.document_stage='validation'
    name,ext,content=validate(request.files.get('ocr_file'))
    sid,run_id=processing.capture(api,name,ext,content)
    if run_id is None:
        flash('This document is already saved. Existing corrections were preserved. Use Reprocess Extraction when needed.','info')
        return redirect(url_for('ppmp_intake_review',sid=sid))
    return extract_saved(api,sid,run_id,name,ext,content)


def extract_saved(api,sid,run_id,name,ext,content):
    try:
        with extraction_path(content,ext) as path:
            g.document_stage='extraction';started=time.perf_counter()
            extracted=api['extract_aoq_document'](path);native_seconds=time.perf_counter()-started
            g.document_stage='mapping';started=time.perf_counter()
            structured=api['map_ppmp_ocr_document'](extracted);mapping_seconds=time.perf_counter()-started
        return save_extraction(api,name,ext,content,extracted,structured,source_id=sid,run_id=run_id,
                               timings=dict(extraction_seconds=native_seconds,mapping_seconds=mapping_seconds))
    except Exception:
        processing.failed(api,sid,run_id)
        raise


def reprocess(api,sid):
    from nlp_runtime_selection import require_available
    require_available(api['app'].config)
    source,run_id=processing.begin_existing(api,str(sid))
    if run_id is None:
        flash('This parser already completed extraction. Existing drafts and corrections were preserved.','info')
        return redirect(url_for('ppmp_intake_review',sid=str(sid)))
    return extract_saved(api,str(sid),run_id,source['filename'],source['extension'],bytes(source['content']))


def review(api,sid):
    sid=str(sid)
    source=api['q']("select id,filename,created_at,owner_id,extraction from psms_documents.sources where id=? and owner_id=? and kind='ppmp'",(sid,durable._uid()),one=True)
    if not source:abort(404)
    page=durable.revision(request.args.get('page','1'))
    if not 1<=page<=10000:abort(400)
    if request.method=='POST':
        if request.form.get('confirm_category')!='yes':abort(400,description='Confirm the item category before saving.')
        destination=category(request.form.get('category'))
        if not destination:abort(400,description='Choose DBM, NON-DBM or LIB for this item.')
        did=durable.revision(request.form.get('draft_id'))
        expected=durable.revision(request.form.get('revision'))
        labels=api['ppmp_review_labels']()
        if any('field_'+str(i) not in request.form for i in range(len(labels))):abort(400)
        fields={label:request.form['field_'+str(i)] for i,label in enumerate(labels)}
        if any(len(value)>5000 for value in fields.values()):abort(400)
        fields=api['normalize_reviewed_ocr_fields'](fields)
        with durable.transaction(api) as (con,uid):
            row=con.execute('select * from ppmp_ocr_drafts where id=? and source_id=? and owner_id=? for update',(did,sid,uid)).fetchone()
            if not row:abort(404)
            if row['revision']!=expected or row['converted'] or row['status'] in ('CANCELLED','CONVERTED_TO_PPMP'):
                abort(409,description='This item changed or became official. Reopen its saved review; your submission was not applied.')
            confidence=api['_json_dict'](row['confidence_json'])
            original=confidence.get('_original_fields') or api['_json_dict'](row['fields_json'])
            from procurement_item_contract import decimal_number
            if (decimal_number(fields.get('Quantity'))!=decimal_number(original.get('Quantity')) and
                    request.form.get('confirm_quantity_change')!='yes'):
                abort(400,description='Confirm the intentional quantity difference from the extracted source.')
            confidence['_category_confirmed']=True
            confidence['_category_review']=dict(previous=row['ppmp_category'],assigned=destination,by=uid,at=api['now']())
            confidence.setdefault('_review_history',[]).append(dict(
                revision=row['revision'],previous_category=row['ppmp_category'],category=destination,
                by=uid,at=api['now'](),before_fields=api['_json_dict'](row['fields_json']),after_fields=dict(fields)))
            # Category assignment is not official review or procurement approval.
            con.execute("update ppmp_ocr_drafts set ppmp_category=?,fields_json=?,confidence_json=?,status='NEEDS_REVIEW',reviewed=false,updated_at=?,revision=revision+1 where id=?",
                (destination,durable.payload(fields),durable.payload(confidence),api['now'](),did))
            durable.audit(api,con,'Assigned Reviewed Item Category','PPMP',did)
        flash('Item corrections and category saved. Complete item review in its category before official creation.','success')
        return redirect(url_for('ppmp_intake_review',sid=sid,page=page))
    extraction,run=processing.latest(api,sid,source['extraction'])
    source=dict(source,extraction=extraction)
    selected=request.args.get('category','ALL')
    selected_sheet=request.args.get('sheet','').strip()
    search=request.args.get('q','').strip()
    sort=request.args.get('sort','source')
    if (selected not in (*CATEGORIES,'ALL','UNASSIGNED') or len(search)>160 or len(selected_sheet)>160
        or any(len(request.args.getlist(key))>1 for key in ('sheet','category','q','sort','page'))):abort(400)
    sorts={'source':'id','name':"lower(fields_json::jsonb->>'Item Name'),id",'name_desc':"lower(fields_json::jsonb->>'Item Name') desc,id",'status':'status,id','newest':'id desc'}
    if sort not in sorts:abort(400)
    source_where='source_id=? and owner_id=?';source_params=[sid,durable._uid()]
    # A historical malformed/absent evidence payload has no known worksheet;
    # it must not prevent opening All worksheets or be rewritten here. PG17.
    sheet_expression="(case when confidence_json IS JSON OBJECT then confidence_json::jsonb #>> '{_source_evidence,worksheet}' end)"
    sheets=api['q']('select distinct '+sheet_expression+' as worksheet from ppmp_ocr_drafts where '+source_where+
        ' and '+sheet_expression+' is not null order by worksheet',tuple(source_params))
    sheets=[row['worksheet'] for row in sheets if row['worksheet']]
    if selected_sheet and selected_sheet not in sheets:abort(400,description='Choose a saved source worksheet.')
    if selected_sheet:
        source_where+=' and '+sheet_expression+'=?';source_params.append(selected_sheet)
    where=source_where;params=list(source_params)
    if selected=='UNASSIGNED':where+=" and status='CATEGORY_REVIEW'"
    elif selected in CATEGORIES:
        where+=" and ppmp_category=? and status<>'CATEGORY_REVIEW'";params.append(selected)
    if search:
        where+=" and strpos(lower(concat_ws(' ',temp_code,fields_json)),lower(?))>0";params.append(search)
    summary=api['q']('''select count(*) total,
        count(*) filter(where ppmp_category='DBM' and status<>'CATEGORY_REVIEW') dbm,
        count(*) filter(where ppmp_category='NON-DBM' and status<>'CATEGORY_REVIEW') non_dbm,
        count(*) filter(where ppmp_category='LIB' and status<>'CATEGORY_REVIEW') lib,
        count(*) filter(where status='CATEGORY_REVIEW') unclassified,
        count(*) filter(where converted) official
        from ppmp_ocr_drafts where '''+source_where,tuple(source_params),one=True)
    drafts=api['q']('select * from ppmp_ocr_drafts where '+where+' order by '+sorts[sort]+' limit 21 offset ?',(*params,(page-1)*20))
    has_next=len(drafts)>20;drafts=drafts[:20]
    items=[]
    for row in drafts:
        value=dict(row)
        value['fields']=api['_json_dict'](row['fields_json'])
        value['confidence']=api['_json_dict'](row['confidence_json'])
        value['suggestion']=value['confidence'].get('_category_suggestion') or dict(category=category(row['ppmp_category']),evidence='existing saved category')
        value['source_number']=(value['confidence'].get('_source_evidence') or {}).get('item_number','')
        items.append(value)
    def workspace_url(**changes):
        values={key:request.args[key] for key in request.args if key in ('q','sort','category','page','sheet') or key.startswith('h_')}
        values.update(changes)
        return url_for('ppmp_intake_review',sid=sid,**values)
    import document_history
    return render_template('ppmp_intake.html',active='ppmp',title='PPMP Document Review',source=source,
        items=items,labels=api['ppmp_review_labels'](),categories=CATEGORIES,page=page,has_next=has_next,
        summary=summary,run=run,selected=selected,selected_sheet=selected_sheet,sheets=sheets,search=search,sort=sort,workspace_url=workspace_url,
        history=document_history.listing(api,'ppmp'))


def source_file(api,sid):
    row=api['q']("select content,extension from psms_documents.sources where id=? and owner_id=? and kind='ppmp'",(str(sid),durable._uid()),one=True)
    if not row:abort(404)
    response=send_file(BytesIO(bytes(row['content'])),as_attachment=True,download_name='source-document'+row['extension'])
    response.headers.update({'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'"})
    return response
