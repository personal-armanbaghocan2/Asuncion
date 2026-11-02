"""Metadata/AST-only clean Git delivery inventory. Never reads records or secrets.

No staging, commits, file copies, app initialization, model load or network.
The reviewed candidate manifest describes required existing source; it does not
approve publication or certify repository history. Tracked-only closure is
checked separately, so untracked files cannot silently satisfy a Git checkout.
"""
import ast
import fnmatch
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT).decode('utf-8', errors='replace')


def runtime_sources(root=ROOT):
    pending = ['app.py', 'wsgi.py', 'serve.py', 'deploy/build_vercel.py', 'deploy/runtime_diagnostics.py']
    found = set()
    while pending:
        name = pending.pop()
        if name in found: continue
        path = root / name
        if not path.is_file(): raise ValueError('REQUIRED_RUNTIME_SOURCE_MISSING')
        found.add(name)
        package = list(Path(name).parts[:-1])
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            modules = []
            if isinstance(node, ast.Import): modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                prefix = package[:len(package) - node.level + 1] if node.level else []
                prefix += node.module.split('.') if node.module else []
                modules = ['.'.join(prefix)] + ['.'.join(prefix + [a.name]) for a in node.names]
            for module in modules:
                relative = module.replace('.', '/')
                for candidate in (relative + '.py', relative + '/__init__.py'):
                    if (root / candidate).is_file() and candidate not in found: pending.append(candidate)
    return found


def check():
    tracked = set(p for p in git('ls-files', '-z').split('\0') if p)
    untracked = set(p for p in git('ls-files', '--others', '--exclude-standard', '-z').split('\0') if p)
    from deploy.build_vercel_assets import ASSETS
    import inference_releases as releases
    required = runtime_sources() | {'requirements.txt', 'deploy/retrained-nlp-constraints.txt',
        'vercel.json', '.vercelignore', 'inference_release_registry.json'}
    required |= {p.relative_to(ROOT).as_posix() for p in (ROOT / 'templates').glob('*.html')}
    required |= {'static/' + p for p in ASSETS}
    patterns = [line.strip() for line in (ROOT / '.vercelignore').read_text().splitlines()
                if line.strip() and not line.lstrip().startswith('#')]
    # Simple exact/glob ordering for this repository; also tested against the
    # existing independent packaging contract in the synthetic gate.
    def included(name):
        value = True
        for raw in patterns:
            negated = raw.startswith('!')
            rule = raw.lstrip('!').lstrip('/')
            match = fnmatch.fnmatchcase(name, rule) if '**' in rule else (
                fnmatch.fnmatchcase(name, rule) if '/' not in name[len(rule.split('*')[0]):] else name == rule)
            if rule.endswith('/'): match = name == rule[:-1] or (not negated and name.startswith(rule))
            if match: value = negated
        return value
    forbidden_prefixes = ('data/', 'uploads/', 'temp/', 'training/', 'training_data/', 'training_v2/',
        'legacy_training/', 'sample_files/', 'experiments/', 'model/', 'models/',
        'Retrained_Colab_Models/', 'inference_releases/', 'output/')
    forbidden_suffixes = ('.xlsx', '.xlsm', '.xls', '.csv', '.joblib', '.model', '.safetensors',
                          '.npy', '.pem', '.key', '.dump', '.backup', '.zip')
    unsafe = sorted(p for p in tracked | untracked if p.startswith(forbidden_prefixes)
                    or p.lower().endswith(forbidden_suffixes) or '/LOCAL_ONLY/' in p
                    or (Path(p).name.startswith('.env') and p != '.env.example'))
    missing = sorted(required - tracked)
    def exact_case(name):
        parent = ROOT
        for part in Path(name).parts:
            if not parent.is_dir() or part not in {p.name for p in parent.iterdir()}: return False
            parent = parent / part
        return parent.is_file()
    status = git('status', '--porcelain=v1', '-z', '-uall').split('\0')
    changed = {row[3:] for row in status if len(row) > 3}
    return {'scope': 'SOURCE METADATA ONLY; NOT PUBLICATION APPROVAL', 'required_source_paths': sorted(required),
        'required_untracked_paths': missing, 'missing_from_workspace': sorted(p for p in required if not (ROOT / p).is_file()),
        'linux_case_mismatches': sorted(p for p in required if not exact_case(p)),
        'tracked_checkout_complete': not missing,
        'vercel_excluded_required': sorted(p for p in required - {'.vercelignore'} if not included(p)),
        'modified_or_untracked_required': sorted(required & changed), 'unsafe_publication_candidates': unsafe,
        'earlier_source_and_review_files_requiring_classification': sorted((tracked | untracked) - required - set(unsafe)),
        'release_pointer_active': bool(releases.read_json(ROOT / releases.REGISTRY).get('deployment_release')),
        'model_bytes_in_manifest': False, 'publication_approved': False, 'database_access': False, 'deployed': False}


if __name__ == '__main__': print(json.dumps(check(), indent=2, sort_keys=True))
