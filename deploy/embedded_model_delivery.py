"""Bind approved model bytes from a private, digest-pinned image at build time.

Only prepares local files. No network, credentials, SQL, approval changes or
publication. Runtime keeps the existing installed-release verification path.
"""
import json
from pathlib import Path
import re
import shutil

import inference_releases as releases

IMAGE_PREFIX = 'vcr.vercel.com/arman-a87f/asuncion/approved-models@sha256:'


def prepare_models(destination, source_base, *, base=releases.BASE):
    """Copy exactly the original approved inputs; never download or deserialize."""
    identifier = 'nlp-online-original-v2'
    evidence = releases.metadata(identifier, base=base, dependencies=False,
        profiles=('retrained_rf', 'semantic_ocr'))
    destination = Path(destination)
    releases.require(not destination.exists(), 'EMBEDDED_NEW_OUTPUT_REQUIRED')
    # Check every input before writing an output directory.
    for name, record in evidence['manifest']['artifacts'].items():
        original = releases.safe_path(source_base, releases.SOURCES[name])
        releases.require(original.stat().st_size == record['bytes']
            and releases.sha256(original) == record['sha256'], 'EMBEDDED_SOURCE_MISMATCH')
    root = destination / 'inference_releases' / identifier
    root.mkdir(parents=True)
    for name in evidence['manifest']['artifacts']:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(releases.safe_path(source_base, releases.SOURCES[name]), target)
    (root / releases.MANIFEST).write_bytes(releases.canonical(evidence['manifest']))
    verified = releases.verify(identifier, base=base, directory=root,
        deployment=True, dependencies=False, profiles=('retrained_rf', 'semantic_ocr'))
    (destination / 'Dockerfile').write_text(
        'FROM scratch\nCOPY inference_releases/ /inference_releases/\n', encoding='utf-8')
    (destination / '.dockerignore').write_text(
        '**\n!Dockerfile\n!inference_releases/\n!inference_releases/**\n', encoding='utf-8')
    return {'release_id': identifier, 'manifest_sha256': evidence['manifest_sha256'],
            'files_verified': verified['files_verified'], 'artifact_bytes': verified['artifact_bytes']}


def apply_image(context, descriptor, *, base=releases.BASE):
    """Use an immutable same-project private image, with full build-time checks."""
    context = Path(context)
    data = releases.read_json(descriptor)
    identifier = 'nlp-online-original-v2'
    evidence = releases.metadata(identifier, base=base, dependencies=False,
        profiles=('retrained_rf', 'semantic_ocr'))
    releases.require(set(data) == {'release_id', 'manifest_sha256', 'image'}
        and data['release_id'] == identifier
        and data['manifest_sha256'] == evidence['manifest_sha256']
        and isinstance(data['image'], str)
        and re.fullmatch(re.escape(IMAGE_PREFIX) + '[0-9a-f]{64}', data['image']),
        'EMBEDDED_IMAGE_PIN_REQUIRED')
    registry = releases.read_json(context / 'runtime' / releases.REGISTRY)
    releases.require(registry.get('deployment_release') == identifier
        and registry.get('releases', {}).get(identifier) == releases.entry_for(identifier, base),
        'EMBEDDED_CONTEXT_RELEASE_MISMATCH')
    dockerfile = context / 'Dockerfile.vercel'
    docker = dockerfile.read_text(encoding='utf-8')
    releases.require(docker.count('\nUSER psms\n') == 1
        and 'PSMS_INFERENCE_ENABLED=1' in docker
        and 'PSMS_NLP_RUNTIME=retrained_rf' in docker
        and 'EMBEDDED_APPROVED_MODELS' not in docker, 'EMBEDDED_CONTEXT_INVALID')
    addition = ('\n# EMBEDDED_APPROVED_MODELS: original bytes, no runtime Storage retrieval.\n'
        'COPY --from=' + data['image'] + ' /inference_releases/' + identifier
        + '/ /app/inference_releases/' + identifier + '/\n'
        'ENV PSMS_INFERENCE_CACHE_ENABLED=0\n'
        'RUN chmod -R a+rX /app/inference_releases\n')
    docker = docker.replace('\nUSER psms\n', addition + '\nUSER psms\n'
        'RUN python -B -c "import inference_releases as r; e=r.selected(); '
        "print('EMBEDDED_MODELS_VERIFIED', e['files_verified'])\"\n")
    dockerfile.write_text(docker, encoding='utf-8')
    return {'model_delivery': 'private_image', 'runtime_model_downloads': False,
            'model_image': data['image'], 'manifest_sha256': evidence['manifest_sha256']}


def use_containerfile(context):
    """Use the supported equivalent filename in its own VCR build repository.

    This preserves all existing dockerfile repository images for rollback.
    Only the generated source-upload context changes, not the runtime files.
    """
    context = Path(context)
    source = context / 'Dockerfile.vercel'
    target = context / 'Containerfile.vercel'
    releases.require(source.is_file() and not target.exists(), 'EMBEDDED_CONTAINERFILE_CONFLICT')
    rules = {}
    for name in ('.dockerignore', '.vercelignore'):
        path = context / name
        rules[path] = path.read_text(encoding='utf-8')
    source.rename(target)
    for path, text in rules.items():
        path.write_text(text + '\n!Containerfile.vercel\n', encoding='utf-8')
