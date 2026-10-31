"""One explicit PPMP creation path with transaction-scoped retry protection."""
import hashlib
import json
import os
import re
import secrets

from flask import abort, current_app, flash, redirect, request, session, url_for
from itsdangerous import BadData, URLSafeTimedSerializer
from psycopg.conninfo import conninfo_to_dict
import durable_ocr_v0_1_0 as durable
import server_sessions_v0_1_0 as sessions


def _signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='ppmp-creation-v1')


def creation_token(ptype):
    return _signer().dumps(dict(uid=durable._uid(), category=ptype,
        session=sessions.token_hash(session.get(sessions.COOKIE_KEY)), nonce=secrets.token_hex(24)))


def source_signature(row):
    """Bind confirmation to every saved source value, not the legacy dedup subset."""
    values={key:value for key,value in row.items() if key != '_creation_signature'}
    return hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',', ':'),default=str).encode()).hexdigest()


def repeat_sources_enabled(api, connection=None):
    """Enable intentional repeats only on a named, verified disposable cluster.

    The isolated runner must supply its random cluster identity. A test-mode
    environment variable alone never authorizes using operational data.
    """
    if os.environ.get('PSMS_ALLOW_REPEAT_PPMP_SOURCE_TESTS') != '1':
        return False
    from runtime_configuration import environment_mode
    expected = os.environ.get('PSMS_PPMP_TEST_CLUSTER', '')
    if environment_mode() != 'test' or not re.fullmatch(r'signatories_[0-9a-f]{32}', expected):
        raise RuntimeError('Repeat-source testing requires the owned disposable PostgreSQL harness.')
    settings = conninfo_to_dict(api['DATABASE_URL'])
    loopback = ('127.0.0.1', 'localhost', '::1')
    port = settings.get('port', '')
    if (settings.get('host') not in loopback or settings.get('dbname') != 'signatories_synthetic' or
            not port.isdigit() or not 1024 <= int(port) <= 65535 or int(port) in (5432, 54322) or
            settings.get('hostaddr', os.environ.get('PGHOSTADDR', '')) not in ('', '127.0.0.1', '::1') or
            settings.get('service') or os.environ.get('PGSERVICE')):
        raise RuntimeError('Repeat-source testing requires an isolated loopback database target.')
    owned = connection is None
    con = connection or api['conn']()
    try:
        identity = con.execute("select current_database() db,current_setting('cluster_name') cluster").fetchone()
        if not identity or identity['db'] != 'signatories_synthetic' or identity['cluster'] != expected:
            raise RuntimeError('Repeat-source database identity verification failed.')
    finally:
        if owned:
            con.rollback()
            con.close()
    return True


def create(api, ptype):
    if ptype not in ('DBM', 'NON-DBM', 'LIB'):
        abort(404)
    if request.form.get('confirm_reviewed') != 'yes':
        abort(400, description='Confirm the selected saved values before creating official PPMP records.')
    try:
        token = _signer().loads(request.form.get('creation_token', ''), max_age=3600)
    except BadData:
        abort(400, description='Creation form expired or is invalid. Reopen the PPMP category.')
    if (not isinstance(token, dict) or token.get('uid') != durable._uid() or token.get('category') != ptype or
            token.get('session') != sessions.token_hash(session.get(sessions.COOKIE_KEY)) or
            not re.fullmatch(r'[0-9a-f]{48}', str(token.get('nonce', '')))):
        abort(403)
    excel_ids, ocr_ids = request.form.getlist('rows'), request.form.getlist('ocr_rows')
    if not 1 <= len(excel_ids)+len(ocr_ids) <= 200 or len(set(excel_ids)) != len(excel_ids) or len(set(ocr_ids)) != len(ocr_ids):
        abort(400, description='Select 1 to 200 distinct eligible items.')
    fingerprint = hashlib.sha256(json.dumps([ptype, sorted(excel_ids), sorted(ocr_ids),
        [(code, request.form.get('ocr_revision_'+code)) for code in sorted(ocr_ids)],
        [(code, request.form.get('source_signature_'+code)) for code in sorted(excel_ids)]], separators=(',', ':')).encode()).hexdigest()
    with durable.transaction(api) as (con, uid):
        con.execute('select pg_advisory_xact_lock(hashtextextended(?,0))', ('ppmp-create:'+token['nonce'],))
        previous = con.execute("select details from audit_trail where action='Confirmed PPMP Creation' and module='PPMP' and ref=?", (token['nonce'],)).fetchone()
        if previous:
            if previous['details'] != fingerprint:
                abort(409, description='This creation request already completed with different selections. Reopen the category.')
            flash('This PPMP creation request already completed. No duplicate records were created.')
            return redirect(url_for('ppmp_home'))
        # Serialize workbook-source consumption across sessions and instances.
        con.execute('select pg_advisory_xact_lock(hashtextextended(?,0))', ('ppmp-excel:'+ptype,))
        allow_repeat = repeat_sources_enabled(api, con)
        rows = api['read_excel'](ptype) if excel_ids else []
        selected = {str(row.get('_row')): row for row in rows}
        if any(code not in selected for code in excel_ids):
            abort(409, description='A selected source item is no longer available. Reopen the category.')
        if any(not request.form.get('source_signature_'+code) or request.form.get('source_signature_'+code) != source_signature(selected[code]) for code in excel_ids):
            abort(409, description='A selected workbook item changed. Review its current saved values and confirm again.')
        used_rows, used_signatures = set(), set()
        if excel_ids and not allow_repeat:
            for record in con.execute('select items from ppmp where type=?', (ptype,)).fetchall():
                for item in api['parse_items'](record['items']):
                    used_rows.add(str(item.get('_row')))
                    if item.get('_excel_signature'):
                        used_signatures.add(str(item['_excel_signature']))
            if any(code in used_rows or selected[code].get('_excel_signature') in used_signatures for code in excel_ids):
                abort(409, description='A selected source item was already used. No records were created.')
        created = durable.create_reviewed_ocr(api, ptype, con, uid, ocr_ids)
        for code in sorted(excel_ids):
            item = dict(selected[code])
            errors = api['planning_item_errors']([item])
            if errors:
                abort(422, description='Source item '+code+': '+' '.join(errors))
            year = api['current_year']()
            no = api['next_document_no'](f'PPMP-{ptype}', f'PPMP-{ptype.replace("-", "")}', 'ppmp', year=year, connection=con)
            items = api['assign_item_identifiers']([item], '_PPMP Item ID', no)
            total = api['items_total'](items)
            if total is None:
                abort(422, description='Source item '+code+' has no valid budget.')
            con.execute('''insert into ppmp(no,type,office,year,status,created_at,items,total,source_dataset_record_id)
                values(?,?,?,?,'Draft',?,?,?,?)''', (no, ptype, str(item.get('_Dataset Requesting Office') or 'General Services Office'),
                year, api['now'](), durable.payload(items), total, int(api['to_float'](item.get('_Dataset Record ID'))) or None))
            api['log']('Created PPMP', 'PPMP', no, 'Reviewed workbook source item '+code+
                       ('; intentional isolated repeat-source test' if allow_repeat else ''), connection=con)
            created.append(no)
        api['log']('Confirmed PPMP Creation', 'PPMP', token['nonce'], fingerprint, connection=con)
    flash('Official PPMP draft records created: '+', '.join(created)+'. Procurement approval remains separate.')
    return redirect(url_for('ppmp_home'))
