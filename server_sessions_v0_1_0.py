"""Server-enforced authentication sessions. No startup/schema/model operations.

Opaque 256-bit IDs exist only in signed cookies; PostgreSQL stores their SHA-256
digests. Registry and account state are checked read-only on each request. Every
write uses an explicit caller-supplied connection factory; never a default DSN.
"""
import hashlib
import hmac
import re
import secrets

VERSION='0.1.1-candidate'
COOKIE_KEY='_auth_sid'
TRIGGER='psms_revoke_auth_sessions_on_account_change'

_STAGES = ('connect', 'readiness', 'account_lock', 'session_insert', 'commit', 'diagnostics')
_ERROR_CODES = {'42883': 'missing_function', '42P01': 'missing_table',
                '42703': 'incompatible_columns', '42501': 'permission_denied',
                '25006': 'read_only_transaction', '23502': 'constraint_failure',
                '23503': 'constraint_failure', '23505': 'constraint_failure',
                '23514': 'constraint_failure'}
_SAFE_REASONS = {stage+':'+code for stage in _STAGES
                 for code in (*set(_ERROR_CODES.values()), 'failed')}
_SAFE_REASONS.update(('unavailable', 'readiness:contract_rejected', 'cleanup:failed'))

class Unavailable(RuntimeError):
    def __init__(self, message, *, reason='unavailable'):
        super().__init__(message)
        self.reason = reason if type(reason) is str and reason in _SAFE_REASONS else 'unavailable'
        self.cleanup_failed = False


def failure_reason(error):
    """Return an allowlisted code only, never driver text, credentials or IDs."""
    value = getattr(error, 'reason', 'unavailable')
    return value if type(value) is str and value in _SAFE_REASONS else 'unavailable'


def _failure(stage, error):
    state = getattr(error, 'sqlstate', None)
    code = _ERROR_CODES.get(state, 'failed') if type(state) is str else 'failed'
    return Unavailable('Session creation is unavailable.', reason=stage+':'+code)


def session_diagnostics(factory):
    """Explicit metadata-only check under the application's effective SQL role.

    Never called by startup or requests. Reads no accounts, password fields,
    session rows or tokens, and returns only fixed keys/booleans/failure codes.
    The caller must obtain target-specific authorization before connecting.
    """
    result = dict(checks={}, ready=False, failure=None, read_only=False,
                  accounts_read=False, database_changes=False, login_tested=False)
    con=None
    stage='connect'
    try:
        con=factory()
        con.execute('SET TRANSACTION READ ONLY')
        result['read_only']=True
        stage='diagnostics'
        row=con.execute("""select
          to_regclass('public.auth_sessions') is not null as sessions_table,
          to_regclass('public.users') is not null as users_table,
          to_regprocedure('public.psms_auth_session_permissions_ready()') is not null as readiness_function,
          to_regprocedure('public.psms_revoke_auth_sessions()') is not null as revocation_function,
          has_schema_privilege('public','USAGE') as schema_usage,
          exists(select 1 from pg_catalog.pg_trigger t
            where t.tgrelid=to_regclass('public.users')
              and t.tgname='psms_revoke_auth_sessions_on_account_change'
              and not t.tgisinternal and t.tgenabled in ('O','A')) as revocation_trigger""").fetchone()
        keys=('sessions_table','users_table','readiness_function','revocation_function','schema_usage','revocation_trigger')
        result['checks'].update({key:bool(row and row.get(key) is True) for key in keys})
        if result['checks']['sessions_table']:
            row=con.execute("""select c.relrowsecurity as registry_rls,
              has_table_privilege(c.oid,'SELECT') as registry_select,
              (select count(*)=5 from pg_catalog.pg_attribute a where a.attrelid=c.oid
                and not a.attisdropped and (a.attname,a.atttypid) in (
                  ('token_hash','text'::regtype),('user_id','bigint'::regtype),
                  ('created_at','timestamptz'::regtype),('expires_at','timestamptz'::regtype),
                  ('revoked_at','timestamptz'::regtype))) as registry_columns
              from pg_catalog.pg_class c where c.oid=to_regclass('public.auth_sessions')""").fetchone()
            result['checks'].update({key:bool(row and row.get(key) is True)
                                     for key in ('registry_rls','registry_select','registry_columns')})
            if result['checks']['registry_columns']:
                row=con.execute("""select
                  has_column_privilege('public.auth_sessions','token_hash','INSERT')
                  and has_column_privilege('public.auth_sessions','user_id','INSERT')
                  and has_column_privilege('public.auth_sessions','expires_at','INSERT') as registry_insert,
                  has_column_privilege('public.auth_sessions','revoked_at','UPDATE') as registry_revoke""").fetchone()
                result['checks'].update({key:bool(row and row.get(key) is True)
                                         for key in ('registry_insert','registry_revoke')})
        if result['checks']['users_table']:
            row=con.execute("""select has_table_privilege('public.users','SELECT') as users_select,
              has_any_column_privilege('public.users','UPDATE') as users_row_lock""").fetchone()
            result['checks'].update({key:bool(row and row.get(key) is True)
                                     for key in ('users_select','users_row_lock')})
        if not result['checks']['readiness_function']:
            result['failure']='readiness:missing_function'
        else:
            row=con.execute("""select has_function_privilege(
              'public.psms_auth_session_permissions_ready()','EXECUTE') as helper_execute""").fetchone()
            result['checks']['helper_execute']=bool(row and row.get('helper_execute') is True)
            if not result['checks']['helper_execute']:
                result['failure']='readiness:permission_denied'
            elif not result['checks']['sessions_table']:
                result['failure']='readiness:missing_table'
            elif all(result['checks'].values()):
                stage='readiness';_ready(con)
                result['ready']=True
        if not result['ready'] and result['failure'] is None:
            result['failure']='readiness:contract_rejected'
    except Unavailable as error:
        result['failure']=failure_reason(error)
    except Exception as error:
        result['failure']=failure_reason(_failure(stage,error))
    finally:
        try:_clean(con)
        except Unavailable:
            result['ready']=False
            result['cleanup_failed']=True
            result['failure']=result['failure'] or 'cleanup:failed'
    return result


class Rejected(ValueError):pass

def token_hash(token):
    if type(token) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{43}',token):return None
    return hashlib.sha256(token.encode('ascii')).hexdigest()

def _ready(con):
    # Read-only shared database contract; applies to validation as well as writes.
    # A failed capability check blocks copied cookies, not merely the action.
    row=con.execute('select public.psms_auth_session_permissions_ready() as ready').fetchone()
    if not row or row['ready'] is not True:
        raise Unavailable('Session permissions or migration are unavailable.', reason='readiness:contract_rejected')

def _clean(con):
    if con is not None:
        try:
            try:con.rollback()
            finally:con.close()
        except Exception:raise Unavailable('Session cleanup is unavailable.', reason='cleanup:failed') from None

def _authorized(account):
    return bool(account and account.get('status')=='Active' and account.get('role') in ('Admin','Staff','Inspector'))

def issue(factory,user_id,expected_password,ttl_seconds):
    if type(user_id) is not int or user_id<=0 or type(ttl_seconds) is not int or not 0<ttl_seconds<2147483647:
        raise Rejected('Invalid session request.')
    con=None
    stage='connect'
    primary_failure=None
    try:
        con=factory()
        stage='readiness';_ready(con)
        stage='account_lock'
        account=con.execute('select id,username,role,status,password from users where id=? for update',(user_id,)).fetchone()
        if (not _authorized(account) or type(expected_password) is not str
                or not hmac.compare_digest(str(account['password']),expected_password)):
            raise Rejected('Account changed during authentication.')
        token=secrets.token_urlsafe(32)
        stage='session_insert'
        con.execute("""insert into public.auth_sessions(token_hash,user_id,expires_at)
                       values(?,?,clock_timestamp()+(? * interval '1 second'))""",
                    (token_hash(token),user_id,ttl_seconds))
        stage='commit';con.commit()
        return {k:account[k] for k in ('id','username','role','status')},token
    except Rejected:raise
    except Unavailable as error:
        primary_failure=error
        raise
    except Exception as error:
        primary_failure=_failure(stage,error)
        raise primary_failure from None
    finally:
        try:_clean(con)
        except Unavailable:
            # Retain the first diagnostic when rollback/close also fails. No
            # session is returned on either failure; successful paths still
            # fail closed if cleanup cannot be confirmed.
            if primary_failure is None:raise
            primary_failure.cleanup_failed=True

def account_for_session(factory,token,user_id):
    digest=token_hash(token)
    if digest is None or type(user_id) is not int or user_id<=0:return None
    con=None
    try:
        con=factory()
        con.execute('SET TRANSACTION READ ONLY')
        _ready(con)
        return con.execute("""select u.id,u.username,u.role,u.status,u.full_name,u.display_name,u.created_at,u.last_login from public.auth_sessions s
          join public.users u on u.id=s.user_id
          where s.token_hash=? and s.user_id=? and s.revoked_at is null
            and s.expires_at>clock_timestamp() and u.status='Active'
            and u.role in ('Admin','Staff','Inspector')""",(digest,user_id)).fetchone()
    except Unavailable:raise
    except Exception:raise Unavailable('Session validation is unavailable.') from None
    finally:_clean(con)

def revoke_current(factory,token,user_id,audit=None):
    digest=token_hash(token)
    if digest is None or type(user_id) is not int or user_id<=0:raise Rejected('Invalid session.')
    con=None
    try:
        con=factory();_ready(con)
        changed=con.execute("""update public.auth_sessions set revoked_at=clock_timestamp()
          where token_hash=? and user_id=? and revoked_at is null""",(digest,user_id)).rowcount
        persisted=con.execute('select revoked_at from public.auth_sessions where token_hash=? and user_id=?',
                              (digest,user_id)).fetchone()
        # Confirm state even for an idempotent concurrent/already-revoked call.
        if changed not in (0,1) or not persisted or persisted['revoked_at'] is None:
            raise Unavailable('Session revocation could not be confirmed.')
        if audit:audit(con)
        con.commit()
    except (Rejected,Unavailable):raise
    except Exception:raise Unavailable('Session revocation is unavailable.') from None
    finally:_clean(con)

def replace_password(factory,token,user_id,expected_password,new_hash,audit=None):
    digest=token_hash(token)
    if digest is None or type(user_id) is not int or user_id<=0:raise Rejected('Invalid session.')
    con=None
    try:
        con=factory();_ready(con)
        # Serialize with login and every password/status update. After acquiring
        # this row lock, recheck both the verified credential and current SID.
        account=con.execute('select id,password,role,status from users where id=? for update',(user_id,)).fetchone()
        active=con.execute("""select user_id from public.auth_sessions where token_hash=?
             and user_id=? and revoked_at is null and expires_at>clock_timestamp() for update""",(digest,user_id)).fetchone()
        if (not active or not _authorized(account)
                or not hmac.compare_digest(str(account['password']),expected_password)):
            raise Rejected('Authentication changed; sign in again.')
        if con.execute('update users set password=? where id=?',(new_hash,user_id)).rowcount!=1:
            raise Unavailable('Password update could not be confirmed.')
        _confirmed_password(con,user_id,new_hash)
        # Reviewed migration trigger revokes ALL sessions atomically with this
        # password change, including this request and other browsers/processes.
        if audit:audit(con)
        con.commit()
    except (Rejected,Unavailable):raise
    except Exception:raise Unavailable('Password update is unavailable.') from None
    finally:_clean(con)


def _confirmed_password(con,user_id,new_hash):
    row=con.execute('select password from users where id=?',(user_id,)).fetchone()
    pending=con.execute('select exists(select 1 from public.auth_sessions where user_id=? and revoked_at is null) as pending',
                        (user_id,)).fetchone()
    if not row or not hmac.compare_digest(str(row['password']),new_hash) or pending['pending']:
        raise Unavailable('Credential revocation could not be confirmed.')


def reset_password(factory,user_id,new_hash,audit=None):
    """Caller retains the existing Admin guard; reset, revocation and audit atomic."""
    if type(user_id) is not int or user_id<=0:raise Rejected('Invalid account.')
    con=None
    try:
        con=factory();_ready(con)
        account=con.execute('select id,username from users where id=? for update',(user_id,)).fetchone()
        if not account:raise Rejected('Account no longer exists.')
        if con.execute('update users set password=? where id=?',(new_hash,user_id)).rowcount!=1:
            raise Unavailable('Password reset could not be confirmed.')
        _confirmed_password(con,user_id,new_hash)
        if audit:audit(con,account)
        con.commit()
    except (Rejected,Unavailable):raise
    except Exception:raise Unavailable('Password reset is unavailable.') from None
    finally:_clean(con)
