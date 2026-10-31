"""Operator-only NLP selection; production RF is synthetic experimental staging only."""
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

RELEASE_CHANNEL = 'experimental_staging'
STAGING_ROLE = 'capstone_staging_backend'
MARKER_FILE = 'CAPSTONE_SYNTHETIC_BUNDLE.json'


def _require(condition, message):
    if not condition:
        raise RuntimeError(message)


def _marker(directory, expected):
    try:
        path = directory / MARKER_FILE
        _require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 4096,
                 'A synthetic staging marker is required.')
        _require(json.loads(path.read_text(encoding='utf-8')) == {
            'purpose': 'synthetic-adviser-staging', 'marker': expected},
            'The synthetic staging marker does not match.')
    except Exception:
        raise RuntimeError('A matching synthetic staging marker is required.') from None


def _hostname(value):
    parts = value.split('.')
    return (len(value) <= 253 and len(parts) > 1 and not parts[-1].isdigit()
            and not value.endswith(('.localhost', '.local', '.internal'))
            and all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', p) for p in parts))


def verify_retrained_artifacts(model_dir, base_dir):
    """Hashes/metadata only: never deserialize a classifier or execute features."""
    try:
        import retrained_nlp_runtime as retrained
        result = retrained.verify_artifacts(model_dir)
        feature = Path(base_dir) / 'model/fasttext_nlp/fasttext_feature_schema_v2.py'
        _require(retrained._sha(feature) == retrained.FEATURE_SHA256,
                 'The shared feature source does not match.')
        return result
    except Exception:
        raise RuntimeError('Retrained artifacts, dependencies or shared feature source failed verification.') from None


def _staging_settings(config, base_dir, model_dir):
    """Private configuration, never included in readiness/health output."""
    from runtime_configuration import environment_mode
    _require(environment_mode() == 'production'
             and os.environ.get('PSMS_NLP_RELEASE_CHANNEL', '').strip() == RELEASE_CHANNEL,
             'Production RF requires explicit experimental staging authorization.')
    required = ('SUPABASE_DB_URL', 'PSMS_DATA_DIR', 'PSMS_RETRAINED_NLP_DIR', 'PSMS_SECRET_KEY',
                'PSMS_HEALTH_HOST', 'PSMS_TRUSTED_HOSTS', 'CAPSTONE_STAGE_ORIGIN', 'CAPSTONE_STAGE_DB_HOST',
                'CAPSTONE_STAGE_DB_NAME', 'CAPSTONE_STAGE_DB_LOGIN', 'CAPSTONE_STAGE_DB_ROLE',
                'CAPSTONE_STAGE_MARKER')
    _require(all(os.environ.get(k, '').strip() for k in required),
             'Explicit experimental staging configuration is required.')
    # libpq environment/service/hostaddr overrides must not redirect a pinned DSN.
    _require(not any(v for k, v in os.environ.items() if k.startswith('PG')),
             'Experimental staging requires an unambiguous explicit database configuration.')
    # connect_db accepts explicit URL targets; other strings invoke its default.
    _require(os.environ['SUPABASE_DB_URL'].startswith(('postgres://', 'postgresql://')),
             'Experimental staging requires an explicit PostgreSQL connection URL.')
    try:
        from psycopg.conninfo import conninfo_to_dict
        database = conninfo_to_dict(os.environ['SUPABASE_DB_URL'])
        origin = urlsplit(os.environ['CAPSTONE_STAGE_ORIGIN'].strip())
        valid_origin = (origin.scheme == 'https' and origin.port in (None, 443)
                        and not origin.username and not origin.password and not origin.query
                        and not origin.fragment and origin.path in ('', '/') and _hostname(origin.hostname or ''))
    except Exception:
        raise RuntimeError('Experimental staging connection or origin configuration is invalid.') from None
    _require(valid_origin, 'An exact HTTPS staging origin is required.')
    allowed = {'host', 'port', 'dbname', 'user', 'password', 'sslmode', 'sslrootcert'}
    _require(set(database) <= allowed and set(database) >= allowed and database['password'],
             'Experimental staging requires a complete pinned database connection.')
    host = os.environ['CAPSTONE_STAGE_DB_HOST'].strip()
    name = os.environ['CAPSTONE_STAGE_DB_NAME'].strip()
    login = os.environ['CAPSTONE_STAGE_DB_LOGIN'].strip()
    role = os.environ['CAPSTONE_STAGE_DB_ROLE'].strip()
    _require(_hostname(host) and role == STAGING_ROLE
             and re.fullmatch(re.escape(role) + r'(?:\.[a-z0-9-]+)?', login)
             and database['host'] == host and database['dbname'] == name and database['user'] == login
             and database['port'] == '5432' and os.environ.get('PSMS_DB_TRANSACTION_POOLER', '0') == '0',
             'Experimental staging database identity or session-pooler settings do not match.')
    certificate = database['sslrootcert']
    _require(database['sslmode'] == 'verify-full' and not certificate.startswith(('//', '\\\\'))
             and Path(certificate).is_absolute() and Path(certificate).is_file(),
             'Experimental staging requires verify-full TLS and a readable root certificate.')
    data = os.environ['PSMS_DATA_DIR']
    _require(not data.startswith(('//', '\\\\')) and Path(data).is_absolute() and Path(data).is_dir()
             and Path(os.environ['PSMS_RETRAINED_NLP_DIR']).is_absolute() and model_dir.is_dir(),
             'Explicit staging data and model directories must exist.')
    secret = os.environ['PSMS_SECRET_KEY']
    _require(len(secret.strip()) >= 32 and not secret.strip().casefold().startswith(
        ('supply-', 'replace-', 'synthetic-', 'asuncion-psms-offline-change-me')),
        'A persistent non-placeholder staging session secret is required.')
    _require(all(os.environ.get(k) == '1' for k in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE')),
             'Experimental staging must retain offline model loading.')
    methods = config.get('WTF_CSRF_METHODS', ('POST', 'PUT', 'PATCH', 'DELETE'))
    _require(isinstance(methods, (list, tuple, set, frozenset)) and all(type(m) is str for m in methods),
             'Experimental staging CSRF configuration is invalid.')
    methods = sorted(methods)
    security = dict(secure=config.get('SESSION_COOKIE_SECURE'), httponly=config.get('SESSION_COOKIE_HTTPONLY'),
                    samesite=config.get('SESSION_COOKIE_SAMESITE'), hosts=config.get('TRUSTED_HOSTS'),
                    debug=config.get('DEBUG'), testing=config.get('TESTING', False),
                    csrf=config.get('WTF_CSRF_ENABLED', True),
                    csrf_default=config.get('WTF_CSRF_CHECK_DEFAULT', True), csrf_methods=methods)
    _require(security['secure'] is True and security['httponly'] is True
             and security['samesite'] in ('Lax', 'Strict') and security['hosts'] == [origin.hostname]
             and security['debug'] is False and security['testing'] is False and security['csrf'] is True
             and security['csrf_default'] is True and {'POST', 'PUT', 'PATCH', 'DELETE'} <= set(methods)
             and [h.strip() for h in os.environ['PSMS_TRUSTED_HOSTS'].split(',')] == [origin.hostname]
             and os.environ['PSMS_HEALTH_HOST'].strip() == origin.hostname,
             'Experimental staging requires secure cookies, exact trusted hosts, CSRF and disabled debug/testing.')
    marker = os.environ['CAPSTONE_STAGE_MARKER'].strip()
    _require(re.fullmatch(r'[A-Za-z0-9_-]{32,128}', marker), 'A staging-only environment marker is required.')
    _marker(base_dir, marker)
    _marker(Path(data).resolve(), marker)
    verify_retrained_artifacts(model_dir, base_dir)
    return dict(dsn=os.environ['SUPABASE_DB_URL'], host=host, name=name, login=login, role=role,
                marker=marker, origin=origin.geturl(), data=str(Path(data).resolve()),
                model=str(model_dir), base=str(base_dir), secret=secret, security=security)


def verify_staging_database(settings):
    """Opt-in read-only identity/capability checks; no accounts, repairs or migrations."""
    try:
        from db import connect_db
        connection = connect_db(settings['dsn'])
        try:
            connection.execute('SET TRANSACTION READ ONLY')
            connection.execute("SET LOCAL statement_timeout = '5000ms'")
            identity = connection.execute("""select current_database() as db, current_user as role,
                session_user as login, (r.rolcanlogin and not r.rolsuper and not r.rolcreatedb
                and not r.rolcreaterole and not r.rolbypassrls
                and not exists(select 1 from pg_auth_members where member=r.oid)
                and not exists(select 1 from pg_tables where tableowner=current_user
                    and schemaname in ('public','staging_support','psms_documents','psms_signatories')))
                as restricted_role from pg_roles r where r.rolname=current_user""").fetchone()
            _require(identity and identity.get('db') == settings['name']
                     and identity.get('role') == identity.get('login') == settings['role']
                     and identity.get('restricted_role') is True, 'Staging database identity rejected.')
            permissions = connection.execute("""select
                has_table_privilege(current_user,'staging_support.environment','SELECT') as can_read,
                has_table_privilege(current_user,'staging_support.environment',
                    'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') as can_write""").fetchone()
            _require(permissions and permissions.get('can_read') is True and permissions.get('can_write') is False,
                     'The staging marker must be read-only for the backend.')
            row = connection.execute('select marker from staging_support.environment where singleton=true').fetchone()
            _require(row and row.get('marker') == settings['marker'], 'Staging database marker rejected.')
            row = connection.execute('select public.psms_auth_session_permissions_ready() as ready').fetchone()
            _require(row and row.get('ready') is True, 'Staging session permissions rejected.')
        finally:
            try: connection.rollback()
            finally: connection.close()
    except Exception:
        raise RuntimeError('Synthetic staging database identity or permissions could not be verified.') from None


def inspect_selection(config, base_dir):
    """Offline selection/configuration/artifact checks. Never opens a database."""
    selected = os.environ.get('PSMS_NLP_RUNTIME', 'legacy').strip()
    _require(selected in ('legacy', 'retrained_rf'), 'PSMS_NLP_RUNTIME must be legacy or retrained_rf.')
    base = Path(base_dir).resolve()
    model = os.environ.get('PSMS_RETRAINED_NLP_DIR') or str(base / 'Retrained_Colab_Models/retrained_nlp')
    if selected == 'retrained_rf' and config['PSMS_ENV'] == 'production':
        _require(not model.startswith(('//', '\\\\')) and Path(model).is_absolute(),
                 'An absolute local staging model directory is required.')
    root = Path(model).resolve()
    stage = None
    if selected == 'retrained_rf':
        _require(all(os.environ.get(k, '').strip() for k in ('SUPABASE_DB_URL', 'PSMS_DATA_DIR')),
                 'retrained_rf requires an explicit synthetic database and data directory; no local defaults.')
        if config['PSMS_ENV'] == 'production':
            stage = _staging_settings(config, base, root)
        else:
            _require(config['PSMS_ENV'] == 'test', 'retrained_rf requires isolated tests or authorized production staging.')
    return dict(runtime=selected, root=str(root), base=str(base), stage=stage)


def _fingerprint(settings):
    return hashlib.sha256(json.dumps(settings, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def configure(app, base_dir):
    selection = inspect_selection(app.config, base_dir)
    if selection['stage'] is not None:
        verify_staging_database(selection['stage'])
    app.config.update(PSMS_NLP_RUNTIME=selection['runtime'], PSMS_RETRAINED_NLP_DIR=selection['root'],
                      _PSMS_NLP_BASE_DIR=selection['base'])
    app.config.pop('_PSMS_NLP_STAGING_FINGERPRINT', None)
    if selection['stage'] is not None:
        app.config['_PSMS_NLP_STAGING_FINGERPRINT'] = _fingerprint(selection['stage'])


def runtime(config):
    selected = config.get('PSMS_NLP_RUNTIME', 'legacy')
    if selected == 'legacy':
        import fasttext_nlp_runtime
        return fasttext_nlp_runtime
    if selected == 'retrained_rf' and config.get('PSMS_ENV') == 'test':
        import retrained_nlp_runtime
        return retrained_nlp_runtime
    if selected == 'retrained_rf' and config.get('PSMS_ENV') == 'production':
        _require(config.get('_PSMS_NLP_BASE_DIR'), 'Experimental staging was not configured.')
        selection = inspect_selection(config, config['_PSMS_NLP_BASE_DIR'])
        _require(selection['runtime'] == selected and selection['stage'] is not None
                 and selection['root'] == config.get('PSMS_RETRAINED_NLP_DIR')
                 and _fingerprint(selection['stage']) == config.get('_PSMS_NLP_STAGING_FINGERPRINT'),
                 'Experimental staging configuration changed; restart with verified settings.')
        _require(config.get('WTF_CSRF_ENABLED') is True, 'Experimental staging CSRF protection is not initialized.')
        _require(config.get('SECRET_KEY') == os.environ['PSMS_SECRET_KEY'],
                 'Experimental staging session security is not initialized.')
        verify_staging_database(selection['stage'])
        import retrained_nlp_runtime
        return retrained_nlp_runtime
    raise RuntimeError('NLP runtime selection is invalid or not authorized for this environment.')


def model_root(config, legacy_root):
    return config['PSMS_RETRAINED_NLP_DIR'] if config.get('PSMS_NLP_RUNTIME') == 'retrained_rf' else legacy_root
