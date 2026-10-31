"""Owner-bound PostgreSQL OCR review; no user payload in globals/cookies/files."""
from contextlib import contextmanager
import hashlib,json,re,secrets,uuid
from flask import abort,flash,redirect,request,session,url_for,Response,jsonify
from werkzeug.exceptions import HTTPException
import server_sessions_v0_1_0 as sessions
from private_document_uploads import validate,extraction_path,ALLOWED

def register(app):
    @app.after_request
    def remove_obsolete_ocr_cookie_payloads(response):
        # Existing signed cookies may predate durable review. Remove only the
        # retired OCR payload/path keys; preserve authentication and CSRF state.
        for key in ('ocr_text','ocr_method','ppmp_ocr_token','aoq_preview_file','aoq_ocr_data'):
            if key in session:session.pop(key)
        return response

def _uid():
    uid=session.get('user_id')
    if type(uid) is not int or uid<=0:abort(403)
    return uid

def _active(con):
    uid=_uid(); digest=sessions.token_hash(session.get(sessions.COOKIE_KEY))
    if not digest:abort(403)
    sessions._ready(con)
    user=con.execute("select id from users where id=? and status='Active' and role in ('Admin','Staff','Inspector') for share",(uid,)).fetchone()
    token=con.execute('select user_id from auth_sessions where user_id=? and token_hash=? and revoked_at is null and expires_at>clock_timestamp() for share',(uid,digest)).fetchone()
    if not user or not token:abort(403)
    return uid

@contextmanager
def transaction(api):
    con=api['conn']()
    try:
        uid=_active(con);yield con,uid;con.commit()
    except Exception:con.rollback();raise
    finally:con.close()

def revision(value):
    if not re.fullmatch(r'[1-9][0-9]{0,18}',str(value or '')):abort(400,description='Reopen the review form before saving.')
    return int(value)

def payload(value):
    text=json.dumps(value,ensure_ascii=False,default=str)
    if len(text.encode('utf-8'))>4*1024*1024:abort(400,description='Extracted document exceeds the review limit.')
    return text

def audit(api,con,action,module,ref):
    api['log'](action,module,str(ref),'Private document review operation',connection=con)

def source(con,uid,kind,category,name,ext,content,state):
    sid=str(uuid.uuid4())
    con.execute('''insert into psms_documents.sources(id,owner_id,kind,category,filename,extension,content,sha256,extraction)
        values(?,?,?,?,?,?,?,?,?::jsonb)''',(sid,uid,kind,category,name,ext,content,hashlib.sha256(content).hexdigest(),payload(state)))
    return sid

def _existing_upload(con, uid, ptype, digest):
    # Parser upgrades, reviewed/converted/cancelled drafts and zero-row sources
    # do not turn the same bytes into an ordinary first-time upload.
    return con.execute('''select s.id from psms_documents.sources s where s.owner_id=? and s.kind='ppmp'
        and s.category=? and s.sha256=? order by s.created_at desc limit 1''', (uid,ptype,digest)).fetchone()


def _existing_response(ptype, existing):
    session['ppmp_ocr_source'] = str(existing['id'])
    flash('This file has already been uploaded and extracted. Please check the existing OCR records.')
    return redirect(url_for('ppmp_type',ptype=ptype,ocr_source=str(existing['id'])))


def ppmp_duplicate_check(api, ptype):
    """Advisory UI check; the upload independently hashes validated source bytes."""
    if ptype not in ('DBM','NON-DBM','LIB'):abort(404)
    digest = request.form.get('sha256', '')
    name = request.form.get('filename', '')
    if not re.fullmatch(r'[0-9a-f]{64}', digest) or not name or len(name)>120 or any(c in name for c in ('/', '\\', '\x00')) or any(ord(c)<32 for c in name):
        abort(400)
    from pathlib import Path
    if Path(name).suffix.lower() not in ALLOWED:
        result = dict(status='unsupported',message='Unsupported file format. Please select a supported procurement document.')
    else:
        with transaction(api) as (con,uid):
            existing = _existing_upload(con,uid,ptype,digest)
            from ppmp_creation import repeat_sources_enabled
            allow_repeat = bool(existing) and repeat_sources_enabled(api, con)
            same_name = None if existing else con.execute('''select id from psms_documents.sources
                where owner_id=? and kind='ppmp' and category=? and lower(filename)=lower(?) limit 1''', (uid,ptype,name)).fetchone()
        if existing:
            result = dict(status='repeat_allowed' if allow_repeat else 'duplicate',
                message='Testing Mode: This file was uploaded previously. Re-extraction is allowed.' if allow_repeat else
                        'This file has already been uploaded and extracted. Please check the existing OCR records.',
                existing_url=url_for('ppmp_type',ptype=ptype,ocr_source=str(existing['id'])))
        elif same_name:
            result = dict(status='same_name',message='An existing file has the same name, but its contents are different.')
        else:
            result = dict(status='new',message='File is ready for extraction.')
    response = jsonify(result)
    response.headers['Cache-Control'] = 'private, no-store'
    return response


def _reprocess_nonce(api, con, uid, ptype):
    from ppmp_creation import repeat_sources_enabled, _signer
    from itsdangerous import BadData
    if request.form.get('reprocess_confirmed') != 'yes' or not repeat_sources_enabled(api, con):abort(403)
    try:token = _signer().loads(request.form.get('creation_token',''),max_age=3600)
    except BadData:abort(400)
    if (not isinstance(token,dict) or token.get('uid')!=uid or token.get('category')!=ptype or
        token.get('session')!=sessions.token_hash(session.get(sessions.COOKIE_KEY)) or
        not re.fullmatch(r'[0-9a-f]{48}',str(token.get('nonce','')))):abort(403)
    return token['nonce']


def ppmp_upload(api,ptype,reprocess=False):
    if ptype not in ('DBM','NON-DBM','LIB'):abort(404)
    # The normal upload button uses the same verified, signed, idempotent test
    # path as /reprocess_ocr. A posted confirmation never grants permission.
    reprocess = reprocess or request.form.get('reprocess_confirmed') == 'yes'
    name,ext,content=validate(request.files.get('ocr_file'))
    digest=hashlib.sha256(content).hexdigest()
    with transaction(api) as (con,uid):
        nonce = _reprocess_nonce(api,con,uid,ptype) if reprocess else None
        existing = _existing_upload(con,uid,ptype,digest)
        if reprocess and not existing:abort(409,description='Reprocessing requires an existing extraction in your category.')
        if existing and not reprocess:return _existing_response(ptype,existing)
    with extraction_path(content,ext) as path:
        extracted=api['extract_aoq_document'](path)
        extracted['filename']=name
        structured=api['map_ppmp_ocr_document'](extracted)
    if not (structured.get('model') or {}).get('active'):
        return Response('The installed semantic model is unavailable. No official record was saved.',status=503,mimetype='text/plain')
    rows=structured.get('rows') or []
    detected_category=(structured.get('document_metadata') or {}).get('category')
    row_categories={row.get('field_confidence',{}).get('_document_metadata',{}).get('category') for row in rows}
    if (detected_category and detected_category!=ptype) or any(category and category!=ptype for category in row_categories):
        categories = {category for category in row_categories | {detected_category} if category}
        if len(categories) == 1 and next(iter(categories)) in ('DBM','NON-DBM','LIB'):
            correct = next(iter(categories))
            message = (f'This source is {correct}, but this upload page is {ptype}. '
                       f'Open PPMP > {correct} ({url_for("ppmp_type",ptype=correct)}) and upload the original document there. ')
        else:
            message = 'This source contains conflicting PPMP categories. Separate DBM and NON-DBM worksheets and upload each in its matching PPMP category. '
        return Response(message+'No drafts were created or moved.',status=422,mimetype='text/plain')
    if len(rows)>200:abort(400,description='Review at most 200 extracted rows per upload.')
    state=dict(text=str(extracted.get('raw_text') or ''),method=extracted.get('extraction_method') or 'document extraction',
        warnings=structured.get('warnings') or [],filename=name,structured=structured,item_count=len(rows),extraction_timestamp=api['now']())
    with transaction(api) as (con,uid):
        # Serialize identical uploads by owner/category even across processes.
        con.execute('select pg_advisory_xact_lock(hashtextextended(?,0))',(f'ppmp-upload:{uid}:{ptype}:{digest}',))
        existing=_existing_upload(con,uid,ptype,digest)
        if reprocess:
            _reprocess_nonce(api,con,uid,ptype)
            con.execute('select pg_advisory_xact_lock(hashtextextended(?,0))',('ppmp-reprocess:'+nonce,))
            previous=con.execute("select details from audit_trail where action='Reprocessed Private OCR Draft' and module='PPMP' and ref=?",(nonce,)).fetchone()
            if previous:
                prior_digest, prior_sid = previous['details'].split(':',1)
                if prior_digest != digest:abort(409)
                return _existing_response(ptype,dict(id=prior_sid))
            state['reprocess_of'] = str(existing['id'])
        elif existing:return _existing_response(ptype,existing)
        sid=source(con,uid,'ppmp',ptype,name,ext,content,state)
        for row in rows:
            row.setdefault('field_confidence', {})['_original_fields'] = dict(row.get('fields') or {})
            did=con.execute('''insert into ppmp_ocr_drafts(upload_token,temp_code,ppmp_category,fields_json,confidence_json,average_confidence,
                source_filename,raw_ocr_text,extraction_method,status,reviewed,created_by,created_at,updated_at,owner_id,source_id)
                values(?,?,?,?,?,?,?,?,?,'NEEDS_REVIEW',false,?,?,?,?,?) returning id''',
                (sid,'OCR-'+str(uuid.uuid4()),ptype,payload(row.get('fields') or {}),payload(row.get('field_confidence') or {}),
                 float(row.get('average_confidence') or 0),name,state['text'],state['method'],session['username'],api['now'](),api['now'](),uid,sid)).fetchone()['id']
            con.execute('update ppmp_ocr_drafts set temp_code=? where id=?',(f'OCR-TEMP-{did:06d}',did))
        audit(api,con,'Extracted Private OCR Draft','PPMP',sid)
        if reprocess:
            api['log']('Reprocessed Private OCR Draft','PPMP',nonce,digest+':'+sid,connection=con)
    session['ppmp_ocr_source']=sid
    flash('Extraction saved as private temporary rows. Review/edit, then explicitly confirm to create official PPMP records.' if rows else
          'No reliable item rows were identified. Source text and uncertain regions were retained for review; no item drafts were created.')
    return redirect(url_for('ppmp_type',ptype=ptype,ocr_source=sid))

def ppmp_state(api,ptype=None):
    params=[_uid()];where='owner_id=? and kind=\'ppmp\''
    if ptype:where+=' and category=?';params.append(ptype)
    requested=request.args.get('ocr_source','latest')
    if requested not in ('latest','all'):
        try:requested=str(uuid.UUID(requested))
        except ValueError:abort(400)
        where+=' and id=?';params.append(requested)
    row=api['q']('select extraction,id from psms_documents.sources where '+where+' order by created_at desc limit 1',params,one=True)
    if not row and requested not in ('latest','all'):abort(404)
    return dict(row['extraction'],source_id=str(row['id'])) if row else None

_STATE_UNSET = object()


def ppmp_rows(api,ptype,state=_STATE_UNSET):
    page=revision(request.args.get('ocr_page','1'))
    if page>100000:abort(400)
    if state is _STATE_UNSET:state=ppmp_state(api,ptype)
    where="owner_id=? and ppmp_category=? and status not in ('CONVERTED_TO_PPMP','CANCELLED')"
    params=[_uid(),ptype]
    selected=request.args.get('ocr_source','latest')!='all' and state
    if selected:where+=' and source_id=?';params.append(state['source_id'])
    params.append((page-1)*200)
    rows=api['q']('select * from ppmp_ocr_drafts where '+where+' order by id '+('asc' if selected else 'desc')+' limit 200 offset ?',params)
    return [api['_ocr_draft_to_ui'](row) for row in rows]

def _ppmp_locked(con,uid,ptype,temp_id):
    row=con.execute('select * from ppmp_ocr_drafts where temp_code=? and ppmp_category=? and owner_id=? for update',(temp_id,ptype,uid)).fetchone()
    if not row:abort(404)
    return row


def ppmp_batch_review(api,ptype):
    """Validate every selected owner-bound draft, then save all in one transaction."""
    text=request.form.get('ocr_batch','')
    if len(text.encode('utf-8'))>2*1024*1024:abort(400,description='Batch exceeds the review limit.')
    try:entries=json.loads(text)
    except (ValueError,TypeError):abort(400,description='Invalid review batch.')
    if not isinstance(entries,list) or not 1<=len(entries)<=200:abort(400,description='Select 1 to 200 drafts on this page.')
    labels=api['ppmp_review_labels']()
    unique={}
    for entry in entries:
        if not isinstance(entry,dict):abort(400)
        code=entry.get('temp_id');fields=entry.get('fields')
        if not isinstance(code,str) or not re.fullmatch(r'OCR-TEMP-[0-9]{1,20}',code):abort(400)
        if not isinstance(fields,dict) or set(fields)!=set(labels):abort(400,description='Submit every review field for each item.')
        if any(not isinstance(value,str) or len(value)>5000 for value in fields.values()):abort(400,description='Each field must contain at most 5000 characters.')
        revision(entry.get('revision'))
        if code in unique and unique[code]!=entry:abort(400,description='Conflicting duplicate draft entries.')
        unique[code]=entry
    errors={};saved=[]
    with transaction(api) as (con,uid):
        pending=[]
        for code,entry in sorted(unique.items()):
            row=_ppmp_locked(con,uid,ptype,code)
            if row['converted'] or row['status'] in ('CONVERTED_TO_PPMP','CANCELLED') or row['revision']!=revision(entry['revision']):
                return jsonify(saved=False,errors={code:['This draft changed, was cancelled, or became official. Reopen its latest saved review.']},
                    message='No items saved. Your corrections remain in this review; compare them with the latest saved revision.'),409
            fields=api['normalize_reviewed_ocr_fields'](entry['fields'])
            try:api['validated_ppmp_item_from_review'](fields,ptype)
            except ValueError as error:errors[code]=[str(error)]
            before=api['_json_dict'](row['fields_json'])
            confidence=api['_json_dict'](row['confidence_json'])
            original=confidence.get('_original_fields') or before
            from procurement_item_contract import decimal_number
            quantity_previously_confirmed = (row['reviewed'] and (confidence.get('Quantity') or {}).get('human_confirmed') is True
                and decimal_number(fields.get('Quantity')) == decimal_number(before.get('Quantity')))
            if decimal_number(fields.get('Quantity')) != decimal_number(original.get('Quantity')) and not quantity_previously_confirmed and entry.get('source_changes_confirmed') is not True:
                errors.setdefault(code,[]).append('Quantity differs from the extracted source. Confirm the intentional source difference for this item.')
            pending.append((code,row,fields))
        if errors:
            return jsonify(saved=False,errors=errors,message='No items saved. Correct the marked items.'),422
        for code,row,fields in pending:
            confidence=api['_json_dict'](row['confidence_json']);before=api['_json_dict'](row['fields_json'])
            confidence.setdefault('_original_fields',dict(before))
            for label in labels:
                if fields[label]!=before.get(label):
                    previous=dict(confidence.get(label) or {})
                    previous.update(human_corrected=True,human_confirmed=True,level='Human corrected',source_match=False)
                    confidence[label]=previous
            con.execute('''update ppmp_ocr_drafts set fields_json=?,confidence_json=?,status='REVIEWED',reviewed=true,
                reviewed_by=?,reviewed_at=?,updated_at=?,revision=revision+1 where id=?''',
                (payload(fields),payload(confidence),session['username'],api['now'](),api['now'](),row['id']))
            audit(api,con,'Reviewed Private OCR Draft','PPMP',code)
            saved.append(dict(temp_id=code,revision=row['revision']+1,fields=fields,field_confidence=confidence,status='Reviewed'))
    response=jsonify(saved=True,items=saved,message='All selected reviews saved. Official creation and procurement approval remain separate.')
    response.headers['Cache-Control']='private, no-store'
    return response


def ppmp_source(api,ptype,sid,diagnostics=False):
    """Original private bytes or administrator diagnostics, checked per request."""
    if ptype not in ('DBM','NON-DBM','LIB'):abort(404)
    if diagnostics and not api['is_admin']():abort(403)
    row=api['q']("select filename,extension,content,extraction from psms_documents.sources where id=? and owner_id=? and kind='ppmp' and category=?",
                 (str(sid),_uid(),ptype),one=True)
    if not row:abort(404)
    if diagnostics:
        from flask import make_response
        response=make_response(api['render_template']('ppmp_source_diagnostics.html',source=row,ptype=ptype,active='ppmp'))
    else:
        from io import BytesIO
        from flask import send_file
        ext=row['extension']
        mime={'.pdf':'application/pdf','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.bmp':'image/bmp'}
        response=send_file(BytesIO(bytes(row['content'])),mimetype=mime.get(ext,'application/octet-stream'),
                           download_name='source-document'+ext,as_attachment=ext not in mime,max_age=0)
        response.headers['Content-Security-Policy']="sandbox; default-src 'none'"
    response.headers.update({'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'})
    return response

def ppmp_post(api,ptype):
    if ptype not in ('DBM','NON-DBM','LIB'):abort(404)
    action=request.form.get('action')
    if action=='update_ocr_rows':return ppmp_batch_review(api,ptype)
    if action=='clear_ocr':
        state=ppmp_state(api,ptype)
        if not state:abort(404)
        with transaction(api) as (con,uid):
            con.execute("update ppmp_ocr_drafts set status='CANCELLED',revision=revision+1 where owner_id=? and ppmp_category=? and source_id=? and status not in ('CONVERTED_TO_PPMP','CANCELLED')",(uid,ptype,state['source_id']))
            audit(api,con,'Cancelled Own OCR Drafts','PPMP',ptype)
        session.pop('ppmp_ocr_source',None)
        flash('Unconverted drafts for this source were cancelled. Other uploads and official records were preserved.')
    elif action=='update_ocr_row':
        temp_id=request.form.get('ocr_temp_id','')
        labels=api['ppmp_review_labels']()
        if any(f'ocr_field_{i}' not in request.form for i in range(len(api['OCR_PPMP_FIELDS']))):abort(400,description='Every official review field must be submitted.')
        if any(len(request.form.get(f'ocr_field_{i}',''))>5000 for i in range(len(labels))):abort(400,description='Each reviewed field is limited to 5000 characters; no value was truncated or saved.')
        fields=api['normalize_reviewed_ocr_fields']({label:api['_text_value'](request.form[f'ocr_field_{i}']) for i,label in enumerate(labels) if f'ocr_field_{i}' in request.form})
        errors=api['validate_ocr_ppmp_record'](fields)
        if api['parse_currency_decimal'](fields.get(api['OCR_PPMP_FIELDS']['budget']['label'])) is None:errors.append('Estimated Budget must be a valid monetary value.')
        try:api['validated_ppmp_item_from_review'](fields,ptype)
        except ValueError as error:errors.append(str(error))
        with transaction(api) as (con,uid):
            row=_ppmp_locked(con,uid,ptype,temp_id)
            if row['converted'] or row['status']=='CANCELLED' or row['revision']!=revision(request.form.get('ocr_revision')):abort(409,description='Draft changed; reopen Review.')
            confidence=api['_json_dict'](row['confidence_json']);before=api['_json_dict'](row['fields_json'])
            confidence.setdefault('_original_fields',dict(before))
            for label in labels:
                if fields.get(label)!=before.get(label):
                    previous=dict(confidence.get(label) or {})
                    previous.update(human_corrected=True,human_confirmed=True,level='Human corrected',source_match=False)
                    confidence[label]=previous
            con.execute('''update ppmp_ocr_drafts set fields_json=?,confidence_json=?,status=?,reviewed=?,reviewed_by=?,reviewed_at=?,updated_at=?,revision=revision+1 where id=?''',
                (payload(fields),payload(confidence),'NEEDS_REVIEW' if errors else 'REVIEWED',not errors,session['username'],api['now'](),api['now'](),row['id']))
            audit(api,con,'Reviewed Private OCR Draft','PPMP',temp_id)
        flash('Review saved. '+(' '.join(dict.fromkeys(errors)) if errors else 'Explicit confirmation is still required to create an official PPMP.'), 'warning' if errors else 'success')
    elif action=='save_ocr_rows':
        if request.form.get('confirm_reviewed')!='yes':abort(400,description='Explicitly confirm the reviewed fields before official saving.')
        ids=request.form.getlist('ocr_rows')
        if not ids or len(ids)>200 or len(ids)!=len(set(ids)):abort(400,description='Select unique reviewed rows.')
        created=[]
        with transaction(api) as (con,uid):
            created = create_reviewed_ocr(api,ptype,con,uid,ids)
        flash('Confirmed PPMP records are saved in PostgreSQL: '+', '.join(created))
        return redirect(url_for('ppmp_home'))
    else:abort(400)
    return redirect(url_for('ppmp_type',ptype=ptype))

def create_reviewed_ocr(api,ptype,con,uid,ids):
    created=[]
    for temp_id in sorted(ids):
        row=_ppmp_locked(con,uid,ptype,temp_id)
        if row['converted'] and row['official_ppmp_id']:
            existing=con.execute('select no from ppmp where id=?',(row['official_ppmp_id'],)).fetchone()
            created.append(existing['no']);continue
        if row['revision']!=revision(request.form.get('ocr_revision_'+temp_id)):abort(409,description='Reviewed data changed. Refresh and confirm again.')
        if row['status']!='REVIEWED' or not row['reviewed']:abort(400,description='Review and correct each selected row first.')
        fields=api['normalize_reviewed_ocr_fields'](api['_json_dict'](row['fields_json']))
        try:item=api['validated_ppmp_item_from_review'](fields,ptype)
        except ValueError:abort(400,description='Stored review fields are invalid; reopen Review.')
        budget=api['parse_currency_decimal'](item.get(api['OCR_PPMP_FIELDS']['budget']['label']))
        if budget is None:abort(400,description='Estimated Budget is invalid.')
        document_metadata=api['_json_dict'](row['confidence_json']).get('_document_metadata') or {}
        source_year=str(document_metadata.get('fiscal_year') or '')
        year=int(source_year) if re.fullmatch(r'20\d{2}',source_year) else api['current_year']()
        office=str(document_metadata.get('office') or '').strip() or 'General Services Office'
        no=api['next_document_no'](f'PPMP-{ptype}',f'PPMP-{ptype.replace("-", "")}', 'ppmp',year=year,connection=con)
        item.update({'_OCR Source':row['extraction_method'],'_OCR Filename':row['source_filename'],'_OCR Temp ID':temp_id,'_OCR Confidence':row['average_confidence']})
        item['_OCR Field Evidence']=api['_json_dict'](row['confidence_json'])
        item['_OCR Source ID']=str(row.get('source_id') or '')
        items=api['assign_item_identifiers']([item],'_PPMP Item ID',no)
        did=con.execute('''insert into ppmp(no,type,office,year,status,created_at,items,total,source_type,ocr_source_filename,ocr_raw_text,
            ocr_average_confidence,ocr_verified,ocr_verified_by,ocr_verified_at,ocr_extraction_timestamp)
            values(?,?,?,?,'Draft',?,?,?,?,?,?,?,?,?,?,?) returning id''',
            (no,ptype,office,year,api['now'](),payload(items),float(budget),'OCR',row['source_filename'],row['raw_ocr_text'],
             row['average_confidence'],True,row['reviewed_by'],row['reviewed_at'],row['created_at'])).fetchone()['id']
        con.execute("update ppmp_ocr_drafts set status='CONVERTED_TO_PPMP',converted=true,official_ppmp_id=?,converted_at=?,updated_at=?,revision=revision+1 where id=?",(did,api['now'](),api['now'](),row['id']))
        audit(api,con,'Created PPMP from Confirmed OCR','PPMP',no);created.append(no)
    return created


def aoq_load(api):
    did=request.form.get('draft_id') or request.args.get('draft_id') or session.get('aoq_draft_id')
    if did:
        try:did=str(uuid.UUID(str(did)))
        except ValueError:abort(400)
        row=api['q']('select * from psms_documents.aoq_drafts where id=? and owner_id=?',(did,_uid()),one=True)
        if not row:abort(404)
        if row['status']!='REVIEW' and request.method=='GET':row=None
    else:
        row=api['q']("select * from psms_documents.aoq_drafts where owner_id=? and status='REVIEW' order by updated_at desc limit 1",(_uid(),),one=True)
    if not row:return {}
    result=dict(row['payload'],draft_id=str(row['id']),draft_revision=row['revision'],source_id=str(row['source_id']),_status=row['status'],_official_id=row['official_aoq_id'])
    return result

def aoq_upload(api):
    try:pr_id=int(request.form.get('pr_id',''))
    except ValueError:abort(400)
    pr=api['q']("select * from pr where id=? and status='Approved'",(pr_id,),one=True)
    if not pr:abort(400,description='Choose an approved PR.')
    name,ext,content=validate(request.files.get('ocr_file'))
    with extraction_path(content,ext) as path:
        document=api['extract_aoq_document'](path)
        preview=api['build_flexible_aoq_preview'](document,api['parse_items'](pr['items']))
    if len(preview.get('offer_items') or [])>200 or len(preview.get('suppliers') or [])>100:abort(400)
    idx=preview.get('winner_index',-1)
    preview.update(pr_id=pr_id,pr_no=pr['no'],ocr_filename=name,stored_filename='',offer_review_token=secrets.token_urlsafe(24),
        offer_supplier_index=preview.get('offer_supplier_index',str(idx) if isinstance(idx,int) and idx>=0 else 'custom'))
    did=str(uuid.uuid4())
    with transaction(api) as (con,uid):
        con.execute('select id from pr where id=? for share',(pr_id,))
        if con.execute('select 1 from aoq where pr_id=?',(pr_id,)).fetchone():abort(409,description='This PR already has an AOQ.')
        sid=source(con,uid,'aoq',None,name,ext,content,document)
        con.execute('insert into psms_documents.aoq_drafts(id,owner_id,source_id,pr_id,payload) values(?,?,?,?,?::jsonb)',(did,uid,sid,pr_id,payload(preview)))
        audit(api,con,'Extracted Private OCR Draft','AOQ',did)
    session['aoq_draft_id']=did
    flash('Extraction saved privately. Edit and explicitly confirm the reviewed quotation before official saving.')
    return redirect(url_for('aoq',draft_id=did))

def aoq_post(api):
    action=request.form.get('action') or 'extract_ocr'
    if action=='extract_ocr':return aoq_upload(api)
    if not request.form.get('draft_id'):abort(400,description='Reopen the saved review form.')
    preview=aoq_load(api)
    if not preview:abort(404)
    did=preview['draft_id']
    if action=='save_ocr' and preview['_status']=='CONFIRMED':
        flash('This confirmed AOQ is already saved. No duplicate was created.')
        return redirect(url_for('aoq_review',id=preview['_official_id']))
    if preview['_status']!='REVIEW':abort(409)
    if preview['draft_revision']!=revision(request.form.get('draft_revision')):
        abort(409,description='Draft changed. Reopen Review before saving.')
    if action=='save_ocr' and request.form.get('confirm_reviewed')!='yes':abort(400,description='Explicitly confirm the reviewed quotation before official saving.')
    from aoq_flexible import winning_offer_error,offer_supplier_error,reviewed_total
    suppliers=api['posted_aoq_suppliers'](preview);items=api['posted_aoq_items']()
    if len(items)>200 or len(suppliers)>100:abort(400)
    try:index=int(request.form.get('winner_index') or -1)
    except ValueError:index=-1
    custom=(request.form.get('winner_custom') or '').strip()[:500]
    remarks=(request.form.get('remarks') or '').strip()[:5000]
    if action=='review_supplier':
        original=preview.get('suppliers') or []
        if 0<=index<len(original) and len(suppliers)==len(original):
            preview.update(offer_items=[dict(i) for i in original[index].get('items') or []],offer_supplier_index=str(index),offer_custom_winner='')
        elif custom and index==-1:preview.update(offer_items=[],offer_supplier_index='custom',offer_custom_winner=custom)
        else:abort(400,description='Select a quotation supplier first.')
        preview.update(offer_review_token=secrets.token_urlsafe(24),supplier_form=suppliers,remarks=remarks,selected_winner_index=index)
    elif action=='save_ocr':
        if 0<=index<len(suppliers):winner=suppliers[index]['supplier']
        elif custom:winner=custom
        else:winner=''
        if not winner or sum(api['_norm_text'](s.get('supplier'))==api['_norm_text'](winner) for s in suppliers)>1:abort(400,description='Choose one unambiguous winning supplier.')
        error=winning_offer_error(items) or offer_supplier_error(preview,index,items,request.form)
        if any(s.get('items') for s in preview.get('suppliers') or []) and len(suppliers)!=len(preview['suppliers']):error='Supplier rows changed; reopen Review.'
        if error:abort(400,description=error)
        amount=round(reviewed_total(items),2)
        if amount<=0:abort(400,description='Reviewed total must be positive.')
    elif action not in ('clear_ocr','prepare_preview'):abort(400)
    with transaction(api) as (con,uid):
        # Parent lock before draft lock: independent drafts for one PR cannot
        # race the one-official-AOQ check. No OCR/file processing occurs here.
        pr=con.execute('select * from pr where id=? for update',(preview['pr_id'],)).fetchone()
        row=con.execute('select * from psms_documents.aoq_drafts where id=? and owner_id=? for update',(did,uid)).fetchone()
        if row['status']=='CONFIRMED' and action=='save_ocr':
            official=row['official_aoq_id']
        else:
            if row['status']!='REVIEW' or row['revision']!=revision(request.form.get('draft_revision')):abort(409,description='Draft changed. Reopen Review before saving.')
            if action=='save_ocr':
                if not pr or pr['status']!='Approved' or con.execute('select 1 from aoq where pr_id=?',(pr['id'],)).fetchone():abort(409,description='The PR is no longer available for this AOQ.')
                no=api['next_document_no']('AOQ','AOQ','aoq',connection=con)
                items=api['assign_item_identifiers'](items,'_AOQ Item ID',no)
                matched=False
                for i,supplier in enumerate(suppliers):
                    supplier['is_winner']=i==index or api['_norm_text'](supplier.get('supplier'))==api['_norm_text'](winner)
                    if supplier['is_winner']:supplier.update(items=items,amount=amount);matched=True
                if not matched:suppliers.append(dict(supplier=winner,amount=amount,remarks=remarks,is_winner=True,items=items))
                profile={key:preview.get(key) for key in ('document_type','extraction_method','confidence','table_count','parsed_table_sources','detected_tables')}
                official=con.execute('''insert into aoq(no,pr_id,status,created_at,suppliers,winner,winning_amount,winning_offer,ocr_filename,ocr_text,
                    stored_filename,source_type,extraction_method,extraction_profile,extraction_warnings)
                    values(?,?,'For Review',?,?,?,?,?,?,?,?,?,?,?,?) returning id''',
                    (no,pr['id'],api['now'](),payload(suppliers),winner,amount,payload(items),preview.get('ocr_filename',''),preview.get('raw_text',''),'',
                     preview.get('document_type',''),preview.get('extraction_method',''),payload(profile),payload(preview.get('warnings') or []))).fetchone()['id']
                con.execute("update psms_documents.aoq_drafts set status='CONFIRMED',official_aoq_id=?,revision=revision+1,updated_at=clock_timestamp() where id=?",(official,did))
                audit(api,con,'Created AOQ from Confirmed OCR','AOQ',no)
            elif action=='clear_ocr':
                con.execute("update psms_documents.aoq_drafts set status='CANCELLED',revision=revision+1,updated_at=clock_timestamp() where id=?",(did,))
                audit(api,con,'Cancelled Own OCR Draft','AOQ',did)
            else:
                con.execute('update psms_documents.aoq_drafts set payload=?::jsonb,revision=revision+1,updated_at=clock_timestamp() where id=?',(payload(preview),did))
                audit(api,con,'Reviewed Private OCR Draft','AOQ',did)
    if action=='save_ocr':
        session.pop('aoq_draft_id',None);flash('Confirmed AOQ saved in PostgreSQL; procurement approval remains separate.')
        return redirect(url_for('aoq_review',id=official))
    if action=='clear_ocr':session.pop('aoq_draft_id',None)
    flash('Private review updated. No inventory quantity was changed.')
    return redirect(url_for('aoq',**({} if action=='clear_ocr' else dict(draft_id=did))))

def aoq_source(api,did):
    row=api['q']("select s.filename,s.extension,s.content from psms_documents.sources s join psms_documents.aoq_drafts d on d.source_id=s.id where d.official_aoq_id=? and d.status='CONFIRMED'",(did,),one=True)
    if not row:return None
    response=Response(bytes(row['content']),mimetype='application/octet-stream')
    response.headers['Content-Disposition']="attachment; filename=\"source-document"+row['extension']+'"'
    response.headers['X-Content-Type-Options']='nosniff';response.headers['Cache-Control']='private, no-store'
    return response

def protected(operation,*args):
    try:return operation(*args)
    except HTTPException:raise
    except Exception:return Response('Document processing could not be confirmed. Reopen the saved review; no success is assumed.',status=503,mimetype='text/plain')
