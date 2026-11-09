"""Revised source-independent procurement path, using the existing Flask APIs.

No startup migrations, environment changes, model substitution or cloud access.
The global policy or an approved deployment capability activates the new path.
Deployment capabilities do not modify the global policy. Historical records and
all existing endpoint URLs remain.
"""
import json
import os
import math
import re
from functools import wraps
import hashlib
import secrets
import uuid
from pathlib import Path
from flask import abort,flash,redirect,render_template,request,url_for,has_request_context,g
from werkzeug.exceptions import HTTPException, ServiceUnavailable
import procurement_comparison_v1 as evidence

RPC={
 'create_pr_from_approved_app_v1':('bigint','text','text','double precision'),
 'approve_pr_v1':('bigint','boolean'),
 'approve_reviewed_aoq_v1':('bigint','boolean','boolean'),
 'create_po_for_review_v2':('bigint',),
 'save_pr_po_verification_v1':('bigint','bigint','text','text','text','double precision','jsonb','text','text'),
 'review_pr_po_verification_v1':('bigint','jsonb','jsonb','text','text','boolean'),
 'approve_po_after_verification_v1':('bigint','boolean','boolean')}

REQUIRED_COLUMNS = [('po','pr_id')] + [('nlp',name) for name in (
    'pr_id','workflow_version','pr_items_hash','po_items_hash','model_version',
    'technical_status','reviewed_by','reviewed_at','review_mapping','relationship_reason','evidence_reference')]
REQUIRED_ROUTINES = dict(RPC, lock_po_for_workflow_v2=('bigint','text'),
    save_reviewed_aoq_independent_v1=('uuid','bigint','text','integer','jsonb','jsonb','text','text','boolean'))

# A true policy cannot select older review/approval routines without the
# saved-evidence guard. The private helper needs no backend EXECUTE grant.
GUARDED_ROUTINES = (
 ('psms_aoq_api.require_saved_technical_match_v2(text,jsonb,jsonb,jsonb)','de6422cdbd430db0b6999fb9579cb26a'),
 ('psms_aoq_api.review_pr_po_verification_v1(bigint,jsonb,jsonb,text,text,boolean)','b8312aa77b0f95629ebb1c92cf760fdd'),
 ('psms_aoq_api.approve_po_after_verification_v1(bigint,boolean,boolean)','293c2341e8054d1bff15ef0a45c30d60'))

RELEASE_ID = 'revised-procurement-v1'
RELEASE_SCHEMA = 'psms_revised_release'
RELEASE_PROJECT = 'prj_WuHbP7ztALH56NUyLCy1e6exvgs4'
RELEASE_GUARDED_ROUTINES = (
    ('psms_revised_release.policy_v1()', '708461349509a23a532125c1cadf6b85'),
    ('psms_revised_release.require_release_v1()', '6ca34abf0edf396ecbcff9dff7da762f'),
    ('psms_revised_release.save_reviewed_aoq_v1(uuid,bigint,text,integer,jsonb,jsonb,text,text,boolean)', 'eeae5fc9952cf8451caa4b490c7b87d7'),
    ('psms_revised_release.save_reviewed_aoq_independent_v1(uuid,bigint,text,integer,jsonb,jsonb,text,text,boolean)', '4c777bd267f8cffcc1fe561c488b786a'),
    ('psms_revised_release.approve_reviewed_aoq_v1(bigint,boolean,boolean)', '8fc82bd7edf6c42b977afb335ccd84a6'),
    ('psms_revised_release.create_pr_from_approved_app_v1(bigint,text,text,double precision)', '364e99e5ba8b7ff1ffe0fc6b5d9564e4'),
    ('psms_revised_release.approve_pr_v1(bigint,boolean)', '97f3f8d630dbb518f77567edddf5e9ae'),
    ('psms_revised_release.create_po_for_review_v2(bigint)', 'ec92dd74b7f1f912c1cdb0a5018143d7'),
    ('psms_revised_release.save_pr_po_verification_v1(bigint,bigint,text,text,text,double precision,jsonb,text,text)', '80aa55fa3f138fcb7da88b97e0ba596b'),
    ('psms_revised_release.lock_po_for_workflow_v2(bigint,text)', 'a5485a580e4bf536e102524d520a77db'),
    ('psms_revised_release.require_saved_technical_match_v2(text,jsonb,jsonb,jsonb)', 'de6422cdbd430db0b6999fb9579cb26a'),
    ('psms_revised_release.review_pr_po_verification_v1(bigint,jsonb,jsonb,text,text,boolean)', '44d516b9109b982bb0c5a682bedab17a'),
    ('psms_revised_release.approve_po_after_verification_v1(bigint,boolean,boolean)', 'a8c1f7f13891cacf08e6d4fca588197c'),
)


def unavailable(diagnostic, message):
    """Fixed safe diagnostics, never driver text, credentials or source values."""
    error = ServiceUnavailable(description=message)
    error.psms_diagnostic = diagnostic
    raise error


def configured_release_context():
    """Validate the existing server-side Option B contract, without activation."""
    release = os.environ.get('PSMS_PROCUREMENT_RELEASE', '')
    token = os.environ.get('PSMS_PROCUREMENT_RELEASE_TOKEN', '')
    if not release and not token:
        return None
    host = os.environ.get('VERCEL_URL', '')
    if (release != RELEASE_ID or not re.fullmatch(r'[0-9a-f]{64}', token)
            or os.environ.get('VERCEL') != '1'
            or os.environ.get('VERCEL_ENV') != 'preview'
            or os.environ.get('VERCEL_PROJECT_ID') != RELEASE_PROJECT
            or not re.fullmatch(r'asuncion-[a-z0-9]{9}-arman-a87f\.vercel\.app', host)):
        unavailable('PROCUREMENT_RELEASE_CONFIG_INVALID', 'The procurement release deployment is not authorized.')
    return release, host, token


def release_context():
    """Deployment capability, never a browser-supplied workflow override."""
    context = configured_release_context()
    if context and (not has_request_context() or request.host != context[1]):
        unavailable('PROCUREMENT_RELEASE_HOST_MISMATCH', 'The procurement release requires its exact authorized Preview hostname.')
    return context


def apply_release_context(con):
    context = release_context()
    if context:
        con.execute("select set_config('psms.procurement_release_id',?,true),"
                    "set_config('psms.procurement_release_host',?,true),"
                    "set_config('psms.procurement_release_token',?,true)", context)
    return bool(context)


def api_schema():
    return RELEASE_SCHEMA if release_context() else 'psms_aoq_api'


def contracts_ready(con):
    """Read metadata only. A true feature policy cannot bypass missing contracts."""
    columns=','.join("('"+table+"','"+column+"')" for table,column in REQUIRED_COLUMNS)
    schema=api_schema()
    required=dict(REQUIRED_ROUTINES)
    if schema==RELEASE_SCHEMA:
        required['save_reviewed_aoq_v1']=required['save_reviewed_aoq_independent_v1']
    routines=','.join("('"+schema+'.'+name+'('+','.join(kinds)+")')" for name,kinds in required.items())
    guarded=RELEASE_GUARDED_ROUTINES if schema==RELEASE_SCHEMA else GUARDED_ROUTINES
    guards=','.join("('"+signature+"','"+digest+"')" for signature,digest in guarded)
    release_metadata=""" and routine.prosecdef=(routine.proname<>'require_saved_technical_match_v2')
              and exists(select 1 from unnest(routine.proconfig) setting
                where replace(setting,' ','')='search_path=pg_catalog,pg_temp')
              and (routine.proname='require_saved_technical_match_v2'
                or 'row_security=on'=any(routine.proconfig))""" if schema==RELEASE_SCHEMA else ''
    release_custody=""" and exists(select 1 from pg_catalog.pg_class
          where oid=to_regclass('psms_revised_release.deployments') and relrowsecurity)
        and not has_table_privilege('psms_revised_release.deployments','SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
        and not has_schema_privilege('psms_revised_release','CREATE')
        and not has_function_privilege('psms_revised_release.require_release_v1()','EXECUTE')
        and not has_function_privilege('psms_revised_release.require_saved_technical_match_v2(text,jsonb,jsonb,jsonb)','EXECUTE')""" if schema==RELEASE_SCHEMA else ''
    row=con.execute("""select
        not exists(select 1 from (values """+columns+""") required(table_name,column_name)
          where not exists(select 1 from pg_catalog.pg_attribute a
            join pg_catalog.pg_class c on c.oid=a.attrelid
            join pg_catalog.pg_namespace n on n.oid=c.relnamespace
            where n.nspname='public' and c.relname=required.table_name
              and a.attname=required.column_name and a.attnum>0 and not a.attisdropped))
        and not exists(select 1 from (values """+routines+""") required(signature)
          where not coalesce(has_function_privilege(to_regprocedure(required.signature),'EXECUTE'),false))
        and not exists(select 1 from (values """+guards+""") guarded(signature,digest)
          where not exists(select 1 from pg_catalog.pg_proc routine
            join pg_catalog.pg_roles owner_role on owner_role.oid=routine.proowner
            where routine.oid=to_regprocedure(guarded.signature)
              and md5(replace(routine.prosrc,chr(13)||chr(10),chr(10)))=guarded.digest
              and owner_role.rolname='psms_aoq_approval_owner'
              and not owner_role.rolsuper and not owner_role.rolbypassrls and not owner_role.rolcanlogin"""+release_metadata+"""))
        and exists(select 1 from pg_catalog.pg_attribute
          where attrelid=to_regclass('psms_documents.aoq_drafts') and attname='pr_id'
            and not attnotnull and not attisdropped)
        and case when to_regclass('psms_documents.pr_po_reviews') is null then false
          else has_table_privilege('psms_documents.pr_po_reviews','SELECT') end"""+release_custody+""" as revised_contract_ready""").fetchone()
    return bool(row and row.get('revised_contract_ready') is True)

def enabled(api):
    if has_request_context() and hasattr(g,'revised_procurement_enabled'):
        return g.revised_procurement_enabled
    con=None;active=False
    try:
        # A missing pre-expansion relation must not poison the shared GET read
        # transaction. Use one private read-only connection, cached per request.
        con=api['conn']();con.execute('SET TRANSACTION READ ONLY')
        con.execute("SET LOCAL statement_timeout='3s'")
        scoped=apply_release_context(con)
        row=con.execute('select psms_revised_release.policy_v1() as policy' if scoped
                        else 'select policy from psms_documents.procurement_policy where singleton').fetchone()
        active=bool(row and type(row['policy']) is dict and row['policy'].get('enabled') is True)
        if scoped and not active:
            unavailable('PROCUREMENT_CAPABILITY_DENIED', 'The procurement release is not enabled for this deployment.')
        if active and (row['policy'].get('link_authority') not in ('ADMIN_DOCUMENTED','EXISTING_REVIEWERS_DOCUMENTED')
                       or row['policy'].get('po_clock')!='ASIA_MANILA'):
            unavailable('PROCUREMENT_POLICY_PENDING', 'The revised relationship authority and PO numbering policy are not approved. No legacy write was attempted.')
        if active and not contracts_ready(con):
            unavailable('PROCUREMENT_CONTRACT_UNAVAILABLE', 'The revised procurement database contract is unavailable. No legacy write was attempted.')
    except HTTPException:
        raise
    except Exception as error:
        # A genuinely absent pre-expansion relation preserves the legacy path.
        # Denied/unavailable metadata must not select an older write path.
        if active or release_context() or getattr(error,'sqlstate',None) not in ('42P01','3F000'):
            state = getattr(error, 'sqlstate', None)
            diagnostic = ('PROCUREMENT_CAPABILITY_DENIED' if state == 'PA007' else
                          'PROCUREMENT_CONTRACT_UNAVAILABLE' if state in ('42P01','3F000','42883') else
                          'PROCUREMENT_DATABASE_UNAVAILABLE')
            unavailable(diagnostic, 'The revised procurement database contract could not be verified. No legacy write was attempted.')
        active=False
    finally:
        if con is not None:
            try:con.rollback()
            finally:con.close()
    if has_request_context():g.revised_procurement_enabled=active
    return active


def pr_reference(api,*,with_nlp=False):
    if not enabled(api):return 'aq.pr_id'
    return 'coalesce(n.pr_id,p.pr_id,aq.pr_id)' if with_nlp else 'coalesce(p.pr_id,aq.pr_id)'

def identifier(value):
    if not re.fullmatch(r'[1-9][0-9]{0,18}',str(value or '')) or int(value)>9223372036854775807:
        abort(400,description='Select valid source document identifiers.')
    return int(value)

def rpc(api,name,values):
    if name not in RPC:raise ValueError('Unknown procurement operation')
    if getattr(g,'workflow_preview',False) or request.method!='POST':abort(405)
    if not enabled(api):abort(409,description='The revised procurement workflow is disabled.')
    try:
        with api['durable_ocr'].transaction(api,read_committed=True) as (con,uid):
            sql='select * from '+api_schema()+'.'+name+'('+','.join('?::'+kind for kind in RPC[name])+')'
            receipt=con.execute(sql,values).fetchone()
            if not receipt:raise RuntimeError('Operation receipt unavailable')
            key=('pr_id' if name in ('create_pr_from_approved_app_v1','approve_pr_v1') else
                 'aoq_id' if name=='approve_reviewed_aoq_v1' else
                 'nlp_id' if name in ('save_pr_po_verification_v1','review_pr_po_verification_v1') else 'po_id')
            if type(receipt.get(key)) is not int or receipt[key]<=0:
                raise RuntimeError('Operation receipt identifier invalid')
            if name.startswith('approve_') and (receipt[key]!=values[0] or receipt.get('new_status')!='Approved'):
                raise RuntimeError('Operation approval receipt invalid')
            if name in ('create_pr_from_approved_app_v1','create_po_for_review_v2','save_pr_po_verification_v1') and type(receipt.get('created')) is not bool:
                raise RuntimeError('Operation creation receipt invalid')
        return receipt
    except HTTPException:raise
    except Exception as error:
        code=getattr(error,'sqlstate',None)
        messages={
          'PA001':(422,'Review complete item correspondence, technical findings and confirmation fields.'),
          'PA002':(409,'The source or review changed, or a required approval is missing. Reopen the saved documents.'),
          'PA003':(403,'An active authorized reviewer is required.'),
          'PA004':(404,'The selected procurement record is unavailable.'),
          'PA005':(409,'The official authority for independent PR-PO relationships has not been approved.'),
          'PA006':(409,'This independent relationship requires an Approved PR with approved APP/PPMP evidence.')}
        if code in messages:abort(messages[code][0],description=messages[code][1])
        if code=='PA007':abort(503,description='The procurement release is disabled or unavailable.')
        # Driver details, parameters and source contents must never reach logs/UI.
        return api['ui_feedback'].database_failure(error)

def source_pair(api,pr_id,po_id):
    pr_id,po_id=identifier(pr_id),identifier(po_id)
    pr=api['q']("select id,no,status,office,items,app_id,ppmp_id from pr where id=? and status='Approved'",(pr_id,),one=True)
    po=api['q']("""select p.*,a.pr_id legacy_pr_id,a.status aoq_status,a.no aoq_no,a.winner aoq_winner
       from po p join aoq a on a.id=p.aoq_id where p.id=?
       and p.status in ('For Review','Approved') and a.status='Approved'""",(po_id,),one=True)
    if not pr or not po:abort(404,description='Select an Approved PR and a PO from an Approved AOQ.')
    if not po.get('aoq_winner') or po.get('supplier')!=po['aoq_winner']:
        abort(409,description='The PO supplier conflicts with its approved AOQ quotation. Reopen the source documents.')
    if ((po.get('legacy_pr_id') is not None and po['legacy_pr_id']!=pr_id)
        or (po.get('pr_id') is not None and po['pr_id']!=pr_id)):
        abort(409,description='The selected PR conflicts with this document\'s existing official relationship.')
    return dict(pr),dict(po)

def comparison(api,pr,po,*,manual_mapping=None,semantic=False):
    left,right=api['parse_items'](pr['items']),api['parse_items'](po['items'])
    candidates=[];semantic_state='NOT_REQUESTED'
    if semantic:
        try:
            from nlp_runtime_selection import model_root
            if api['app'].config.get('PSMS_NLP_RUNTIME','legacy')=='legacy':
                candidates=evidence.fasttext_candidates(left,right,model_root(api['app'].config,api['NLP_FASTTEXT_DIR']))
                semantic_state='INSTALLED_FASTTEXT_INFERENCE'
            else:
                semantic_state='PAIR_CLASSIFIER_RUNTIME; ITEM_CANDIDATES_NOT_AVAILABLE'
        except Exception:semantic_state='UNAVAILABLE; NO_SYNTHETIC_SCORE'
    result=evidence.compare(left,right,semantic_candidates=candidates,manual_mapping=manual_mapping)
    result['semantic_state']=semantic_state
    result['semantic_candidates']=[candidate for row in range(1,len(left)+1) for candidate in
        sorted((c for c in candidates if c['pr_position']==row),key=lambda c:c['similarity'],reverse=True)[:3]]
    return result

def nlp_page(api):
    if request.method=='POST':
        pr_id,po_id=identifier(request.form.get('pr_id')),identifier(request.form.get('po_id'))
        if request.form.get('action')=='compare':return redirect(url_for('nlp',pr_id=pr_id,po_id=po_id))
        if request.form.get('action')!='run':abort(400)
        pr,po=source_pair(api,pr_id,po_id)
        if po['status']!='For Review':abort(409,description='Final PO approval already occurred. Open its saved verification.')
        existing=api['q']('select id,pr_id,pr_items_hash,po_items_hash,workflow_version from nlp where po_id=? order by id',(po_id,))
        if existing:
            if (len(existing)==1 and existing[0].get('workflow_version')==1
                    and existing[0].get('pr_id')==pr_id
                    and existing[0].get('pr_items_hash')==evidence.fingerprint(pr['items'])
                    and existing[0].get('po_items_hash')==evidence.fingerprint(po['items'])):
                return redirect(url_for('nlp_review',id=existing[0]['id']))
            abort(409,description='The saved verification uses different or changed sources. '
                  'Open the existing evidence for review; no result was overwritten or rerun.')
        from nlp_runtime_selection import require_available
        require_available(api['app'].config)
        try:
            result,confidence,raw=api['nlp_predict'](
                evidence.inference_items(api['parse_items'](pr['items']),'PR'),
                evidence.inference_items(api['parse_items'](po['items']),'PO'))
            if raw.get('trained_model_used') is not True or result not in ('Acceptable','Needs Review','Needs Recanvass','Reject'):
                raise RuntimeError('Trained model result required')
            if isinstance(confidence,bool) or not math.isfinite(float(confidence)) or not 0<=float(confidence)<=100:
                raise RuntimeError('Invalid model confidence')
            technical=comparison(api,pr,po,semantic=True)
            metadata=raw.get('model_metadata') or {}
            metadata_hash=hashlib.sha256(json.dumps(metadata,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()
            artifact_hashes={}
            if api['app'].config.get('PSMS_NLP_RUNTIME','legacy')=='legacy':
                import fasttext_nlp_runtime
                from nlp_runtime_selection import model_root
                for kind,path in fasttext_nlp_runtime.required_paths(model_root(api['app'].config,api['NLP_FASTTEXT_DIR'])).items():
                    artifact_hashes[kind]=hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except Exception:
            abort(503,description='Model verification is unavailable for these sources. No fallback result was saved.')
        model_version=(str(metadata.get('model_version') or metadata.get('feature_schema') or raw.get('runtime') or raw.get('model'))
            +':manifest='+metadata_hash[:16]+':artifacts='+hashlib.sha256(json.dumps(artifact_hashes,sort_keys=True).encode()).hexdigest()[:16])[:200]
        details={key:raw[key] for key in ('model','architecture','runtime','decision_mode','trained_status_label',
            'trained_status_confidence','trained_status_probabilities','trained_primary_issue','trained_issue_confidence',
            'trained_issue_probabilities','feature_dimension') if key in raw}
        details.update(trained_model_used=True,automatic_approval=False,human_review_required=True,
            model_metadata_sha256=metadata_hash,model_artifacts_sha256=artifact_hashes,
            approved_inference_release_id=api['app'].config.get('_PSMS_INFERENCE_RELEASE_ID'),
            workflow_version=1,source_pr_id=pr_id,source_po_id=po_id,technical_comparison=technical,
            overall_verification_status=(result if result in ('Reject','Needs Recanvass') or technical['technical_status']=='MATCH' else 'Needs Review'))
        receipt=rpc(api,'save_pr_po_verification_v1',(po_id,pr_id,evidence.fingerprint(pr['items']),
            evidence.fingerprint(po['items']),result,float(confidence),json.dumps(details,ensure_ascii=False),model_version,technical['technical_status']))
        if not isinstance(receipt,dict):return receipt
        flash('Verification saved. Review the original sources and authorize their relationship before final PO approval.')
        return redirect(url_for('nlp_review',id=receipt['nlp_id']))
    prs=api['q']("select id,no,office,items from pr where status='Approved' order by id desc")
    pos=api['q']("""select p.id,p.no,p.supplier,p.items,a.no aoq_no,exists(select 1 from nlp n where n.po_id=p.id) has_verification
       from po p join aoq a on a.id=p.aoq_id where p.status='For Review' and a.status='Approved' order by p.id desc""")
    rows=api['q']("""select n.id,n.no,n.result,n.status,n.technical_status,n.workflow_version,
       p.no po_no,r.no pr_no from nlp n join po p on p.id=n.po_id
       left join aoq a on a.id=p.aoq_id left join pr r on r.id=coalesce(n.pr_id,p.pr_id,a.pr_id) order by n.id desc""")
    pr=po=None;technical=None
    if request.args.get('pr_id') or request.args.get('po_id'):
        pr,po=source_pair(api,request.args.get('pr_id'),request.args.get('po_id'))
        technical=comparison(api,pr,po)
    return render_template('revised_nlp.html',active='nlp',title='PR-PO NLP Verification',prs=prs,pos=pos,rows=rows,
        selected_pr=pr,selected_po=po,comparison=technical,
        pr_items=api['parse_items'](pr['items']) if pr else [],po_items=api['parse_items'](po['items']) if po else [],
        model_state=api['trained_nlp_model_status']())

def relationship_review_allowed(api,po):
    """Display existing SQL-authorized review actions; SQL remains authoritative."""
    account=getattr(g,'psms_account',None) if has_request_context() else None
    if (not account or account.get('status')!='Active'
            or account.get('role') not in ('Admin','Staff','Inspector')):return False
    con=None
    try:
        con=api['conn']()
        con.execute('SET TRANSACTION READ ONLY')
        con.execute("SET LOCAL statement_timeout='3s'")
        scoped=apply_release_context(con)
        row=con.execute('select psms_revised_release.policy_v1() as policy' if scoped
                        else 'select policy from psms_documents.procurement_policy where singleton').fetchone()
        if scoped and not contracts_ready(con):return False
    except Exception:
        return False
    finally:
        if con is not None:
            try:con.rollback()
            finally:con.close()
    policy=row.get('policy') if row else None
    if not isinstance(policy,dict) or policy.get('enabled') is not True:return False
    if scoped and (policy.get('po_clock')!='ASIA_MANILA'
                   or policy.get('link_authority') not in ('ADMIN_DOCUMENTED','EXISTING_REVIEWERS_DOCUMENTED')):
        return False
    # Historical approved-PR links retain the existing reviewer-role rule.
    if po.get('legacy_pr_id') is not None:return True
    authority=policy.get('link_authority')
    return (authority=='ADMIN_DOCUMENTED' and account['role']=='Admin'
            or authority=='EXISTING_REVIEWERS_DOCUMENTED')


def modern_review(api,row):
    pr,po=source_pair(api,row['pr_id'],row['po_id'])
    recorded_mapping=row.get('review_mapping')
    technical=comparison(api,pr,po,manual_mapping=recorded_mapping if isinstance(recorded_mapping,list) else None)
    try:details=json.loads(row.get('details') or '{}')
    except (ValueError,TypeError):details={}
    stale=evidence.fingerprint(pr['items'])!=row['pr_items_hash'] or evidence.fingerprint(po['items'])!=row['po_items_hash']
    return render_template('revised_nlp_review.html',active='nlp',title='Review PR-PO Verification',row=row,pr=pr,po=po,
        pr_items=api['parse_items'](pr['items']),po_items=api['parse_items'](po['items']),comparison=technical,
        details=details,stale=stale,can_review_relationship=relationship_review_allowed(api,po))

def stored_review_matches(row,mapping,current):
    """Confirmation cannot replace saved server compliance or correspondence."""
    try:
        details=json.loads(row.get('details') or '{}')
        saved=details.get('technical_comparison')
        if (row.get('technical_status')!='MATCH' or type(saved) is not dict or type(current) is not dict
                or type(mapping) is not list or not mapping):return False
        for value in (saved,current):
            if (value.get('technical_status')!='MATCH' or value.get('version')!='PR_PO_REVIEW_V1'
                    or value.get('extra_or_unallocated_items')!=[]
                    or value.get('missing_or_unresolved_pr_items')!=[]
                    or type(value.get('rows')) is not list):return False
        count=len(mapping)
        if (len(saved['rows'])!=count or len(current['rows'])!=count
                or any(type(value) is not int or not 1<=value<=count for value in mapping)
                or len(set(mapping))!=count or type(saved.get('source_fields')) is not dict
                or saved['source_fields']!=current.get('source_fields')):return False
        for position,(original,reviewed,target) in enumerate(zip(saved['rows'],current['rows'],mapping),1):
            for value in (original,reviewed):
                if (type(value) is not dict or type(value.get('pr_position')) is not int
                        or type(value.get('po_position')) is not int or value['pr_position']!=position
                        or value['po_position']!=target or type(value.get('checks')) is not list):return False
            if (original.get('pr_reference')!=reviewed.get('pr_reference')
                    or original.get('po_reference')!=reviewed.get('po_reference')
                    or len(original['checks'])<2
                    or json.dumps(original['checks'],sort_keys=True,default=str)!=json.dumps(reviewed['checks'],sort_keys=True,default=str)):
                return False
            if any(type(check) is not dict or check.get('state')!='MATCH'
                   or check.get('conditional_on_identity') is not False
                   or type(check.get('attribute')) is not str or not check['attribute'].strip()
                   for check in original['checks']):return False
        return True
    except (ValueError,TypeError,AttributeError,KeyError):
        return False


def approve_verification(api,row):
    if request.form.get('review_confirm')!='yes' or request.form.get('final_confirm')!='yes':
        abort(400,description='Explicit review and relationship confirmation are required.')
    pr,po=source_pair(api,row['pr_id'],row['po_id'])
    if not relationship_review_allowed(api,po):abort(403,description='Relationship authorization is unavailable for this account or policy.')
    if row.get('result') not in ('Acceptable','Needs Review'):
        abort(422,description='This model result requires corrective procurement action before relationship approval.')
    try:
        mapping=[identifier(value) for value in request.form.getlist('match_po_position')]
        technical=comparison(api,pr,po,manual_mapping=mapping)
    except ValueError:abort(422,description='Provide one unique corresponding PO item for every PR item.')
    if technical['technical_status']!='MATCH':
        abort(422,description='Technical, quantity, missing or extra-item findings remain unresolved. No relationship or PO was approved.')
    if not stored_review_matches(row,mapping,technical):
        abort(422,description='Saved server technical evidence does not authorize this correspondence. Confirmation cannot upgrade or replace unresolved verification evidence.')
    receipt=rpc(api,'review_pr_po_verification_v1',(row['id'],json.dumps(mapping),json.dumps(technical,default=str),
        request.form.get('relationship_reason',''),request.form.get('evidence_reference',''),True))
    if not isinstance(receipt,dict):return receipt
    flash('PR-PO relationship authorized and recorded. Final PO approval remains a separate explicit action.')
    return redirect(url_for('po_review',id=receipt['po_id']))

def approve_aoq(api,identifier_value):
    guard=api['require_review_confirmation']('aoq_review',identifier_value)
    if guard:return guard
    if request.form.get('final_confirm')!='yes':abort(400,description='Explicit final approval confirmation is required.')
    receipt=rpc(api,'approve_reviewed_aoq_v1',(identifier_value,True,True))
    if not isinstance(receipt,dict):return receipt
    if receipt.get('aoq_id')!=identifier_value or receipt.get('new_status')!='Approved':abort(503)
    flash('AOQ approved. Its reviewed quotation is available for PO generation.')
    return redirect(url_for('po'))

def create_po(api,identifier_value):
    receipt=rpc(api,'create_po_for_review_v2',(identifier(identifier_value),))
    if not isinstance(receipt,dict):return receipt
    if type(receipt.get('created')) is not bool or type(receipt.get('po_id')) is not int or receipt['po_id']<=0:abort(503)
    flash('PO saved For Review from the approved AOQ quotation.' if receipt['created'] else 'The existing PO was reopened; no duplicate was created.')
    return redirect(url_for('po_review',id=receipt['po_id']))

def create_pr(api,identifier_value,purpose=None):
    import unicodedata
    purpose='' if purpose is None else purpose
    if not isinstance(purpose,str) or len(purpose)>2000 or any(unicodedata.category(char)=='Cc' and char not in '\n\r\t' for char in purpose):
        abort(422,description='Enter a valid purpose of at most 2,000 characters.')
    row=api['q']("""select a.items,a.total,m.items ppmp_items from app_plan a join ppmp m on m.id=a.ppmp_id
        where a.id=? and a.status='Approved' and m.status='Approved'""",(identifier(identifier_value),),one=True)
    if not row:abort(409,description='Select an Approved APP and its approved source PPMP.')
    raw=row.get('items') or row.get('ppmp_items');items=api['parse_items'](raw)
    errors=api['planning_item_errors'](items) if items else ['No approved APP items were found.']
    if errors:abort(422,description='Cannot create Purchase Request: '+' '.join(errors))
    total=api['items_total'](items)
    if total is None or total<=0:total=api['parse_currency_decimal'](row['total'])
    if total is None or total<=0:abort(422,description='The approved APP total is invalid.')
    receipt=rpc(api,'create_pr_from_approved_app_v1',(identifier(identifier_value),purpose.strip(),evidence.fingerprint(raw),float(total)))
    if not isinstance(receipt,dict):return receipt
    flash('Purchase Request saved For Review.' if receipt['created'] else 'The existing Purchase Request was reopened; no duplicate was created.')
    return redirect(url_for('pr_review',id=receipt['pr_id']))

def approve_pr(api,identifier_value):
    guard=api['require_review_confirmation']('pr_review',identifier_value)
    if guard:return guard
    receipt=rpc(api,'approve_pr_v1',(identifier_value,True))
    if not isinstance(receipt,dict):return receipt
    flash('Purchase Request approved. Its original items remain available for PR-PO verification.')
    return redirect(url_for('pr_review',id=identifier_value))

def approve_po(api,identifier_value):
    guard=api['require_review_confirmation']('po_review',identifier_value)
    if guard:return guard
    if request.form.get('final_confirm')!='yes':abort(400,description='Explicit final approval confirmation is required.')
    # Re-evaluate even previously authorized evidence under the current checker.
    # Item correspondence recorded by an older checker cannot waive unparsed
    # specifications. The secure SQL function still locks/rechecks source hashes,
    # immutable proof, session, authority and status inside its transaction.
    verifications=api['q']("""select n.* from nlp n where n.po_id=?
        and n.workflow_version=1 and n.status='Approved' order by n.id""",(identifier_value,))
    if (len(verifications)!=1 or verifications[0].get('workflow_version')!=1
            or verifications[0].get('status')!='Approved'
            or verifications[0].get('result') not in ('Acceptable','Needs Review')):
        abort(409,description='A current authorized PR-PO verification is required before final approval.')
    verification=verifications[0]
    pr,po=source_pair(api,verification['pr_id'],identifier_value)
    if (verification.get('po_id')!=identifier_value
            or evidence.fingerprint(pr['items'])!=verification.get('pr_items_hash')
            or evidence.fingerprint(po['items'])!=verification.get('po_items_hash')):
        abort(409,description='The reviewed source documents changed. No PO was approved.')
    try:
        mapping=verification.get('review_mapping')
        if type(mapping) is not list:raise ValueError('Recorded correspondence required')
        technical=comparison(api,pr,po,manual_mapping=mapping)
    except (ValueError,TypeError):
        abort(409,description='Current source and item correspondence could not be verified. No PO was approved.')
    if technical['technical_status']!='MATCH':
        abort(409,description='Current specification findings remain unresolved. No PO was approved.')
    if not stored_review_matches(verification,mapping,technical):
        abort(409,description='The original server technical evidence does not support this approval. No PO was approved.')
    receipt=rpc(api,'approve_po_after_verification_v1',(identifier_value,True,True))
    if not isinstance(receipt,dict):return receipt
    if receipt.get('po_id')!=identifier_value or receipt.get('new_status')!='Approved':abort(503)
    flash('PO approved after authorized PR-PO verification. Continue through delivery and receiving.')
    return redirect(url_for('po'))

def po_has_review(api,po_id):
    row=api['q']("""select exists(select 1 from psms_documents.pr_po_reviews r
        join nlp n on n.id=r.nlp_id where r.po_id=? and n.status='Approved') ready""",(po_id,),one=True)
    return bool(row and row['ready'])

def upload_aoq(api):
    """Optional legacy PR association, never a source of quotation item content."""
    from private_document_uploads import validate,extraction_path
    from nlp_runtime_selection import require_available
    require_available(api['app'].config)
    raw_pr=(request.form.get('pr_id') or '').strip()
    pr_id=identifier(raw_pr) if raw_pr else None
    pr=api['q']("select id,no,status,items from pr where id=? and status='Approved'",(pr_id,),one=True) if pr_id else None
    if pr_id and not pr:abort(400,description='The optional linked PR must be Approved.')
    name,extension,content=validate(request.files.get('ocr_file'))
    digest=hashlib.sha256(content).hexdigest();durable=api['durable_ocr']
    lookup="""select d.id,d.status,d.official_aoq_id from psms_documents.aoq_drafts d
      join psms_documents.sources s on s.id=d.source_id
      where d.owner_id=? and d.pr_id is not distinct from ?::bigint and s.sha256=? and d.status<>'CANCELLED'
      order by d.created_at limit 1"""
    def existing_response(row):
        return redirect(url_for('aoq_review',id=row['official_aoq_id']) if row['status']=='CONFIRMED'
                        else url_for('aoq',draft_id=str(row['id'])))
    row=api['q'](lookup,(durable._uid(),pr_id,digest),one=True)
    if row:return existing_response(row)
    with extraction_path(content,extension) as path:
        document=api['extract_aoq_document'](path)
        preview=api['build_flexible_aoq_preview'](document,[])
    if len(preview.get('offer_items') or [])>200 or len(preview.get('suppliers') or [])>100:abort(400)
    preview.update(pr_id=pr_id,pr_no=pr['no'] if pr else '',ocr_filename=name,stored_filename='',
        offer_review_token=secrets.token_urlsafe(24),source_award_hint=preview.get('winner_index',-1),
        selected_winner_index=-1,offer_items=[],offer_supplier_index='')
    did=str(uuid.uuid4())
    with durable.transaction(api,read_committed=True) as (con,uid):
        con.execute('select pg_advisory_xact_lock(hashtextextended(?,0))',
            ('AOQ-UPLOAD:'+str(uid)+':'+str(pr_id)+':'+digest,))
        row=con.execute(lookup,(uid,pr_id,digest)).fetchone()
        if row:return existing_response(row)
        if pr_id:
            current=con.execute("select id from pr where id=? and status='Approved'",(pr_id,)).fetchone()
            if not current or con.execute('select 1 from aoq where pr_id=?',(pr_id,)).fetchone():
                abort(409,description='This linked PR changed or already has an AOQ.')
        sid=durable.source(con,uid,'aoq',None,name,extension,content,document)
        con.execute('insert into psms_documents.aoq_drafts(id,owner_id,source_id,pr_id,payload) values(?,?,?,?,?::jsonb)',
            (did,uid,sid,pr_id,durable.payload(preview)))
        durable.audit(api,con,'Extracted Private OCR Draft','AOQ',did)
    from flask import session
    session['aoq_draft_id']=did
    flash('AOQ extraction saved privately. Review its actual quotations and explicitly select one supplier.')
    return redirect(url_for('aoq',draft_id=did))
