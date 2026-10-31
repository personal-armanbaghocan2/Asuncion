"""Explicit runtime configuration; no proxy, credential or database initialization."""
import os
from pathlib import Path
import re


CLOUD_MODES = ('staging', 'production')
LOCAL_DATABASE_HOSTS = frozenset(('localhost', '127.0.0.1', '::1'))
_LOCAL_LIBPQ_OVERRIDES = frozenset((
    'PGHOST', 'PGHOSTADDR', 'PGPORT', 'PGDATABASE', 'PGUSER',
    'PGSERVICE', 'PGSERVICEFILE', 'PGSYSCONFDIR', 'PGOPTIONS',
))
_CLOUD_PARAMETERS = frozenset((
    'host', 'port', 'dbname', 'user', 'password', 'sslmode', 'sslrootcert',
))
_RESERVED_DATABASE_ROLES = frozenset((
    'postgres', 'public', 'anon', 'authenticated', 'service_role',
    'authenticator', 'admin', 'root', 'dashboard_user', 'pgbouncer',
    'supavisor', 'realtime_admin',
))


def validate_database_configuration(dsn, *, environ=None):
    """Validate explicit libpq parameters offline; never connect or return secrets.

    Cloud preparation requires PSMS_ENV=staging/production and explicit
    PSMS_DB_EXPECTED_HOST, PSMS_DB_EXPECTED_DATABASE, PSMS_DB_BACKEND_ROLE,
    PSMS_SUPABASE_PROJECT_REF and PSMS_DB_CONNECTION_MODE expectations.
    These string checks do not authenticate a server or establish SQL privileges.
    db.Connection keeps cloud connections disabled independently of this validator.
    """
    environment = os.environ if environ is None else environ
    mode = environment_mode(environment)
    cloud = mode in CLOUD_MODES
    # Do not resolve services, read credential files, clear overrides, or echo values.
    if any(value and (key.upper().startswith('PG') if cloud else
                      key.upper() in _LOCAL_LIBPQ_OVERRIDES)
           for key, value in environment.items()):
        raise RuntimeError('Inherited libpq overrides are not allowed for this database profile.')
    if not isinstance(dsn, str) or not dsn.strip():
        raise RuntimeError('Explicit staging/production database configuration is required.' if cloud else
                           'An explicit local database target is required.')
    from psycopg.conninfo import conninfo_to_dict
    try:
        parameters = conninfo_to_dict(dsn)
    except Exception:
        raise RuntimeError('Database connection configuration is invalid.') from None
    if any(key in parameters for key in ('hostaddr', 'service', 'options')):
        raise RuntimeError('Database address, service and startup-option overrides are not allowed.')
    host = parameters.get('host', '')
    port = parameters.get('port', '5432')
    if not re.fullmatch(r'[0-9]{1,5}', port) or not 1 <= int(port) <= 65535:
        raise RuntimeError('One explicit database port is required; port lists are not allowed.')
    if any(part in parameters.get('dbname', '') for part in ('=', '://')):
        raise RuntimeError('Nested database connection parameters are not allowed.')
    pooler_flag = environment.get('PSMS_DB_TRANSACTION_POOLER', '0')
    if not cloud:
        if host.lower() not in LOCAL_DATABASE_HOSTS:
            raise RuntimeError('Local/test database profiles require an approved loopback host.')
        return
    if pooler_flag not in ('0', '1'):
        raise RuntimeError('PSMS_DB_TRANSACTION_POOLER must explicitly select a supported setting.')

    required = ('PSMS_DB_EXPECTED_HOST', 'PSMS_DB_EXPECTED_DATABASE',
                'PSMS_DB_BACKEND_ROLE', 'PSMS_SUPABASE_PROJECT_REF', 'PSMS_DB_CONNECTION_MODE')
    if not all(environment.get(key, '').strip() for key in required):
        raise RuntimeError('Complete cloud database identity expectations are required.')
    if set(parameters) != _CLOUD_PARAMETERS or not all(parameters.values()):
        raise RuntimeError('Cloud requires only explicit host, port, database, login and verified TLS parameters.')
    role = environment['PSMS_DB_BACKEND_ROLE'].strip()
    project = environment['PSMS_SUPABASE_PROJECT_REF'].strip()
    expected_host = environment['PSMS_DB_EXPECTED_HOST'].strip().lower()
    database = environment['PSMS_DB_EXPECTED_DATABASE'].strip()
    connection_mode = environment['PSMS_DB_CONNECTION_MODE'].strip()
    if (not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', role)
            or role in _RESERVED_DATABASE_ROLES or role.startswith(('pg_', 'supabase_'))):
        raise RuntimeError('Cloud requires an explicitly reviewed nonprivileged backend role name.')
    if (not re.fullmatch(r'[a-z0-9]{20}', project)
            or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,62}', database)
            or host.lower() != expected_host or parameters['dbname'] != database):
        raise RuntimeError('Cloud database target does not match the explicit expectations.')
    project_host = 'db.' + project + '.supabase.co'
    shared_pooler = bool(re.fullmatch(r'(?:[a-z0-9]+(?:-[a-z0-9]+)*\.)+pooler\.supabase\.com', expected_host))
    if expected_host == project_host:
        allowed_modes = ('direct', 'transaction')
        expected_login = role
    elif shared_pooler:
        allowed_modes = ('session', 'transaction')
        expected_login = role + '.' + project
    else:
        raise RuntimeError('Cloud host does not match the supported Supabase project/pooler forms.')
    if connection_mode not in allowed_modes or parameters['user'] != expected_login:
        raise RuntimeError('Cloud connection mode or project-scoped backend login does not match.')
    transaction_pooler = connection_mode == 'transaction'
    if (port != ('6543' if transaction_pooler else '5432')
            or pooler_flag != ('1' if transaction_pooler else '0')):
        raise RuntimeError('Cloud pooler port and prepared-statement settings are incompatible.')
    certificate = parameters['sslrootcert']
    if (parameters['sslmode'] != 'verify-full' or certificate.startswith(('//', '\\\\'))
            or not Path(certificate).is_absolute() or not Path(certificate).is_file()):
        raise RuntimeError('Cloud requires verify-full TLS and an explicit existing local root certificate.')


def validate_local_launcher():
    """Preflight only, before the local launcher touches keys, models or a database."""
    if environment_mode() not in ('local', 'test'):
        raise RuntimeError('START_SYSTEM.ps1 accepts only local/test database profiles.')
    from db import database_url
    validate_database_configuration(database_url())


def environment_mode(environ=None):
    """Use one fail-closed interpretation in both Flask and database callers."""
    environment = os.environ if environ is None else environ
    mode = environment.get('PSMS_ENV', 'local').strip().lower()
    if mode not in ('local', 'test', *CLOUD_MODES):
        raise RuntimeError('PSMS_ENV must be local, test, staging or production.')
    return mode


def configure(app):
    mode=environment_mode()
    app.config.update(SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',MAX_CONTENT_LENGTH=9*1024*1024)
    app.config['PSMS_ENV'] = mode
    if mode in CLOUD_MODES:
        validate_database_configuration(os.environ.get('SUPABASE_DB_URL', ''))
        hosts=[h.strip() for h in os.environ.get('PSMS_TRUSTED_HOSTS','').split(',') if h.strip()]
        if not hosts:raise RuntimeError('Reviewed staging/production trusted hosts are required.')
        app.config.update(SESSION_COOKIE_SECURE=True,TRUSTED_HOSTS=hosts,DEBUG=False)
    # Forwarded headers remain untrusted. ProxyFix requires separately reviewed
    # proxy counts and infrastructure; configuration never enables it implicitly.

    def liveness():
        # Deliberately shallow: no accounts, database, models, paths or settings.
        return {'status': 'alive'}

    app.add_url_rule('/health/live', 'runtime_liveness', liveness, methods=['GET'])

    @app.after_request
    def safe_response_headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('Referrer-Policy', 'same-origin')
        return response
