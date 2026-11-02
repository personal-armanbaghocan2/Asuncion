"""Actual Flask login/session routes with synthetic transactional SQL doubles.

No live database, real accounts, initialization, migrations or password changes.
Run from the project root: python -B tests/test_auth_session_routes.py
PostgreSQL permissions/locks/trigger behavior still require isolated PostgreSQL.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from werkzeug.security import generate_password_hash
import server_sessions_v0_1_0 as sessions
from test_nlp_comparison_routes import ActualApplication, SyntheticDatabase, Cursor


class DriverFailure(RuntimeError):
    def __init__(self, code=None):
        super().__init__('SYNTHETIC_PRIVATE_DRIVER_DATA password=do-not-log token=do-not-log')
        self.sqlstate=code


class AuthDatabase(SyntheticDatabase):
    """Allowlisted SQL only; writes live in a per-connection pending snapshot."""
    def __init__(self):
        super().__init__()
        self.password=secrets.token_urlsafe(24)
        hashed=generate_password_hash(self.password,method='pbkdf2:sha256:1000')
        for account in self.accounts.values():account['password']=hashed
        self.registry={}
        self.ready=True
        self.faults={}
        self.connection_count=0
        self.metadata={k:True for k in ('sessions_table','users_table','readiness_function','revocation_function',
            'schema_usage','revocation_trigger','registry_rls','registry_select','registry_columns',
            'registry_insert','registry_revoke','users_select','users_row_lock','helper_execute')}

    def fault(self, stage):
        if stage in self.faults:raise DriverFailure(self.faults[stage])

    def connect(self, *args, **kwargs):
        self.connection_count+=1
        # The actual login's initial account lookup is deliberately healthy.
        if self.connection_count>1:self.fault('connect')
        store=self
        class Connection:
            def __init__(self):
                self.pending=deepcopy(store.registry)
                self.changes=[]
                self.read_only=False
                self.auth_connection=False

            def execute(self, sql, params=()):
                text=' '.join(sql.lower().split())
                store.calls.append((text,tuple(params)))
                if text=='set transaction read only':
                    store.fault('read_only');self.read_only=True;return Cursor()
                if text.startswith('select to_regclass('):return Cursor([store.metadata])
                if text.startswith('select c.relrowsecurity'):return Cursor([store.metadata])
                if text.startswith('select has_column_privilege('):return Cursor([store.metadata])
                if text.startswith('select has_table_privilege('):return Cursor([store.metadata])
                if text.startswith('select has_function_privilege('):return Cursor([store.metadata])
                if 'psms_auth_session_permissions_ready() as ready' in text:
                    self.auth_connection=True
                    store.fault('readiness')
                    return Cursor([dict(ready=store.ready)])
                if text.startswith('select * from users where username='):
                    store.fault('account_lookup')
                    return Cursor([a for a in store.accounts.values() if a['username']==params[0]])
                if text.startswith('select id,username,role,status,password from users'):
                    store.fault('account_lock')
                    return Cursor([store.accounts[params[0]]] if params[0] in store.accounts else [])
                if text.startswith('insert into public.auth_sessions('):
                    store.fault('session_insert')
                    digest,uid,ttl=params
                    self.pending[digest]=dict(user_id=uid,expires_at=time.time()+ttl,revoked_at=None)
                    self.changes.append(('session_insert',uid));return Cursor(rowcount=1)
                if 'from public.auth_sessions s' in text:
                    store.fault('account_read')
                    digest,uid=params
                    record=self.pending.get(digest);account=store.accounts.get(uid)
                    allowed=record and record['user_id']==uid and record['revoked_at'] is None and record['expires_at']>time.time()
                    allowed=allowed and account and account['status']=='Active' and account['role'] in ('Admin','Staff','Inspector')
                    return Cursor([account] if allowed else [])
                if text.startswith('update public.auth_sessions set revoked_at='):
                    digest,uid=params;record=self.pending.get(digest)
                    change=bool(record and record['user_id']==uid and record['revoked_at'] is None)
                    if change:record['revoked_at']=time.time();self.changes.append(('session_revoke',uid))
                    return Cursor(rowcount=int(change))
                if text.startswith('select revoked_at from public.auth_sessions'):
                    digest,uid=params;record=self.pending.get(digest)
                    return Cursor([record] if record and record['user_id']==uid else [])
                if text.startswith('update users set last_login='):
                    self.changes.append(('last_login',params[1]));return Cursor(rowcount=1)
                if text.startswith('insert into audit_trail('):
                    self.changes.append(('audit',params[2]));return Cursor([dict(id=1)],1)
                if text.startswith('select id,username,role from users order by'):
                    return Cursor([{k:a[k] for k in ('id','username','role')} for a in store.accounts.values()])
                if text.startswith('select id, no, status, created_at,') or text in (
                    'select category, qty from inventory','select id, dt, "user", module, action from audit_trail'):
                    return Cursor()
                # Remaining queries are the existing read-only NLP fixtures.
                return store.execute(sql,params)

            def commit(self):
                store.fault('commit')
                if self.read_only:raise AssertionError('Read-only diagnostics attempted commit')
                store.registry=deepcopy(self.pending);store.writes.extend(self.changes)
                self.changes=[];store.commits+=1

            def rollback(self):
                store.fault('rollback')
                self.pending=deepcopy(store.registry);self.changes=[]

            def close(self):
                self.changes=[];store.closes+=1
                if self.auth_connection:store.fault('close')
        return Connection()


class AuthSessionRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime=ActualApplication().__enter__()
        cls.module=cls.runtime.module

    @classmethod
    def tearDownClass(cls):cls.runtime.__exit__(None,None,None)

    def setUp(self):
        self.db=AuthDatabase()
        # Preserve actual conn(), db/signatory adapter and session functions.
        self.module.connect_db=self.db.connect
        self.module.nlp_pricing_advisory=lambda record_id:None
        self.client=self.module.app.test_client()
        self.hashes={uid:a['password'] for uid,a in self.db.accounts.items()}

    def tearDown(self):
        self.assertEqual(self.hashes,{uid:a['password'] for uid,a in self.db.accounts.items()})
        self.runtime.firewall.assert_not_called()

    def token(self, client=None, path='/login'):
        response=(client or self.client).get(path)
        return re.search(r'name="csrf-token" content="([^"]+)"',response.get_data(as_text=True))[1]

    def login(self, uid=1, client=None, password=None):
        client=client or self.client
        return client.post('/login',data=dict(csrf_token=self.token(client),
            username=self.db.accounts[uid]['username'],password=self.db.password if password is None else password))

    def assert_denied(self, response, reason):
        self.assertEqual(response.status_code,503)
        self.assertIn('Sign-in is temporarily unavailable',response.get_data(as_text=True))
        self.assertNotIn(reason,response.get_data(as_text=True))
        self.assertNotIn('SYNTHETIC_PRIVATE',response.get_data(as_text=True))
        with self.client.session_transaction() as state:self.assertFalse(state.get('logged_in'))

    def failure_case(self, stage, code, expected):
        self.db.faults[stage]=code
        with self.assertLogs(self.module.app.logger,level='WARNING') as logs:
            response=self.login()
        self.assert_denied(response,expected)
        output=' '.join(logs.output)
        self.assertIn(expected,output)
        for secret in ('SYNTHETIC_PRIVATE',self.db.password,*self.hashes.values()):self.assertNotIn(secret,output)
        self.assertFalse(self.db.registry)

    def test_login_page_loads_without_database_reads(self):
        self.assertEqual(self.client.get('/login').status_code,200)
        self.assertEqual(self.db.connection_count,0)

    def test_correct_admin_issues_server_session_and_renders_actual_dashboard(self):
        response=self.login()
        self.assertEqual(response.status_code,302);self.assertEqual(response.location,'/')
        with self.client.session_transaction() as state:
            self.assertEqual(state['role'],'Admin');sid=state[sessions.COOKIE_KEY]
        self.assertIn(sessions.token_hash(sid),self.db.registry)
        self.assertNotIn(sid,self.db.registry)
        self.assertEqual(self.client.get('/').status_code,200)

    def test_invalid_credentials_rejected_without_issuing_sessions(self):
        self.assertEqual(self.login(password='SYNTHETIC-WRONG').status_code,200)
        response=self.client.post('/login',data=dict(csrf_token=self.token(),username='SYNTHETIC-UNKNOWN',password='SYNTHETIC-WRONG'))
        self.assertEqual(response.status_code,200);self.assertFalse(self.db.registry)
        self.assertFalse(any('psms_auth_session_permissions_ready() as ready' in sql for sql,_ in self.db.calls))

    def test_inactive_or_unknown_role_is_rejected_before_session_creation(self):
        for status,role in (('Inactive','Admin'),('Active','Unknown')):
            self.db.accounts[1].update(status=status,role=role)
            self.assertEqual(self.login().status_code,200)
        self.assertFalse(self.db.registry)

    def test_login_csrf_is_enabled_and_missing_token_prevents_account_lookup(self):
        self.assertEqual(self.client.post('/login',data=dict(username='synthetic_1',password=self.db.password)).status_code,400)
        self.assertFalse(self.db.calls)

    def test_false_readiness_fails_securely_with_safe_diagnostic(self):
        self.db.ready=False
        with self.assertLogs(self.module.app.logger,level='WARNING') as logs:response=self.login()
        self.assert_denied(response,'readiness:contract_rejected')
        self.assertIn('readiness:contract_rejected',' '.join(logs.output));self.assertFalse(self.db.registry)

    def test_missing_helper_and_denied_helper_have_distinct_safe_codes(self):
        for code,reason in (('42883','readiness:missing_function'),('42501','readiness:permission_denied')):
            with self.subTest(code=code):self.failure_case('readiness',code,reason)

    def test_users_row_lock_permission_failure_is_distinguished_from_readiness(self):
        self.failure_case('account_lock','42501','account_lock:permission_denied')

    def test_login_and_logout_after_synthetic_readiness_is_restored(self):
        # SQL is not executed here. Simulate restoration of the database contract
        # to verify the actual Flask routes recover without credential changes.
        self.db.faults['readiness']='42883'
        with self.assertLogs(self.module.app.logger,level='WARNING'):
            self.assert_denied(self.login(),'readiness:missing_function')
        self.db.faults.clear()
        response=self.login()
        self.assertEqual(response.status_code,302)
        cookie=self.client.get_cookie('session').value
        self.assertEqual(self.client.get('/nlp').status_code,200)
        response=self.client.post('/logout',data=dict(csrf_token=self.token(path='/nlp')))
        self.assertEqual(response.status_code,302)
        replay=self.module.app.test_client();replay.set_cookie('session',cookie)
        self.assertEqual(replay.get('/nlp').status_code,302)
        self.assertTrue(all(row['revoked_at'] is not None for row in self.db.registry.values()))

    def test_insert_failures_preserve_security_and_identify_the_stage(self):
        for code,reason in (('42P01','missing_table'),('42703','incompatible_columns'),
                            ('25006','read_only_transaction'),('23514','constraint_failure')):
            with self.subTest(code=code):self.failure_case('session_insert',code,'session_insert:'+reason)

    def test_connection_failure_at_issue_is_safe_and_identifiable(self):
        self.failure_case('connect',None,'connect:failed')

    def test_commit_failure_returns_no_cookie_or_false_success(self):
        self.failure_case('commit',None,'commit:failed')

    def test_cleanup_failure_after_commit_never_returns_authenticated_cookie(self):
        for stage in ('rollback','close'):
            with self.subTest(stage=stage):
                self.db.faults={stage:None}
                with self.assertLogs(self.module.app.logger,level='WARNING') as logs:response=self.login()
                self.assert_denied(response,'cleanup:failed')
                self.assertIn('cleanup:failed',' '.join(logs.output))
        self.assertGreater(self.db.closes,0)

    def test_cleanup_failure_preserves_primary_readiness_reason(self):
        self.db.faults={'readiness':'42883','rollback':None}
        with self.assertLogs(self.module.app.logger,level='WARNING') as logs:response=self.login()
        self.assert_denied(response,'readiness:missing_function')
        self.assertIn('readiness:missing_function',' '.join(logs.output))
        self.assertIn('cleanup_failed=True',' '.join(logs.output));self.assertFalse(self.db.registry)

    def test_logout_requires_csrf_and_revokes_only_current_session(self):
        other=self.module.app.test_client()
        self.assertEqual(self.login().status_code,302);self.assertEqual(self.login(client=other).status_code,302)
        cookie=self.client.get_cookie('session').value
        self.assertEqual(self.client.post('/logout').status_code,400)
        response=self.client.post('/logout',data=dict(csrf_token=self.token(path='/nlp')))
        self.assertEqual(response.status_code,302)
        replay=self.module.app.test_client();replay.set_cookie('session',cookie)
        self.assertEqual(replay.get('/nlp').status_code,302)
        self.assertEqual(other.get('/nlp').status_code,200)
        self.assertEqual(sum(r['revoked_at'] is not None for r in self.db.registry.values()),1)

    def test_expired_registry_session_denies_fresh_signed_cookie(self):
        self.login()
        for row in self.db.registry.values():row['expires_at']=time.time()-10
        self.assertEqual(self.client.get('/nlp').status_code,302)

    def test_admin_role_is_database_authoritative_for_user_management(self):
        self.login();self.assertEqual(self.client.get('/users').status_code,200)
        staff=self.module.app.test_client();self.login(uid=2,client=staff)
        with staff.session_transaction() as state:state['role']='Admin'
        self.assertEqual(staff.get('/users').status_code,302)
        with staff.session_transaction() as state:self.assertEqual(state['role'],'Staff')

    def test_nlp_verification_still_requires_active_registry_session(self):
        self.assertEqual(self.client.get('/nlp?comparison_po_id=1').status_code,302)
        self.login();response=self.client.get('/nlp?comparison_po_id=1')
        self.assertEqual(response.status_code,200)
        self.assertIn('PR-PO Technical Item Comparison',response.get_data(as_text=True))

    def test_cli_requires_explicit_target_authorization_before_connecting(self):
        runner=self.module.app.test_cli_runner()
        self.assertNotEqual(runner.invoke(args=['check-auth-sessions']).exit_code,0)
        self.assertEqual(self.db.connection_count,0)
        with patch.dict(os.environ,{'SUPABASE_DB_URL':''}):
            result=runner.invoke(args=['check-auth-sessions','--confirm-database-target'])
        self.assertNotEqual(result.exit_code,0);self.assertEqual(self.db.connection_count,0)

    def test_cli_checks_metadata_under_actual_adapter_without_account_reads_or_writes(self):
        result=self.module.app.test_cli_runner().invoke(args=['check-auth-sessions','--confirm-database-target'])
        self.assertEqual(result.exit_code,0,result.output)
        report=json.loads(result.output)
        self.assertTrue(report['ready']);self.assertTrue(report['read_only'])
        self.assertFalse(report['accounts_read']);self.assertFalse(report['login_tested'])
        self.assertFalse(self.db.writes);self.assertEqual(self.db.commits,0)
        for sql,_ in self.db.calls:
            self.assertTrue(sql.startswith('select ') or sql=='set transaction read only')
            self.assertNotRegex(sql,r'from (?:public\.)?(?:users|auth_sessions)\b')
        for secret in (self.db.password,*self.hashes.values()):self.assertNotIn(secret,result.output)

    def test_metadata_check_identifies_missing_objects_without_executing_missing_helper(self):
        self.db.metadata.update(sessions_table=False,readiness_function=False,revocation_function=False,revocation_trigger=False)
        report=sessions.session_diagnostics(self.db.connect)
        self.assertFalse(report['ready']);self.assertEqual(report['failure'],'readiness:missing_function')
        self.assertFalse(any('psms_auth_session_permissions_ready() as ready' in sql for sql,_ in self.db.calls))
        self.assertFalse(self.db.writes)

    def test_metadata_check_denies_each_incomplete_permission_or_trigger_contract(self):
        for key in self.db.metadata:
            with self.subTest(key=key):
                self.db.metadata={k:True for k in self.db.metadata};self.db.metadata[key]=False
                report=sessions.session_diagnostics(self.db.connect)
                self.assertFalse(report['ready'])
        self.assertFalse(self.db.writes)

    def test_metadata_failure_and_cleanup_are_redacted(self):
        self.db.faults={'readiness':'42501','rollback':None}
        report=sessions.session_diagnostics(self.db.connect)
        self.assertFalse(report['ready']);self.assertTrue(report['cleanup_failed'])
        self.assertEqual(report['failure'],'readiness:permission_denied')
        self.assertNotIn('SYNTHETIC_PRIVATE',json.dumps(report))

    def test_failure_reason_allowlist_rejects_untrusted_text(self):
        for value in ('SYNTHETIC_PRIVATE_DRIVER_DATA',{'private':'text'},None):
            error=sessions.Unavailable('generic',reason=value)
            self.assertEqual(sessions.failure_reason(error),'unavailable')

    def test_health_validation_failure_is_logged_before_health_runs(self):
        self.assertEqual(self.login().status_code,302)
        writes=deepcopy(self.db.writes);registry=deepcopy(self.db.registry)
        for stage,code,reason in (
            ('connect','28P01','connect:database_authentication_failed'),
            ('read_only',None,'read_only:failed'),
            ('readiness','42883','readiness:missing_function'),
            ('readiness','42501','readiness:permission_denied'),
            ('account_read','42703','account_read:incompatible_columns'),
            ('account_read','42P01','account_read:missing_table'),
            ('account_read','42501','account_read:permission_denied')):
            with self.subTest(stage=stage,code=code):
                self.db.faults={stage:code}
                with patch.object(self.module,'db_ping',side_effect=AssertionError('HEALTH ROUTE MUST NOT RUN')) as ping, \
                     self.assertLogs(self.module.app.logger,level='WARNING') as logs:
                    response=self.client.get('/health')
                self.assertEqual(response.status_code,503)
                self.assertIn('Account validation is temporarily unavailable',response.get_data(as_text=True))
                output=' '.join(logs.output)
                self.assertIn(reason,output);ping.assert_not_called()
                for secret in ('SYNTHETIC_PRIVATE',self.db.password,*self.hashes.values()):
                    self.assertNotIn(secret,output);self.assertNotIn(secret,response.get_data(as_text=True))
                self.assertEqual(self.db.writes,writes);self.assertEqual(self.db.registry,registry)

    def test_account_connection_categories_never_log_driver_text(self):
        from psycopg import OperationalError
        self.assertEqual(self.login().status_code,302)
        for error,reason in (
            (OperationalError('SYNTHETIC_PRIVATE SSL error: certificate verify failed'), 'tls_verification_failed'),
            (OperationalError('SYNTHETIC_PRIVATE failed to resolve host'), 'host_resolution_failed'),
            (OperationalError('SYNTHETIC_PRIVATE connection timeout expired'), 'timeout'),
            (OperationalError('SYNTHETIC_PRIVATE connection refused'), 'network_unavailable'),
            (RuntimeError('SYNTHETIC_PRIVATE configuration detail'), 'configuration_rejected'),
            (OperationalError('SYNTHETIC_PRIVATE unknown detail'), 'failed')):
            with self.subTest(reason=reason), patch.object(self.module,'connect_db',side_effect=error), \
                 self.assertLogs(self.module.app.logger,level='WARNING') as logs:
                response=self.client.get('/health')
            self.assertEqual(response.status_code,503)
            output=' '.join(logs.output)
            self.assertIn('connect:'+reason,output);self.assertNotIn('SYNTHETIC_PRIVATE',output)
            self.assertNotIn('SYNTHETIC_PRIVATE',response.get_data(as_text=True))

    def test_account_validation_retains_primary_failure_when_cleanup_also_fails(self):
        self.login();self.db.faults={'readiness':'42883','rollback':None}
        with self.assertLogs(self.module.app.logger,level='WARNING') as logs:
            response=self.client.get('/health')
        self.assertEqual(response.status_code,503)
        output=' '.join(logs.output)
        self.assertIn('readiness:missing_function',output);self.assertIn('cleanup_failed=True',output)
        self.assertNotIn('SYNTHETIC_PRIVATE',output)

    def test_health_recovers_only_after_session_validation_succeeds(self):
        self.login();self.db.ready=False
        with self.assertLogs(self.module.app.logger,level='WARNING') as logs:
            self.assertEqual(self.client.get('/health').status_code,503)
        self.assertIn('readiness:contract_rejected',' '.join(logs.output))
        self.db.ready=True
        with patch.dict(self.module.app.config,{'PSMS_INFERENCE_ENABLED':False}), \
             patch.object(self.module,'db_ping',return_value=True), \
             patch.object(self.module,'trained_nlp_model_status',return_value={'active':False}), \
             patch.object(self.module,'ocr_mapper_model_status',return_value={'active':False}), \
             patch.object(self.module,'configure_tesseract',return_value=False):
            response=self.client.get('/health')
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json['database']);self.assertEqual(response.json['application'],'ready')

    def test_login_lookup_failure_is_redacted_and_logged(self):
        self.db.faults={'account_lookup':'42501'}
        with self.assertLogs(self.module.app.logger,level='WARNING') as logs:
            response=self.login()
        self.assert_denied(response,'account_lookup:permission_denied')
        output=' '.join(logs.output)
        self.assertIn('account_lookup:permission_denied',output);self.assertNotIn('SYNTHETIC_PRIVATE',output)
        self.assertFalse(self.db.registry)


if __name__=='__main__':unittest.main()
