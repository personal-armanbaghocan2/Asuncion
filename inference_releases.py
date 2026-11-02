"""Private, immutable inference releases. No database or pickle imports.

The reviewed registry in application source pins each manifest. A manifest in a
model directory is never its own trust anchor. Approval is a separate reviewed
decision; checksums and successful synthetic inference do not grant approval.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import sys

BASE = Path(__file__).resolve().parent
REGISTRY = 'inference_release_registry.json'
SOURCE_CONTRACT = 'inference_release_sources.json'
MANIFEST = 'release.json'
SCHEMA = 'ASUNCION_INFERENCE_RELEASE_V1'
HEAD = 'models/Random_Forest/setting_7/seed_42'
FEATURE = 'legacy/fasttext_feature_schema_v2.py'
PROFILES = {'legacy': 'legacy', 'retrained_rf': 'rf', 'semantic_ocr': 'semantic'}
STATUS = ['Acceptable', 'Needs Review', 'Needs Recanvass', 'Reject']
ISSUES = ['Requirement satisfied', 'Missing or ambiguous information', 'Below minimum',
          'Above maximum', 'Exact or range mismatch', 'Quantity or unit mismatch',
          'Specification mismatch', 'Different item', 'Missing item']
CONTRACTS = {
    'legacy': {'schema': 'FASTTEXT_NUMERIC_RELATION_V2', 'embedding_size': 120,
               'issue_features': 659, 'status_features': 668, 'numeric_features': 59,
               'status_uses_issue_probabilities': True, 'classifier': 'LogisticRegression',
               'status_labels': STATUS, 'issue_labels': ISSUES},
    'retrained_rf': {'schema': 'FASTTEXT_NUMERIC_RELATION_V2', 'embedding_size': 100,
                     'issue_features': 559, 'status_features': 559, 'dtype': 'float32',
                     'numeric_features': 59, 'status_uses_issue_probabilities': False,
                     'classifier': 'RandomForestClassifier', 'setting': 7, 'seed': 42,
                     'status_labels': STATUS, 'issue_labels': ISSUES},
    'semantic_ocr': {'architecture': 'all-MiniLM-L6-v2', 'embedding_size': 384,
                     'modules': ['Transformer', 'Pooling', 'Normalize'],
                     'local_files_only': True},
}
LEGACY = ('fasttext_model.model', 'fasttext_model.model.wv.vectors_ngrams.npy',
          'status_classifier.joblib', 'issue_classifier.joblib', 'labels.json',
          'training_manifest.json', 'fasttext_feature_schema_v2.py')
RF = ('fasttext_shared.model', 'configuration.json', 'comparison.json', 'environment.json',
      'feature_contract.json', 'labels.json', 'reload_probes.json', 'reload_probe_features.npy',
      HEAD + '/status.joblib', HEAD + '/issue.joblib',
      HEAD + '/status_parameters.json', HEAD + '/issue_parameters.json')
SEMANTIC = ('config.json', 'config_sentence_transformers.json', 'model.safetensors',
            'modules.json', 'sentence_bert_config.json', 'tokenizer.json',
            'tokenizer_config.json', '1_Pooling/config.json', 'README.md')
SOURCES = {**{'legacy/' + p: 'model/fasttext_nlp/' + p for p in LEGACY},
           **{'rf/' + p: 'Retrained_Colab_Models/retrained_nlp/' + p for p in RF},
           **{'semantic/' + p: 'models/ocr_mapper/all-MiniLM-L6-v2/' + p for p in SEMANTIC}}
PACKAGES = ('numpy', 'scipy', 'gensim', 'scikit-learn', 'joblib', 'threadpoolctl',
            'sentence-transformers', 'torch', 'transformers', 'tokenizers', 'safetensors')


class ReleaseError(RuntimeError):
    """Only non-sensitive, stable diagnostic codes cross the runtime boundary."""


def inference_enabled():
    """Explicit deployment off switch; local/previous opt-in behavior is preserved."""
    value = os.environ.get('PSMS_INFERENCE_ENABLED', '1')
    require(value in ('0', '1'), 'INFERENCE_SETTING_INVALID')
    return value == '1'


def require_inference():
    require(inference_enabled(), 'INFERENCE_DISABLED')


def require(condition, code):
    if not condition:
        raise ReleaseError(code)


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode('utf-8')


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def safe_path(root, name, *, exists=True):
    """Reject traversal, Windows paths, symlinks and junctions before resolution."""
    require(isinstance(name, str) and '\\' not in name and ':' not in name,
            'RELEASE_UNSAFE_PATH')
    rel = PurePosixPath(name)
    require(name == rel.as_posix() and not rel.is_absolute()
            and all(p not in ('', '.', '..') for p in name.split('/')), 'RELEASE_UNSAFE_PATH')
    root = Path(root)
    path = root
    # Also check ancestors of the root: a junction must not redirect trusted inputs.
    for part in [*reversed(root.parents), root]:
        if part.exists():
            info = part.lstat()
            require(not part.is_symlink() and not getattr(info, 'st_file_attributes', 0) & 0x400,
                    'RELEASE_LINK_FORBIDDEN')
    for component in rel.parts:
        path = path / component
        if path.exists() or path.is_symlink():
            info = path.lstat()
            require(not path.is_symlink() and not stat.S_ISLNK(info.st_mode)
                    and not getattr(info, 'st_file_attributes', 0) & 0x400, 'RELEASE_LINK_FORBIDDEN')
    if exists:
        require(path.is_file(), 'RELEASE_ARTIFACT_MISSING')
    return path


def read_json(path):
    try:
        require(Path(path).stat().st_size <= 1024 * 1024, 'RELEASE_METADATA_LIMIT')
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except ReleaseError:
        raise
    except Exception:
        raise ReleaseError('RELEASE_METADATA_INVALID') from None


def release_id(value):
    require(isinstance(value, str) and re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,79}', value)
            and '..' not in value, 'RELEASE_ID_INVALID')
    return value


def entry_for(identifier, base=BASE):
    identifier = release_id(identifier)
    registry = read_json(safe_path(base, REGISTRY))
    require(registry.get('schema') == SCHEMA, 'RELEASE_REGISTRY_VERSION')
    entry = registry.get('releases', {}).get(identifier)
    require(isinstance(entry, dict), 'RELEASE_NOT_TRUSTED')
    require(entry.get('directory') == 'inference_releases/' + identifier, 'RELEASE_DIRECTORY_INVALID')
    return entry


def check_approval(manifest, entry, profiles):
    approval = manifest.get('approval')
    require(approval == entry.get('approval'), 'RELEASE_APPROVAL_MISMATCH')
    require(approval.get('status') == 'approved'
            and approval.get('quality_review') and approval.get('deployment_review')
            and set(profiles) <= set(approval.get('approved_profiles', [])),
            'RELEASE_ACTIVATION_BLOCKED')


def _manifest_contract(manifest, entry, identifier, deployment, profiles, dependencies):
    require(manifest.get('schema') == SCHEMA and manifest.get('release_id') == identifier,
            'RELEASE_VERSION_MISMATCH')
    require(manifest.get('model_version') == entry['model_version'], 'RELEASE_MODEL_VERSION')
    require(manifest.get('profiles') == PROFILES and manifest.get('feature_contract') == CONTRACTS,
            'RELEASE_FEATURE_CONTRACT')
    require(manifest.get('approval') == entry.get('approval'), 'RELEASE_APPROVAL_MISMATCH')
    if deployment:
        check_approval(manifest, entry, profiles)
        require(manifest['runtime'].get('linux_verified') is True, 'RELEASE_LINUX_UNVERIFIED')
    require(set(manifest['artifacts']) == set(SOURCES), 'RELEASE_ARTIFACT_SET')
    require(manifest['runtime']['packages'].keys() == set(PACKAGES), 'RELEASE_DEPENDENCY_CONTRACT')
    require(manifest['runtime']['python_implementation'] == 'CPython'
            and manifest['runtime']['python_major'] == 3, 'RELEASE_PYTHON_CONTRACT')
    if dependencies:
        require(platform.python_implementation() == manifest['runtime']['python_implementation']
                and sys.version_info.major == manifest['runtime']['python_major']
                and '.'.join(platform.python_version().split('.')[:2]) ==
                '.'.join(manifest['runtime']['locally_verified_python'].split('.')[:2]),
                'RELEASE_PYTHON_VERSION')
        for name, version in manifest['runtime']['packages'].items():
            require(importlib.metadata.version(name) == version, 'RELEASE_DEPENDENCY_VERSION')


def metadata(identifier, *, base=BASE, deployment=True, profiles=('legacy', 'semantic_ocr'),
             dependencies=True):
    """Validate Git-pinned release metadata, WITHOUT downloading/loading weights.

    The inline manifest is non-secret review metadata, not evidence that artifact
    bytes are installed. selected()/verify() must still check every downloaded byte.
    """
    try:
        entry = entry_for(identifier, base)
        if deployment:
            check_approval({'approval': entry.get('approval')}, entry, profiles)
        manifest = entry.get('manifest')
        require(isinstance(manifest, dict), 'RELEASE_INLINE_MANIFEST_REQUIRED')
        require(hashlib.sha256(canonical(manifest)).hexdigest() == entry['manifest_sha256'],
                'RELEASE_MANIFEST_NOT_PINNED')
        _manifest_contract(manifest, entry, identifier, deployment, profiles, dependencies)
        original = read_json(safe_path(base, SOURCE_CONTRACT))['artifacts']
        require(set(original) == set(SOURCES) and all(record['source'] == SOURCES[name]
                for name, record in original.items()) and manifest['artifacts'] == {
                    name: {key: record[key] for key in ('bytes', 'sha256')}
                    for name, record in original.items()}, 'RELEASE_ORIGINAL_ARTIFACTS_REQUIRED')
        return {'manifest': manifest, 'manifest_sha256': entry['manifest_sha256'],
                'artifact_bytes': sum(record['bytes'] for record in manifest['artifacts'].values())}
    except ReleaseError:
        raise
    except Exception:
        raise ReleaseError('RELEASE_METADATA_INVALID') from None


def verify(identifier, *, base=BASE, deployment=False, profiles=('legacy', 'semantic_ocr'),
           dependencies=True, directory=None):
    """Hash every byte before executing feature code or deserializing any model.

    directory is an internal export/install staging path, never an environment
    override. The registry remains the same trust anchor for staged bytes.
    """
    try:
        entry = entry_for(identifier, base)
        root = Path(directory) if directory is not None else Path(base) / entry['directory']
        manifest_path = safe_path(root, MANIFEST)
        require(sha256(manifest_path) == entry['manifest_sha256'], 'RELEASE_MANIFEST_NOT_PINNED')
        manifest = read_json(manifest_path)
        _manifest_contract(manifest, entry, identifier, deployment, profiles, dependencies)
        artifacts = manifest['artifacts']
        require(set(artifacts) == set(SOURCES), 'RELEASE_ARTIFACT_SET')
        actual = set()
        for path in root.rglob('*'):
            rel = path.relative_to(root).as_posix()
            safe_path(root, rel, exists=False)
            if path.is_file():
                actual.add(rel)
        # Feature source is compiled in memory; loading a release writes no pyc.
        require(actual == set(artifacts) | {MANIFEST}, 'RELEASE_UNEXPECTED_FILE')
        total = 0
        for name, record in artifacts.items():
            path = safe_path(root, name)
            require(path.stat().st_size == record['bytes'] and sha256(path) == record['sha256'],
                    'RELEASE_CHECKSUM_MISMATCH')
            total += record['bytes']
        return {'root': root, 'manifest': manifest, 'manifest_sha256': entry['manifest_sha256'],
                'files_verified': len(artifacts), 'artifact_bytes': total,
                'release_bytes': total + manifest_path.stat().st_size}
    except ReleaseError:
        raise
    except Exception:
        raise ReleaseError('RELEASE_VERIFICATION_FAILED') from None


def configured_identifier(*, base=BASE, environment=None, deployment=False):
    """Git-reviewed deployment pointer; an explicit environment pin overrides it.

    Local development keeps its original paths unless explicitly opting in.
    Updating the approved pointer in Git can activate the next immutable release
    on the next deployment without editing a production environment variable.
    """
    identifier = os.environ.get('PSMS_INFERENCE_RELEASE_ID', '').strip()
    if identifier:
        return identifier
    mode = (environment or os.environ.get('PSMS_ENV', 'local')).strip().lower()
    if deployment or mode in ('staging', 'production') or os.environ.get('VERCEL') == '1':
        if not (Path(base) / REGISTRY).exists():
            return None
        registry = read_json(safe_path(base, REGISTRY))
        require(registry.get('schema') == SCHEMA, 'RELEASE_REGISTRY_VERSION')
        return registry.get('deployment_release')
    return None


def selected(*, base=BASE, environment=None):
    """An explicit bad release never falls back to legacy source directories."""
    require_inference()
    identifier = configured_identifier(base=base, environment=environment)
    if not identifier:
        return None
    mode = (environment or os.environ.get('PSMS_ENV', 'local')).strip().lower()
    runtime = os.environ.get('PSMS_NLP_RUNTIME', 'legacy').strip()
    if os.environ.get('PSMS_INFERENCE_CACHE_ENABLED') == '1':
        from runtime_model_cache import ensure
        evidence = metadata(identifier, base=base, profiles=(runtime, 'semantic_ocr'))
        directory = ensure(identifier, evidence, base=base, profiles=(runtime, 'semantic_ocr'))
        return verify(identifier, base=base, deployment=True, directory=directory,
                      profiles=(runtime, 'semantic_ocr'))
    return verify(identifier, base=base, deployment=mode in ('staging', 'production')
                  or os.environ.get('VERCEL') == '1', profiles=(runtime, 'semantic_ocr'))


def selected_metadata(*, base=BASE, environment=None):
    """Startup selection in the source-only container does no network/model I/O."""
    if not inference_enabled():
        return None
    if os.environ.get('PSMS_INFERENCE_CACHE_ENABLED') != '1':
        return selected(base=base, environment=environment)
    identifier = configured_identifier(base=base, environment=environment, deployment=True)
    require(bool(identifier), 'RELEASE_SELECTION_REQUIRED')
    runtime = os.environ.get('PSMS_NLP_RUNTIME', 'legacy').strip()
    evidence = metadata(identifier, base=base, profiles=(runtime, 'semantic_ocr'))
    from runtime_model_cache import location
    evidence['root'] = location(identifier)
    return evidence


def profile_evidence(model_root, profile):
    """Recognize release paths even when the caller omitted the release setting."""
    root = Path(model_root).absolute()
    require(profile in PROFILES, 'RELEASE_PROFILE_INVALID')
    evidence = selected()
    if evidence is not None:
        require(root == evidence['root'].absolute() / PROFILES[profile], 'RELEASE_PROFILE_PATH')
        return evidence
    # Never treat an untrusted directory claiming to be a release as a local model.
    if root.name == PROFILES[profile] and (root.parent / MANIFEST).exists():
        identifier = root.parent.name
        evidence = verify(identifier, deployment=os.environ.get('PSMS_ENV') in ('staging', 'production')
                          or os.environ.get('VERCEL') == '1', profiles=(profile, 'semantic_ocr'))
        require(root == evidence['root'].absolute() / PROFILES[profile], 'RELEASE_PROFILE_PATH')
        return evidence
    require('inference_releases' not in root.parts, 'RELEASE_NOT_TRUSTED')
    return None


def execute_feature(path, name):
    """Execute verified feature code without writing inside an immutable release."""
    from types import ModuleType
    module = ModuleType(name)
    module.__file__ = str(path)
    exec(compile(Path(path).read_bytes(), str(path), 'exec'), module.__dict__)
    return module
