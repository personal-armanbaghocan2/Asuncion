"""One private PPMP source, item-level category review, existing official workflow."""
import hashlib
import json
import re
import uuid
from io import BytesIO

from flask import abort, flash, g, redirect, render_template, request, send_file, session, url_for
import durable_ocr_v0_1_0 as durable
from private_document_uploads import validate, extraction_path

CATEGORIES=('DBM','NON-DBM','LIB')


def category(value):
    text=re.sub(r'\s+','-',str(value or '').strip().upper())
    if text=='NONDBM':text='NON-DBM'
    return text if text in CATEGORIES else None


def suggest_category(row):
    """Use explicit row, worksheet or scoped document evidence; never item-name guesses."""
    confidence=row.get('field_confidence') or {}
    evidence=confidence.get('_source_evidence') or {}
    candidates=[]
    cells=str(evidence.get('text') or '').split(' | ')
    for index,label in enumerate(evidence.get('headers') or []):
        if re.sub(r'[^a-z]+',' ',str(label).lower()).strip() in ('category','ppmp category','source category'):
            value=category(cells[index]) if index<len(cells) else None
            if value:candidates.append((value,'item category column'))
    # Explicit item columns take precedence over shared document context.
    if not candidates:
        value=category(evidence.get('worksheet'))
        if value:candidates.append((value,'worksheet category'))
    if not candidates:
        value=category((confidence.get('_document_metadata') or {}).get('category'))
        if value:candidates.append((value,'scoped source category'))
    values={value for value,_ in candidates}
    return (next(iter(values)),candidates[0][1]) if len(values)==1 else (None,'manual category assignment required')


def save_extraction(api,name,ext,content,extracted,structured):
    """Persist atomically after extraction; repeated source bytes reuse existing drafts."""
    rows=structured.get('rows') or []
    if len(rows)>200:abort(400,description='Review at most 200 items per upload.')
    if not (structured.get('model') or {}).get('active'):
        abort(503,description='The semantic mapper is unavailable. No extraction was saved.')
    digest=hashlib.sha256(content).hexdigest()
    state=dict(text=str(extracted.get('raw_text') or ''),method=extracted.get('extraction_method') or 'native extraction',
        warnings=structured.get('warnings') or [],filename=name,structured=structured,item_count=len(rows),
        extraction_timestamp=api['now'](),intake='item-category-review-v1')
    g.document_stage='draft_persistence'
    with durable.transaction(api,read_committed=True) as (con,uid):
        con.execute('select pg_advisory_xact_lock(hashtextextended(?,0))',(f'ppmp-intake:{uid}:{digest}',))
        existing=con.execute("select id from psms_documents.sources where owner_id=? and kind='ppmp' and sha256=? order by created_at limit 1",(uid,digest)).fetchone()
        if existing:
            flash('This document already has a saved extraction. Existing corrections were preserved.','info')
            return redirect(url_for('ppmp_intake_review',sid=str(existing['id'])))
        sid=durable.source(con,uid,'ppmp','MIXED',name,ext,content,state)
        for row in rows:
            fields=dict(row.get('fields') or {})
            confidence=dict(row.get('field_confidence') or {})
            confidence['_original_fields']=dict(fields)
            from offline_ocr_mapper import KEY_TO_LABEL
            project_label=KEY_TO_LABEL['project_type']
            if category(fields.get(project_label)):
                fields[project_label]=''
                confidence[project_label]=dict(score=0,level='Unresolved',source='PPMP category is not procurement project type')
            proposed,reason=suggest_category(row)
            confidence['_category_suggestion']=dict(category=proposed,evidence=reason)
            confidence['_category_confirmed']=False
            did=con.execute('''insert into ppmp_ocr_drafts(upload_token,temp_code,ppmp_category,fields_json,confidence_json,
                average_confidence,source_filename,raw_ocr_text,extraction_method,status,reviewed,created_by,created_at,updated_at,owner_id,source_id)
                values(?,?,?,?,?,?,?,?,?,'CATEGORY_REVIEW',false,?,?,?,?,?) returning id''',
                (sid,'OCR-'+str(uuid.uuid4()),proposed or 'UNASSIGNED',durable.payload(fields),durable.payload(confidence),
                 float(row.get('average_confidence') or 0),name,state['text'],state['method'],session['username'],api['now'](),api['now'](),uid,sid)).fetchone()['id']
            con.execute('update ppmp_ocr_drafts set temp_code=? where id=?',(f'OCR-TEMP-{did:06d}',did))
        durable.audit(api,con,'Extracted Item Category Review','PPMP',sid)
    flash('Extraction saved. Confirm a category for every item, correct its fields, then continue its normal review.','success')
    return redirect(url_for('ppmp_intake_review',sid=sid))


def upload(api):
    from nlp_runtime_selection import require_available
    require_available(api['app'].config)
    g.document_stage='validation'
    name,ext,content=validate(request.files.get('ocr_file'))
    digest=hashlib.sha256(content).hexdigest()
    existing=api['q']("select id from psms_documents.sources where owner_id=? and kind='ppmp' and sha256=? order by created_at limit 1",(durable._uid(),digest),one=True)
    if existing:return redirect(url_for('ppmp_intake_review',sid=str(existing['id'])))
    with extraction_path(content,ext) as path:
        g.document_stage='extraction'
        extracted=api['extract_aoq_document'](path)
        g.document_stage='mapping'
        structured=api['map_ppmp_ocr_document'](extracted)
    return save_extraction(api,name,ext,content,extracted,structured)


def review(api,sid):
    sid=str(sid)
    source=api['q']("select id,filename,created_at,owner_id,extraction from psms_documents.sources where id=? and owner_id=? and kind='ppmp'",(sid,durable._uid()),one=True)
    if not source:abort(404)
    page=durable.revision(request.args.get('page','1'))
    if page>10000:abort(400)
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
    drafts=api['q']('select * from ppmp_ocr_drafts where source_id=? and owner_id=? order by id limit 21 offset ?',(sid,durable._uid(),(page-1)*20))
    has_next=len(drafts)>20;drafts=drafts[:20]
    items=[]
    for row in drafts:
        value=dict(row)
        value['fields']=api['_json_dict'](row['fields_json'])
        value['confidence']=api['_json_dict'](row['confidence_json'])
        value['suggestion']=value['confidence'].get('_category_suggestion') or dict(category=category(row['ppmp_category']),evidence='existing saved category')
        items.append(value)
    return render_template('ppmp_intake.html',active='ppmp',title='PPMP Document Review',source=source,
        items=items,labels=api['ppmp_review_labels'](),categories=CATEGORIES,page=page,has_next=has_next)


def source_file(api,sid):
    row=api['q']("select content,extension from psms_documents.sources where id=? and owner_id=? and kind='ppmp'",(str(sid),durable._uid()),one=True)
    if not row:abort(404)
    response=send_file(BytesIO(bytes(row['content'])),as_attachment=True,download_name='source-document'+row['extension'])
    response.headers.update({'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'"})
    return response
