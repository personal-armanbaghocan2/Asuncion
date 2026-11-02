"""Copy-only private model export, offline verification and verified build retrieval.

Run as ``python -m deploy.inference_release ...`` from the project root.
Export never changes source models, trust pins, approvals or environment settings.
"""
from __future__ import annotations

import argparse
import io
import importlib.metadata
import json
import os
import platform
from pathlib import Path
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import inference_releases as releases


def prepare(identifier, model_version, *, base=releases.BASE):
    """Create review proposals only; never alter trusted pins or approvals.

    Use after separately approved local retraining. This inventory does not
    establish quality, provenance, portability, or safety of pickle content.
    """
    import hashlib
    identifier = releases.release_id(identifier)
    releases.require(isinstance(model_version, str) and 0 < len(model_version) <= 160,
                     'RELEASE_MODEL_VERSION')
    previous = releases.read_json(Path(base) / releases.SOURCE_CONTRACT)
    artifacts = {}
    for target, source in releases.SOURCES.items():
        path = releases.safe_path(base, source)
        artifacts[target] = {'source': source, 'bytes': path.stat().st_size,
                             'sha256': releases.sha256(path)}
    contract = {'schema': releases.SCHEMA, 'release_id': identifier, 'model_version': model_version,
        'profiles': releases.PROFILES, 'feature_contract': releases.CONTRACTS,
        'runtime': {**previous['runtime'], 'locally_verified_python': platform.python_version(),
                    'linux_verified': False,
                    'packages': {p: importlib.metadata.version(p) for p in releases.PACKAGES}},
        'approval': {'status': 'local_validation_only', 'approved_profiles': [],
                     'quality_review': None, 'deployment_review': None}, 'artifacts': artifacts}
    manifest = manifest_from_contract(contract)
    entry = {'directory': 'inference_releases/' + identifier, 'model_version': model_version,
             'manifest_sha256': hashlib.sha256(releases.canonical(manifest)).hexdigest(),
             'approval': contract['approval']}
    folder = releases.safe_path(base, 'output/inference_releases/' + identifier, exists=False)
    folder.mkdir(parents=True, exist_ok=False)
    (folder / 'sources.PROPOSED.json').write_bytes(releases.canonical(contract))
    (folder / 'registry-entry.PROPOSED.json').write_bytes(releases.canonical({identifier: entry}))
    return {'release_id': identifier, 'proposed': True, 'approval': 'local_validation_only'}


def _rename_owned(source, destination):
    # Windows scanners can briefly hold a freshly copied directory open. Retry
    # only a sharing/access denial; do not reinterpret other filesystem errors.
    for attempt in range(5):
        try:
            source.rename(destination)
            return
        except PermissionError:
            if os.name != 'nt' or attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def manifest_from_contract(contract):
    releases.require(contract['schema'] == releases.SCHEMA
                     and contract['feature_contract'] == releases.CONTRACTS
                     and set(contract['artifacts']) == set(releases.SOURCES), 'RELEASE_SOURCE_CONTRACT')
    for name, record in contract['artifacts'].items():
        releases.require(record['source'] == releases.SOURCES[name], 'RELEASE_SOURCE_CONTRACT')
    return {key: contract[key] for key in ('schema', 'release_id', 'model_version', 'profiles',
            'feature_contract', 'runtime', 'approval')} | {
        'artifacts': {name: {'sha256': record['sha256'], 'bytes': record['bytes']}
                      for name, record in contract['artifacts'].items()}}


def export(base=releases.BASE, *, expected_id=None):
    """Export only reviewed inference inputs. Existing releases are never replaced."""
    base = Path(base)
    contract = releases.read_json(releases.safe_path(base, releases.SOURCE_CONTRACT))
    identifier = releases.release_id(contract['release_id'])
    releases.require(expected_id is None or identifier == expected_id, 'RELEASE_EXPORT_SELECTION')
    entry = releases.entry_for(identifier, base)
    manifest = manifest_from_contract(contract)
    releases.require(__import__('hashlib').sha256(releases.canonical(manifest)).hexdigest()
                     == entry['manifest_sha256'], 'RELEASE_EXPORT_NOT_PINNED')
    target = releases.safe_path(base, entry['directory'], exists=False)
    releases.require(not target.exists(), 'RELEASE_ALREADY_EXISTS')
    # Validate all sources before creating any output, including the Gensim sidecar.
    for record in contract['artifacts'].values():
        source = releases.safe_path(base, record['source'])
        releases.require(source.stat().st_size == record['bytes']
                         and releases.sha256(source) == record['sha256'], 'RELEASE_SOURCE_CHANGED')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.export-', dir=target.parent) as temporary:
        staged = Path(temporary) / identifier
        staged.mkdir()
        for name, record in contract['artifacts'].items():
            destination = releases.safe_path(staged, name, exists=False)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(releases.safe_path(base, record['source']), destination)
        (staged / releases.MANIFEST).write_bytes(releases.canonical(manifest))
        evidence = releases.verify(identifier, base=base, directory=staged, dependencies=False)
        # mkdir reserves the destination without POSIX rename replacing a raced-in directory.
        target.mkdir()
        try:
            for child in staged.iterdir():
                _rename_owned(child, target / child.name)
        except BaseException:
            # Remove only the directory this export reserved; never touch model sources.
            shutil.rmtree(target)
            raise
    return summary(evidence)


def archive_bytes(root):
    """Deterministic stored ZIP, no timestamps, symlinks or external paths."""
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(Path(root).rglob('*')):
            if path.is_file():
                info = zipfile.ZipInfo(path.relative_to(root).as_posix(), (1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o100600 << 16
                archive.writestr(info, path.read_bytes())
    return stream.getvalue()


def pack(identifier, base=releases.BASE):
    """Write a private transport archive and a review descriptor; never edit registry."""
    import hashlib
    evidence = releases.verify(identifier, base=base, dependencies=False)
    payload = archive_bytes(evidence['root'])
    folder = releases.safe_path(base, 'output/inference_releases', exists=False)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (identifier + '.zip')
    with path.open('xb') as stream:
        stream.write(payload)
    descriptor = {'release_id': identifier, 'manifest_sha256': evidence['manifest_sha256'],
                  'archive_sha256': hashlib.sha256(payload).hexdigest(), 'archive_bytes': len(payload),
                  'approval': evidence['manifest']['approval'], 'published': False}
    (folder / (identifier + '.transport.json')).write_bytes(releases.canonical(descriptor))
    return descriptor


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def install_archive(payload, identifier, *, base=releases.BASE, deployment=True, profiles=None):
    """Verified extraction of a bounded, pinned archive; no extractall or overwrite."""
    import hashlib
    entry = releases.entry_for(identifier, base)
    delivery = entry.get('delivery', {})
    releases.require(len(payload) == delivery.get('archive_bytes')
                     and hashlib.sha256(payload).hexdigest() == delivery.get('archive_sha256'),
                     'RELEASE_ARCHIVE_CHECKSUM')
    profiles = profiles or (os.environ.get('PSMS_NLP_RUNTIME', 'legacy'), 'semantic_ocr')
    if deployment:
        releases.check_approval({'approval': entry['approval']}, entry, profiles)
    target = releases.safe_path(base, entry['directory'], exists=False)
    releases.require(not target.exists(), 'RELEASE_ALREADY_EXISTS')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.retrieve-', dir=target.parent) as temporary:
        staged = Path(temporary) / identifier
        staged.mkdir()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = archive.infolist()
            names = [m.filename for m in members]
            releases.require(len(names) == len(set(names)) and set(names)
                             == set(releases.SOURCES) | {releases.MANIFEST}, 'RELEASE_ARCHIVE_FILES')
            releases.require(all(m.compress_type == zipfile.ZIP_STORED and not m.is_dir()
                                 and m.file_size <= delivery['archive_bytes']
                                 and (m.external_attr >> 16) & 0o170000 == 0o100000
                                 for m in members), 'RELEASE_ARCHIVE_TYPE')
            releases.require(sum(m.file_size for m in members) <= delivery['archive_bytes'],
                             'RELEASE_ARCHIVE_LIMIT')
            for member in members:
                path = releases.safe_path(staged, member.filename, exists=False)
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, path.open('xb') as destination:
                    shutil.copyfileobj(source, destination)
        evidence = releases.verify(identifier, base=base, directory=staged, deployment=deployment,
                                   profiles=profiles, dependencies=False)
        target.mkdir()
        try:
            for child in staged.iterdir():
                _rename_owned(child, target / child.name)
        except BaseException:
            shutil.rmtree(target)
            raise
    return summary(evidence)


def retrieve(identifier, *, base=releases.BASE):
    """Build only: approved immutable HTTPS object, TLS verified, no redirects.

    The registry pins the non-secret URL and archive digest. A narrowly scoped
    read-only bearer token comes from deployment secret management. No URLs,
    tokens, response bodies or upstream exceptions are logged.
    """
    entry = releases.entry_for(identifier, base)
    releases.check_approval({'approval': entry['approval']}, entry,
                           (os.environ.get('PSMS_NLP_RUNTIME', 'legacy'), 'semantic_ocr'))
    delivery = entry.get('delivery', {})
    url = delivery.get('url', '')
    token = os.environ.get('PSMS_INFERENCE_READ_TOKEN', '')
    try:
        parsed = urllib.parse.urlsplit(url)
        releases.require(parsed.scheme == 'https' and parsed.hostname and not parsed.username
                         and not parsed.password and not parsed.query and not parsed.fragment
                         and parsed.port in (None, 443) and token and '\r' not in token
                         and '\n' not in token and type(delivery['archive_bytes']) is int
                         and 0 < delivery['archive_bytes'] <= 512 * 1024 * 1024,
                         'RELEASE_DELIVERY_CONFIGURATION')
        request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token})
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=60) as response:
            releases.require(response.status == 200, 'RELEASE_RETRIEVAL_FAILED')
            payload = response.read(delivery['archive_bytes'] + 1)
        return install_archive(payload, identifier, base=base)
    except releases.ReleaseError:
        raise
    except Exception:
        raise releases.ReleaseError('RELEASE_RETRIEVAL_FAILED') from None


def summary(evidence):
    return {key: evidence[key] for key in ('files_verified', 'artifact_bytes', 'release_bytes',
                                          'manifest_sha256')}


def synthetic(identifier, *, base=releases.BASE):
    """Real engines, synthetic strings only; does not select or authorize RF in Flask."""
    evidence = releases.verify(identifier, base=base)
    root = evidence['root']
    import fasttext_nlp_runtime as legacy
    import retrained_nlp_runtime as rf
    from sentence_transformers import SentenceTransformer
    import numpy as np
    pair = ('SYNTHETIC computer; minimum 16 GB RAM; quantity 10 pieces',
            'SYNTHETIC computer; 8 GB RAM; quantity 8 pieces')
    # Loader entry points independently verify their release before deserialization.
    predictions = [legacy.predict_pair(str(root / 'legacy'), *pair),
                   rf.predict_pair(root / 'rf', *pair)]
    releases.require(all(p['status'] in releases.STATUS and p['issue'] in releases.ISSUES
                         for p in predictions), 'RELEASE_SYNTHETIC_PREDICTION')
    model = SentenceTransformer(str(root / 'semantic'), local_files_only=True, device='cpu')
    vectors = model.encode(['SYNTHETIC item description', 'SYNTHETIC allocated budget'],
                           normalize_embeddings=True)
    releases.require(vectors.shape == (2, 384) and np.isfinite(vectors).all(),
                     'RELEASE_SEMANTIC_CONTRACT')
    return {'legacy_features': predictions[0]['feature_dimension'],
            'legacy_status_features': predictions[0]['status_feature_dimension'],
            'rf_features': predictions[1]['feature_dimension'], 'semantic_shape': list(vectors.shape),
            'quality_approval': False, 'online_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'export', 'pack', 'verify', 'fetch'))
    parser.add_argument('--release')
    parser.add_argument('--model-version')
    parser.add_argument('--synthetic', action='store_true')
    parser.add_argument('--deployment', action='store_true')
    args = parser.parse_args()
    try:
        identifier = args.release or releases.read_json(releases.BASE / releases.SOURCE_CONTRACT)['release_id']
        if args.action == 'prepare':
            releases.require(bool(args.release and args.model_version), 'RELEASE_PROPOSAL_ARGUMENTS')
            report = prepare(args.release, args.model_version)
        elif args.action == 'export':
            report = export(expected_id=identifier)
        elif args.action == 'pack':
            report = pack(identifier)
        elif args.action == 'fetch':
            report = retrieve(identifier)
        else:
            report = summary(releases.verify(identifier, deployment=args.deployment,
                                            profiles=(os.environ.get('PSMS_NLP_RUNTIME', 'legacy'),
                                                      'semantic_ocr')))
        if args.synthetic:
            os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
            report['synthetic'] = synthetic(identifier)
        print(json.dumps(report, sort_keys=True))
        return 0
    except releases.ReleaseError as error:
        print(str(error))
        return 1
    except Exception:
        print('RELEASE_OPERATION_FAILED')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
