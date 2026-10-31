"""Explicit Admin-attested archive reconstruction. Never infers from current names."""
import hashlib,json,re,uuid
from flask import abort,flash,redirect,render_template,request,session,url_for,Response
from werkzeug.exceptions import Conflict
from private_document_uploads import validate
import signatory_management_v0_1_0 as signatories

KINDS={'ppmp':'ppmp','app':'app_plan','pr':'pr','po':'po','iar':'iar','ris':'ris_ics','ics':'ris_ics'}

class LegacyHistoryUnavailable(Conflict):
    def __init__(self,kind,row):
        super().__init__('Historical signatory snapshot is unavailable. Consult the archived official record; no current identity or signature was substituted.')
        self.kind=kind;self.document_no=row.get('no','');self.document_id=row['id']

def normalized(value):return re.sub(r'\s+',' ',str(value or '')).strip().casefold()

def exact_identity(text,value):
    return bool(re.search(r'(?<![\w-])'+re.escape(normalized(value))+r'(?![\w-])',normalized(text)))

def register(app,factory):
    @app.errorhandler(signatories.Invalid)
    def invalid(error):
        return Response(str(error),status=400,mimetype='text/plain')

    @app.errorhandler(LegacyHistoryUnavailable)
    def unresolved(error):
        return render_template('legacy_signatory_unresolved.html',error=error,title='Historical identity unresolved'),409

    @app.route('/admin/signatories/legacy',methods=['GET','POST'])
    def historical_evidence():
        if request.method=='GET':return render_template('historical_signatory_import.html',title='Historical signatory evidence',active='users',slots=signatories.SLOTS)
        if request.form.get('verify_archive')!='yes' or request.form.get('confirm_reconstruction')!='yes':
            abort(400,description='A verified original archive and explicit reconstruction acknowledgement are required.')
        kind=request.form.get('document_kind')
        if kind not in KINDS:abort(400)
        did=signatories.integer(request.form.get('document_id'))
        number=signatories.text_field(request.form.get('document_no'),160)
        note=signatories.text_field(request.form.get('verification_note'),1000)
        _,_,content=validate(request.files.get('archive'),only_pdf=True)
        import fitz
        with fitz.open(stream=content,filetype='pdf') as doc:
            text=normalized(' '.join(page.get_text('text') for page in doc))
        if not text or not exact_identity(text,number):abort(400,description='The native PDF text must contain the exact document number. Scans/unverifiable sources remain unresolved.')
        try:claims=json.loads(request.form.get('identities_json',''))
        except (ValueError,TypeError):abort(400,description='Provide a JSON list of source-backed identities and exact quotes.')
        if not isinstance(claims,list) or not 1<=len(claims)<=11:abort(400)
        document='ris_ics' if kind in ('ris','ics') else kind
        allowed=[key for key,value in signatories.SLOTS.items() if value[0]==document]
        identities={slot:dict(name='',position='',office='',person_id=None,person_revision=None,signature_version=None,signature_authorized=False) for slot in allowed}
        seen=set();quotes=[]
        for claim in claims:
            if not isinstance(claim,dict):abort(400)
            slot=claim.get('slot')
            if slot not in allowed or slot in seen:abort(400,description='Use unique existing fields applicable to this document.')
            name=signatories.text_field(claim.get('name'),160)
            position=signatories.text_field(claim.get('position',''),160,False)
            quote=signatories.text_field(claim.get('quote'),4000)
            if normalized(quote) not in text or not exact_identity(quote,name) or (position and not exact_identity(quote,position)):
                abort(400,description='Every supplied name/designation must appear in its exact retained-source quote.')
            identities[slot].update(name=name,position=position);seen.add(slot);quotes.append(dict(slot=slot,quote=quote))
        con=factory()
        try:
            uid=signatories._admin(con)
            uid,digest=signatories.actor_credentials()
            con.execute("select set_config('psms.signatory_actor',?,true)",(json.dumps(dict(user_id=uid,sid=digest)),))
            con.execute('select psms_signatories.record_verified_archive(?,?,?,?::jsonb,?,?,?::jsonb,?,?::uuid)',
                (kind,did,number,json.dumps(identities),content,hashlib.sha256(content).hexdigest(),json.dumps(quotes),note,str(uuid.uuid4())))
            con.commit()
        except Exception as error:
            con.rollback()
            if isinstance(error,signatories.Forbidden):abort(403)
            if getattr(error,'sqlstate',None)=='23505':abort(409,description='An immutable snapshot already exists; it was not replaced.')
            from werkzeug.exceptions import HTTPException
            if isinstance(error,HTTPException):raise
            return Response('Historical evidence was not recorded. Verify the document identity, archive and existing snapshot state.',status=409,mimetype='text/plain')
        finally:con.close()
        flash('Source-backed historical reconstruction recorded. The retained original, approval status and original timestamps were preserved. No signature image was applied.')
        return redirect(url_for('historical_evidence'))

    @app.get('/admin/signatories/legacy/<uuid:evidence_id>/archive')
    def historical_archive(evidence_id):
        con=factory()
        try:
            con.execute('SET TRANSACTION READ ONLY')
            row=con.execute('select original_pdf from psms_signatories.legacy_evidence where id=?',(evidence_id,)).fetchone()
            if not row:abort(404)
            response=Response(bytes(row['original_pdf']),mimetype='application/octet-stream')
            response.headers['Content-Disposition']='attachment; filename="retained-archived-original.pdf"'
            response.headers['Content-Security-Policy']="default-src 'none'; sandbox"
            return response
        finally:con.rollback();con.close()
