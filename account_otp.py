"""Opt-in OTP replacement. Existing recovery remains unchanged while disabled."""
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from functools import wraps
from urllib import request as http
from urllib.error import HTTPError,URLError

from flask import Blueprint,Response,abort,current_app,flash,redirect,render_template,request,session,url_for
from werkzeug.security import check_password_hash,generate_password_hash
import account_management
import account_profiles
import account_recovery

bp=Blueprint('account_otp',__name__)
GENERIC='If the supplied details are eligible, a code was requested. It expires after 180 seconds. Do not immediately resend.'
ID=re.compile(r'^[0-9a-f]{64}$')


def enabled():return os.environ.get('PSMS_ACCOUNT_OTP_ENABLED')=='1'
def api():return current_app.extensions['asuncion_otp_api']
def mac(value):
    secret=current_app.secret_key
    return hmac.new(secret.encode() if isinstance(secret,str) else secret,value.encode(),hashlib.sha256).hexdigest()
def proof(challenge,code):return mac('asuncion-otp-v1:'+challenge+':'+code)


def finish_public_request(started):
    # Mask ordinary provider-versus-no-recipient latency without changing token
    # lifetime or assuming a background thread survives a serverless response.
    # Infrastructure timeouts can exceed this floor; deployment latency review
    # must measure those paths before claiming timing-resistant enumeration.
    time.sleep(max(0,9-(time.monotonic()-started)))


@contextmanager
def transaction():
    con=None
    try:
        con=api()['conn']();con.execute("SET LOCAL statement_timeout='5s'");con.execute("SET LOCAL lock_timeout='5s'")
        yield con
        con.commit()  # False consumes/limits MUST commit; never abort on wrong OTP.
    except Exception:
        if con is not None:
            try:con.rollback()
            except Exception:pass
        raise
    finally:
        if con is not None:con.close()


def rpc(name,params,authenticated=False):
    signatures={'issue':'?,?,?,?,?,?,?,?','delivery':'?,?,?','consume':'?,?,?,?,?','notice':'?,?',
                'state':'?::bigint[]','email_replace':'?,?,?,?'}
    context=account_profiles.connection(api(),write=name!='state') if authenticated else transaction()
    with context as connection:
        con=connection[0] if authenticated else connection
        row=con.execute('select psms_accounts.otp_'+name+'_v1('+signatures[name]+') as result',params).fetchone()
        return row['result'] if row else None


def state(application,ids):
    if not enabled():return {}
    with account_profiles.connection(application) as (con,uid):
        row=con.execute('select psms_accounts.otp_state_v1(?::bigint[]) as result',(list(ids),)).fetchone()
        return (row or {}).get('result') or {}


def send_message(email,subject,text):
    """One server-side TLS call; no retries, response-body or secret logging."""
    key=os.environ.get('BREVO_API_KEY','');sender=os.environ.get('BREVO_SENDER_EMAIL','')
    if (not enabled() or not key or len(key)>512 or any(ord(c)<33 or ord(c)>126 for c in key)
        or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[A-Za-z]{2,}',sender)):
        raise account_recovery.DeliveryFailure(False)
    payload=dict(sender=dict(email=sender,name='Asuncion Supply Management'),to=[dict(email=email)],subject=subject,textContent=text)
    req=http.Request('https://api.brevo.com/v3/smtp/email',data=json.dumps(payload).encode(),method='POST',
        headers={'api-key':key,'Content-Type':'application/json','Accept':'application/json'})
    try:
        with http.build_opener(account_recovery.NoRedirect()).open(req,timeout=8) as response:
            data=response.read(8193)
            if response.status!=201 or len(data)>8192:raise account_recovery.DeliveryFailure(True)
            payload=json.loads(data)
            message=payload.get('messageId') if isinstance(payload,dict) else None
            if not isinstance(message,str) or not re.fullmatch(r'[\x21-\x7e]{1,512}',message):raise account_recovery.DeliveryFailure(True)
            return message
    except account_recovery.DeliveryFailure:raise
    except HTTPError as error:
        ambiguous=error.code>=500;error.close();raise account_recovery.DeliveryFailure(ambiguous) from None
    except (URLError,OSError,ValueError,TypeError):raise account_recovery.DeliveryFailure(True) from None


def issue(username,email,kind,expected_hash=None,authenticated=False):
    challenge=secrets.token_hex(32);code=f'{secrets.randbelow(1000000):06d}'
    peer=mac('otp-peer:'+str(request.remote_addr))
    identity=mac('otp-identity:'+username+'\0'+email)
    recipient=rpc('issue',(username,email,kind,challenge,proof(challenge,code),peer,identity,expected_hash),authenticated)
    # The persistent identity budget contains only this server-keyed HMAC.
    if recipient:
        status='accepted';message=''
        try:message=send_message(recipient['email'],'Asuncion: '+('Gmail activation code' if kind=='activate' else 'verification code'),
            'Your one-time code is '+code+'. It expires exactly 180 seconds after issuance and permits two incorrect attempts. Never share it. No password is included.')
        except account_recovery.DeliveryFailure as error:status='unknown' if error.ambiguous else 'failed'
        finally:code=None
        rpc('delivery',(challenge,status,message),authenticated)
    return challenge


def finish(challenge,kind,code,password,authenticated=False):
    if not ID.fullmatch(challenge or ''):return False
    if kind!='verify':
        error=account_management.password_issue(password)
        if error:abort(400,description=error)
    new_hash=generate_password_hash(password) if kind!='verify' else None
    result=rpc('consume',(challenge,kind,proof(challenge,code[:32]),new_hash,mac('otp-peer:'+str(request.remote_addr))),authenticated)
    if not result or result.get('success') is not True:return False
    if kind!='verify':
        status='accepted'
        try:send_message(result['email'],'Asuncion: password changed',
            'Your Asuncion system password was changed. Previous sessions were revoked. If this was not you, contact the Administrator immediately.')
        except account_recovery.DeliveryFailure as error:status='unknown' if error.ambiguous else 'failed'
        # Commit already succeeded. Never repeat the password mutation if the
        # notification/audit write fails; return a truthful success message.
        try:rpc('notice',(challenge,status),False)
        except Exception:current_app.logger.warning('OTP password changed; notification audit unavailable')
    return True


def fields(allowed):
    if request.files or set(request.form)-set(allowed)-{'csrf_token'} or any(len(request.form.getlist(k))!=1 for k in request.form):abort(400)
    if any(len(value)>1024 for value in request.form.values()):abort(400)


def protected(template,**values):
    response=current_app.make_response(render_template(template,**values))
    response.headers.update({'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Robots-Tag':'noindex, nofollow',
        'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})
    return response


def shield(handler):
    @wraps(handler)
    def safe(*args,**kwargs):
        try:return handler(*args,**kwargs)
        except Exception as error:
            from werkzeug.exceptions import HTTPException
            if isinstance(error,HTTPException):raise
            return account_recovery.protected_failure()
    return safe


@bp.route('/password-otp',methods=['GET','POST'])
def public():
    if not enabled():abort(503,description='OTP recovery is not enabled.')
    kind=request.args.get('mode','forgot')
    if kind not in ('forgot','activate','verify'):abort(400)
    if session.get('logged_in') is True and kind!='verify':
        return redirect(url_for('account'))
    if request.method=='POST':
        fields(('action','username','email','code','new_password','confirm_password'))
        action=request.form.get('action')
        if action=='request':
            started=time.monotonic()
            username=request.form.get('username','').strip();email=request.form.get('email','').strip().lower()
            if not 3<=len(username)<=80:abort(400)
            try:email=account_recovery.normalize_email(email)
            except ValueError:abort(400,description='Use a valid Gmail address.')
            try:challenge=issue(username,email,kind)
            except Exception:
                # Same public response for unknown accounts, throttles, provider
                # failures and ambiguous state. Never auto-retry issuance.
                challenge=secrets.token_hex(32)
                current_app.logger.warning('OTP request unavailable; no automatic retry')
            finish_public_request(started)
            session['asuncion_otp_public']={'id':challenge,'kind':kind};flash(GENERIC,'info')
        elif action=='complete':
            pending=session.get('asuncion_otp_public') or {}
            if pending.get('kind')!=kind:abort(400)
            password=request.form.get('new_password','')
            if kind!='verify' and password!=request.form.get('confirm_password'):abort(400,description='Passwords must match.')
            try:changed=finish(pending.get('id',''),kind,request.form.get('code',''),password)
            except Exception as error:
                from werkzeug.exceptions import HTTPException
                if isinstance(error,HTTPException):raise
                current_app.logger.warning('OTP completion unavailable; inspect state before retrying')
                return Response('The result could not be confirmed. Do not repeat immediately.',503,headers={'Cache-Control':'no-store'})
            if changed:
                if kind!='verify':session.clear()
                else:session.pop('asuncion_otp_public',None)
                flash('Gmail verified.' if kind=='verify' else 'Password saved. Sign in again.','success')
                return redirect(url_for('account') if kind=='verify' and session.get('logged_in') else url_for('login'))
            flash('The code is invalid, expired, exhausted or no longer applicable.','error')
        else:abort(400)
    return protected('account_otp.html',kind=kind,pending=(session.get('asuncion_otp_public') or {}).get('kind')==kind)


@bp.route('/account/password-otp',methods=['POST'])
@shield
def change():
    if not enabled():abort(503)
    fields(('action','current_password','code','new_password','confirm_password'))
    uid=session.get('user_id')
    if type(uid) is not int:abort(403)
    user=api()['q']('select id,username,password from users where id=?',(uid,),one=True)
    if not user:abort(403)
    action=request.form.get('action')
    if action=='request':
        try:valid=check_password_hash(str(user['password']),request.form.get('current_password',''))
        except Exception:valid=False
        if not valid:flash('Current password is incorrect.','error');return redirect(url_for('account'))
        challenge=issue(user['username'],'','change',str(user['password']),True)
        session['asuncion_otp_change']=challenge;flash(GENERIC,'info')
    elif action=='complete':
        password=request.form.get('new_password','')
        if password!=request.form.get('confirm_password'):abort(400,description='Passwords must match.')
        if finish(session.get('asuncion_otp_change',''),'change',request.form.get('code',''),password,True):
            session.clear();flash('Password saved. Previous sessions revoked. Sign in again.','success')
            return redirect(url_for('login'))
        flash('The code is invalid, expired, exhausted or no longer applicable.','error')
    else:abort(400)
    return redirect(url_for('account')+'#change-password')


@bp.route('/admin/otp-email',methods=['POST'])
@shield
def admin_email():
    if not enabled() or session.get('role')!='Admin':abort(403)
    fields(('target_id','email','reason','current_password'))
    try:target=int(request.form.get('target_id',''))
    except ValueError:abort(400)
    try:email=account_recovery.normalize_email(request.form.get('email',''))
    except ValueError:abort(400,description='Use a valid registered Gmail.')
    user=api()['q']('select id,username,password from users where id=?',(session.get('user_id'),),one=True)
    if not user or not check_password_hash(str(user['password']),request.form.get('current_password','')):abort(403)
    rpc('email_replace',(target,email,request.form.get('reason',''),str(user['password'])),True)
    flash('Registered Gmail replaced; ownership must be verified again.','success')
    return redirect(url_for('users'))


def init(app,application):
    app.extensions['asuncion_otp_api']=application
    app.register_blueprint(bp)
