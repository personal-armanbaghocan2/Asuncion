"""Session-authorized account writes, independent of procurement activation.

Uses existing database privileges only; never installs grants or schema objects.
All changes and their audit entry share one transaction. No account is deleted.
"""
import re
import uuid
from functools import lru_cache
from flask import abort, current_app
from account_profiles import connection


PASSWORD_GUIDANCE='Use 12 to 1024 characters with at least three character types (uppercase, lowercase, numbers, symbols), or a passphrase of at least 16 characters excluding outer spaces, three distinct words and 12 letters or numbers.'
PASSWORD_SHORT_GUIDANCE='12+ characters with 3 types, or a 16+ character passphrase (3 distinct words and 12 letters/numbers).'


@lru_cache(maxsize=1)
def password_client_policy():
    """Public character rules from this Python runtime; never password values.

    Browser Unicode categories, whitespace and lowercasing differ from Python's
    isdigit/isalnum/split/casefold. Send compact ranges so employee forms agree
    with the existing backend even for international characters/passphrases.
    Computed only on first rendering of the employee password forms, then cached.
    """
    methods=('isupper','islower','isdigit','isalnum','isspace')
    ranges={name:[] for name in methods};fold={}
    for point in range(0x110000):
        char=chr(point)
        for name in methods:
            if getattr(char,name)():
                rows=ranges[name]
                if rows and rows[-1][1]+1==point:rows[-1][1]=point
                else:rows.append([point,point])
        if char.casefold()!=char:fold[char]=char.casefold()
    return dict(ranges=ranges,casefold=fold)


def password_issue(value):
    """Policy for NEW passwords only. Never normalize or rewrite credentials."""
    if (not isinstance(value,str) or not 12<=len(value)<=1024
            or any(ord(char)<32 or ord(char)==127 for char in value)):
        return PASSWORD_GUIDANCE
    classes=sum((any(c.isupper() for c in value),any(c.islower() for c in value),
                 any(c.isdigit() for c in value),any(not c.isalnum() and not c.isspace() for c in value)))
    words=value.split()
    phrase=(len(value.strip())>=16 and len({word.casefold() for word in words})>=3
            and sum(char.isalnum() for char in value)>=12)
    if classes<3 and not phrase:return PASSWORD_GUIDANCE
    return None


def creation_receipt(api, username):
    """Fresh, read-only Admin/session check after an uncertain create response.

    Finding the username is not proof this request created it or audited it.
    Never retry the insert or report account-creation success from this read.
    """
    from flask import session
    import server_sessions_v0_1_0 as sessions
    with connection(api) as (con,uid):
        actor=con.execute('select role,status from users where id=?',(uid,)).fetchone()
        token=con.execute('select user_id from auth_sessions where user_id=? and token_hash=? and revoked_at is null and expires_at>clock_timestamp()',
                          (uid,sessions.token_hash(session.get(sessions.COOKIE_KEY)))).fetchone()
        if not actor or actor['role']!='Admin' or actor['status']!='Active' or not token:abort(403)
        return bool(con.execute('select id from users where username=?',(username,)).fetchone())


def failure_response(error, operation, *, api=None, username=None):
    """Expose fixed diagnostics, never driver text, hashes, tokens or account data."""
    from flask import Response, render_template, url_for
    state = ''
    cause = error
    for _ in range(8):
        code = getattr(cause, 'sqlstate', None)
        if isinstance(code, str) and re.fullmatch(r'[0-9A-Z]{5}', code):
            state = code
            break
        cause = getattr(cause, '__context__', None)
        if cause is None:
            break
    diagnostic = {'42501': 'ACCOUNT_PERMISSION_UNAVAILABLE',
        '42883': 'ACCOUNT_CONTRACT_UNAVAILABLE', '42P01': 'ACCOUNT_CONTRACT_UNAVAILABLE',
        '3F000': 'ACCOUNT_CONTRACT_UNAVAILABLE',
        '42703': 'ACCOUNT_CONTRACT_UNAVAILABLE', '23505': 'ACCOUNT_UNIQUE_CONFLICT',
        '23502': 'ACCOUNT_CONSTRAINT_REJECTED', '23514': 'ACCOUNT_CONSTRAINT_REJECTED',
        '40P01': 'ACCOUNT_RETRY_REQUIRED', '55P03': 'ACCOUNT_RETRY_REQUIRED'}.get(state,
        'ACCOUNT_WRITE_UNCONFIRMED')
    reference = uuid.uuid4().hex[:16]
    # Log only catalog identifiers and SQLSTATE, never driver messages/details:
    # those can include usernames, credential hashes or complete failed rows.
    diag=getattr(cause,'diag',None)
    def identifier(name):
        value=getattr(diag,name,None)
        return value if isinstance(value,str) and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,62}',value) else 'unavailable'
    current_app.logger.warning('Account operation unavailable operation=%s diagnostic=%s sqlstate=%s schema=%s table=%s constraint=%s reference=%s',
                               operation,diagnostic,state or 'unavailable',identifier('schema_name'),
                               identifier('table_name'),identifier('constraint_name'),reference)
    status=409 if state == '23505' else 503
    receipt='UNAVAILABLE';message='Account change could not be confirmed.'
    if operation=='create' and api is not None and isinstance(username,str):
        try:receipt='RECORDED' if creation_receipt(api,username) else 'NOT_FOUND'
        except Exception:pass  # Original diagnostic remains visible; never auto-retry.
        message+=(' An account with this username is recorded; inspect it before any retry.' if receipt=='RECORDED' else
                  ' A fresh read found no matching username. The database rejected a unique-value constraint; this does not identify a username conflict. Inspect the failure reference before retrying.' if receipt=='NOT_FOUND' and state=='23505' else
                  ' A fresh read found no matching username; an uncertain commit is not resolved by this response. Reopen the account list before retrying.' if receipt=='NOT_FOUND' else
                  ' The saved account state could not be read safely; do not repeat the submission until it is checked.')
    response = Response(render_template('operation_error.html',active='users',title='User Accounts & Roles',
        message=message+' Reopen User Accounts & Roles before retrying. Reference: '+reference,
        status=status,back_url=url_for('users',**({'q':username} if operation=='create' and isinstance(username,str) else {})),back_label='Inspect User Accounts & Roles'),
        status=status,mimetype='text/html')
    response.headers.update({'Cache-Control': 'private, no-store',
        'X-PSMS-Diagnostic': diagnostic, 'X-PSMS-Reference': reference})
    if operation=='create':response.headers['X-PSMS-Account-Receipt']=receipt
    return response


def create(api, username, password_hash, role):
    if (role not in ('Staff', 'Inspector') or not 3 <= len(username) <= 80
            or any(ord(c) < 32 for c in username)):
        abort(400, description='Use a username of 3 to 80 characters and a Staff or Inspector role.')
    with connection(api, write=True) as (con, uid):
        actor = con.execute('select role from users where id=?', (uid,)).fetchone()
        if not actor or actor['role'] != 'Admin':
            abort(403)
        if con.execute('select id from users where username=?', (username,)).fetchone():
            abort(409, description='This username already exists. Reopen User Accounts & Roles.')
        result = con.execute('select psms_accounts.create_user_v1(?,?,?) as id',
                             (username, password_hash, role)).fetchone()
        if not result or type(result['id']) is not int or result['id'] <= 0:
            raise RuntimeError('Account insertion was not confirmed.')
        saved = con.execute('select id,username,role,status from users where id=?', (result['id'],)).fetchone()
        if not saved or saved['username'] != username or saved['role'] != role or saved['status'] != 'Active':
            raise RuntimeError('Saved account could not be confirmed.')


def deactivate(api, target_id, reason):
    """Recheck actor, lock target, deactivate/revoke/audit atomically.

    The existing password/status trigger MUST revoke every target session. Its
    readiness contract and the result are checked; a failure rolls back status.
    Reactivation and Admin account changes are intentionally not exposed here.
    """
    if type(target_id) is not int or target_id <= 0 or not 10 <= len(reason) <= 500:
        abort(400, description='Select an employee and document the reason (10 to 500 characters).')
    if any(ord(c) < 32 and c not in '\n\r\t' for c in reason):
        abort(400)
    with connection(api, write=True) as (con, uid):
        actor = con.execute('select role from users where id=?', (uid,)).fetchone()
        if not actor or actor['role'] != 'Admin':
            abort(403)
        if target_id == uid:
            abort(403, description='You cannot deactivate your own account.')
        target = con.execute('select id,username,role,status from users where id=? for update',
                             (target_id,)).fetchone()
        if not target:
            abort(404)
        if target['role'] not in ('Staff', 'Inspector'):
            abort(403, description='The designated administrator cannot be deactivated.')
        if target['status'] != 'Active':
            abort(409, description='This account is already inactive. No change was made.')
        changed = con.execute('select psms_accounts.deactivate_user_v1(?,?) as changed',
                              (target_id, reason)).fetchone()
        saved = con.execute('select status from users where id=?', (target_id,)).fetchone()
        pending = con.execute('select exists(select 1 from public.auth_sessions where user_id=? and revoked_at is null) as pending',
                              (target_id,)).fetchone()
        if not changed or changed['changed'] is not True or not saved or saved['status'] != 'Inactive' or not pending or pending['pending']:
            raise RuntimeError('Account deactivation and session revocation were not confirmed.')
