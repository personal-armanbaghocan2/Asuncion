"""Configuration isolation regressions. No SQL, server, credentials or model loading."""
import ast
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote, urlencode

from flask import Flask
from psycopg.conninfo import conninfo_to_dict, make_conninfo

import db
from runtime_configuration import configure, validate_database_configuration, validate_local_launcher

ROOT = Path(__file__).resolve().parents[1]
PROJECT = 'abcdefghijklmnopqrst'
ROLE = 'capstone_staging_backend'


def cloud_environment(certificate, *, mode='production', connection_mode='direct', dedicated=False):
    """Synthetic configuration only; fake certificate does not establish TLS trust."""
    shared = connection_mode != 'direct' and not dedicated
    host = 'synthetic.pooler.supabase.com' if shared else 'db.' + PROJECT + '.supabase.co'
    user = ROLE + '.' + PROJECT if shared else ROLE
    parameters = dict(host=host, port='6543' if connection_mode == 'transaction' else '5432',
                      dbname='synthetic', user=user, password='SYNTHETIC-ONLY',
                      sslmode='verify-full', sslrootcert=str(certificate))
    return dict(PSMS_ENV=mode, SUPABASE_DB_URL=make_conninfo(**parameters),
                PSMS_DB_EXPECTED_HOST=host, PSMS_DB_EXPECTED_DATABASE='synthetic',
                PSMS_DB_BACKEND_ROLE=ROLE, PSMS_SUPABASE_PROJECT_REF=PROJECT,
                PSMS_DB_CONNECTION_MODE=connection_mode,
                PSMS_DB_TRANSACTION_POOLER='1' if connection_mode == 'transaction' else '0',
                PSMS_TRUSTED_HOSTS='approved.example')


def uri(parameters):
    host = parameters['host']
    if ':' in host:
        host = '[' + host + ']'
    authority = quote(parameters['user'], safe='') + ':' + quote(parameters['password'], safe='')
    return ('postgresql://' + authority + '@' + host + ':' + parameters['port'] + '/'
            + quote(parameters['dbname'], safe='') + '?' + urlencode({
                key: value for key, value in parameters.items()
                if key not in ('host', 'port', 'dbname', 'user', 'password')}, quote_via=quote))


class DatabaseIsolationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='.database-config-test-', dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.certificate = Path(temporary.name) / 'synthetic ca.crt'
        self.certificate.write_text('SYNTHETIC PLACEHOLDER; NOT A TRUSTED CA', encoding='utf-8')
        self.driver = patch.object(db.psycopg, 'connect', side_effect=AssertionError('NO DATABASE'))
        self.connect = self.driver.start()
        self.addCleanup(self.driver.stop)

    def validate(self, environment):
        return validate_database_configuration(environment['SUPABASE_DB_URL'], environ=environment)

    def test_local_default_and_owner_identity_preserved_without_connection(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(db.database_url() == db.DEFAULT_LOCAL_URL)
            self.assertEqual(conninfo_to_dict(db.database_url())['user'], 'postgres')
            validate_local_launcher()
        self.connect.assert_not_called()

    def test_local_uri_keyword_ipv4_ipv6_and_localhost(self):
        for host in ('127.0.0.1', '::1', 'localhost', 'LOCALHOST'):
            parameters = dict(host=host, port='54322', dbname='synthetic', user='postgres', password='SYNTHETIC')
            for target in (make_conninfo(**parameters), uri(parameters)):
                with self.subTest(host=host, syntax='URI' if '://' in target else 'keyword'):
                    validate_database_configuration(target, environ={})
        self.connect.assert_not_called()

    def test_inherited_cloud_or_non_loopback_url_is_rejected_locally(self):
        for host in ('db.' + PROJECT + '.supabase.co', 'localhost.evil.invalid', '127.1', '/tmp'):
            for target in (make_conninfo(host=host, user='postgres', dbname='synthetic'),
                           'postgresql://postgres@' + quote(host, safe='.') + '/synthetic'):
                with patch.dict(os.environ, {'SUPABASE_DB_URL': target}, clear=True):
                    with self.assertRaises(RuntimeError): validate_local_launcher()
        self.connect.assert_not_called()

    def test_cloud_profiles_cannot_use_local_default_or_local_launcher(self):
        for mode in ('staging', ' Production '):
            with patch.dict(os.environ, {'PSMS_ENV': mode}, clear=True):
                with self.assertRaises(RuntimeError): db.database_url()
                with self.assertRaises(RuntimeError): validate_local_launcher()
        self.connect.assert_not_called()

    def test_no_implicit_socket_nested_conninfo_or_multi_host_fallback(self):
        for parameters in ({'dbname': 'synthetic'}, {'host': ''},
                           {'host': 'localhost,127.0.0.1'}, {'host': 'localhost', 'port': '5432,54322'},
                           {'host': 'localhost', 'dbname': 'host=outside.invalid'},
                           {'host': 'localhost', 'dbname': 'postgresql://outside.invalid/synthetic'}):
            with self.assertRaises(RuntimeError):
                validate_database_configuration(make_conninfo(**parameters), environ={})

    def test_libpq_address_services_options_and_inherited_overrides_rejected(self):
        local = make_conninfo(host='localhost', dbname='synthetic', user='postgres')
        for key, value in (('hostaddr', '203.0.113.1'), ('service', 'unreviewed'),
                           ('options', '-c role=postgres')):
            with self.subTest(parameter=key), self.assertRaises(RuntimeError):
                validate_database_configuration(make_conninfo(local, **{key: value}), environ={})
        for key in ('PGHOST', 'PGHOSTADDR', 'PGPORT', 'PGDATABASE', 'PGUSER',
                    'PGSERVICE', 'PGSERVICEFILE', 'PGSYSCONFDIR', 'PGOPTIONS'):
            with self.subTest(environment=key), self.assertRaises(RuntimeError):
                validate_database_configuration(local, environ={key: 'SYNTHETIC-OVERRIDE'})
        # Existing local credential mechanisms are not changed or read by validation.
        validate_database_configuration(local, environ={'PGPASSWORD': 'SYNTHETIC', 'PGPASSFILE': 'synthetic-passfile'})

    def test_uri_query_overrides_use_effective_libpq_parameters(self):
        for query in ('host=outside.invalid', 'hostaddr=203.0.113.1', 'service=unreviewed',
                      'host=localhost%2Coutside.invalid', 'port=5432%2C54322', 'options=-crole%3Dpostgres'):
            with self.subTest(query=query), self.assertRaises(RuntimeError):
                validate_database_configuration('postgresql://postgres@localhost/synthetic?' + query, environ={})

    def test_cloud_direct_shared_and_dedicated_pooler_configurations_parse_offline(self):
        for mode in ('staging', 'production'):
            for connection_mode, dedicated in (('direct', False), ('session', False),
                                                ('transaction', False), ('transaction', True)):
                environment = cloud_environment(self.certificate, mode=mode,
                                                connection_mode=connection_mode, dedicated=dedicated)
                for target in (environment['SUPABASE_DB_URL'], uri(conninfo_to_dict(environment['SUPABASE_DB_URL']))):
                    with self.subTest(mode=mode, connection_mode=connection_mode, dedicated=dedicated):
                        self.validate(dict(environment, SUPABASE_DB_URL=target))
        self.connect.assert_not_called()

    def test_missing_cloud_settings_fail_closed(self):
        environment = cloud_environment(self.certificate)
        for key in ('SUPABASE_DB_URL', 'PSMS_DB_EXPECTED_HOST', 'PSMS_DB_EXPECTED_DATABASE',
                    'PSMS_DB_BACKEND_ROLE', 'PSMS_SUPABASE_PROJECT_REF', 'PSMS_DB_CONNECTION_MODE'):
            missing = dict(environment); missing[key] = ''
            with self.subTest(setting=key), self.assertRaises(RuntimeError): self.validate(missing)

    def test_cloud_rejects_owner_api_internal_roles_and_wrong_login(self):
        environment = cloud_environment(self.certificate)
        for role in ('postgres', 'postgres.' + PROJECT, 'anon', 'authenticated', 'service_role',
                     'authenticator', 'supabase_admin', 'pg_monitor', 'admin', 'root'):
            parameters = conninfo_to_dict(environment['SUPABASE_DB_URL']); parameters['user'] = role
            with self.subTest(role=role), self.assertRaises(RuntimeError):
                self.validate(dict(environment, PSMS_DB_BACKEND_ROLE=role,
                                   SUPABASE_DB_URL=make_conninfo(**parameters)))
        parameters['user'] = 'unexpected_backend'
        with self.assertRaises(RuntimeError): self.validate(dict(environment, SUPABASE_DB_URL=make_conninfo(**parameters)))

    def test_target_project_database_tls_and_certificate_mismatches(self):
        environment = cloud_environment(self.certificate)
        for key, value in (('host', 'outside.invalid'), ('dbname', 'other'), ('sslmode', 'require'),
                           ('sslrootcert', 'relative.crt'), ('sslrootcert', str(self.certificate) + '.missing'),
                           ('sslrootcert', '//unreviewed.invalid/cert.crt')):
            parameters = conninfo_to_dict(environment['SUPABASE_DB_URL']); parameters[key] = value
            with self.subTest(parameter=key), self.assertRaises(RuntimeError):
                self.validate(dict(environment, SUPABASE_DB_URL=make_conninfo(**parameters)))
        for key, value in (('PSMS_SUPABASE_PROJECT_REF', 'z' * 20), ('PSMS_DB_EXPECTED_DATABASE', 'other'),
                           ('PSMS_DB_EXPECTED_HOST', 'outside.invalid')):
            with self.subTest(expectation=key), self.assertRaises(RuntimeError): self.validate(dict(environment, **{key: value}))

    def test_pooler_modes_ports_project_login_and_preparation_must_agree(self):
        for connection_mode in ('direct', 'session', 'transaction'):
            environment = cloud_environment(self.certificate, connection_mode=connection_mode)
            for flag in ('invalid', '0' if connection_mode == 'transaction' else '1'):
                with self.subTest(mode=connection_mode, flag=flag), self.assertRaises(RuntimeError):
                    self.validate(dict(environment, PSMS_DB_TRANSACTION_POOLER=flag))
            parameters = conninfo_to_dict(environment['SUPABASE_DB_URL']); parameters['port'] = '54322'
            with self.assertRaises(RuntimeError): self.validate(dict(environment, SUPABASE_DB_URL=make_conninfo(**parameters)))
        environment = cloud_environment(self.certificate, connection_mode='session')
        parameters = conninfo_to_dict(environment['SUPABASE_DB_URL']); parameters['user'] = ROLE + '.' + 'z' * 20
        with self.assertRaises(RuntimeError): self.validate(dict(environment, SUPABASE_DB_URL=make_conninfo(**parameters)))

    def test_cloud_rejects_all_inherited_libpq_parameters_and_extra_dsn_options(self):
        environment = cloud_environment(self.certificate)
        for key in ('PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE', 'PGPASSWORD', 'PGPASSFILE', 'PGSSLMODE', 'PGOPTIONS'):
            with self.subTest(environment=key), self.assertRaises(RuntimeError):
                self.validate(dict(environment, **{key: 'SYNTHETIC-OVERRIDE'}))
        for key, value in (('passfile', 'unreviewed'), ('target_session_attrs', 'any'), ('sslmode', 'disable')):
            parameters = conninfo_to_dict(environment['SUPABASE_DB_URL']); parameters[key] = value
            with self.subTest(parameter=key), self.assertRaises(RuntimeError):
                self.validate(dict(environment, SUPABASE_DB_URL=make_conninfo(**parameters)))

    def test_cloud_connection_fence_cannot_be_enabled_by_environment_flags(self):
        for mode in ('staging', 'production'):
            environment = cloud_environment(self.certificate, mode=mode)
            environment.update(PSMS_ALLOW_CLOUD='1', PSMS_CLOUD_CONNECTIONS_ENABLED='1')
            with patch.dict(os.environ, environment, clear=True):
                message = ('Staging database connections require explicit activation' if mode == 'staging'
                           else 'Production cloud database connections are not authorized')
                for target in (environment['SUPABASE_DB_URL'], uri(conninfo_to_dict(environment['SUPABASE_DB_URL']))):
                    with self.assertRaisesRegex(RuntimeError, message): db.connect_db(target)
                with self.assertRaisesRegex(RuntimeError, message): db.Connection()
        self.connect.assert_not_called()

    def test_explicit_keyword_target_cannot_be_discarded_for_local_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            for target in ('host=outside.invalid user=postgres dbname=synthetic', 'https://outside.invalid'):
                with self.assertRaises(RuntimeError): db.connect_db(target)
        self.connect.assert_not_called()

    def test_explicit_production_activation_keeps_target_tls_and_pooler_validation(self):
        environment = cloud_environment(self.certificate, connection_mode='transaction')
        environment['PSMS_PRODUCTION_DB_ENABLED'] = '1'
        with patch.dict(os.environ, environment, clear=True), patch.object(db.psycopg, 'connect') as connect:
            connection = db.Connection()
            self.assertEqual(connect.call_args.args[0], environment['SUPABASE_DB_URL'])
            self.assertIsNone(connect.call_args.kwargs['prepare_threshold'])
            connection.commit(); connection.rollback(); connection.close()
        parameters = conninfo_to_dict(environment['SUPABASE_DB_URL'])
        for key, value in (('user','postgres'), ('sslmode','require'), ('host','outside.invalid')):
            invalid = dict(parameters, **{key:value})
            with self.subTest(parameter=key), patch.dict(os.environ, environment, clear=True):
                with self.assertRaises(RuntimeError): db.Connection(make_conninfo(**invalid))
        self.connect.assert_not_called()

    def test_vercel_startup_cannot_fall_back_to_an_implicit_local_profile(self):
        for profile in ({}, {'PSMS_ENV':'local'}, {'PSMS_ENV':'test'}):
            with self.subTest(profile=profile), patch.dict(os.environ,dict(VERCEL='1',**profile),clear=True):
                with self.assertRaisesRegex(RuntimeError,'Vercel requires an explicit'):
                    configure(Flask('synthetic-hosted-profile'))
        self.connect.assert_not_called()

    def test_local_adapter_keeps_credentials_and_transaction_methods_unchanged(self):
        environment = {'PSMS_ENV': 'local', 'PSMS_DB_TRANSACTION_POOLER': '1'}
        target = make_conninfo(host='localhost', port=54322, dbname='synthetic', user='postgres', password='SYNTHETIC')
        with patch.dict(os.environ, environment, clear=True), patch.object(db.psycopg, 'connect') as connect:
            connection = db.connect_db(target)
            self.assertTrue(connect.call_args.args[0] == target)
            self.assertIsNone(connect.call_args.kwargs['prepare_threshold'])
            connection.commit(); connection.rollback(); connection.close()
            for name in ('commit', 'rollback', 'close'): getattr(connect.return_value, name).assert_called_once()
        self.connect.assert_not_called()

    def test_local_legacy_path_and_unset_pooler_flag_keep_existing_defaults(self):
        with patch.dict(os.environ, {'PSMS_DB_TRANSACTION_POOLER': ''}, clear=True), \
                patch.object(db.psycopg, 'connect') as connect:
            db.connect_db('legacy-file.db').close()
            self.assertTrue(connect.call_args.args[0] == db.DEFAULT_LOCAL_URL)
            self.assertNotIn('prepare_threshold', connect.call_args.kwargs)
        self.connect.assert_not_called()

    def test_staging_and_production_cookie_configuration_remains_secure(self):
        for mode in ('staging', 'production'):
            with patch.dict(os.environ, cloud_environment(self.certificate, mode=mode), clear=True):
                application = Flask('synthetic-cloud-configuration'); configure(application)
                self.assertTrue(application.config['SESSION_COOKIE_SECURE'])
                self.assertFalse(application.debug)
        self.connect.assert_not_called()

    def test_configuration_errors_never_echo_private_canary(self):
        for target in ('SYNTHETIC-PRIVATE-CANARY', 'host=outside.invalid password=SYNTHETIC-PRIVATE-CANARY'):
            with self.assertRaises(RuntimeError) as error: validate_database_configuration(target, environ={})
            self.assertNotIn('SYNTHETIC-PRIVATE-CANARY', str(error.exception))

    def test_local_launcher_preflight_precedes_keys_ping_models_and_app(self):
        source = (ROOT / 'START_SYSTEM.ps1').read_text(encoding='utf-8-sig')
        preflight = source.index('validate_local_launcher()')
        for boundary in ('# Reload the persistent user key', 'from db import ping',
                         'from offline_ocr_mapper', 'python .\\app.py'):
            self.assertLess(preflight, source.index(boundary))
        self.assertIn('if ($LASTEXITCODE -ne 0)', source[preflight:source.index('# Reload the persistent user key')])
        # The application keeps its original captured-DSN adapter path.
        tree = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8-sig'))
        connection = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'conn')
        self.assertIn('connect_db(DATABASE_URL)', ast.unparse(connection))

    @unittest.skipUnless(shutil.which('powershell') or shutil.which('pwsh'), 'PowerShell is unavailable')
    def test_local_launcher_powershell_syntax_without_execution(self):
        base = {key: os.environ[key] for key in ('SYSTEMROOT', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP') if key in os.environ}
        base['PSMS_TEST_LAUNCHER'] = str(ROOT / 'START_SYSTEM.ps1')
        command = """$tokens=$null; $errors=$null;
[void][System.Management.Automation.Language.Parser]::ParseFile($env:PSMS_TEST_LAUNCHER,[ref]$tokens,[ref]$errors);
if ($errors.Count) { exit 1 }; exit 0"""
        result = subprocess.run([shutil.which('powershell') or shutil.which('pwsh'), '-NoProfile', '-NonInteractive',
                                 '-Command', command], cwd=ROOT, env=base,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
