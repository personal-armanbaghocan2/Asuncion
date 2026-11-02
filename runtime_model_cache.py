"""Bounded, private, instance-local delivery of ORIGINAL approved model bytes.

Only called on model use, never by liveness/startup or a background task. The
Git-reviewed inline manifest/transport digest are trust anchors; credentials and
HTTP responses are never written to logs. This is not durable document storage.
"""
from contextlib import contextmanager
import base64
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import stat
import tempfile
import threading
import time
from urllib.parse import quote, urlsplit
import urllib.request
import zipfile

import inference_releases as releases

CACHE_BASE = Path('/tmp/psms-model-cache')
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_ARTIFACT_BYTES = 200 * 1024 * 1024
MAX_PART_BYTES = 40_000_000  # also fits Supabase Free's per-object allowance
RESERVE_BYTES = 64 * 1024 * 1024
LOCK_SECONDS = 75
DOWNLOAD_SECONDS = 60
_LOCK = threading.Lock()
SIGNED_PARTS_AUTH = 'supabase_signed_parts'
SIGNED_PARTS_SECRET = 'PSMS_MODEL_PART_TOKENS'
MODEL_BUCKET = 'psms-private-model-releases'


def validate_signed_parts(delivery, identifier):
    """Pin the Supabase project, private bucket and ROOT object names; no secrets."""
    auth = delivery.get('authentication', {})
    releases.require(isinstance(auth, dict) and set(auth) == {
        'type', 'project_ref', 'bucket', 'release_id'} and auth['type'] == SIGNED_PARTS_AUTH
        and re.fullmatch(r'[a-z0-9]{20}', auth['project_ref'] or '')
        and auth['bucket'] == MODEL_BUCKET and auth['release_id'] == identifier,
        'RELEASE_SIGNED_CONFIGURATION')
    parts = delivery.get('parts')
    releases.require(isinstance(parts, list) and bool(parts), 'RELEASE_SIGNED_CONFIGURATION')
    for index, part in enumerate(parts):
        name = identifier + f'.{index:03d}.part'
        expected = ('https://' + auth['project_ref'] + '.supabase.co/storage/v1/object/sign/'
                    + MODEL_BUCKET + '/' + name)
        releases.require(part.get('object_name') == name and part.get('url') == expected,
                         'RELEASE_SIGNED_OBJECT_PATH')


def _signed_tokens(delivery):
    """Preflight object-scoped capabilities, NOT Auth/API keys or local JWT verification.

    Supabase verifies signature/scope/expiry over TLS. These untrusted claims only
    prevent configuration errors and broad role credentials before sending I/O.
    No signing key, user password or refresh/service-role credential is needed.
    """
    try:
        validate_signed_parts(delivery, delivery['authentication']['release_id'])
        releases.require(not os.environ.get('PSMS_INFERENCE_READ_TOKEN'), 'RELEASE_LEGACY_TOKEN_REMOVE')
        raw = os.environ.get(SIGNED_PARTS_SECRET, '')
        releases.require(raw and len(raw) <= 24000, 'RELEASE_SIGNED_TOKENS_REQUIRED')
        tokens = json.loads(raw)
        releases.require(isinstance(tokens, dict) and set(tokens) == {
            p['object_name'] for p in delivery['parts']}, 'RELEASE_SIGNED_TOKEN_SET')
        expirations = []
        for part in delivery['parts']:
            token = tokens[part['object_name']]
            releases.require(isinstance(token, str) and len(token) <= 4096
                and re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', token),
                'RELEASE_SIGNED_TOKEN_INVALID')
            header, payload, _ = token.split('.')
            decode = lambda s: json.loads(base64.urlsafe_b64decode(s + '=' * (-len(s) % 4)))
            header, payload = decode(header), decode(payload)
            releases.require(isinstance(header, dict) and header.get('alg') in {
                'HS256','HS384','HS512','RS256','RS384','RS512','ES256','ES384','ES512','EdDSA'},
                'RELEASE_SIGNED_TOKEN_INVALID')
            # Legacy download tokens omit scope; upload/Auth tokens are never accepted.
            releases.require(isinstance(payload, dict) and set(payload) <= {
                'url','scope','iat','exp','versionId'} and payload.get('scope') in (None, 'download')
                and payload.get('url') == MODEL_BUCKET + '/' + part['object_name'],
                'RELEASE_SIGNED_TOKEN_SCOPE')
            releases.require(type(payload.get('exp')) is int
                and payload['exp'] > time.time() + DOWNLOAD_SECONDS + 30,
                'RELEASE_SIGNED_TOKEN_EXPIRED')
            expirations.append(payload['exp'])
        return tokens, min(expirations)
    except releases.ReleaseError:
        raise
    except Exception:
        raise releases.ReleaseError('RELEASE_SIGNED_TOKEN_INVALID') from None


def location(identifier):
    return releases.safe_path(CACHE_BASE, releases.release_id(identifier), exists=False)


def delivery_settings(identifier, evidence, base=releases.BASE):
    """No I/O/credentials: safe both during context creation and image build."""
    try:
        delivery = releases.entry_for(identifier, base).get('delivery', {})
        releases.require(type(delivery.get('archive_bytes')) is int
            and 0 < delivery['archive_bytes'] <= MAX_ARCHIVE_BYTES
            and isinstance(delivery.get('archive_sha256'), str)
            and len(delivery['archive_sha256']) == 64
            and all(c in '0123456789abcdef' for c in delivery['archive_sha256'])
            and 0 < evidence['artifact_bytes'] <= MAX_ARTIFACT_BYTES,
            'RELEASE_DELIVERY_LIMIT')
        parts = delivery.get('parts')
        if parts is None:
            parts = [dict(url=delivery.get('url'), bytes=delivery['archive_bytes'],
                          sha256=delivery['archive_sha256'])]
        releases.require(isinstance(parts, list) and 1 <= len(parts) <= 8, 'RELEASE_DELIVERY_LIMIT')
        host = None
        for part in parts:
            url = part['url']
            parsed = urlsplit(url)
            releases.require(parsed.scheme == 'https' and parsed.hostname
                and parsed.port in (None, 443) and not parsed.username and not parsed.password
                and not parsed.query and not parsed.fragment and '\\' not in url
                and not any(c.isspace() for c in url), 'RELEASE_DELIVERY_CONFIGURATION')
            releases.require(host in (None, parsed.hostname), 'RELEASE_DELIVERY_HOST')
            host = parsed.hostname
            releases.require(type(part['bytes']) is int and 0 < part['bytes'] <=
                (MAX_PART_BYTES if 'parts' in delivery else MAX_ARCHIVE_BYTES)
                and isinstance(part['sha256'], str) and len(part['sha256']) == 64
                and all(c in '0123456789abcdef' for c in part['sha256']), 'RELEASE_DELIVERY_LIMIT')
        releases.require(sum(p['bytes'] for p in parts) == delivery['archive_bytes']
            and len({p['url'] for p in parts}) == len(parts), 'RELEASE_DELIVERY_LIMIT')
        delivery = dict(delivery, parts=parts)
        auth = delivery.get('authentication')
        if auth is not None:
            validate_signed_parts(delivery, identifier)
        else:
            releases.require(not any((urlsplit(p['url']).hostname or '').endswith('.supabase.co')
                for p in parts), 'RELEASE_SUPABASE_SCOPED_AUTH_REQUIRED')
        return delivery
    except releases.ReleaseError:
        raise
    except Exception:
        raise releases.ReleaseError('RELEASE_DELIVERY_CONFIGURATION') from None


@contextmanager
def _locked(identifier):
    """Thread + Linux flock protects one download/extract/publish per instance.

    Windows tests also use a bounded OS file lock; production image uses flock.
    No lock coordinates database/workbook state or survives instance destruction.
    """
    deadline = time.monotonic() + LOCK_SECONDS
    releases.require(_LOCK.acquire(timeout=LOCK_SECONDS), 'RELEASE_CACHE_LOCK_TIMEOUT')
    locked = False
    stream = None
    try:
        CACHE_BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
        location(identifier)  # checks cache ancestors for links/junctions
        if os.name == 'posix':
            info = CACHE_BASE.stat()
            releases.require(info.st_uid == os.geteuid() and not info.st_mode & 0o077,
                             'RELEASE_CACHE_PERMISSIONS')
        lock_path = releases.safe_path(CACHE_BASE, identifier + '.lock', exists=False)
        stream = lock_path.open('a+b')
        stream.seek(0)
        if not stream.read(1):
            stream.write(b'0'); stream.flush()
        while not locked:
            try:
                if os.name == 'posix':
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                locked = True
            except (BlockingIOError, OSError):
                releases.require(time.monotonic() < deadline, 'RELEASE_CACHE_LOCK_TIMEOUT')
                time.sleep(0.05)
        yield
    finally:
        if stream is not None:
            try:
                if locked:
                    if os.name == 'posix':
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                    else:
                        import msvcrt
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                stream.close()
        _LOCK.release()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _download(delivery, path):
    if delivery.get('authentication') is not None:
        tokens, _ = _signed_tokens(delivery)
        requests = [(p['url'] + '?token=' + quote(tokens[p['object_name']], safe=''),
                     {'Accept-Encoding': 'identity'}) for p in delivery['parts']]
    else:
        token = os.environ.get('PSMS_INFERENCE_READ_TOKEN', '')
        releases.require(token and not any(c.isspace() for c in token), 'RELEASE_READ_TOKEN_REQUIRED')
        requests = [(p['url'], {'Authorization': 'Bearer ' + token, 'Accept-Encoding': 'identity'})
                    for p in delivery['parts']]
    # Do not inherit arbitrary HTTP(S)_PROXY settings for credential-bearing I/O.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    deadline = time.monotonic() + DOWNLOAD_SECONDS
    digest = hashlib.sha256()
    received = 0
    with path.open('xb') as output:
        for part, (url, headers) in zip(delivery['parts'], requests):
            releases.require(time.monotonic() < deadline, 'RELEASE_RETRIEVAL_TIMEOUT')
            request = urllib.request.Request(url, headers=headers)
            part_hash = hashlib.sha256()
            part_bytes = 0
            with opener.open(request, timeout=min(10, max(0.1, deadline-time.monotonic()))) as response:
                releases.require(response.status == 200, 'RELEASE_RETRIEVAL_FAILED')
                while True:
                    releases.require(time.monotonic() < deadline, 'RELEASE_RETRIEVAL_TIMEOUT')
                    # read1 returns after one underlying read; read(n) can wait
                    # to fill n bytes and defeat the between-read deadline.
                    block = response.read1(min(1024 * 1024, part['bytes'] - part_bytes + 1))
                    if not block:
                        break
                    part_bytes += len(block); received += len(block)
                    releases.require(part_bytes <= part['bytes'], 'RELEASE_ARCHIVE_LIMIT')
                    part_hash.update(block); digest.update(block); output.write(block)
            releases.require(part_bytes == part['bytes'] and part_hash.hexdigest() == part['sha256'],
                             'RELEASE_ARCHIVE_CHECKSUM')
    releases.require(received == delivery['archive_bytes']
        and digest.hexdigest() == delivery['archive_sha256'], 'RELEASE_ARCHIVE_CHECKSUM')


def _extract(path, staged, evidence):
    expected = evidence['manifest']['artifacts']
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        names = [m.filename for m in members]
        releases.require(len(names) == len(set(names))
            and set(names) == set(releases.SOURCES) | {releases.MANIFEST}, 'RELEASE_ARCHIVE_FILES')
        for member in members:
            size = (len(releases.canonical(evidence['manifest'])) if member.filename == releases.MANIFEST
                    else expected[member.filename]['bytes'])
            releases.require(member.compress_type == zipfile.ZIP_STORED and not member.is_dir()
                and not member.flag_bits & 1 and member.file_size == member.compress_size == size
                and stat.S_IFMT(member.external_attr >> 16) == stat.S_IFREG,
                'RELEASE_ARCHIVE_TYPE')
            target = releases.safe_path(staged, member.filename, exists=False)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)


def ensure(identifier, evidence, *, base=releases.BASE, profiles=('legacy', 'semantic_ocr')):
    """Return a verified release directory or fail closed; no model deserialization."""
    try:
        # Revalidate approval at this boundary, including before a cache hit.
        trusted = releases.metadata(identifier, base=base, profiles=profiles, dependencies=False)
        releases.require(trusted == evidence or (trusted['manifest'] == evidence['manifest']
            and trusted['manifest_sha256'] == evidence['manifest_sha256']), 'RELEASE_METADATA_INVALID')
        delivery = delivery_settings(identifier, trusted, base)
        target = location(identifier)
        with _locked(identifier):
            if target.exists():
                releases.verify(identifier, base=base, directory=target, deployment=True,
                                profiles=profiles, dependencies=False)
                return target
            required = delivery['archive_bytes'] + trusted['artifact_bytes'] + 1024 * 1024 + RESERVE_BYTES
            releases.require(shutil.disk_usage(CACHE_BASE).free >= required, 'RELEASE_CACHE_SPACE')
            with tempfile.TemporaryDirectory(prefix='.download-', dir=CACHE_BASE) as temporary:
                temporary = Path(temporary)
                archive = temporary / 'models.zip'
                staged = temporary / 'verified'
                staged.mkdir()
                _download(delivery, archive)
                _extract(archive, staged, trusted)
                releases.verify(identifier, base=base, directory=staged, deployment=True,
                                profiles=profiles, dependencies=False)
                # Atomic directory publication; no partial cache can be consumed.
                os.replace(staged, target)
            return target
    except releases.ReleaseError:
        raise
    except Exception:
        raise releases.ReleaseError('RELEASE_RETRIEVAL_FAILED') from None
