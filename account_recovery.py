"""Opt-in verified Gmail recovery. No startup SQL, retries or secret logging."""
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import re
import secrets
from urllib import request as http
from urllib.error import HTTPError, URLError

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, session, url_for, current_app
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.exceptions import HTTPException, ServiceUnavailable

import account_management
import account_profiles

TOKEN=re.compile(r'^[0-9a-f]{64}$')
ORIGIN='https://asuncion-one.vercel.app'
bp=Blueprint('account_recovery',__name__)
def api():return current_app.extensions['asuncion_recovery_api']


class DeliveryFailure(RuntimeError):
    def __init__(self,ambiguous=False):self.ambiguous=ambiguous


def enabled():return os.environ.get('PSMS_EMAIL_RECOVERY_ENABLED')=='1'


def normalize_email(value):
    if not isinstance(value,str):raise ValueError('Use a valid Gmail address.')
    value=value.strip().lower()
    if (len(value)>254 or not re.fullmatch(r'[a-z0-9][a-z0-9.+_-]{0,63}@gmail[.]com',value)
            or '..' in value.split('@')[0] or value.split('@')[0].endswith('.')):
        raise ValueError('Use a valid @gmail.com address.')
    return value


def configuration():
    if not enabled():raise ServiceUnavailable('Verified email recovery is not enabled.')
    key=os.environ.get('BREVO_API_KEY','')
    sender=os.environ.get('BREVO_SENDER_EMAIL','').strip()
    name=os.environ.get('BREVO_SENDER_NAME','Asuncion Supply Management').strip()
    if (not key or len(key)>512 or any(ord(c)<33 or ord(c)>126 for c in key)
        or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[A-Za-z]{2,}',sender)
        or not 1<=len(name)<=100 or any(ord(c)<32 for c in name)
        or os.environ.get('PSMS_RECOVERY_ORIGIN')!=ORIGIN):
        raise ServiceUnavailable('Email recovery configuration is unavailable.')
    return key,sender,name


class NoRedirect(http.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def send_email(email,purpose,token):
    """One bounded TLS API call. Acceptance is NOT evidence of delivery."""
    key,sender,name=configuration()
    if purpose not in ('verify','reset') or not TOKEN.fullmatch(token):raise ValueError('Invalid email request.')
    label='Verify your recovery email' if purpose=='verify' else 'Reset your password'
    # Fragment stays out of server URL/access logs. The page removes it before
    # making an explicit, CSRF-protected POST; GET/link scanners consume nothing.
    link=ORIGIN+'/account-recovery#purpose='+purpose+'&token='+token
    data=json.dumps(dict(sender=dict(email=sender,name=name),to=[dict(email=email)],
        subject='Asuncion: '+label,textContent=label+' for your Asuncion account.\n'+link+
        '\nThis single-use link expires shortly. If you did not expect this email, ignore it.')).encode()
    req=http.Request('https://api.brevo.com/v3/smtp/email',data=data,method='POST',
        headers={'api-key':key,'Content-Type':'application/json','Accept':'application/json'})
    try:
        with http.build_opener(NoRedirect()).open(req,timeout=8) as response:
            payload=json.loads(response.read(8193))
            message=payload.get('messageId') if isinstance(payload,dict) else None
            if response.status!=201 or not isinstance(message,str) or not re.fullmatch(r'[\x21-\x7e]{1,512}',message):
                raise DeliveryFailure(True)
            return message
    except DeliveryFailure:raise
    except HTTPError as error:
        ambiguous=error.code>=500;error.close()
        raise DeliveryFailure(ambiguous) from None
    except (URLError,OSError,ValueError,TypeError):raise DeliveryFailure(True) from None


def state(api,ids):
    if not enabled():return {}
    with account_profiles.connection(api) as (con,uid):
        row=con.execute('select psms_accounts.recovery_state_v1(?::bigint[]) as state',(list(ids),)).fetchone()
        return row['state'] or {}


def change_own_password(api,expected_hash,new_hash):
    """Opt-in private RPC; never fall back to a broader write if it fails."""
    import server_sessions_v0_1_0 as sessions
    try:
        with account_profiles.connection(api,write=True) as (con,uid):
            con.execute('select psms_accounts.change_own_password_v1(?,?)',(expected_hash,new_hash))
    except HTTPException as error:
        if error.code in (401,403):raise sessions.Rejected('Account/session changed.') from None
        raise sessions._failure('password_update',error) from None
    except Exception as error:raise sessions._failure('password_update',error) from None


def save_email(api,target,email,current_password):
    email=normalize_email(email)
    if not isinstance(current_password,str) or len(current_password)>1024:abort(400)
    expected=None
    if target==session.get('user_id'):
        row=api['q']('select id,username,password from users where id=?',(target,),one=True)
        try:valid=bool(row) and check_password_hash(str(row['password']),current_password)
        except Exception:valid=False
        if not valid:abort(400,description='The current password is required to change your recovery email.')
        expected=str(row['password'])
    with account_profiles.connection(api,write=True) as (con,uid):
        con.execute('select psms_accounts.recovery_email_set_v1(?,?,?)',(target,email,expected))


def issue(api,target,purpose):
    configuration()  # Fail before writing a token when the provider is unconfigured.
    token=secrets.token_hex(32);digest=hashlib.sha256(token.encode('ascii')).hexdigest()
    with account_profiles.connection(api,write=True) as (con,uid):
        row=con.execute('select psms_accounts.recovery_issue_v1(?,?,?) as ticket',
                        (target,purpose,digest)).fetchone()
        ticket=row['ticket']
    status='accepted';provider_id=''
    try:provider_id=send_email(ticket['email'],purpose,token)
    except DeliveryFailure as error:status='unknown' if error.ambiguous else 'failed'
    finally:token=None
    # Separate from the provider operation. Never resend after ambiguous DB/API
    # outcomes. Only accepted AND committed tickets may be redeemed.
    with account_profiles.connection(api,write=True) as (con,uid):
        con.execute('select psms_accounts.recovery_delivery_v1(?,?,?)',(digest,status,provider_id))
    return status


def protected_failure():
    reference=secrets.token_hex(8)
    current_app.logger.warning('Account recovery unavailable reference=%s',reference)
    return Response('Email recovery could not be confirmed. Do not repeat immediately. Reference: '+reference,
                    status=503,headers={'Cache-Control':'no-store'})


@bp.route('/account/recovery-email',methods=['POST'])
def manage():
    from account_otp import enabled as otp_enabled
    if otp_enabled():abort(409,description='Use the registered Gmail OTP workflow. No legacy change attempted.')
    if not enabled():abort(503,description='Verified email recovery is not enabled.')
    allowed={'csrf_token','target_id','action','email','current_password'}
    if (set(request.form)-allowed or request.files
            or any(len(request.form.getlist(k))!=1 for k in request.form)):
        abort(400)
    target=request.form.get('target_id','')
    if not re.fullmatch(r'[1-9][0-9]{0,17}',target):abort(400)
    target=int(target)
    # Database RPC rechecks roles and sessions. This prevents unauthorized role
    # requests from reaching email or employee metadata even in local fixtures.
    if target!=session.get('user_id') and session.get('role')!='Admin':abort(403)
    action=request.form.get('action','')
    if action=='reset' and (session.get('role')!='Admin' or target==session.get('user_id')):abort(403)
    try:
        if action=='save':
            save_email(api(),target,request.form.get('email',''),request.form.get('current_password',''))
            flash('Email saved as unverified. Send its verification link before using recovery.','success')
        elif action in ('verify','reset'):
            status=issue(api(),target,action)
            flash({'accepted':'Email accepted by Brevo; delivery is not confirmed.',
                   'failed':'Email provider rejected the request. No password changed.',
                   'unknown':'Email acceptance is uncertain. Do not repeat immediately; no password changed.'}[status],
                  'success' if status=='accepted' else 'error')
        else:abort(400)
    except ValueError as error:abort(400,description=str(error))
    except HTTPException as error:
        if error.code!=503:raise
        return protected_failure()
    except Exception:return protected_failure()
    return redirect(url_for('account') if target==session.get('user_id') else url_for('users'))


@contextmanager
def public_transaction():
    con=None
    try:
        con=api()['conn']()
        con.execute("SET LOCAL statement_timeout='5s'")
        con.execute("SET LOCAL lock_timeout='5s'")
        yield con
        con.commit()
    except Exception:
        if con is not None:
            try:con.rollback()
            except Exception:pass
        raise
    finally:
        if con is not None:con.close()


def consume(token,purpose,password):
    if purpose not in ('verify','reset') or not TOKEN.fullmatch(token):return False
    # Persisted peer throttling occurs before expensive password hashing.
    peer=hmac.new(current_app.secret_key.encode() if isinstance(current_app.secret_key,str) else current_app.secret_key,
        ('recovery-peer:'+str(request.remote_addr)).encode(),hashlib.sha256).hexdigest()
    with public_transaction() as con:
        row=con.execute('select psms_accounts.recovery_throttle_v1(?) as allowed',(peer,)).fetchone()
        if not row or row['allowed'] is not True:abort(429)
    hashed=None
    if purpose=='reset':
        issue=account_management.password_issue(password)
        if issue:abort(400,description=issue)
        hashed=generate_password_hash(password)
    digest=hashlib.sha256(token.encode('ascii')).hexdigest()
    with public_transaction() as con:
        row=con.execute('select psms_accounts.recovery_consume_v1(?,?,?) as changed',(digest,purpose,hashed)).fetchone()
        return bool(row and row['changed'] is True)


@bp.route('/account-recovery',methods=['GET','POST'])
def redeem():
    from account_otp import enabled as otp_enabled
    if otp_enabled():abort(409,description='Use the Gmail OTP workflow. No legacy reset attempted.')
    if not enabled():abort(503,description='Verified email recovery is not enabled.')
    if request.method=='POST':
        if (set(request.form)-{'csrf_token','token','purpose','new_password','confirm_password'}
                or request.files or any(len(request.form.getlist(k))!=1 for k in request.form)):
            abort(400)
        purpose=request.form.get('purpose','')
        if purpose=='reset' and request.form.get('new_password','')!=request.form.get('confirm_password',''):abort(400)
        try:changed=consume(request.form.get('token',''),purpose,request.form.get('new_password',''))
        except HTTPException:raise
        except Exception:return protected_failure()
        if not changed:return Response('This link is invalid, expired, already used or no longer applicable.',status=400,
                                        headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
        session.clear()
        flash('Email verified.' if purpose=='verify' else 'Password reset. Sign in again; previous sessions were revoked.','success')
        return redirect(url_for('login'))
    response=current_app.make_response(render_template('account_recovery.html',
        password_short_guidance=account_management.PASSWORD_SHORT_GUIDANCE,
        password_client_policy=account_management.password_client_policy))
    response.headers.update({'Cache-Control':'no-store','Referrer-Policy':'no-referrer',
        'X-Robots-Tag':'noindex, nofollow','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})
    return response


def init(app,api):
    app.extensions['asuncion_recovery_api']=api
    app.register_blueprint(bp)
