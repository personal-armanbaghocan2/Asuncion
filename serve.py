"""Portable Waitress launcher; existing Windows launchers remain supported."""
import os
from runtime_configuration import environment_mode


def server_settings():
    mode = environment_mode()
    host = os.environ.get('PSMS_BIND_HOST', '0.0.0.0' if mode == 'production' else '127.0.0.1').strip()
    if not host or any(c.isspace() for c in host) or '/' in host:
        raise RuntimeError('PSMS_BIND_HOST must be a valid listening interface.')
    try:
        port = int(os.environ.get('PORT', '5000'))
        threads = int(os.environ.get('PSMS_THREADS', '4'))
    except ValueError:
        raise RuntimeError('PORT and PSMS_THREADS must be integers.') from None
    if not 1 <= port <= 65535 or not 1 <= threads <= 16:
        raise RuntimeError('PORT must be 1-65535; PSMS_THREADS must be 1-16.')
    from transfer_limits import request_limit
    return dict(host=host, port=port, threads=threads, max_request_body_size=request_limit(),
                clear_untrusted_proxy_headers=True)


def main():
    settings = server_settings()
    from waitress import serve
    from wsgi import application
    if application.debug:
        raise RuntimeError('The production WSGI launcher refuses debug mode.')
    serve(application, **settings)


if __name__ == '__main__': main()
