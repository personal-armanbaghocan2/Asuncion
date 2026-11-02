"""Conservative Vercel transfer bounds; preserve original local file limits."""
import os


def on_vercel():
    return os.environ.get('VERCEL') == '1'


def document_limit():
    return 4000000 if on_vercel() else 8 * 1024 * 1024


def request_limit():
    # Leave room for multipart boundaries, form fields and headers below 4.5 MB.
    return 4400000 if on_vercel() else 9 * 1024 * 1024
