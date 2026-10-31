"""Private Admin-managed signatories. Candidate; no startup DDL or cloud access.

Configuration, authenticated workflow approval and image placement are separate.
This module NEVER places a signature image on a procurement document.
"""
from contextlib import contextmanager
from datetime import date
from io import BytesIO
import hashlib
import json
import re
import unicodedata
import uuid
import warnings

from flask import Blueprint, Response, abort, has_request_context, redirect, render_template, request, session, url_for
from PIL import Image, UnidentifiedImageError
from werkzeug.exceptions import HTTPException
import server_sessions_v0_1_0 as sessions

SLOTS = {
    'ppmp_prepared': ('ppmp', 'PPMP — Prepared by'),
    'ppmp_approved': ('ppmp', 'PPMP — Approved by'),
    'app_prepared': ('app', 'APP — Prepared by'),
    'app_approved': ('app', 'APP — Approved by'),
    'pr_requested': ('pr', 'PR — Requested by'),
    'pr_approved': ('pr', 'PR — Approved by'),
    'po_authorized': ('po', 'PO — Authorized by'),
    'iar_inspection': ('iar', 'IAR — Inspected by'),
    'iar_acceptance': ('iar', 'IAR — Accepted by'),
    'ris_issued': ('ris_ics', 'RIS/ICS — Issued by'),
    'ris_received': ('ris_ics', 'RIS/ICS — Received by'),
}
IMAGE_LIMIT = 524288
DOC_MUTATION = re.compile(r'^\s*(?:update\s+|insert\s+into\s+)(?:public\.)?(?:ppmp|app_plan|pr|po|iar|ris_ics)\b', re.I)


class Invalid(ValueError):
    pass


class Conflict(ValueError):
    pass


class Forbidden(ValueError):
    pass


def actor_credentials():
    uid = session.get('user_id')
    digest = sessions.token_hash(session.get(sessions.COOKIE_KEY))
    if session.get('logged_in') is not True or type(uid) is not int or uid <= 0 or digest is None:
        raise Forbidden('Active Administrator account required.')
    return uid, digest


class Connection:
    """Preserve db.py semantics; bind actor only for relevant document mutations.

    No query is injected before session validation's SET TRANSACTION READ ONLY.
    SET LOCAL expires at transaction end, including pooled-connection rollback.
    """
    def __init__(self, connection):
        self.inner = connection

    def execute(self, sql, params=()):
        if DOC_MUTATION.match(str(sql)) and has_request_context():
            uid = session.get('user_id')
            sid = sessions.token_hash(session.get(sessions.COOKIE_KEY))
            context = {'user_id': uid, 'sid': sid} if type(uid) is int and uid > 0 and sid else None
            self.inner.execute("select set_config('psms.signatory_actor',?,true)", (json.dumps(context) if context else '',))
        return self.inner.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def __enter__(self):
        self.inner.__enter__()
        return self

    def __exit__(self, *args):
        return self.inner.__exit__(*args)


def text_field(value, maximum, required=True):
    if not isinstance(value, str) or len(value) > maximum * 2:
        raise Invalid('Invalid text field.')
    value = unicodedata.normalize('NFC', value).strip()
    if len(value) > maximum or (required and not value) or any(unicodedata.category(c).startswith('C') for c in value):
        raise Invalid('Invalid text field.')
    return value


def integer(value, zero=False):
    if not isinstance(value, str) or not re.fullmatch(r'0|[1-9][0-9]{0,17}', value):
        raise Invalid('Reopen the configuration before saving.')
    number = int(value)
    if number < (0 if zero else 1):
        raise Invalid('Reopen the configuration before saving.')
    return number


def person_fields(form):
    try:
        start = date.fromisoformat(form.get('effective_from', ''))
        end = date.fromisoformat(form['effective_to']) if form.get('effective_to') else None
    except (ValueError, TypeError):
        raise Invalid('Use valid effective dates.') from None
    if end and end < start:
        raise Invalid('Effective end cannot precede effective start.')
    return (text_field(form.get('full_name'), 160), text_field(form.get('position'), 160),
            text_field(form.get('office', ''), 120, False), start, end)


def normalized_image(upload):
    if not upload or not isinstance(upload.filename, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_. -]{0,100}\.(?:png|jpg|jpeg)', upload.filename, re.I):
        raise Invalid('Choose a PNG or JPEG with a plain filename.')
    raw = upload.stream.read(IMAGE_LIMIT + 1)
    if not raw or len(raw) > IMAGE_LIMIT:
        raise Invalid('Signature image must be at most 512 KiB.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as probe:
                fmt, width, height = probe.format, probe.width, probe.height
                expected = 'PNG' if upload.filename.lower().endswith('.png') else 'JPEG'
                if fmt != expected or fmt not in ('PNG', 'JPEG') or getattr(probe, 'n_frames', 1) != 1:
                    raise Invalid('Only single-frame PNG/JPEG images are accepted.')
                if not 1 <= width <= 1600 or not 1 <= height <= 600 or width * height > 960000:
                    raise Invalid('Image dimensions exceed the signature limits.')
                probe.verify()
            with Image.open(BytesIO(raw)) as image:
                image.load()
                # Re-encode pixels in a clean image: no uploaded metadata/path,
                # polyglot trailing bytes, EXIF, profile, or original filename.
                clean = Image.new('RGBA', image.size)
                clean.paste(image.convert('RGBA'))
                result = BytesIO()
                clean.save(result, format='PNG')
                data = result.getvalue()
        if len(data) > IMAGE_LIMIT:
            raise Invalid('Normalized image exceeds the storage limit.')
        return data, width, height
    except Invalid:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise Invalid('Image could not be safely validated.') from None


def _admin(con):
    uid, digest = actor_credentials()
    ready = con.execute('select public.psms_auth_session_permissions_ready() as ready').fetchone()
    if not ready or ready['ready'] is not True:
        raise RuntimeError('Session permissions unavailable.')
    # Same lock order as account/session operations. SHARE prevents disable,
    # demotion or revocation from racing this authorized configuration transaction.
    account = con.execute("select id from public.users where id=? and role='Admin' and status='Active' for share", (uid,)).fetchone()
    active = con.execute('''select user_id from public.auth_sessions where token_hash=? and user_id=?
        and revoked_at is null and expires_at>clock_timestamp() for share''', (digest, uid)).fetchone()
    if not account or not active:
        raise Forbidden('Active Administrator account required.')
    return uid


@contextmanager
def mutation(factory, form):
    con = factory()
    try:
        uid = _admin(con)
        state = con.execute('select revision from psms_signatories.config_state where id=1 for update').fetchone()
        if not state or state['revision'] != integer(form.get('config_revision'), True):
            raise Conflict('Configuration changed. Reopen the page before saving.')
        yield con, uid
        if con.execute('update psms_signatories.config_state set revision=revision+1 where id=1').rowcount != 1:
            raise RuntimeError('Configuration revision unavailable.')
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def locked_person(con, person_id, form):
    row = con.execute('select * from psms_signatories.people where id=? for update', (person_id,)).fetchone()
    if not row:
        abort(404)
    if row['revision'] != integer(form.get('person_revision')):
        raise Conflict('Signatory changed. Reopen the page before saving.')
    return row


def metadata(row):
    return {key: str(row[key]) if row.get(key) is not None else None for key in
            ('id', 'full_name', 'position', 'office', 'active', 'effective_from', 'effective_to', 'revision', 'current_signature')}


def changed(con, uid, audit, action, reference, detail):
    con.execute('''insert into psms_signatories.change_history(id,actor_id,action,reference,detail)
        values(?,?,?,?,?::jsonb)''', (str(uuid.uuid4()), uid, action, str(reference), json.dumps(detail)))
    # Existing Audit Trail remains useful to its existing viewers, but private
    # before/after identities and signature content stay in the Admin-only store.
    audit(action, 'Authorized Signatories', str(reference), 'Administrator changed private signatory configuration', connection=con)


def register(app, factory, audit):
    bp = Blueprint('signatories', __name__, url_prefix='/admin/signatories')

    @app.before_request
    def admin_boundary():
        if not (request.path == '/admin/signatories' or request.path.startswith('/admin/signatories/')):
            return None
        archive_import = request.path == '/admin/signatories/legacy' and request.method == 'POST'
        request.max_content_length = 9 * 1024 * 1024 if archive_import else 768 * 1024
        # Werkzeug's multipart decoder also bounds its incoming buffer with this
        # value. A tiny field limit would reject valid images before validation.
        # The complete request is bounded here; individual text values below are
        # separately limited to their exact database field lengths.
        request.max_form_memory_size = request.max_content_length
        request.max_form_parts = 24
        try:
            actor_credentials()
            account = sessions.account_for_session(factory, session.get(sessions.COOKIE_KEY), session.get('user_id'))
            if not account or account.get('status') != 'Active' or account.get('role') != 'Admin':
                raise Forbidden()
        except Forbidden:
            abort(403)
        except Exception:
            return Response('Signatory authorization is unavailable.', status=503, mimetype='text/plain')
        # Registered before the existing ensure()/CSRF hook. Unauthorized users
        # get 403 even without a token; authorized unsafe actions still hit the
        # unchanged global Flask-WTF protection, with no exemptions.

    @app.after_request
    def private_headers(response):
        if request.path == '/admin/signatories' or request.path.startswith('/admin/signatories/'):
            response.headers['Cache-Control'] = 'private, no-store'
            response.headers['Pragma'] = 'no-cache'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['X-Frame-Options'] = 'DENY'
            response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @bp.errorhandler(Invalid)
    def invalid(error):
        return Response(str(error), status=400, mimetype='text/plain')

    @bp.errorhandler(Conflict)
    def conflict(error):
        return Response(str(error), status=409, mimetype='text/plain')

    @bp.errorhandler(Forbidden)
    def forbidden(error):
        return Response('Active Administrator account required.', status=403, mimetype='text/plain')

    def unavailable():
        return Response('Signatory configuration could not be confirmed. No change was saved.', status=503, mimetype='text/plain')

    @bp.get('')
    def index():
        page = integer(request.args.get('page', '1'))
        if page > 100000:
            raise Invalid('Invalid page.')
        con = factory()
        try:
            con.execute('SET TRANSACTION READ ONLY')
            state = con.execute('select revision from psms_signatories.config_state where id=1').fetchone()
            people = con.execute('select * from psms_signatories.people order by created_at,id limit 50 offset ?', ((page - 1) * 50,)).fetchall()
            assignments = con.execute('''select a.slot,a.person_id,p.full_name,p.position,p.active
                from psms_signatories.assignments a join psms_signatories.people p on p.id=a.person_id
                order by a.slot''').fetchall()
            return render_template('admin_signatories.html', active='users', title='Authorized Signatories',
                                   people=people, assignments=assignments, slots=SLOTS, config_revision=state['revision'], today=date.today().isoformat(), page=page)
        except Exception:
            return unavailable()
        finally:
            con.rollback()
            con.close()

    @bp.post('/add')
    def add():
        fields = person_fields(request.form)
        person_id = str(uuid.uuid4())
        try:
            with mutation(factory, request.form) as (con, uid):
                con.execute('''insert into psms_signatories.people(id,full_name,position,office,effective_from,effective_to,created_by,updated_by)
                    values(?,?,?,?,?,?,?,?)''', (person_id, *fields, uid, uid))
                row = con.execute('select * from psms_signatories.people where id=?', (person_id,)).fetchone()
                changed(con, uid, audit, 'Added Signatory', person_id, {'after': metadata(row)})
        except (Invalid, Conflict, Forbidden, HTTPException):
            raise
        except Exception:
            return unavailable()
        return redirect(url_for('signatories.index'))

    @bp.post('/<uuid:person_id>/edit')
    def edit(person_id):
        fields = person_fields(request.form)
        try:
            with mutation(factory, request.form) as (con, uid):
                before = locked_person(con, person_id, request.form)
                con.execute('''update psms_signatories.people set full_name=?,position=?,office=?,effective_from=?,effective_to=?,
                    revision=revision+1,updated_by=?,updated_at=clock_timestamp() where id=?''', (*fields, uid, person_id))
                after = con.execute('select * from psms_signatories.people where id=?', (person_id,)).fetchone()
                changed(con, uid, audit, 'Edited Signatory', person_id, {'before': metadata(before), 'after': metadata(after)})
        except (Invalid, Conflict, Forbidden, HTTPException):
            raise
        except Exception:
            return unavailable()
        return redirect(url_for('signatories.index'))

    @bp.post('/<uuid:person_id>/status')
    def status(person_id):
        active = request.form.get('active')
        if active not in ('true', 'false'):
            raise Invalid('Choose a valid activation state.')
        try:
            with mutation(factory, request.form) as (con, uid):
                before = locked_person(con, person_id, request.form)
                con.execute('''update psms_signatories.people set active=?,revision=revision+1,updated_by=?,updated_at=clock_timestamp() where id=?''', (active == 'true', uid, person_id))
                changed(con, uid, audit, 'Activated Signatory' if active == 'true' else 'Deactivated Signatory', person_id,
                        {'before': metadata(before), 'active': active == 'true'})
        except (Invalid, Conflict, Forbidden, HTTPException):
            raise
        except Exception:
            return unavailable()
        return redirect(url_for('signatories.index'))

    @bp.post('/assign')
    def assign():
        slot = request.form.get('slot')
        if slot not in SLOTS:
            raise Invalid('Choose an existing document signing field.')
        raw_id = request.form.get('person_id', '')
        try:
            person_id = str(uuid.UUID(raw_id)) if raw_id else None
        except (ValueError, TypeError):
            raise Invalid('Choose a valid signatory.') from None
        try:
            with mutation(factory, request.form) as (con, uid):
                prior = con.execute('select person_id from psms_signatories.assignments where slot=?', (slot,)).fetchone()
                if prior and str(prior['person_id']) != person_id and request.form.get('confirm_reassign') != 'yes':
                    raise Conflict('Confirm replacement/removal of the existing role assignment.')
                if person_id:
                    target = con.execute('select id,active from psms_signatories.people where id=? for share', (person_id,)).fetchone()
                    if not target or not target['active']:
                        raise Invalid('Choose an active configured signatory.')
                    con.execute('''insert into psms_signatories.assignments(slot,person_id,updated_by) values(?,?,?)
                        on conflict(slot) do update set person_id=excluded.person_id,updated_by=excluded.updated_by,updated_at=clock_timestamp()''', (slot, person_id, uid))
                else:
                    con.execute('delete from psms_signatories.assignments where slot=?', (slot,))
                changed(con, uid, audit, 'Assigned Signing Field', slot, {'before': str(prior['person_id']) if prior else None, 'after': person_id})
        except (Invalid, Conflict, Forbidden, HTTPException):
            raise
        except Exception:
            return unavailable()
        return redirect(url_for('signatories.index'))

    @bp.post('/<uuid:person_id>/signature')
    def upload_signature(person_id):
        if request.form.get('confirm_storage') != 'yes':
            raise Invalid('Confirm permission to store the image for private Admin preview.')
        data, width, height = normalized_image(request.files.get('signature'))
        version_id = str(uuid.uuid4())
        try:
            with mutation(factory, request.form) as (con, uid):
                before = locked_person(con, person_id, request.form)
                con.execute('''insert into psms_signatories.signature_versions(id,person_id,png,sha256,width,height,uploaded_by)
                    values(?,?,?,?,?,?,?)''', (version_id, person_id, data, hashlib.sha256(data).hexdigest(), width, height, uid))
                con.execute('''update psms_signatories.people set current_signature=?,revision=revision+1,updated_by=?,updated_at=clock_timestamp() where id=?''', (version_id, uid, person_id))
                changed(con, uid, audit, 'Replaced Signature' if before['current_signature'] else 'Uploaded Signature', person_id,
                        {'before_version': str(before['current_signature']) if before['current_signature'] else None, 'after_version': version_id, 'document_placement_authorized': False})
        except (Invalid, Conflict, Forbidden, HTTPException):
            raise
        except Exception:
            return unavailable()
        return redirect(url_for('signatories.index'))

    @bp.post('/<uuid:person_id>/signature/remove')
    def remove_signature(person_id):
        try:
            with mutation(factory, request.form) as (con, uid):
                before = locked_person(con, person_id, request.form)
                con.execute('''update psms_signatories.people set current_signature=null,revision=revision+1,updated_by=?,updated_at=clock_timestamp() where id=?''', (uid, person_id))
                changed(con, uid, audit, 'Removed Current Signature', person_id,
                        {'before_version': str(before['current_signature']) if before['current_signature'] else None, 'immutable_versions_retained': True})
        except (Invalid, Conflict, Forbidden, HTTPException):
            raise
        except Exception:
            return unavailable()
        return redirect(url_for('signatories.index'))

    @bp.get('/<uuid:person_id>/signature/<uuid:version_id>')
    def preview(person_id, version_id):
        con = factory()
        try:
            con.execute('SET TRANSACTION READ ONLY')
            row = con.execute('select png from psms_signatories.signature_versions where id=? and person_id=?', (version_id, person_id)).fetchone()
            if not row:
                abort(404)
            response = Response(bytes(row['png']), mimetype='image/png')
            response.headers['Content-Disposition'] = 'inline; filename="private-signature-preview.png"'
            response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
            return response
        finally:
            con.rollback()
            con.close()

    @bp.get('/history')
    def history():
        con = factory()
        try:
            con.execute('SET TRANSACTION READ ONLY')
            rows = con.execute('select * from psms_signatories.change_history order by created_at desc,id limit 100').fetchall()
            return render_template('admin_signatory_history.html', rows=rows, active='users', title='Signatory Configuration History')
        except Exception:
            return unavailable()
        finally:
            con.rollback()
            con.close()

    app.register_blueprint(bp)


def print_context(factory, kind, row):
    if kind == 'ris_ics':
        if row.get('type') not in ('RIS', 'ICS'):
            abort(409)
        document_kind = row['type'].lower()
    else:
        document_kind = kind
    slots = [slot for slot, (document, _) in SLOTS.items() if document == kind]
    if not slots:
        abort(409)
    con = factory()
    try:
        con.execute('SET TRANSACTION READ ONLY')
        if row.get('status') == 'Approved':
            snapshot = con.execute('''select identities,captured_at,origin,legacy_evidence_id from psms_signatories.document_snapshots
                where document_kind=? and document_id=?''', (document_kind, row['id'])).fetchone()
            if not snapshot:
                # Never substitute today's configuration for a historical record.
                from historical_signatories import LegacyHistoryUnavailable
                raise LegacyHistoryUnavailable(document_kind, row)
            identities = snapshot['identities']
            notice = 'Workflow approved. Historical identity snapshot retained. No signature image has been applied.'
            if snapshot['origin'] == 'verified_archive':
                notice = 'Historical identity reconstruction from Admin-verified archived evidence. This is not an original signed document. Original approval time was not reconstructed; consult the retained archive. No signature image has been applied.'
            captured_at = snapshot['captured_at']
        else:
            # One read-only query gives a consistent configuration view. Draft
            # identities remain previews and never imply workflow approval.
            values = con.execute('''select a.slot,p.full_name,p.position from psms_signatories.assignments a
                join psms_signatories.people p on p.id=a.person_id where p.active and p.effective_from<=current_date
                and (p.effective_to is null or p.effective_to>=current_date)''').fetchall()
            identities = {v['slot']: {'name': v['full_name'], 'position': v['position']} for v in values}
            notice = 'Unsigned draft. Configured identities are previews only; no procurement approval or signature is implied.'
            captured_at = None
        sig = {slot: (identities.get(slot, {}).get('name') or 'Not configured — unsigned',
                      identities.get(slot, {}).get('position') or '') for slot in slots}
        return {'sig': sig, 'signatory_notice': notice, 'signatory_captured_at': captured_at}
    finally:
        con.rollback()
        con.close()
