"""Required session-bound internal GET links; never a replacement for authorization.

The URL contains only an opaque MAC. Its private input binds the validated
account's ID, server-session digest, endpoint and canonical route arguments.
Ordinary query filters remain editable; route/resource checks still run normally.
Missing, foreign or invalid navigation context requires a fresh sign-in.
Register after account/session validation and before route execution.
"""
import base64
import hashlib
import hmac
import json
import re

from flask import current_app, g, jsonify, redirect, request, session, url_for, Response
from werkzeug.routing import BuildError

import server_sessions_v0_1_0 as sessions

PARAMETER = '_nav'
PUBLIC_ENDPOINTS = frozenset(('static', 'login', 'runtime_liveness'))
_SIGNATURE = re.compile(r'[A-Za-z0-9_-]{43}\Z')
_PURPOSE = b'psms-session-navigation-v1\0'


def _identity():
    uid = session.get('user_id')
    digest = sessions.token_hash(session.get(sessions.COOKIE_KEY))
    if session.get('logged_in') is not True or type(uid) is not int or uid <= 0 or not digest:
        return None
    return uid, digest


def _validated_identity():
    identity = _identity()
    account = getattr(g, 'psms_account', None)
    if (identity is None or not account or type(account.get('id')) is not int
            or account.get('id') != identity[0] or account.get('status') != 'Active'
            or account.get('role') not in ('Admin', 'Staff', 'Inspector')):
        return None
    return identity


def _secret(app):
    value = app.secret_key
    if isinstance(value, str):
        value = value.encode('utf-8')
    return value if isinstance(value, bytes) and len(value) >= 32 else None


def _canonical_route(app, endpoint, values):
    # Let the installed Werkzeug converters normalize int/UUID/path arguments.
    # No ordinary query values enter this canonical representation. The builder
    # does not call Flask URL defaults, so signing cannot recursively call itself.
    try:
        arguments = {name for rule in app.url_map.iter_rules(endpoint) for name in rule.arguments}
        route_values = {name: values[name] for name in arguments if name in values}
        return app.url_map.bind('navigation.invalid').build(
            endpoint, route_values, append_unknown=False)
    except (BuildError, LookupError, TypeError, ValueError):
        return None


def _token(app, endpoint, values):
    identity = _identity()
    key = _secret(app)
    if identity is None or key is None or endpoint in PUBLIC_ENDPOINTS:
        return ''
    route = _canonical_route(app, endpoint, values)
    if route is None:
        return ''
    message = json.dumps([identity[0], identity[1], endpoint, route],
                         ensure_ascii=True, separators=(',', ':')).encode('ascii')
    digest = hmac.new(key, _PURPOSE + message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')


def navigation_token(endpoint=None, **route_values):
    """Return a hidden GET-form token for the current route, or a named route.

    Example: <input type="hidden" name="_nav" value="{{ navigation_token() }}">.
    Callers choosing an endpoint must supply its resource/route arguments.
    """
    if endpoint is None:
        endpoint = request.endpoint
        route_values = dict(request.view_args or {})
    return _token(current_app, endpoint, route_values) if endpoint else ''


def _api_request():
    return (request.path == '/api' or request.path.startswith('/api/')
            or request.endpoint in ('excel_status','health'))


def _denied(status):
    message = 'Sign in to continue.' if status == 401 else 'This navigation could not be verified. Open the page from the application menu.'
    if _api_request():
        response = jsonify(error=message)
        response.status_code = status
    elif status == 401:
        response = redirect(url_for('login'))
    else:
        response = Response(message, status=status, mimetype='text/plain')
    response.headers.update({'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer'})
    return response


def register(app):
    """Add URL signing, the navigation gate, and the GET-form template helper.

    Existing session validation must run first and set ``g.psms_account``.
    Unsafe methods retain their existing session, CSRF and authorization checks.
    """
    if 'session_navigation' in app.extensions:
        return
    if _secret(app) is None:
        raise RuntimeError('A persistent application secret is required for internal navigation.')
    app.extensions['session_navigation'] = {'parameter': PARAMETER}

    @app.url_defaults
    def signed_internal_url(endpoint, values):
        # Sign the post-login dashboard too: bare launch URLs follow the same
        # policy as protected details, APIs, pagination and document downloads.
        if endpoint not in PUBLIC_ENDPOINTS:
            token = _token(app, endpoint, values)
            if token:
                values[PARAMETER] = token

    @app.context_processor
    def navigation_context():
        return {'navigation_token': navigation_token}

    @app.before_request
    def require_internal_navigation():
        if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
            return None
        if request.method not in ('GET', 'HEAD'):
            return None
        if _validated_identity() is None:
            return _denied(401)
        supplied = request.args.getlist(PARAMETER)
        # A static URL cannot identify copying. Require the same navigation
        # context for every protected GET, including a manually typed URL.
        # Never mint replacement context from an untrusted/foreign destination.
        if len(supplied) != 1 or not _SIGNATURE.fullmatch(supplied[0]):
            session.clear()
            return _denied(401)
        expected = _token(app, request.endpoint, request.view_args or {})
        if not expected or not hmac.compare_digest(supplied[0], expected):
            session.clear()
            return _denied(401)
        return None
