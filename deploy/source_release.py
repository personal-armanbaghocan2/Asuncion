"""Reproducible source-only review archive. No Git mutations or activation.

The source proposal is a review inventory, not a model/deployment approval.
Only its exact runtime allowlist is copied; the archive is kept under output/.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PROPOSAL = ROOT / 'deploy/proposals/online_backend_source_manifest.PROPOSED.json'
FORBIDDEN = {'data', 'uploads', 'temp', 'output', 'training', 'training_data', 'training_v2',
             'legacy_training', 'sample_files', 'experiments', 'model', 'models',
             'Retrained_Colab_Models', 'inference_releases', 'secrets', '.git', '.aws'}
SUFFIXES = {'.pem', '.key', '.pfx', '.p12', '.csv', '.xlsx', '.xlsm', '.xls', '.joblib',
            '.npy', '.model', '.safetensors', '.db', '.dump', '.backup', '.zip'}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def safe_name(name):
    path = PurePosixPath(name)
    require(isinstance(name, str) and name == path.as_posix() and not path.is_absolute()
            and all(p not in ('', '.', '..') and ':' not in p and '\\' not in p for p in path.parts)
            and bool(path.parts), 'SOURCE_PATH_INVALID')
    require(not any(p in FORBIDDEN or 'LOCAL_ONLY' in p for p in path.parts)
            and not path.name.startswith('.env') and path.suffix.lower() not in SUFFIXES,
            'SOURCE_PRIVATE_PATH_DENIED')
    return path


def normalized(content, mode):
    if mode == 'UTF8-LF-text':
        return content.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n').encode('utf-8')
    require(mode == 'binary', 'SOURCE_HASH_MODE_INVALID')
    return content


def manifest(path=PROPOSAL):
    proposal = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    require(proposal.get('schema') == 'ASUNCION_ONLINE_SOURCE_REVIEW_V1'
            and proposal.get('approval') == 'BLOCKED_PENDING_OWNER_REVIEW'
            and proposal.get('models_in_source_manifest') is False, 'SOURCE_PROPOSAL_INVALID')
    files = proposal['files']
    require(files and len({p.casefold() for p in files}) == len(files), 'SOURCE_PATH_COLLISION')
    for name in files:
        safe_name(name)
    return files


def validate_bytes(content, entry):
    require(len(content) == entry['bytes'] and hashlib.sha256(content).hexdigest() == entry['sha256'],
            'SOURCE_HASH_MISMATCH')


def source_bytes(root=ROOT, proposal=PROPOSAL):
    root = Path(root).resolve()
    files = manifest(proposal)
    # Imported only for AST analysis; this does not import the Flask application.
    sys.path.insert(0, str(ROOT))
    from scripts.audits.online_backend_manifest import runtime_sources
    require(runtime_sources(root) <= set(files), 'SOURCE_IMPORT_CLOSURE_INCOMPLETE')
    result = {}
    for name, entry in files.items():
        path = root.joinpath(*safe_name(name).parts)
        require(path.resolve().is_relative_to(root) and path.is_file(), 'SOURCE_FILE_MISSING')
        current = path
        while current != root:
            require(not current.is_symlink() and not getattr(current, 'is_junction', lambda: False)(),
                    'SOURCE_LINK_DENIED')
            current = current.parent
        content = normalized(path.read_bytes(), entry['hash_mode'])
        validate_bytes(content, entry)
        result[name] = content
    return result


def output_path(path):
    path = Path(path).resolve()
    require(path.is_relative_to((ROOT / 'output').resolve()) and path.suffix == '.zip',
            'SOURCE_ARCHIVE_OUTPUT_MUST_BE_PRIVATE')
    require(not path.exists(), 'SOURCE_ARCHIVE_ALREADY_EXISTS')
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def pack(destination, *, root=ROOT, proposal=PROPOSAL):
    contents = source_bytes(root, proposal)
    destination = output_path(destination)
    # Exclusive creation: existing evidence/archives are never overwritten.
    with destination.open('xb') as stream, zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(contents.items()):
            info = zipfile.ZipInfo(name, (2000, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return {'files': len(contents), 'source_bytes': sum(map(len, contents.values())),
            'archive_bytes': destination.stat().st_size,
            'archive_sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
            'model_bytes_included': False, 'publication_approved': False}


def verify(archive_path, *, proposal=PROPOSAL):
    files = manifest(proposal)
    contents = {}
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        require(len(members) == len(files) and {m.filename for m in members} == set(files),
                'SOURCE_ARCHIVE_MEMBERS_INVALID')
        for member in members:
            safe_name(member.filename)
            require(member.compress_type == zipfile.ZIP_STORED and member.file_size == files[member.filename]['bytes']
                    and member.external_attr >> 16 == 0o100644, 'SOURCE_ARCHIVE_MEMBER_INVALID')
            content = archive.read(member)
            validate_bytes(content, files[member.filename])
            contents[member.filename] = content
    return contents


# Runs in a new isolated interpreter with only extracted source and installed
# dependencies. Neither the worktree nor tests are available as import fallbacks.
SMOKE = r'''
import importlib, json, os, pathlib, sys
root = pathlib.Path.cwd().resolve()
workspace = root.parent.parent
dependency_prefix = pathlib.Path(sys.prefix).resolve()
sys.path.insert(0, str(root))
sys.dont_write_bytecode = True
def guard(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr'}:
        raise PermissionError('SOURCE_PROBE_NETWORK_DENIED')
    if event == 'open' and isinstance(args[0], (str, bytes)):
        target = pathlib.Path(os.fsdecode(args[0])).resolve()
        if target.is_relative_to(workspace) and not target.is_relative_to(root):
            # Allow a project-local installed venv, never other worktree files.
            if not (dependency_prefix != workspace and target.is_relative_to(dependency_prefix)):
                raise PermissionError('SOURCE_PROBE_WORKSPACE_READ_DENIED')
sys.addaudithook(guard)
import psycopg
def denied(*args, **kwargs):
    raise AssertionError('SOURCE_PROBE_DATABASE_DENIED')
psycopg.connect = denied
try:
    try:
        (workspace / 'app.py').read_bytes()
    except PermissionError:
        pass
    else:
        raise AssertionError('SOURCE_PROBE_WORKSPACE_GUARD_FAILED')
    import app, wsgi
    assert wsgi.application is app.app
    # Compile all delivered Python, resolve all templates and import every
    # delivered runtime module, including lazy procurement/review imports.
    count = 0
    for path in sorted(root.rglob('*.py')):
        compile(path.read_text(encoding='utf-8-sig'), str(path), 'exec')
        parts = path.relative_to(root).with_suffix('').parts
        name = '.'.join(parts[:-1] if parts[-1] == '__init__' else parts)
        if name:
            loaded = importlib.import_module(name)
            assert pathlib.Path(loaded.__file__).resolve().is_relative_to(root)
            count += 1
    templates = app.app.jinja_env.list_templates()
    for name in templates:
        app.app.jinja_env.get_template(name)
    client = app.app.test_client()
    assert client.get('/health/live').json == {'status': 'alive'}
    assert client.get('/login').status_code == 200
    for path in ('/', '/ppmp/dataset', '/inventory', '/users'):
        assert client.get(path).status_code in (302, 401, 403)
    from deploy.build_vercel_assets import build_assets
    assets = build_assets(root)
    print(json.dumps({'code': 'SOURCE_SMOKE_PASSED', 'modules': count,
                      'templates': len(templates), 'assets': assets,
                      'database': False, 'network': False, 'workspace_reads_denied': True,
                      'hosted_verified': False}))
except Exception as error:
    # Never forward raw configuration, source lines, paths or connection errors.
    print(json.dumps({'code': 'SOURCE_SMOKE_FAILED', 'error_type': type(error).__name__}))
    raise SystemExit(1)
'''


def smoke(archive, *, proposal=PROPOSAL):
    contents = verify(archive, proposal=proposal)
    output = ROOT / 'output'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='source-probe-', dir=output) as folder:
        root = Path(folder)
        for name, content in contents.items():
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        environment = {k: v for k, v in os.environ.items() if k.upper() in
                       ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'LANG')}
        environment.update(PSMS_ENV='test', PSMS_SECRET_KEY='SYNTHETIC-source-probe-only-' + 'x' * 48,
                           PSMS_DATA_DIR=str(root / 'synthetic-data'),
                           SUPABASE_DB_URL='postgresql://synthetic:synthetic@127.0.0.1:1/synthetic',
                           HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false',
                           OMP_NUM_THREADS='1')
        process = subprocess.run([sys.executable, '-I', '-B', '-c', SMOKE], cwd=root, env=environment,
                                 capture_output=True, text=True, timeout=120)
        lines = [line for line in process.stdout.splitlines() if line.startswith('{')]
        require(bool(lines), 'SOURCE_SMOKE_NO_RESULT')
        result = json.loads(lines[-1])
        require(process.returncode == 0 and result.get('code') == 'SOURCE_SMOKE_PASSED',
                'SOURCE_SMOKE_FAILED_' + str(result.get('error_type', 'UNKNOWN')))
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('check', 'pack', 'verify'))
    parser.add_argument('--archive')
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    try:
        if args.operation == 'check':
            result = {'files': len(source_bytes()), 'code': 'SOURCE_HASHES_VALID', 'publication_approved': False}
        elif args.operation == 'pack':
            require(bool(args.archive), 'SOURCE_ARCHIVE_REQUIRED')
            result = pack(args.archive)
        else:
            require(bool(args.archive), 'SOURCE_ARCHIVE_REQUIRED')
            result = {'files': len(verify(args.archive)), 'code': 'SOURCE_ARCHIVE_VERIFIED'}
        if args.smoke:
            require(args.operation != 'check', 'SOURCE_ARCHIVE_REQUIRED')
            result['smoke'] = smoke(args.archive)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, zipfile.BadZipFile, subprocess.TimeoutExpired) as error:
        code = str(error)
        print(json.dumps({'code': code if re.fullmatch(r'SOURCE_[A-Za-z_]+', code) else 'SOURCE_CHECK_FAILED'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
