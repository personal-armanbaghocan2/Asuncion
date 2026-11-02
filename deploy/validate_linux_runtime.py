"""Offline Linux CPU verification, never installation, database access or approval.

Run inside an explicitly isolated Linux validation environment. The candidate
lock is a hypothesis until wheel installation, pip check and these probes pass.
Windows results cannot pass this gate. No model is loaded without --models.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LOCK = ROOT / 'deploy/requirements-staging-linux-amd64.lock.txt'


def require(condition, code):
    if not condition:
        raise ValueError(code)


def pins(path=LOCK):
    result = {}
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith(('#', '--')):
            continue
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,-]+\])?==([A-Za-z0-9_.+!-]+)', line)
        require(match is not None, 'LINUX_CANDIDATE_LOCK_INVALID')
        name, version = match.groups()
        name = re.sub(r'[-_.]+', '-', name.lower())
        require(name not in result, 'LINUX_CANDIDATE_LOCK_DUPLICATE')
        result[name] = version
    require(result.get('torch', '').endswith('+cpu'), 'LINUX_CPU_CONTRACT_REQUIRED')
    return result


def check(*, expected_python='3.13', ca_file=None, ca_sha256=None, release=None, models=False):
    require(platform.system() == 'Linux' and platform.machine().lower() in ('x86_64', 'amd64'),
            'LINUX_AMD64_RUNTIME_REQUIRED')
    require(platform.python_implementation() == 'CPython'
            and '.'.join(platform.python_version().split('.')[:2]) == expected_python,
            'LINUX_PYTHON_VERSION_MISMATCH')
    expected = pins()
    missing, mismatched = [], []
    for name, version in expected.items():
        try:
            if importlib.metadata.version(name) != version:
                mismatched.append(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    result = {'scope': 'ISOLATED LINUX ONLY; NOT HOSTED APPROVAL', 'python': platform.python_version(),
              'machine': platform.machine(), 'packages_checked': len(expected),
              'missing_packages': missing, 'mismatched_packages': mismatched,
              'database_contacted': False, 'model_loading_tested': False,
              'deployment_approved': False, 'hosted_verified': False}
    require(not missing and not mismatched, 'LINUX_DEPENDENCY_CONTRACT_FAILED')
    # Subprocess output is intentionally withheld: pip may include local paths.
    process = subprocess.run([sys.executable, '-I', '-m', 'pip', 'check'],
                             capture_output=True, timeout=60)
    require(process.returncode == 0, 'LINUX_PIP_CHECK_FAILED')
    import torch
    require(torch.version.cuda is None and not torch.cuda.is_available(), 'LINUX_CPU_TORCH_REQUIRED')
    from deploy.runtime_diagnostics import native_probe
    result['native'] = native_probe()
    require(result['native']['code'] == 'READY', 'LINUX_TESSERACT_ENGLISH_REQUIRED')
    # No target DSN is inspected, and no TLS connection is made. An independent
    # reviewed digest is required, even for a public CA/system trust bundle.
    require(ca_file and ca_sha256 and re.fullmatch(r'[a-f0-9]{64}', ca_sha256),
            'LINUX_REVIEWED_CA_REQUIRED')
    certificate = Path(ca_file)
    require(certificate.is_absolute() and certificate.is_file()
            and hashlib.sha256(certificate.read_bytes()).hexdigest() == ca_sha256,
            'LINUX_CA_DIGEST_MISMATCH')
    import ssl
    ssl.create_default_context(cafile=str(certificate))
    result['ca'] = {'code': 'REVIEWED_CA_PARSE_PASSED', 'target_tls_verified': False}
    if models:
        require(bool(release), 'LINUX_RELEASE_REQUIRED')
        # Model hashes and exact Python/package contract are checked before all
        # deserialization. This never sets a deployment pointer or RF gate.
        import inference_releases as releases
        from deploy.inference_release import synthetic
        evidence = releases.verify(release)
        started = time.monotonic()
        result['synthetic'] = synthetic(release)
        result['synthetic_elapsed_seconds'] = round(time.monotonic() - started, 3)
        result['release_bytes'] = evidence['release_bytes']
        result['model_loading_tested'] = True
        import resource
        result['process_peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    result['code'] = 'LINUX_RUNTIME_PROBES_PASSED'
    return result


def deny_network(event, args):
    if event in {'socket.connect', 'socket.connect_ex', 'socket.getaddrinfo',
                 'socket.gethostbyname', 'socket.gethostbyaddr'}:
        raise PermissionError('LINUX_PROBE_NETWORK_DENIED')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-python', choices=('3.11', '3.12', '3.13', '3.14'), default='3.13')
    parser.add_argument('--ca-file')
    parser.add_argument('--ca-sha256')
    parser.add_argument('--release')
    parser.add_argument('--models', action='store_true')
    args = parser.parse_args()
    # This process is a probe, never an application launch. Remove inherited
    # runtime/DB overrides to avoid accidental cloud selection or model download.
    for key in list(os.environ):
        if key.startswith(('PSMS_', 'SUPABASE_', 'CAPSTONE_', 'PG', 'VERCEL', 'OCR_SEMANTIC_MODEL_PATH')):
            del os.environ[key]
    os.environ.update(PSMS_ENV='test', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      TOKENIZERS_PARALLELISM='false', OMP_NUM_THREADS='1')
    sys.addaudithook(deny_network)
    try:
        result = check(expected_python=args.expected_python, ca_file=args.ca_file,
                       ca_sha256=args.ca_sha256, release=args.release, models=args.models)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:
        code = str(error)
        if not re.fullmatch(r'(?:LINUX|RELEASE)_[A-Z0-9_]+', code):
            code = 'LINUX_RUNTIME_PROBE_FAILED'
        print(json.dumps({'code': code, 'hosted_verified': False, 'deployment_approved': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
