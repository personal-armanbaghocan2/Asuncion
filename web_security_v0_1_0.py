"""Flask-WTF protection for every unsafe browser action; no DB/model access."""
from flask import Response
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError

VERSION = '0.1.0'


def init_csrf(app):
    """Register after account/session middleware; preserve its fail-closed checks."""
    app.config.setdefault('WTF_CSRF_TIME_LIMIT', 3600)
    protection = CSRFProtect(app)

    @app.errorhandler(CSRFError)
    def csrf_failed(error):
        # Never echo the submitted token, URL, form values, or internal reason.
        return Response(
            'This form could not be verified. Return to the page, refresh it, and try again.',
            status=400, mimetype='text/plain', headers={'Cache-Control': 'no-store'})

    return protection
